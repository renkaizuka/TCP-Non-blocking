"""
TCPServer.py - Server TCP multi-client model THREAD-PER-CLIENT (blocking I/O).

Setiap koneksi dilayani satu thread OS tersendiri. Panggilan recv() yang
blocking pada satu thread tidak menghambat client lain karena kernel
menjadwalkan thread lain.

Jalankan:  python TCPServer.py --port 12000
Benchmark: python TCPServer.py --port 12000 --quiet
"""

import argparse
import json
import os
import socket
import threading
import time

import metrics
from protocol import (T_CMD, T_ERROR, T_FILE, T_INFO, T_TEXT, pack_file,
                      recv_frame, send_frame, unpack_file)

FILES_DIR = "server_files"
QUIET = False

# Tabel client aktif: connectionSocket -> {"name": str, "lock": Lock}
clients = {}
clients_lock = threading.Lock()


def log(msg):
    if not QUIET:
        print(f"[{time.strftime('%H:%M:%S')}] [{threading.current_thread().name}] {msg}",
              flush=True)


def safe_send(conn, mtype, payload):
    """Beberapa thread bisa mengirim ke soket yang sama (mis. broadcast),
    jadi tiap soket punya lock agar frame tidak saling bertumpuk."""
    with clients_lock:
        info = clients.get(conn)
    if info is None:
        return
    try:
        with info["lock"]:
            send_frame(conn, mtype, payload)
    except OSError:
        pass


def broadcast(mtype, payload, exclude=None):
    with clients_lock:
        targets = [c for c in clients if c is not exclude]
    for c in targets:
        safe_send(c, mtype, payload)


def handle_command(conn, cmd):
    """Return False jika client minta keluar."""
    parts = cmd.strip().split(maxsplit=1)
    if not parts:
        return True
    name, arg = parts[0].lower(), (parts[1] if len(parts) > 1 else "")

    if name == "nick" and arg:
        with clients_lock:
            old = clients[conn]["name"]
            clients[conn]["name"] = arg
        safe_send(conn, T_INFO, f"Nama diubah menjadi '{arg}'".encode())
        broadcast(T_INFO, f"{old} sekarang bernama {arg}".encode(), exclude=conn)
    elif name == "users":
        with clients_lock:
            names = [v["name"] for v in clients.values()]
        safe_send(conn, T_INFO, f"Online ({len(names)}): {', '.join(names)}".encode())
    elif name == "list":
        files = sorted(os.listdir(FILES_DIR))
        text = "File di server: " + (", ".join(files) if files else "(kosong)")
        safe_send(conn, T_INFO, text.encode())
    elif name == "get" and arg:
        path = os.path.join(FILES_DIR, os.path.basename(arg))
        if not os.path.isfile(path):
            safe_send(conn, T_ERROR, f"File '{arg}' tidak ditemukan".encode())
        else:
            with open(path, "rb") as f:
                safe_send(conn, T_FILE, pack_file(os.path.basename(path), f.read()))
    elif name == "echo":
        # Balas HANYA ke pengirim (tanpa broadcast). Dipakai benchmark untuk
        # mengukur model konkurensi murni, tanpa beban fan-out O(N^2).
        safe_send(conn, T_INFO, f"ACK: {arg}".encode())
    elif name == "stats":
        # Server mengukur DIRINYA SENDIRI lalu melaporkannya lewat soket.
        # Jauh lebih andal daripada benchmark mengintip lewat PID, yang gagal
        # di Windows (PID Git Bash bukan PID Windows) dan di OS tanpa /proc.
        snap = metrics.snapshot()
        with clients_lock:
            snap["clients"] = len(clients)
        safe_send(conn, T_INFO, ("STATS " + json.dumps(snap)).encode())
    elif name == "quit":
        return False
    else:
        safe_send(conn, T_ERROR, f"Perintah tidak dikenal: {cmd}".encode())
    return True


def handle_client(connectionSocket, addr):
    """Dijalankan di thread terpisah untuk SETIAP client."""
    default_name = f"{addr[0]}:{addr[1]}"
    with clients_lock:
        clients[connectionSocket] = {"name": default_name, "lock": threading.Lock()}
        total = len(clients)
    log(f"TERHUBUNG {addr} | fd connectionSocket={connectionSocket.fileno()} "
        f"| client aktif={total} | thread aktif={threading.active_count()}")
    safe_send(connectionSocket, T_INFO,
              f"Selamat datang {default_name}. Ketik /help untuk bantuan.".encode())

    try:
        while True:
            mtype, payload = recv_frame(connectionSocket)   # 1 frame = 1 pesan utuh
            with clients_lock:
                sender = clients[connectionSocket]["name"]

            if mtype == T_TEXT:
                text = payload.decode(errors="replace")
                log(f"TEXT dari {sender}: {text!r}")
                safe_send(connectionSocket, T_INFO, f"ACK: {text}".encode())
                broadcast(T_TEXT, f"[{sender}] {text}".encode(), exclude=connectionSocket)

            elif mtype == T_FILE:
                fname, data = unpack_file(payload)
                fname = os.path.basename(fname)        # cegah path traversal
                with open(os.path.join(FILES_DIR, fname), "wb") as f:
                    f.write(data)
                log(f"FILE dari {sender}: {fname} ({len(data)} byte)")
                safe_send(connectionSocket, T_INFO,
                          f"File '{fname}' ({len(data)} byte) tersimpan di server".encode())
                broadcast(T_INFO, f"{sender} mengunggah '{fname}'. Unduh: /get {fname}".encode(),
                          exclude=connectionSocket)

            elif mtype == T_CMD:
                cmd = payload.decode(errors="replace")
                log(f"CMD dari {sender}: /{cmd}")
                if not handle_command(connectionSocket, cmd):
                    break
            else:
                safe_send(connectionSocket, T_ERROR, b"Tipe frame tidak dikenal")
    except (ConnectionError, ValueError, OSError, EOFError) as e:
        log(f"Koneksi {default_name} berakhir: {e}")
    finally:
        with clients_lock:
            info = clients.pop(connectionSocket, None)
            total = len(clients)
        connectionSocket.close()               # connection socket DITUTUP per client
        if info:
            broadcast(T_INFO, f"{info['name']} keluar".encode())
        log(f"TERPUTUS {addr} | client aktif={total}")


def main():
    global QUIET
    ap = argparse.ArgumentParser(description="TCP server thread-per-client")
    ap.add_argument("--host", default="")
    ap.add_argument("--port", type=int, default=12000)
    ap.add_argument("--backlog", type=int, default=128)
    ap.add_argument("--quiet", action="store_true", help="matikan log (untuk benchmark)")
    args = ap.parse_args()
    QUIET = args.quiet
    os.makedirs(FILES_DIR, exist_ok=True)

    # ---------- WELCOMING SOCKET ----------
    serverSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    serverSocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    serverSocket.bind((args.host, args.port))
    serverSocket.listen(args.backlog)
    serverSocket.settimeout(1.0)   # agar Ctrl+C tetap responsif (terutama di Windows)
    print(f"[THREAD] Welcoming socket LISTEN di port {args.port} "
          f"| pid={os.getpid()} | fd={serverSocket.fileno()}", flush=True)

    try:
        while True:
            try:
                connectionSocket, addr = serverSocket.accept()   # 3-way handshake selesai
            except socket.timeout:
                continue
            connectionSocket.settimeout(None)                   # connection socket blocking
            t = threading.Thread(target=handle_client,
                                 args=(connectionSocket, addr),
                                 name=f"client-{addr[1]}", daemon=True)
            t.start()                                           # spawn 1 thread per client
    except KeyboardInterrupt:
        print("\n[THREAD] Server dihentikan", flush=True)
    finally:
        serverSocket.close()


if __name__ == "__main__":
    main()
