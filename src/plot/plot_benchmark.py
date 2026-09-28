"""
plot_benchmark.py - Menggambar grafik dari hasil benchmark socket server.

Membaca:
    results.jsonl   - hasil run_benchmark.sh (50 & 100 klien, mode echo & chat)
    scale.jsonl     - hasil uji skalabilitas lanjutan (200 & 400 klien), opsional

Menghasilkan (folder grafik/):
    1_memori_per_koneksi.png    Memori per koneksi - temuan utama
    2_skala_memori.png          Total memori vs jumlah koneksi
    3_jumlah_thread.png         Jumlah thread vs jumlah koneksi
    4_latensi.png               Latensi rata-rata & p95
    5_throughput.png            Throughput
    6_echo_vs_chat.png          Pengaruh broadcast O(N^2)
    ringkasan.png               Keenam grafik dalam satu halaman

Pakai:
    python plot_benchmark.py                    # mode terang
    python plot_benchmark.py --dark             # mode gelap
    python plot_benchmark.py --only 1 4         # hanya grafik tertentu
    python plot_benchmark.py --format pdf       # vektor, untuk laporan cetak

Kebutuhan:  pip install matplotlib
"""

import argparse
import json
import os
import statistics
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")                    # tanpa GUI, aman di server/WSL
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.path import Path
from matplotlib.patches import PathPatch

# --------------------------------------------------------------------------
# Tema warna.
#
# Tiga seri memakai tiga hue kategorikal yang sudah diuji keterbacaannya,
# termasuk untuk pembaca buta warna (deuteranopia/protanopia/tritanopia).
# Warna TIDAK dipakai sendirian: tiap batang juga diberi label angka dan
# legenda, sehingga grafik tetap terbaca bila dicetak hitam-putih.
# --------------------------------------------------------------------------

LIGHT = {
    "surface":   "#fcfcfb",
    "text":      "#0b0b0b",
    "text2":     "#52514e",
    "grid":      "#e4e3df",
    "series":    ["#2a78d6", "#eb6834", "#1baf7a"],   # biru, oranye, aqua
}
DARK = {
    "surface":   "#1a1a19",
    "text":      "#ffffff",
    "text2":     "#c3c2b7",
    "grid":      "#33332f",
    "series":    ["#3987e5", "#d95926", "#199e70"],
}

MODELS = ["Threading", "Asyncio", "Selectors"]
NICE = {
    "Threading": "Threading (thread-per-client)",
    "Asyncio":   "Asyncio (event-driven)",
    "Selectors": "Selectors/epoll (event-driven)",
}

T = LIGHT          # diisi ulang di main()
SAVE_DPI = 160
NOTE = ""          # keterangan kecil di bawah subjudul (jumlah ulangan)


# --------------------------------------------------------------------------
# Pembacaan data
# --------------------------------------------------------------------------

def load(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def median_of(rows, mode, clients, label, key):
    """Median sebuah metrik untuk satu kombinasi. None bila datanya tidak ada."""
    vals = [r[key] for r in rows
            if r.get("mode", "echo") == mode and r["clients"] == clients
            and r["label"] == label and r.get(key) is not None]
    return statistics.median(vals) if vals else None


def client_counts(rows, mode="echo"):
    return sorted({r["clients"] for r in rows if r.get("mode", "echo") == mode})


# --------------------------------------------------------------------------
# Elemen gambar
# --------------------------------------------------------------------------

def style_axes(ax, ylabel=None, xlabel=None):
    """Sumbu dan grid dibuat recessive: data yang harus menonjol, bukan rangkanya."""
    ax.set_facecolor(T["surface"])
    ax.yaxis.grid(True, color=T["grid"], linewidth=1, linestyle="-", zorder=0)
    ax.set_axisbelow(True)
    ax.xaxis.grid(False)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(T["grid"])
    ax.spines["bottom"].set_linewidth(1)
    ax.tick_params(which="both", colors=T["text2"], length=0, labelsize=9)
    if ylabel:
        ax.set_ylabel(ylabel, color=T["text2"], fontsize=9, labelpad=8)
    if xlabel:
        ax.set_xlabel(xlabel, color=T["text2"], fontsize=9, labelpad=8)


def titles(ax, title, subtitle=None):
    sub = " ".join(x for x in (subtitle, NOTE) if x)
    ax.set_title(title, color=T["text"], fontsize=12, fontweight="bold",
                 loc="left", pad=20 if sub else 10)
    if sub:
        ax.text(0, 1.02, sub, transform=ax.transAxes,
                color=T["text2"], fontsize=8.5, va="bottom", ha="left")


def rounded_bar(ax, x, height, width, color, radius_frac=0.18, zorder=3):
    """Batang dengan ujung-data membulat dan pangkal siku di garis nol.

    matplotlib tidak punya opsi ini, jadi batang digambar sebagai Path:
    dua sudut atas dibulatkan, dua sudut bawah dibiarkan siku supaya
    batang terlihat 'duduk' rapat di baseline.
    """
    if height is None or height <= 0:
        return
    r = min(width * radius_frac, height * 0.5)
    x0, x1 = x - width / 2, x + width / 2
    verts = [
        (x0, 0), (x0, height - r),
        (x0, height), (x0 + r, height),          # kurva sudut kiri-atas
        (x1 - r, height),
        (x1, height), (x1, height - r),          # kurva sudut kanan-atas
        (x1, 0), (x0, 0),
    ]
    codes = [Path.MOVETO, Path.LINETO,
             Path.CURVE3, Path.CURVE3,
             Path.LINETO,
             Path.CURVE3, Path.CURVE3,
             Path.LINETO, Path.CLOSEPOLY]
    ax.add_patch(PathPatch(Path(verts, codes), facecolor=color,
                           edgecolor="none", zorder=zorder))


def bar_label(ax, x, y, text, dy_frac=0.02):
    """Label nilai di ujung batang. Teks memakai warna tinta, bukan warna seri."""
    ymax = ax.get_ylim()[1]
    ax.text(x, y + ymax * dy_frac, text, ha="center", va="bottom",
            color=T["text"], fontsize=8.5, zorder=4)


def px_to_units(ax, px):
    """Ubah ukuran piksel menjadi satuan data sumbu-x.

    Dipakai agar ketebalan batang tetap wajar (maks ~24 px) berapa pun
    jumlah kelompok dan ukuran gambar. Tanpa ini, batang jadi gemuk dan
    grafik terlihat berat.
    """
    fig = ax.figure
    axes_px = ax.get_position().width * fig.get_figwidth() * SAVE_DPI
    xmin, xmax = ax.get_xlim()
    return px * (xmax - xmin) / max(axes_px, 1)


def grouped_bars(ax, groups, series_vals, fmt="{:.1f}", ylabel=None,
                 xlabel=None, headroom=1.22, max_bar_px=24):
    """Batang berkelompok: sumbu-x = groups, satu warna per model.

    Batang di dalam satu kelompok dibuat berdempetan dan hanya dipisahkan
    celah 2 px berwarna permukaan, sedangkan antar kelompok diberi ruang
    kosong lebar. Pemisahnya ruang kosong, bukan garis tepi.
    """
    n = len(MODELS)
    xs = list(range(len(groups)))
    ax.set_xlim(-0.5, len(groups) - 0.5)          # penting: patch tidak ikut autoscale

    width = min(px_to_units(ax, max_bar_px), 0.78 / n)
    gap = px_to_units(ax, 2)                      # celah permukaan antar batang
    pitch = width + gap

    peak = max([v for vals in series_vals.values() for v in vals if v is not None]
               or [1])
    ax.set_ylim(0, peak * headroom)

    for xi in xs:
        # Susun label satu kelompok sekaligus supaya tidak bertabrakan:
        # bila dua batang bersebelahan tingginya mirip, labelnya dinaikkan
        # bergantian agar tetap terbaca.
        prev_y = None
        for i, m in enumerate(MODELS):
            v = series_vals[m][xi]
            if v is None:
                continue
            off = (i - (n - 1) / 2) * pitch
            rounded_bar(ax, xi + off, v, width, T["series"][i])
            y = v
            if prev_y is not None and abs(y - prev_y) < peak * 0.07:
                y = max(y, prev_y) + peak * 0.055
            bar_label(ax, xi + off, y, fmt.format(v))
            prev_y = y

    ax.set_xticks(xs)
    ax.set_xticklabels(groups)
    style_axes(ax, ylabel, xlabel)


def legend(ax, ncol=3, loc="upper center", bbox=(0.5, -0.13)):
    handles = [plt.Line2D([], [], marker="s", linestyle="none", markersize=8,
                          color=T["series"][i], label=NICE[m])
               for i, m in enumerate(MODELS)]
    lg = ax.legend(handles=handles, loc=loc, bbox_to_anchor=bbox, ncol=ncol,
                   frameon=False, fontsize=8.5, handletextpad=0.5,
                   columnspacing=1.6)
    for t in lg.get_texts():
        t.set_color(T["text2"])
    return lg


def line_series(ax, xs, series_vals, ylabel=None, xlabel=None, fmt="{:.0f}",
                logy=False, label_last=True):
    """Garis 2px dengan penanda bulat; hanya titik terakhir yang diberi label."""
    for i, m in enumerate(MODELS):
        ys = series_vals[m]
        pts = [(x, y) for x, y in zip(xs, ys) if y is not None]
        if not pts:
            continue
        px, py = zip(*pts)
        ax.plot(px, py, color=T["series"][i], linewidth=2,
                solid_capstyle="round", solid_joinstyle="round", zorder=3)
        # cincin warna permukaan agar penanda tetap terbaca saat bertumpuk
        ax.plot(px, py, "o", markersize=8, color=T["series"][i],
                markeredgecolor=T["surface"], markeredgewidth=2, zorder=4)
        if label_last:
            ax.annotate(fmt.format(py[-1]), (px[-1], py[-1]),
                        textcoords="offset points", xytext=(10, 0),
                        va="center", color=T["text"], fontsize=8.5, zorder=5)
    if logy:
        ax.set_yscale("log")
        # skala log memunculkan tick minor yang ramai; hanya pangkat 10 yang disimpan
        ax.yaxis.set_minor_locator(mticker.NullLocator())
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(
            lambda v, _: f"{v:,.0f}".replace(",", ".")))
    ax.set_xticks(xs)
    ax.set_xticklabels([str(x) for x in xs])
    ax.margins(x=0.12)
    style_axes(ax, ylabel, xlabel)


# --------------------------------------------------------------------------
# Enam grafik
# --------------------------------------------------------------------------

def chart_memory_per_conn(ax, rows, scale):
    all_rows = rows + scale
    ns = sorted({r["clients"] for r in all_rows if r.get("mode", "echo") == "echo"})
    vals = {m: [median_of(all_rows, "echo", n, m, "rss_per_conn_kb") for n in ns]
            for m in MODELS}
    grouped_bars(ax, [f"{n} koneksi" for n in ns], vals, "{:.1f}",
                 ylabel="KB per koneksi")
    titles(ax, "Memori per koneksi",
           "Selisih ini adalah stack thread. Makin kecil makin baik.")


def chart_memory_total(ax, rows, scale):
    all_rows = rows + scale
    ns = sorted({r["clients"] for r in all_rows if r.get("mode", "echo") == "echo"})
    vals = {m: [(lambda v: v / 1024 if v is not None else None)(
                median_of(all_rows, "echo", n, m, "rss_delta_kb")) for n in ns]
            for m in MODELS}
    line_series(ax, ns, vals, ylabel="Kenaikan RSS (MB)",
                xlabel="Jumlah koneksi bersamaan", fmt="{:.1f} MB")
    titles(ax, "Total memori vs jumlah koneksi",
           "Model thread naik linier dengan kemiringan jauh lebih curam.")


def chart_threads(ax, rows, scale):
    all_rows = rows + scale
    ns = sorted({r["clients"] for r in all_rows if r.get("mode", "echo") == "echo"})
    vals = {m: [median_of(all_rows, "echo", n, m, "threads_peak") for n in ns]
            for m in MODELS}
    line_series(ax, ns, vals, ylabel="Jumlah thread (skala log)",
                xlabel="Jumlah koneksi bersamaan", fmt="{:.0f}", logy=True)
    titles(ax, "Jumlah thread di proses server",
           "Model event-driven tetap 1-2 thread berapa pun koneksinya.")


def chart_latency(ax, rows):
    ns = client_counts(rows, "echo")
    groups, vals = [], {m: [] for m in MODELS}
    for n in ns:
        for key, nm in (("lat_avg_ms", "rata-rata"), ("lat_p95_ms", "p95")):
            groups.append(f"{n} kon.\n{nm}")
            for m in MODELS:
                vals[m].append(median_of(rows, "echo", n, m, key))
    grouped_bars(ax, groups, vals, "{:.1f}", ylabel="Latensi (ms)")
    titles(ax, "Waktu respons - mode echo",
           "p95 memperlihatkan ekor distribusi: di situ selisihnya lebih jelas.")


def chart_throughput(ax, rows):
    ns = client_counts(rows, "echo")
    vals = {m: [(lambda v: v / 1000 if v is not None else None)(
                median_of(rows, "echo", n, m, "throughput_msg_s")) for n in ns]
            for m in MODELS}
    grouped_bars(ax, [f"{n} koneksi" for n in ns], vals, "{:.1f}k",
                 ylabel="Ribu pesan per detik")
    titles(ax, "Throughput - mode echo",
           "Beban O(N): server hanya membalas pengirim.")


def chart_echo_vs_chat(ax, rows):
    """Dua panel bersebelahan (small multiples), masing-masing dengan skalanya
    sendiri.

    Digambar terpisah karena rentang nilainya terlalu jauh (satuan ms vs
    ratusan ms). Kalau dipaksa satu sumbu, batang mode echo jadi tidak
    terlihat; kalau dipaksa sumbu log, panjang batang tidak lagi sebanding
    dengan nilainya. Skala tiap panel ditulis jelas di sumbu-Y.
    """
    fig = ax.figure
    spec = ax.get_subplotspec()
    ax.remove()
    gs = spec.subgridspec(1, 2, wspace=0.42)
    axes2 = [fig.add_subplot(gs[0]), fig.add_subplot(gs[1])]

    ns = client_counts(rows, "echo")
    n = ns[-1] if ns else 100
    panels = [
        ("echo", "Mode echo - balas pengirim, O(N)"),
        ("chat", f"Mode chat broadcast - ke N−1 klien, O(N²)"),
    ]
    for a, (mode, sub) in zip(axes2, panels):
        vals = {m: [median_of(rows, mode, n, m, "lat_avg_ms")] for m in MODELS}
        if all(v[0] is None for v in vals.values()):
            a.set_visible(False)
            continue
        grouped_bars(a, [""], vals, "{:.0f} ms", ylabel="Latensi rata-rata (ms)")
        a.set_title(sub, color=T["text2"], fontsize=9, loc="left", pad=10)

    axes2[0].text(0, 1.20, f"Beban aplikasi menutupi model konkurensi "
                  f"({n} koneksi)", transform=axes2[0].transAxes,
                  color=T["text"], fontsize=12, fontweight="bold",
                  va="bottom", ha="left")
    axes2[0].text(0, 1.13, "Perhatikan skala kedua panel berbeda: broadcast "
                  "memperlambat ~45–75×, dan ketiga model jadi setara.",
                  transform=axes2[0].transAxes, color=T["text2"], fontsize=8.5,
                  va="bottom", ha="left")
    return axes2[0], (1.15, -0.16)


CHARTS = [
    ("1_memori_per_koneksi", chart_memory_per_conn, True),
    ("2_skala_memori",       chart_memory_total,    True),
    ("3_jumlah_thread",      chart_threads,         True),
    ("4_latensi",            chart_latency,         False),
    ("5_throughput",         chart_throughput,      False),
    ("6_echo_vs_chat",       chart_echo_vs_chat,    False),
]


# --------------------------------------------------------------------------

def save(fig, path):
    fig.savefig(path, dpi=SAVE_DPI, facecolor=T["surface"], bbox_inches="tight",
                pad_inches=0.35)
    plt.close(fig)
    print(f"  tersimpan: {path}")


def main():
    global T, NOTE
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="scripts/results.jsonl")
    ap.add_argument("--scale", default="scripts/scale.jsonl")
    ap.add_argument("--outdir", default="docs/grafik")
    ap.add_argument("--dark", action="store_true", help="tema gelap")
    ap.add_argument("--format", default="png", choices=["png", "pdf", "svg"])
    ap.add_argument("--only", nargs="*", type=int,
                    help="nomor grafik yang dibuat, mis. --only 1 4")
    args = ap.parse_args()

    T = DARK if args.dark else LIGHT

    rows = load(args.results)
    scale = load(args.scale)
    if not rows:
        print(f"[!] {args.results} kosong atau tidak ada.")
        print("    Jalankan dulu:  bash run_benchmark.sh 30 3 both")
        return

    reps = max(len(v) for v in _group(rows).values())
    NOTE = f"(median {reps} ulangan, loopback lokal)"
    print(f"Membaca {len(rows)} run dari {args.results}"
          + (f" + {len(scale)} run dari {args.scale}" if scale else ""))

    os.makedirs(args.outdir, exist_ok=True)
    plt.rcParams["font.family"] = ["DejaVu Sans"]

    picked = CHARTS if not args.only else [
        c for i, c in enumerate(CHARTS, 1) if i in args.only]

    for name, fn, uses_scale in picked:
        fig, ax = plt.subplots(figsize=(8, 4.8), facecolor=T["surface"])
        ret = fn(ax, rows, scale) if uses_scale else fn(ax, rows)
        lax, bbox = ret if ret else (ax, (0.5, -0.13))
        legend(lax, bbox=bbox)
        save(fig, os.path.join(args.outdir, f"{name}.{args.format}"))

    # Halaman ringkasan: keenam grafik sekaligus
    if not args.only:
        fig, axes = plt.subplots(3, 2, figsize=(15, 13.5), facecolor=T["surface"])
        first = None
        for (name, fn, uses_scale), ax in zip(CHARTS, axes.ravel()):
            ret = fn(ax, rows, scale) if uses_scale else fn(ax, rows)
            if first is None:
                first = ax
        legend(first, ncol=3, loc="upper center", bbox=(1.05, 1.28))
        fig.suptitle("Perbandingan Model Konkurensi Server TCP",
                     color=T["text"], fontsize=17, fontweight="bold", y=0.985)
        fig.tight_layout(rect=[0, 0, 1, 0.92], h_pad=4.5, w_pad=4.0)
        save(fig, os.path.join(args.outdir, f"ringkasan.{args.format}"))

    print(f"\nSelesai. Semua grafik ada di folder '{args.outdir}/'.")


def _group(rows):
    g = defaultdict(list)
    for r in rows:
        g[(r.get("mode", "echo"), r["clients"], r["label"])].append(r)
    return g


if __name__ == "__main__":
    main()
