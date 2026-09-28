"""
summarize.py - Membaca results.jsonl dan mencetak tabel perbandingan (median).

Median dipilih (bukan rata-rata) agar satu run yang terganggu proses lain
tidak menggeser kesimpulan.

Pakai:  python summarize.py [results.jsonl]
Hasil ditulis juga ke hasil_benchmark.md agar bisa disalin ke laporan.
"""

import json
import statistics
import sys
from collections import defaultdict

PATH = sys.argv[1] if len(sys.argv) > 1 else "scripts/results.jsonl"
ORDER = ["Threading", "Asyncio", "Selectors"]

COLS = [
    ("lat_avg_ms", "RTT avg (ms)", 2),
    ("lat_p50_ms", "p50 (ms)", 2),
    ("lat_p95_ms", "p95 (ms)", 2),
    ("lat_p99_ms", "p99 (ms)", 2),
    ("throughput_msg_s", "Throughput (msg/s)", 1),
    ("rss_delta_kb", "RSS naik (KB)", 0),
    ("rss_per_conn_kb", "RSS/koneksi (KB)", 1),
    ("threads_peak", "Thread", 0),
    ("fds_peak", "FD", 0),
    ("cpu_percent", "CPU (%)", 1),
    ("cpu_us_per_msg", "CPU/pesan (us)", 1),
]


def main():
    groups = defaultdict(list)
    try:
        with open(PATH) as f:
            for line in f:
                line = line.strip()
                if line:
                    d = json.loads(line)
                    groups[(d.get("mode", "echo"), d["clients"], d["label"])].append(d)
    except FileNotFoundError:
        print(f"File {PATH} tidak ada. Jalankan run_benchmark.sh dulu.")
        return

    if not groups:
        print("Tidak ada data.")
        return

    out = ["# Hasil Benchmark: Thread-per-Client vs Event-Driven", ""]
    reps = max(len(v) for v in groups.values())
    out.append(f"Nilai adalah **median dari {reps} kali pengulangan** tiap kombinasi.")
    out.append("")

    for mode in ["echo", "chat"]:
        keys = [k for k in groups if k[0] == mode]
        if not keys:
            continue
        judul = ("Skenario A - Echo (balasan hanya ke pengirim, beban O(N))"
                 if mode == "echo" else
                 "Skenario B - Chat broadcast (ACK + kirim ke N-1 client, beban O(N^2))")
        out += [f"## {judul}", ""]

        for n in sorted({k[1] for k in keys}):
            out += [f"### {n} koneksi bersamaan", ""]
            header = "| Model | " + " | ".join(c[1] for c in COLS) + " |"
            sep = "|---" * (len(COLS) + 1) + "|"
            out += [header, sep]

            for label in ORDER:
                runs = groups.get((mode, n, label))
                if not runs:
                    continue
                cells = []
                for key, _, nd in COLS:
                    vals = [r[key] for r in runs if r.get(key) is not None]
                    if not vals:
                        cells.append("-")
                    else:
                        m = statistics.median(vals)
                        cells.append(f"{m:.{nd}f}" if nd else f"{int(round(m))}")
                errs = sum(r.get("errors", 0) for r in runs)
                name = label + (" (thread-per-client)" if label == "Threading"
                                else " (event-driven)")
                if errs:
                    name += f" [{errs} error]"
                out.append(f"| {name} | " + " | ".join(cells) + " |")
            out.append("")

    text = "\n".join(out)
    print(text)
    with open("hasil_benchmark.md", "w") as f:
        f.write(text + "\n")
    print("\n(Tabel juga disimpan ke hasil_benchmark.md)")


if __name__ == "__main__":
    main()
