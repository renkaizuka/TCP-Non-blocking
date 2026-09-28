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


# ---------------- Pembacaan metrik server ----------------
#
# Cara utama : TANYA SERVER lewat soket (perintah /stats). Server mengukur
#              dirinya sendiri, jadi tidak bergantung pada PID maupun /proc.
#              Inilah yang dipakai secara default.
# Cadangan   : baca /proc/<pid> atau psutil dari luar, hanya kalau --pid
#              diberikan dan server tidak menjawab /stats.

HAS_PROC = os.path.isdir("/proc")
try:
    import psutil
except ImportError:
    psutil = None


async def ask_server_stats(host, port):
    """Buka koneksi singkat, kirim /stats, kembalikan hasil ukur server."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=10)
    except (OSError, asyncio.TimeoutError):
        return None
    try:
        await asyncio.wait_for(recv_frame_async(reader), timeout=10)  # selamat datang
        writer.write(build_frame(T_CMD, b"stats"))
        await writer.drain()
        for _ in range(20):          # lewati pesan lain (broadcast, dsb)
            mtype, data = await asyncio.wait_for(recv_frame_async(reader),
                                                 timeout=10)
            if mtype == T_INFO and data.startswith(b"STATS "):
                return json.loads(data[6:].decode())
    except Exception:
        return None
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except OSError:
            pass
    return None


def read_proc(pid):
    """Cadangan: ukur proses lain dari luar. Bisa gagal (PID salah, OS lain)."""
    out = {"rss_kb": None, "threads": None, "cpu_s": None, "fds": None}
    if pid is None:
        return out

    if HAS_PROC:
        try:
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        out["rss_kb"] = int(line.split()[1])
                    elif line.startswith("Threads:"):
                        out["threads"] = int(line.split()[1])
            with open(f"/proc/{pid}/stat") as f:
                parts = f.read().rsplit(") ", 1)[1].split()
                out["cpu_s"] = (int(parts[11]) + int(parts[12])) / CLK_TCK
            out["fds"] = len(os.listdir(f"/proc/{pid}/fd"))
        except (OSError, IndexError, ValueError):
            pass
        return out

    if psutil is not None:
        try:
            p = psutil.Process(pid)
            with p.oneshot():
                out["rss_kb"] = p.memory_info().rss // 1024
                out["threads"] = p.num_threads()
                cpu = p.cpu_times()
                out["cpu_s"] = cpu.user + cpu.system
                try:
                    out["fds"] = (p.num_fds() if hasattr(p, "num_fds")
                                  else p.num_handles())
                except Exception:
                    pass
        except Exception:
            pass
    return out


async def measure(host, port, pid):
    """Ambil metrik server: coba lewat soket dulu, baru lewat PID."""
    snap = await ask_server_stats(host, port)
    if snap and snap.get("rss_kb") is not None:
        snap["_via"] = "soket"
        return snap
    fallback = read_proc(pid)
    fallback["_via"] = "pid"
    if snap:                                   # soket jawab tapi RSS kosong
        for k in ("threads", "fds", "cpu_s"):
            if fallback.get(k) is None and snap.get(k) is not None:
                fallback[k] = snap[k]
        fallback["_via"] = "campuran"
    return fallback


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

    base = await measure(args.host, args.port, args.pid)

    tasks = [asyncio.create_task(
        one_client(i, args.host, args.port, args.rounds, args.interval,
                   args.mode, latencies, errors, ready, go))
        for i in range(args.clients)]

    # Tunggu semua client selesai connect
    for _ in range(args.clients):
        await ready.acquire()
    await asyncio.sleep(0.5)                    # biarkan server stabil

    peak = await measure(args.host, args.port, args.pid)  # saat N koneksi terbuka

    t_start = time.perf_counter()
    go.set()
    await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - t_start

    after = await measure(args.host, args.port, args.pid)

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

    have_rss = base.get("rss_kb") is not None and peak.get("rss_kb") is not None
    res.update({
        "metrics_via": peak.get("_via"),
        "rss_source": peak.get("rss_source"),
        "rss_idle_kb": base.get("rss_kb"),
        "rss_peak_kb": peak.get("rss_kb"),
        "rss_delta_kb": (peak["rss_kb"] - base["rss_kb"]) if have_rss else None,
        "rss_per_conn_kb": round((peak["rss_kb"] - base["rss_kb"]) / args.clients, 1)
        if have_rss else None,
        "threads_idle": base.get("threads"),
        "threads_peak": peak.get("threads"),
        "fds_idle": base.get("fds"),
        "fds_peak": peak.get("fds"),
    })

    if base.get("cpu_s") is not None and after.get("cpu_s") is not None:
        used = after["cpu_s"] - base["cpu_s"]
        res["cpu_s_used"] = round(used, 3)
        if elapsed:
            res["cpu_percent"] = round(used / elapsed * 100, 1)
        if total_msgs:
            res["cpu_us_per_msg"] = round(used / total_msgs * 1e6, 1)

    if not have_rss:
        res["metrics_warning"] = (
            "Memori tidak terukur. Pastikan server versi terbaru (punya "
            "metrics.py), atau pasang psutil: pip install psutil")

    if errors:
        res["error_sample"] = errors[:3]

    print(json.dumps(res, indent=2))
    if not have_rss:
        print("\n[!] Memori server tidak terukur.", flush=True)
        print("    1. Pastikan server dijalankan dari folder yang berisi metrics.py")
        print("    2. Uji langsung:  python metrics.py")
        print("    3. Kalau masih kosong:  pip install psutil\n")
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
    ap.add_argument("--pid", type=int,
                    help="PID server (opsional, hanya sebagai cadangan; "
                         "pengukuran utama lewat perintah /stats di soket)")
    ap.add_argument("--mode", choices=["echo", "chat"], default="echo",
                    help="echo = balasan hanya ke pengirim; chat = ACK + broadcast")
    ap.add_argument("--label", default="server")
    ap.add_argument("--out", help="file JSONL untuk menyimpan hasil")
    args = ap.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
