"""
TCPServerSelect.py - Server TCP non-blocking memakai selectors (select/epoll).

Versi ini memperlihatkan mekanisme event-driven secara EKSPLISIT, yang pada
asyncio disembunyikan di balik async/await. Berguna untuk laporan karena
terlihat jelas bahwa:

  1. Semua soket dipasang setblocking(False).
  2. selectors.DefaultSelector() memilih epoll di Linux, kqueue di macOS,
     select di Windows. Satu panggilan select() menunggu SEMUA soket sekaligus.
  3. Karena recv() bisa mengembalikan byte sebagian, tiap koneksi menyimpan
     buffer sendiri (state machine) untuk merakit frame — tidak bisa memakai
     recv_exact() yang blocking.

Jalankan:  python TCPServerSelect.py --port 12200
"""

import argparse
import os
import selectors
import socket
import time

from protocol import (HEADER, HEADER_SIZE, MAX_PAYLOAD, T_CMD, T_ERROR,
                      T_FILE, T_INFO, T_TEXT, build_frame, pack_file, unpack_file)

FILES_DIR = "server_files"
QUIET = False
sel = selectors.DefaultSelector()
conns = {}          # socket -> Conn


def log(msg):
    if not QUIET:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class Conn:
    """State per koneksi: buffer masuk (perakit frame) dan buffer keluar."""

    def __init__(self, sock, addr):
        self.sock = sock
        self.addr = addr
        self.name = f"{addr[0]}:{addr[1]}"
        self.inbuf = bytearray()     # byte mentah yang belum jadi frame utuh
        self.outbuf = bytearray()    # byte yang menunggu soket siap ditulis

    def queue(self, mtype, payload):
        self.outbuf.extend(build_frame(mtype, payload))
        # Minta event loop memberi tahu saat soket siap ditulis.
        sel.modify(self.sock, selectors.EVENT_READ | selectors.EVENT_WRITE, self)

    def frames(self):
        """Generator: keluarkan setiap frame utuh yang sudah lengkap di inbuf."""
        while len(self.inbuf) >= HEADER_SIZE:
            mtype, length = HEADER.unpack(bytes(self.inbuf[:HEADER_SIZE]))
            if length > MAX_PAYLOAD:
                raise ValueError("Frame terlalu besar")
            if len(self.inbuf) < HEADER_SIZE + length:
                return                      # frame belum lengkap, tunggu data lagi
            payload = bytes(self.inbuf[HEADER_SIZE:HEADER_SIZE + length])
            del self.inbuf[:HEADER_SIZE + length]
            yield mtype, payload


def broadcast(mtype, payload, exclude=None):
    for c in list(conns.values()):
        if c.sock is not exclude:
            c.queue(mtype, payload)


def handle_command(c, cmd):
    parts = cmd.strip().split(maxsplit=1)
    if not parts:
        return True
    name, arg = parts[0].lower(), (parts[1] if len(parts) > 1 else "")

    if name == "nick" and arg:
        old, c.name = c.name, arg
        c.queue(T_INFO, f"Nama diubah menjadi '{arg}'".encode())
        broadcast(T_INFO, f"{old} sekarang bernama {arg}".encode(), exclude=c.sock)
    elif name == "users":
        names = [x.name for x in conns.values()]
        c.queue(T_INFO, f"Online ({len(names)}): {', '.join(names)}".encode())
    elif name == "list":
        files = sorted(os.listdir(FILES_DIR))
        text = "File di server: " + (", ".join(files) if files else "(kosong)")
        c.queue(T_INFO, text.encode())
    elif name == "get" and arg:
        path = os.path.join(FILES_DIR, os.path.basename(arg))
        if not os.path.isfile(path):
            c.queue(T_ERROR, f"File '{arg}' tidak ditemukan".encode())
        else:
            with open(path, "rb") as f:
                c.queue(T_FILE, pack_file(os.path.basename(path), f.read()))
    elif name == "echo":
        # Balas HANYA ke pengirim (tanpa broadcast). Dipakai benchmark untuk
        # mengukur model konkurensi murni, tanpa beban fan-out O(N^2).
        c.queue(T_INFO, f"ACK: {arg}".encode())
    elif name == "stats":
        try:
            nfd = len(os.listdir(f"/proc/{os.getpid()}/fd"))
        except OSError:
            nfd = -1
        c.queue(T_INFO, f"STATS clients={len(conns)} threads=1 fds={nfd} "
                        f"selector={type(sel).__name__}".encode())
    elif name == "quit":
        return False
    else:
        c.queue(T_ERROR, f"Perintah tidak dikenal: {cmd}".encode())
    return True


def close_conn(sock):
    c = conns.pop(sock, None)
    try:
        sel.unregister(sock)
    except (KeyError, ValueError):
        pass
    sock.close()
    if c:
        log(f"TERPUTUS {c.addr} | client aktif={len(conns)}")
        broadcast(T_INFO, f"{c.name} keluar".encode())


def on_accept(serverSocket):
    conn, addr = serverSocket.accept()
    conn.setblocking(False)                 # kunci model non-blocking
    c = Conn(conn, addr)
    conns[conn] = c
    sel.register(conn, selectors.EVENT_READ, c)
    log(f"TERHUBUNG {addr} | fd={conn.fileno()} | client aktif={len(conns)} | 1 thread")
    c.queue(T_INFO, f"Selamat datang {c.name}. Ketik /help untuk bantuan.".encode())


def on_read(c):
    try:
        data = c.sock.recv(65536)           # non-blocking: kembali segera
    except BlockingIOError:
        return
    except OSError:
        close_conn(c.sock)
        return
    if not data:                            # FIN dari peer
        close_conn(c.sock)
        return
    c.inbuf.extend(data)

    try:
        for mtype, payload in c.frames():
            if mtype == T_TEXT:
                text = payload.decode(errors="replace")
                log(f"TEXT dari {c.name}: {text!r}")
                c.queue(T_INFO, f"ACK: {text}".encode())
                broadcast(T_TEXT, f"[{c.name}] {text}".encode(), exclude=c.sock)
            elif mtype == T_FILE:
                fname, fdata = unpack_file(payload)
                fname = os.path.basename(fname)
                with open(os.path.join(FILES_DIR, fname), "wb") as f:
                    f.write(fdata)
                log(f"FILE dari {c.name}: {fname} ({len(fdata)} byte)")
                c.queue(T_INFO, f"File '{fname}' ({len(fdata)} byte) tersimpan".encode())
            elif mtype == T_CMD:
                cmd = payload.decode(errors="replace")
                log(f"CMD dari {c.name}: /{cmd}")
                if not handle_command(c, cmd):
                    close_conn(c.sock)
                    return
            else:
                c.queue(T_ERROR, b"Tipe frame tidak dikenal")
    except (ValueError, OSError):
        close_conn(c.sock)


def on_write(c):
    if c.outbuf:
        try:
            sent = c.sock.send(c.outbuf)    # bisa mengirim sebagian saja
        except BlockingIOError:
            return
        except OSError:
            close_conn(c.sock)
            return
        del c.outbuf[:sent]
    if not c.outbuf:                        # tidak ada lagi yang perlu ditulis
        try:
            sel.modify(c.sock, selectors.EVENT_READ, c)
        except (KeyError, ValueError):
            pass


def main():
    global QUIET
    ap = argparse.ArgumentParser(description="TCP server non-blocking (selectors)")
    ap.add_argument("--host", default="")
    ap.add_argument("--port", type=int, default=12200)
    ap.add_argument("--backlog", type=int, default=128)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    QUIET = args.quiet
    os.makedirs(FILES_DIR, exist_ok=True)

    serverSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    serverSocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    serverSocket.bind((args.host, args.port))
    serverSocket.listen(args.backlog)
    serverSocket.setblocking(False)
    sel.register(serverSocket, selectors.EVENT_READ, None)
    print(f"[SELECT] {type(sel).__name__} LISTEN di port {args.port} "
          f"| pid={os.getpid()} | fd={serverSocket.fileno()}", flush=True)

    try:
        while True:
            # SATU panggilan menunggu SEMUA soket sekaligus
            for key, mask in sel.select(timeout=1.0):
                if key.data is None:
                    on_accept(serverSocket)
                else:
                    if mask & selectors.EVENT_READ:
                        on_read(key.data)
                    if mask & selectors.EVENT_WRITE and key.data.sock in conns:
                        on_write(key.data)
    except KeyboardInterrupt:
        print("\n[SELECT] Server dihentikan", flush=True)
    finally:
        sel.close()
        serverSocket.close()


if __name__ == "__main__":
    main()
