"""
tcp_raw_demo.py - Demonstrasi MASALAH message boundary pada TCP (tanpa framing).

Terminal 1:  python tcp_raw_demo.py server
Terminal 2:  python tcp_raw_demo.py client

Client memanggil send() 3 kali tanpa delay; server yang memakai recv() biasa
biasanya menerima ketiganya sebagai SATU potongan byte yang tergabung.
"""

import socket
import sys
import time

PORT = 12002


def server():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", PORT))
    s.listen(1)
    print(f"[RAW] menunggu di port {PORT} ...")
    conn, addr = s.accept()
    time.sleep(0.5)          # beri waktu agar ketiga pesan menumpuk di receive buffer
    n = 0
    while True:
        data = conn.recv(1024)
        if not data:
            break
        n += 1
        print(f"[RAW] recv() ke-{n}: {len(data)} byte -> {data!r}")
    print(f"[RAW] Client mengirim 3 pesan, server menerima {n} potongan.")
    conn.close()
    s.close()


def client():
    c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c.connect(("127.0.0.1", PORT))
    for msg in (b"Halo", b"Apa kabar?", b"Selesai"):
        c.send(msg)          # tanpa delay, tanpa header panjang
    c.close()


if __name__ == "__main__":
    {"server": server, "client": client}.get(
        sys.argv[1] if len(sys.argv) > 1 else "", lambda: print(__doc__))()
