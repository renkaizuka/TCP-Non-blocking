"""
UDPClient.py - UDP Pinger: 10 ping, timeout 1 detik, hitung RTT & packet loss.

Jalankan:
    python UDPClient.py --host 127.0.0.1 --port 12001
    python UDPClient.py --bind-port 5432       # skenario bind eksplisit (Bagian B.4)
"""

import argparse
import socket
import time

TIMEOUT = 1.0
ALPHA, BETA = 0.125, 0.25     # konstanta EstimatedRTT & DevRTT (Kurose, Bab 3.5.3)


def flush_socket(sock):
    """Kosongkan antrean receive buffer dari balasan terlambat (stale)
    sebelum mengirim ping berikutnya. Return jumlah datagram yang dibuang."""
    dropped = 0
    sock.setblocking(False)
    try:
        while True:
            sock.recvfrom(2048)
            dropped += 1
    except (BlockingIOError, OSError):   # antrean kosong
        pass
    finally:
        sock.settimeout(TIMEOUT)
    return dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=12001)
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--bind-port", type=int, default=None,
                    help="bind eksplisit port lokal client (mis. 5432)")
    args = ap.parse_args()
    server = (args.host, args.port)

    clientSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if args.bind_port is not None:
        try:
            clientSocket.bind(("", args.bind_port))
        except OSError as e:
            print(f"[!] Gagal bind ke port {args.bind_port}: {e}")
            print("    Kemungkinan port sudah dipakai instansi client lain.")
            return
    clientSocket.settimeout(TIMEOUT)   # timeout 1 detik

    rtts, lost = [], 0
    est_rtt = dev_rtt = None

    for seq in range(1, args.count + 1):
        stale = flush_socket(clientSocket)
        if stale:
            print(f"   (membuang {stale} balasan terlambat dari antrean)")

        send_time = time.perf_counter()
        message = f"PING {seq} {time.time():.6f}"
        clientSocket.sendto(message.encode(), server)
        if seq == 1:
            print(f"Port lokal client: {clientSocket.getsockname()[1]} "
                  f"({'bind eksplisit' if args.bind_port else 'ephemeral dari OS'})\n")

        deadline = send_time + TIMEOUT
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                print(f"Ping {seq:2d}: Request timed out")
                lost += 1
                break
            clientSocket.settimeout(remaining)
            try:
                data, _ = clientSocket.recvfrom(2048)
            except socket.timeout:
                print(f"Ping {seq:2d}: Request timed out")
                lost += 1
                break
            except ConnectionResetError:     # Windows: ICMP port unreachable
                print(f"Ping {seq:2d}: Port tujuan tidak terjangkau (server mati?)")
                lost += 1
                break

            recv_time = time.perf_counter()
            parts = data.decode(errors="replace").split()
            # Hanya terima balasan yang nomor urutnya cocok
            if len(parts) >= 2 and parts[0] == "PONG" and parts[1] == str(seq):
                sample = (recv_time - send_time) * 1000
                rtts.append(sample)
                if est_rtt is None:                      # sampel pertama (RFC 6298)
                    est_rtt, dev_rtt = sample, sample / 2
                else:
                    dev_rtt = (1 - BETA) * dev_rtt + BETA * abs(sample - est_rtt)
                    est_rtt = (1 - ALPHA) * est_rtt + ALPHA * sample
                print(f"Ping {seq:2d}: balasan='{data.decode()}' RTT={sample:.3f} ms "
                      f"| EstimatedRTT={est_rtt:.3f} ms")
                break
            print(f"   (abaikan balasan tidak cocok: {data.decode()!r})")

        time.sleep(args.interval)

    clientSocket.close()

    sent = args.count
    print("\n--- Statistik UDP Ping ---")
    print(f"Terkirim={sent}, Diterima={len(rtts)}, Hilang={lost} "
          f"({lost / sent * 100:.1f}% packet loss)")
    if rtts:
        print(f"RTT min/avg/max = {min(rtts):.3f} / {sum(rtts) / len(rtts):.3f} / "
              f"{max(rtts):.3f} ms")
        print(f"EstimatedRTT = {est_rtt:.3f} ms, DevRTT = {dev_rtt:.3f} ms, "
              f"TimeoutInterval = {est_rtt + 4 * dev_rtt:.3f} ms")


if __name__ == "__main__":
    main()
