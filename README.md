# Hybrid Socket: Sistem Distribusi Pesan & File (TCP + UDP)

Tugas Jaringan Komputer Lanjut (S2 Ilmu Komputer).
Referensi: Kurose & Ross, *Computer Networking: A Top-Down Approach* 9th Ed., Section 2.6.

Nama  : Fachry Anwar Rafi
NIM   : 25/573150/PPA/07212

## Struktur File

| File | Fungsi |
|------|--------|
| `src/protocol.py` | Protokol aplikasi: framing header fixed-length 5 byte (1B TYPE + 4B LENGTH). Dipakai bersama oleh ketiga server |
| `src/TCPServer.py` | Server TCP **thread-per-client** (blocking I/O) — port 12000 |
| `src/TCPServerAsync.py` | Server TCP **event-driven / non-blocking** (asyncio) — port 12100 |
| `src/TCPServerSelect.py` | Server TCP **event-driven** dengan `selectors` (epoll/kqueue/select) — port 12200 |
| `src/TCPClient.py` | Client CLI: chat, unggah/unduh file, uji burst. Kompatibel dengan ketiga server |
| `src/UDPServer.py` | Server heartbeat UDP (1 soket), opsi simulasi packet loss & delay |
| `src/UDPClient.py` | Pinger: 10 ping, timeout 1 detik, RTT min/avg/max, EstimatedRTT, packet loss |
| `src/tcp_raw_demo.py` | Demonstrasi masalah message boundary TCP tanpa framing |
| `src/benchmark.py` | Alat ukur: RSS, CPU, thread, FD, dan latensi p50/p95/p99 |
| `scripts/run_benchmark.sh` | Menjalankan seluruh skenario perbandingan otomatis |
| `src/summarize.py` | Meringkas `results.jsonl` menjadi tabel markdown (median) |
| `hasil_benchmark.md` | Hasil pengukuran yang sudah jadi tabel |

## Format Frame TCP

```
+--------+----------------------+-----------------+
| TYPE 1B| LENGTH 4B big-endian | PAYLOAD (LENGTH)|
+--------+----------------------+-----------------+
TYPE: 1=TEXT 2=FILE 3=CMD 4=INFO 5=ERROR
```

Penerima membaca tepat 5 byte header, lalu tepat LENGTH byte payload. Dengan begitu
pesan yang tergabung (*coalescing*) maupun terpotong (*partial read*) tetap terpisah benar.

## Kebutuhan

Python 3.8+ saja, tanpa library tambahan.

## Cara Menjalankan

```bash
# --- Layanan TCP: pilih SALAH SATU model server ---
python src/TCPServer.py       --port 12000     # thread-per-client
python src/TCPServerAsync.py  --port 12100     # asyncio (event-driven)
python src/TCPServerSelect.py --port 12200     # selectors/epoll (event-driven)

# --- Layanan UDP heartbeat ---
python src/UDPServer.py --port 12001                  # normal
python src/UDPServer.py --port 12001 --loss 0.3       # simulasi 30% paket hilang

# --- Client (sesuaikan --port dengan server yang dijalankan) ---
python src/TCPClient.py --host 127.0.0.1 --port 12000
python src/UDPClient.py --host 127.0.0.1 --port 12001
```

Perintah pada client TCP: `/nick <nama>`, `/users`, `/send <path>`, `/list`,
`/get <nama>`, `/stats`, `/burst`, `/quit`, atau ketik teks biasa untuk chat.

Jika server ada di komputer lain, ganti `--host` dengan IP server dan buka
port 12000/TCP (atau 12100/12200) serta 12001/UDP di firewall.

## Skenario Pengujian

```bash
# Uji framing: 3 pesan tanpa delay diterima sebagai 3 pesan terpisah
python src/TCPClient.py --port 12000 --demo-burst

# Bandingkan dengan TCP tanpa framing (pesan tergabung jadi satu)
python src/tcp_raw_demo.py server      # terminal A
python src/tcp_raw_demo.py client      # terminal B

# Balasan UDP terlambat (uji pembuangan paket stale)
python src/UDPServer.py --loss 0.2 --delay 1.5

# Bind eksplisit port client (jalankan 2 kali bersamaan untuk melihat konflik)
python src/UDPClient.py --bind-port 5432 --count 20
```

## Benchmark: Thread-per-Client vs Event-Driven

```bash
# Seluruh skenario otomatis (3 model x 50/100 klien x 2 mode x 3 ulangan)
bash scripts/run_benchmark.sh 30 3 both

# Atau satu pengukuran manual
python src/TCPServerAsync.py --port 12100 --quiet &
python src/benchmark.py --port 12100 --clients 100 --rounds 30 \
       --mode echo --pid $! --label "Asyncio"
```

Dua mode beban tersedia:

- `--mode echo` — server hanya membalas pengirim. Mengukur **model konkurensi murni**,
  beban tumbuh O(N).
- `--mode chat` — server membalas ACK *dan* mem-broadcast ke N−1 client lain.
  Mengukur **beban aplikasi nyata**, tumbuh O(N²).

Hasil lengkap ada di [`hasil_benchmark.md`](hasil_benchmark.md).
Ringkasan pada 100 koneksi bersamaan, mode echo (median 3 ulangan, 2 vCPU):

| Model | RTT avg | p95 | Throughput | RSS/koneksi | Thread |
|---|---|---|---|---|---|
| Threading | 6,92 ms | 7,90 ms | 14.061 msg/s | 18,8 KB | 101 |
| Asyncio | 6,18 ms | 7,34 ms | 15.696 msg/s | 7,0 KB | 2 |
| Selectors | 4,35 ms | 4,94 ms | 22.382 msg/s | 2,6 KB | 1 |

Temuan utama: pada 50–100 koneksi ketiganya sama-sama sanggup (0 error dari 81.000
pesan), tetapi model event-driven memakai memori **7–13× lebih hemat** dan tidak
menambah thread sama sekali. Selisih latensi baru melebar jelas saat jumlah koneksi
dinaikkan ke 200–400.
