# Hasil Benchmark: Thread-per-Client vs Event-Driven

Nilai adalah **median dari 3 kali pengulangan** tiap kombinasi.

## Skenario A - Echo (balasan hanya ke pengirim, beban O(N))

### 50 koneksi bersamaan

| Model | RTT avg (ms) | p50 (ms) | p95 (ms) | p99 (ms) | Throughput (msg/s) | RSS naik (KB) | RSS/koneksi (KB) | Thread | FD | CPU (%) | CPU/pesan (us) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Threading (thread-per-client) | 4.05 | 4.07 | 4.91 | 7.27 | 11533.5 | 1056 | 21.1 | 51 | 54 | 46.1 | 60.0 |
| Asyncio (event-driven) | 2.41 | 2.25 | 3.40 | 3.62 | 19987.1 | 360 | 7.2 | 2 | 57 | 78.1 | 40.0 |
| Selectors (event-driven) | 2.27 | 2.14 | 3.48 | 4.93 | 21235.6 | 228 | 4.6 | 1 | 55 | 45.4 | 20.0 |

### 100 koneksi bersamaan

| Model | RTT avg (ms) | p50 (ms) | p95 (ms) | p99 (ms) | Throughput (msg/s) | RSS naik (KB) | RSS/koneksi (KB) | Thread | FD | CPU (%) | CPU/pesan (us) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Threading (thread-per-client) | 6.92 | 6.91 | 7.90 | 8.10 | 14060.7 | 1884 | 18.8 | 101 | 104 | 46.9 | 33.3 |
| Asyncio (event-driven) | 6.18 | 6.19 | 7.34 | 7.65 | 15696.3 | 696 | 7.0 | 2 | 107 | 68.0 | 43.3 |
| Selectors (event-driven) | 4.35 | 4.29 | 4.94 | 5.89 | 22381.9 | 264 | 2.6 | 1 | 105 | 44.8 | 20.0 |

## Skenario B - Chat broadcast (ACK + kirim ke N-1 client, beban O(N^2))

### 50 koneksi bersamaan

| Model | RTT avg (ms) | p50 (ms) | p95 (ms) | p99 (ms) | Throughput (msg/s) | RSS naik (KB) | RSS/koneksi (KB) | Thread | FD | CPU (%) | CPU/pesan (us) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Threading (thread-per-client) | 41.95 | 36.61 | 87.77 | 138.22 | 1063.3 | 1056 | 21.1 | 51 | 54 | 31.9 | 300.0 |
| Asyncio (event-driven) | 78.45 | 74.60 | 105.53 | 112.95 | 634.0 | 380 | 7.6 | 2 | 57 | 24.3 | 386.7 |
| Selectors (event-driven) | 39.79 | 37.99 | 61.04 | 64.49 | 1247.5 | 228 | 4.6 | 1 | 55 | 54.1 | 440.0 |

### 100 koneksi bersamaan

| Model | RTT avg (ms) | p50 (ms) | p95 (ms) | p99 (ms) | Throughput (msg/s) | RSS naik (KB) | RSS/koneksi (KB) | Thread | FD | CPU (%) | CPU/pesan (us) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Threading (thread-per-client) | 282.63 | 311.89 | 485.56 | 624.39 | 341.0 | 1884 | 18.8 | 101 | 104 | 20.9 | 583.3 |
| Asyncio (event-driven) | 313.54 | 313.16 | 371.68 | 390.18 | 317.4 | 688 | 6.9 | 2 | 107 | 20.1 | 633.3 |
| Selectors (event-driven) | 324.04 | 321.78 | 391.76 | 409.03 | 306.7 | 264 | 2.6 | 1 | 105 | 30.3 | 983.3 |


## Skenario C - Uji skalabilitas lanjutan (mode echo, 10 pesan/klien)

Di luar rentang 50-100 yang diminta, untuk memperlihatkan arah trennya.

| Model | Koneksi | RSS naik (KB) | RSS/koneksi (KB) | Thread | FD | RTT avg (ms) | p95 (ms) | Error |
|---|---|---|---|---|---|---|---|---|
| Threading | 200 | 3596 | 18.0 | 201 | 204 | 14.512 | 18.994 | 0 |
| Asyncio | 200 | 1308 | 6.5 | 2 | 207 | 9.355 | 22.222 | 0 |
| Selectors | 200 | 352 | 1.8 | 1 | 205 | 8.274 | 9.589 | 0 |
| Threading | 400 | 7052 | 17.6 | 401 | 404 | 27.433 | 36.731 | 0 |
| Asyncio | 400 | 2512 | 6.3 | 2 | 407 | 21.88 | 29.134 | 0 |
| Selectors | 400 | 532 | 1.3 | 1 | 405 | 21.019 | 26.111 | 0 |
