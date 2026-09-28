"""
UDPServer.py - UDP Heartbeat / Pinger server (1 soket untuk semua client).

Jalankan:
    python UDPServer.py --port 12001
    python UDPServer.py --loss 0.3             # simulasi 30% paket hilang
    python UDPServer.py --loss 0.2 --delay 1.5 # + delay acak 0-1.5 s
"""

import argparse
import random
import socket
import time


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=12001)
    ap.add_argument("--loss", type=float, default=0.0, help="probabilitas drop 0..1")
    ap.add_argument("--delay", type=float, default=0.0, help="delay acak maksimum (detik)")
    args = ap.parse_args()

    serverSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    serverSocket.bind(("", args.port))
    serverSocket.settimeout(1.0)   # agar Ctrl+C responsif
    log(f"UDP server siap di port {args.port} | fd={serverSocket.fileno()} "
        f"| loss={args.loss} delay<={args.delay}s")

    seen_clients = set()
    try:
        while True:
            try:
                message, clientAddress = serverSocket.recvfrom(2048)  # 1 datagram utuh
            except socket.timeout:
                continue
            except ConnectionResetError:     # Windows: ICMP unreachable
                continue

            if clientAddress not in seen_clients:
                seen_clients.add(clientAddress)
                log(f"Client baru {clientAddress} | total alamat unik={len(seen_clients)} "
                    f"(tetap 1 soket)")

            text = message.decode(errors="replace")
            if random.random() < args.loss:
                log(f"DROP (simulasi) dari {clientAddress}: {text}")
                continue
            if args.delay > 0:
                time.sleep(random.uniform(0, args.delay))

            reply = text.replace("PING", "PONG", 1)
            serverSocket.sendto(reply.encode(), clientAddress)
            log(f"{clientAddress} -> {text}  | balas: {reply}")
    except KeyboardInterrupt:
        log("Server dihentikan")
    finally:
        serverSocket.close()


if __name__ == "__main__":
    main()
