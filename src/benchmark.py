"""
benchmark.py - Membandingkan server thread-per-client vs event-driven.

Mengukur, untuk N koneksi bersamaan:
  - Memori server  : RSS (Resident Set Size) dari /proc/<pid>/status
  - Beban prosesor : waktu CPU (utime+stime) dari /proc/<pid>/stat
  - Waktu respons  : RTT aplikasi (kirim TEXT -> terima ACK), p50/p95/p99
  - Overhead OS    : jumlah thread dan file descriptor server

Client dijalankan dengan asyncio agar 100 koneksi tidak memerlukan 100 thread
di sisi penguji (sehingga yang terukur adalah beban SERVER, bukan client).

Contoh:
    python benchmark.py --port 12000 --clients 50  --label "Threading"
    python benchmark.py --port 12100 --clients 100 --label "Asyncio"
"""

import argparse
import asyncio
import json
import os
import statistics
import time

from protocol import T_CMD, T_INFO, T_TEXT, build_frame, recv_frame_async

CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
PAGE_KB = 4


# ---------------- Pembacaan metrik proses server (Linux) ----------------

def read_proc(pid):
    """Ambil RSS (KB), jumlah thread, dan waktu CPU (detik) proses server."""
    out = {"rss_kb": None, "threads": None, "cpu_s": None, "fds": None}
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    out["rss_kb"] = int(line.split()[1])
                elif line.startswith("Threads:"):
                    out["threads"] = int(line.split()[1])
        with open(f"/proc/{pid}/stat") as f:
            parts = f.read().rsplit(") ", 1)[1].split()
            # field 14 utime, 15 stime (indeks 11 dan 12 setelah pemotongan)
            out["cpu_s"] = (int(parts[11]) + int(parts[12])) / CLK_TCK
        out["fds"] = len(os.listdir(f"/proc/{pid}/fd"))
    except (OSError, IndexError, ValueError):
        pass
    return out


# ---------------- Client asinkron ----------------

async def one_client(cid, host, port, rounds, interval, mode, latencies, errors,
                     ready, go):
    """Satu koneksi: connect, tunggu semua siap, lalu kirim <rounds> pesan
    dan catat waktu sampai ACK diterima.

    mode 'echo' : kirim /echo -> server hanya membalas pengirim.
                  Mengukur model konkurensi murni (beban O(N)).
    mode 'chat' : kirim TEXT -> server membalas ACK DAN broadcast ke N-1
                  client lain. Mengukur beban aplikasi nyata (O(N^2)).
    """
    try:
        reader, writer = await asyncio.open_connection(host, port)
    except OSError as e:
        errors.append(f"connect: {e}")
        ready.release()
        return

    try:
        await recv_frame_async(reader)          # pesan selamat datang
    except Exception as e:
        errors.append(f"welcome: {e}")
        ready.release()
        return

    ready.release()
    await go.wait()                             # semua client mulai serempak

    try:
        for r in range(rounds):
            body = f"c{cid}-r{r}"
            if mode == "echo":
                frame = build_frame(T_CMD, f"echo {body}".encode())
            else:
                frame = build_frame(T_TEXT, body.encode())
            t0 = time.perf_counter()
            writer.write(frame)
            await writer.drain()

            # Tunggu ACK milik kita sendiri. Broadcast dari client lain
            # (tipe T_TEXT) diabaikan.
            while True:
                mtype, data = await asyncio.wait_for(recv_frame_async(reader),
                                                     timeout=30)
                if mtype == T_INFO and data.startswith(b"ACK:"):
                    latencies.append((time.perf_counter() - t0) * 1000)
                    break
            if interval:
                await asyncio.sleep(interval)
    except Exception as e:
        errors.append(f"{type(e).__name__}: {e}")
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except OSError:
            pass


async def run(args):
    latencies, errors = [], []
    ready = asyncio.Semaphore(0)
    go = asyncio.Event()

    base = read_proc(args.pid) if args.pid else None

    tasks = [asyncio.create_task(
        one_client(i, args.host, args.port, args.rounds, args.interval,
                   args.mode, latencies, errors, ready, go))
        for i in range(args.clients)]

    # Tunggu semua client selesai connect
    for _ in range(args.clients):
        await ready.acquire()
    await asyncio.sleep(0.5)                    # biarkan server stabil

    peak = read_proc(args.pid) if args.pid else None   # diukur saat N koneksi terbuka

    t_start = time.perf_counter()
    go.set()
    await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - t_start

    after = read_proc(args.pid) if args.pid else None

    total_msgs = len(latencies)
    res = {
        "label": args.label,
        "mode": args.mode,
        "clients": args.clients,
        "rounds": args.rounds,
        "messages_ok": total_msgs,
        "errors": len(errors),
        "elapsed_s": round(elapsed, 3),
        "throughput_msg_s": round(total_msgs / elapsed, 1) if elapsed else 0,
    }
    if latencies:
        s = sorted(latencies)
        res.update({
            "lat_avg_ms": round(statistics.fmean(s), 3),
            "lat_p50_ms": round(s[len(s) // 2], 3),
            "lat_p95_ms": round(s[int(len(s) * 0.95)], 3),
            "lat_p99_ms": round(s[min(int(len(s) * 0.99), len(s) - 1)], 3),
            "lat_max_ms": round(s[-1], 3),
        })
    if base and peak:
        res.update({
            "rss_idle_kb": base["rss_kb"],
            "rss_peak_kb": peak["rss_kb"],
            "rss_delta_kb": (peak["rss_kb"] - base["rss_kb"])
            if peak["rss_kb"] and base["rss_kb"] else None,
            "rss_per_conn_kb": round((peak["rss_kb"] - base["rss_kb"]) / args.clients, 1)
            if peak["rss_kb"] and base["rss_kb"] else None,
            "threads_idle": base["threads"],
            "threads_peak": peak["threads"],
            "fds_idle": base["fds"],
            "fds_peak": peak["fds"],
            "cpu_s_used": round(after["cpu_s"] - base["cpu_s"], 3)
            if after and after["cpu_s"] is not None else None,
        })
        if after and after["cpu_s"] is not None and elapsed:
            res["cpu_percent"] = round(
                (after["cpu_s"] - base["cpu_s"]) / elapsed * 100, 1)
            res["cpu_us_per_msg"] = round(
                (after["cpu_s"] - base["cpu_s"]) / total_msgs * 1e6, 1) if total_msgs else None

    if errors:
        res["error_sample"] = errors[:3]

    print(json.dumps(res, indent=2))
    if args.out:
        with open(args.out, "a") as f:
            f.write(json.dumps(res) + "\n")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--clients", type=int, default=50)
    ap.add_argument("--rounds", type=int, default=20, help="pesan per client")
    ap.add_argument("--interval", type=float, default=0.0)
    ap.add_argument("--pid", type=int, help="PID server untuk pengukuran /proc")
    ap.add_argument("--mode", choices=["echo", "chat"], default="echo",
                    help="echo = balasan hanya ke pengirim; chat = ACK + broadcast")
    ap.add_argument("--label", default="server")
    ap.add_argument("--out", help="file JSONL untuk menyimpan hasil")
    args = ap.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
