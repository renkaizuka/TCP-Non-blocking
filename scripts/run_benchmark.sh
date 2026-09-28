#!/usr/bin/env bash
# run_benchmark.sh - Menjalankan seluruh skenario perbandingan secara otomatis.
#
# Untuk setiap model server (threading, asyncio, selectors), setiap jumlah
# client (50 dan 100), dan setiap mode beban, skrip ini:
#   1. menjalankan server dengan --quiet di latar belakang,
#   2. menjalankan benchmark.py dengan PID server tersebut,
#   3. mematikan server agar pengukuran berikutnya mulai dari kondisi bersih.
#
# Setiap kombinasi diulang REPS kali; summarize.py mengambil MEDIAN-nya
# supaya hasil tidak bias oleh satu run yang kebetulan terganggu.
#
# Pakai:  bash run_benchmark.sh [ROUNDS] [REPS] [MODE]
#   ROUNDS : pesan per client (default 20)
#   REPS   : pengulangan tiap kombinasi (default 3)
#   MODE   : echo | chat | both (default both)

set -u
ROUNDS="${1:-20}"
REPS="${2:-3}"
MODE="${3:-both}"
OUT="results.jsonl"
: > "$OUT"

if [ "$MODE" = "both" ]; then MODES="echo chat"; else MODES="$MODE"; fi

run_one () {
  local script="$1" port="$2" clients="$3" label="$4" mode="$5" rep="$6"
  python3 "$script" --port "$port" --quiet > /dev/null 2>&1 &
  local pid=$!
  sleep 1.5
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "!! server $script gagal start (port $port dipakai?)"; return 1
  fi
  echo "### $label | $clients klien | mode=$mode | rep=$rep | pid=$pid"
  python3 benchmark.py --port "$port" --clients "$clients" --rounds "$ROUNDS" \
          --mode "$mode" --pid "$pid" --label "$label" --out "$OUT"
  kill "$pid" 2>/dev/null
  wait "$pid" 2>/dev/null
  sleep 1
}

for mode in $MODES; do
  for n in 50 100; do
    for rep in $(seq 1 "$REPS"); do
      run_one TCPServer.py       12000 "$n" "Threading" "$mode" "$rep"
      run_one TCPServerAsync.py  12100 "$n" "Asyncio"   "$mode" "$rep"
      run_one TCPServerSelect.py 12200 "$n" "Selectors" "$mode" "$rep"
    done
  done
done

echo
echo "Selesai. Hasil mentah ($(wc -l < "$OUT") baris) ada di $OUT"
python3 summarize.py
