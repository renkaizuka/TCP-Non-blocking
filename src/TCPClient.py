"""
TCPClient.py - Client CLI untuk TCPServer.py maupun TCPServerAsync.py.

Protokol framing keduanya identik, jadi cukup ganti --port.

Jalankan:
    python TCPClient.py --host 127.0.0.1 --port 12000   # server threading
    python TCPClient.py --host 127.0.0.1 --port 12100   # server asyncio
    python TCPClient.py --demo-burst                    # 3 pesan tanpa delay
"""

import argparse
import os
import socket
import sys
import threading
import time

from protocol import (T_CMD, T_FILE, T_TEXT, TYPE_NAMES, pack_file,
                      recv_frame, send_frame, unpack_file)

DOWNLOAD_DIR = "downloads"
HELP = """Perintah:
  <teks>            kirim pesan chat ke semua client
  /nick <nama>      ganti nama
  /users            daftar client online
  /send <path>      unggah file ke server
  /list             daftar file di server
  /get <nama>       unduh file dari server
  /stats            status internal server (client, thread, fd)
  /burst            kirim 3 pesan berturut-turut TANPA delay (uji framing)
  /quit             keluar"""

send_lock = threading.Lock()


def send(sock, mtype, payload):
    with send_lock:
        send_frame(sock, mtype, payload)


def receiver(sock, stop_event):
    """Thread penerima: setiap recv_frame() menghasilkan tepat 1 pesan."""
    try:
        while not stop_event.is_set():
            mtype, payload = recv_frame(sock)
            if mtype == T_FILE:
                name, data = unpack_file(payload)
                os.makedirs(DOWNLOAD_DIR, exist_ok=True)
                path = os.path.join(DOWNLOAD_DIR, os.path.basename(name))
                with open(path, "wb") as f:
                    f.write(data)
                print(f"\n<< [FILE] tersimpan: {path} ({len(data)} byte)")
            else:
                print(f"\n<< [{TYPE_NAMES.get(mtype, mtype)}] "
                      f"{payload.decode(errors='replace')}")
            print(">> ", end="", flush=True)
    except (ConnectionError, OSError, ValueError):
        if not stop_event.is_set():
            print("\n[!] Koneksi ke server terputus.")
    finally:
        stop_event.set()


def send_burst(sock):
    for msg in ("Pesan-1: Halo", "Pesan-2: Apa kabar?", "Pesan-3: Selesai"):
        send(sock, T_TEXT, msg.encode())       # tanpa sleep di antaranya


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=12000)
    ap.add_argument("--demo-burst", action="store_true")
    args = ap.parse_args()

    clientSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    clientSocket.connect((args.host, args.port))
    print(f"Terhubung ke {args.host}:{args.port} dari port lokal "
          f"{clientSocket.getsockname()[1]} (ephemeral)")

    stop = threading.Event()
    threading.Thread(target=receiver, args=(clientSocket, stop), daemon=True).start()

    try:
        if args.demo_burst:
            send_burst(clientSocket)
            time.sleep(1)
            send(clientSocket, T_CMD, b"quit")
            time.sleep(0.3)
            return

        print(HELP)
        while not stop.is_set():
            line = input(">> ").strip()
            if not line:
                continue
            if stop.is_set():
                break
            if line == "/help":
                print(HELP)
            elif line == "/burst":
                send_burst(clientSocket)
            elif line.startswith("/send "):
                path = line[6:].strip().strip('"')
                if not os.path.isfile(path):
                    print("File tidak ditemukan")
                    continue
                with open(path, "rb") as f:
                    send(clientSocket, T_FILE,
                         pack_file(os.path.basename(path), f.read()))
            elif line.startswith("/"):
                send(clientSocket, T_CMD, line[1:].encode())
                if line == "/quit":
                    time.sleep(0.2)
                    break
            else:
                send(clientSocket, T_TEXT, line.encode())
    except (KeyboardInterrupt, EOFError):
        pass
    except OSError as e:
        print(f"[!] Error: {e}")
    finally:
        stop.set()
        clientSocket.close()
        print("Client ditutup.")
        sys.exit(0)


if __name__ == "__main__":
    main()
