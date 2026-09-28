"""
TCPServerAsync.py - Server TCP model NON-BLOCKING / EVENT-DRIVEN (asyncio).

Perbedaan inti dengan TCPServer.py:
  - Tidak ada thread per client. SEMUA client dilayani satu thread tunggal.
  - Soket dipasang non-blocking; event loop memakai epoll (Linux) / kqueue
    (macOS) / IOCP-select (Windows) untuk menunggu banyak soket sekaligus.
  - "await reader.readexactly(...)" tidak memblokir proses: ia menyerahkan
    kendali ke event loop, yang menjalankan coroutine lain yang datanya siap,
    lalu melanjutkan coroutine ini ketika datanya tiba.

Protokol frame-nya IDENTIK dengan TCPServer.py, sehingga TCPClient.py yang
sama bisa dipakai untuk kedua server (hanya port yang berbeda).

Jalankan:  python TCPServerAsync.py --port 12100
Benchmark: python TCPServerAsync.py --port 12100 --quiet
"""

import argparse
import asyncio
import json
import os
import time

import metrics
from protocol import (T_CMD, T_ERROR, T_FILE, T_INFO, T_TEXT, build_frame,
                      pack_file, recv_frame_async, unpack_file)

FILES_DIR = "server_files"
QUIET = False

# Tabel client aktif: writer -> {"name": str, "addr": tuple}
# Tidak perlu Lock: event loop single-threaded, dan tidak ada await di
# tengah pembacaan/penulisan dict ini, jadi tidak mungkin ada race condition.
clients = {}


def log(msg):
    if not QUIET:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def safe_send(writer, mtype, payload):
    """write() hanya menaruh byte di buffer transport; event loop yang
    mengirimnya saat soket siap. Jadi client lambat tidak memblokir yang lain."""
    if writer.is_closing():
        return
    try:
        writer.write(build_frame(mtype, payload))
    except OSError:
        pass


def broadcast(mtype, payload, exclude=None):
    frame = build_frame(mtype, payload)
    for w in list(clients):
        if w is exclude or w.is_closing():
            continue
        try:
            w.write(frame)
        except OSError:
            pass


async def handle_command(writer, cmd):
    """Return False jika client minta keluar."""
    parts = cmd.strip().split(maxsplit=1)
    if not parts:
        return True
    name, arg = parts[0].lower(), (parts[1] if len(parts) > 1 else "")

    if name == "nick" and arg:
        old = clients[writer]["name"]
        clients[writer]["name"] = arg
        safe_send(writer, T_INFO, f"Nama diubah menjadi '{arg}'".encode())
        broadcast(T_INFO, f"{old} sekarang bernama {arg}".encode(), exclude=writer)
    elif name == "users":
        names = [v["name"] for v in clients.values()]
        safe_send(writer, T_INFO, f"Online ({len(names)}): {', '.join(names)}".encode())
    elif name == "list":
        files = sorted(os.listdir(FILES_DIR))
        text = "File di server: " + (", ".join(files) if files else "(kosong)")
        safe_send(writer, T_INFO, text.encode())
    elif name == "get" and arg:
        path = os.path.join(FILES_DIR, os.path.basename(arg))
        if not os.path.isfile(path):
            safe_send(writer, T_ERROR, f"File '{arg}' tidak ditemukan".encode())
        else:
            # File I/O bersifat blocking; dilempar ke thread pool agar event
            # loop tidak berhenti saat membaca file besar.
            data = await asyncio.to_thread(lambda p: open(p, "rb").read(), path)
            safe_send(writer, T_FILE, pack_file(os.path.basename(path), data))
    elif name == "echo":
        # Balas HANYA ke pengirim (tanpa broadcast). Dipakai benchmark untuk
        # mengukur model konkurensi murni, tanpa beban fan-out O(N^2).
        safe_send(writer, T_INFO, f"ACK: {arg}".encode())
    elif name == "stats":
        # Server mengukur dirinya sendiri (lihat metrics.py).
        snap = metrics.snapshot()
        snap["clients"] = len(clients)
        snap["tasks"] = len(asyncio.all_tasks())
        safe_send(writer, T_INFO, ("STATS " + json.dumps(snap)).encode())
    elif name == "quit":
        return False
    else:
        safe_send(writer, T_ERROR, f"Perintah tidak dikenal: {cmd}".encode())
    return True


async def handle_client(reader, writer):
    """Satu COROUTINE (bukan thread) per client. Biayanya hanya beberapa KB."""
    addr = writer.get_extra_info("peername")
    default_name = f"{addr[0]}:{addr[1]}"
    clients[writer] = {"name": default_name, "addr": addr}
    sock = writer.get_extra_info("socket")
    log(f"TERHUBUNG {addr} | fd={sock.fileno() if sock else '?'} "
        f"| client aktif={len(clients)} | task aktif={len(asyncio.all_tasks())}")
    safe_send(writer, T_INFO,
              f"Selamat datang {default_name}. Ketik /help untuk bantuan.".encode())

    try:
        while True:
            # await = titik yield ke event loop, BUKAN blokir proses
            mtype, payload = await recv_frame_async(reader)
            sender = clients[writer]["name"]

            if mtype == T_TEXT:
                text = payload.decode(errors="replace")
                log(f"TEXT dari {sender}: {text!r}")
                safe_send(writer, T_INFO, f"ACK: {text}".encode())
                broadcast(T_TEXT, f"[{sender}] {text}".encode(), exclude=writer)

            elif mtype == T_FILE:
                fname, data = unpack_file(payload)
                fname = os.path.basename(fname)
                await asyncio.to_thread(
                    lambda p, d: open(p, "wb").write(d),
                    os.path.join(FILES_DIR, fname), data)
                log(f"FILE dari {sender}: {fname} ({len(data)} byte)")
                safe_send(writer, T_INFO,
                          f"File '{fname}' ({len(data)} byte) tersimpan di server".encode())
                broadcast(T_INFO,
                          f"{sender} mengunggah '{fname}'. Unduh: /get {fname}".encode(),
                          exclude=writer)

            elif mtype == T_CMD:
                cmd = payload.decode(errors="replace")
                log(f"CMD dari {sender}: /{cmd}")
                if not await handle_command(writer, cmd):
                    break
            else:
                safe_send(writer, T_ERROR, b"Tipe frame tidak dikenal")

            # Backpressure: jika buffer kirim menumpuk (client lambat membaca),
            # tunggu sampai buffer menyusut. Tanpa ini memori server bisa membengkak.
            await writer.drain()

    except (asyncio.IncompleteReadError, ConnectionError, ValueError, OSError):
        pass
    except asyncio.CancelledError:
        raise
    finally:
        info = clients.pop(writer, None)
        try:
            writer.close()
            await writer.wait_closed()
        except OSError:
            pass
        if info:
            broadcast(T_INFO, f"{info['name']} keluar".encode())
        log(f"TERPUTUS {addr} | client aktif={len(clients)}")


async def main_async(args):
    os.makedirs(FILES_DIR, exist_ok=True)
    server = await asyncio.start_server(
        handle_client, args.host or None, args.port, backlog=args.backlog)
    fds = [s.fileno() for s in server.sockets]
    print(f"[ASYNC] Event loop {type(asyncio.get_running_loop()).__name__} "
          f"LISTEN di port {args.port} | pid={os.getpid()} | listen fd={fds}", flush=True)
    async with server:
        await server.serve_forever()


def main():
    global QUIET
    ap = argparse.ArgumentParser(description="TCP server non-blocking (asyncio)")
    ap.add_argument("--host", default="")
    ap.add_argument("--port", type=int, default=12100)
    ap.add_argument("--backlog", type=int, default=128)
    ap.add_argument("--quiet", action="store_true", help="matikan log (untuk benchmark)")
    args = ap.parse_args()
    QUIET = args.quiet
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n[ASYNC] Server dihentikan", flush=True)


if __name__ == "__main__":
    main()
