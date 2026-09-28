#!/usr/bin/env bash
# run_benchmark.sh - Menjalankan seluruh skenario perbandingan secara otomatis.
#
# Untuk setiap model server (threading, asyncio, selectors), setiap jumlah
# client (50 dan 100), dan setiap mode beban, skrip ini:
#   1. menjalankan server di latar belakang,
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
OUT="scripts/results.jsonl"
LOGDIR="server_logs"

# ---------------------------------------------------------------------------
# 1. Cari perintah Python yang benar.
#    Di Windows (Git Bash) biasanya hanya ada 'python', bukan 'python3'.
# ---------------------------------------------------------------------------
PY=""
for cand in python3 python py; do
  if command -v "$cand" > /dev/null 2>&1; then
    ver=$("$cand" -c "import sys; print(sys.version_info[0])" 2>/dev/null)
    if [ "$ver" = "3" ]; then PY="$cand"; break; fi
  fi
done
if [ -z "$PY" ]; then
  echo "!! Python 3 tidak ditemukan. Coba jalankan 'python --version' dulu."
  exit 1
fi
echo "Memakai perintah Python: $PY ($("$PY" --version 2>&1))"

# ---------------------------------------------------------------------------
# 2. Pastikan dijalankan dari folder yang benar.
# ---------------------------------------------------------------------------
for f in src/protocol.py src/benchmark.py src/TCPServer.py src/TCPServerAsync.py src/TCPServerSelect.py; do
  if [ ! -f "$f" ]; then
    echo "!! File $f tidak ada di folder ini ($(pwd))."
    echo "   Pindah ke folder proyek dulu, lalu jalankan ulang."
    exit 1
  fi
done

# ---------------------------------------------------------------------------
# 3. Cek port yang akan dipakai masih bebas.
# ---------------------------------------------------------------------------
port_busy () {
  "$PY" - "$1" <<'PYEOF' 2>/dev/null
import socket, sys
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(("", int(sys.argv[1])))
    sys.exit(1)          # bebas
except OSError:
    sys.exit(0)          # dipakai
finally:
    s.close()
PYEOF
}

for p in 12000 12100 12200; do
  if port_busy "$p"; then
    echo "!! Port $p masih dipakai proses lain."
    echo "   Kemungkinan ada server dari percobaan sebelumnya yang belum mati."
    echo "   Lihat pemakainya:"
    echo "     Linux/macOS : lsof -i :$p     lalu  kill <PID>"
    echo "     Windows     : netstat -ano | findstr :$p   lalu  taskkill /PID <PID> /F"
    exit 1
  fi
done

mkdir -p "$LOGDIR"
: > "$OUT"

# ---------------------------------------------------------------------------
# 4. Bersihkan server kalau skrip dihentikan di tengah jalan.
#    Tanpa ini, Ctrl+C (atau output di-pipe ke 'head') meninggalkan server
#    yang masih hidup dan memegang port - itu penyebab paling sering
#    munculnya pesan "port dipakai" pada percobaan berikutnya.
# ---------------------------------------------------------------------------
SERVER_PID=""
cleanup () {
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    echo
    echo "Menghentikan server (pid $SERVER_PID) ..."
    kill "$SERVER_PID" 2>/dev/null
  fi
}
trap 'cleanup; exit 130' INT TERM
trap cleanup EXIT

if [ "$MODE" = "both" ]; then MODES="echo chat"; else MODES="$MODE"; fi

run_one () {
  local script="$1" port="$2" clients="$3" label="$4" mode="$5" rep="$6"
  local log="$LOGDIR/${label}_${mode}_${clients}_r${rep}.log"

  # Output server DISIMPAN, bukan dibuang, supaya kalau gagal start
  # penyebab aslinya kelihatan (ImportError, port bentrok, dsb).
  "$PY" "$script" --port "$port" --quiet > "$log" 2>&1 &
  local pid=$!
  SERVER_PID="$pid"
  sleep 2

  if ! kill -0 "$pid" 2>/dev/null; then
    echo "!! Server $script berhenti sebelum siap. Pesan aslinya:"
    echo "---------------------------------------------------------------"
    cat "$log"
    echo "---------------------------------------------------------------"
    echo "   (log lengkap: $log)"
    SERVER_PID=""
    return 1
  fi

  echo "### $label | $clients klien | mode=$mode | rep=$rep | pid=$pid"
  "$PY" src/benchmark.py --port "$port" --clients "$clients" --rounds "$ROUNDS" \
        --mode "$mode" --pid "$pid" --label "$label" --out "$OUT"
  local rc=$?

  kill "$pid" 2>/dev/null
  wait "$pid" 2>/dev/null
  SERVER_PID=""
  sleep 1

  if [ $rc -ne 0 ]; then
    echo "!! src/benchmark.py gagal (kode $rc). Log server: $log"
    return 1
  fi
  return 0
}

fail=0
for mode in $MODES; do
  for n in 50 100; do
    for rep in $(seq 1 "$REPS"); do
      run_one src/TCPServer.py       12000 "$n" "Threading" "$mode" "$rep" || fail=1
      run_one src/TCPServerAsync.py  12100 "$n" "Asyncio"   "$mode" "$rep" || fail=1
      run_one src/TCPServerSelect.py 12200 "$n" "Selectors" "$mode" "$rep" || fail=1
    done
  done
done

echo
if [ -s "$OUT" ]; then
  echo "Selesai. Hasil mentah ($(wc -l < "$OUT") baris) ada di $OUT"
  "$PY" src/summarize.py
  echo
  echo "Gambar grafiknya dengan:  $PY src/plot/plot_benchmark.py"
else
  echo "!! Tidak ada hasil sama sekali. Periksa pesan error di atas dan folder $LOGDIR/."
fi
[ "$fail" -eq 0 ] || echo "(Sebagian run gagal - lihat pesan bertanda !! di atas.)"
