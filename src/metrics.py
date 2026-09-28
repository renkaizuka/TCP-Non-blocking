"""
metrics.py - Pengukuran sumber daya proses SENDIRI, lintas sistem operasi.

Kenapa proses mengukur dirinya sendiri, bukan diintip dari luar lewat PID?

  1. PID tidak selalu benar. Di Git Bash (Windows), `$!` memberi PID milik
     MSYS, bukan PID Windows yang asli. Akibatnya pembacaan gagal dan semua
     metrik jadi null padahal servernya jalan normal.
  2. /proc hanya ada di Linux. macOS dan Windows tidak punya.
  3. Mengintip proses lain bisa ditolak OS karena alasan perizinan.

Proses yang mengukur dirinya sendiri tidak punya satu pun masalah di atas.

Urutan sumber data, dari yang paling akurat:
    /proc/self/status  (Linux, tanpa library tambahan)
    psutil             (semua OS, kalau terpasang)
    ctypes + psapi     (Windows, tanpa library tambahan)
    resource.getrusage (Unix, tapi nilainya PUNCAK bukan saat ini)
"""

import os
import sys
import threading
import time

try:
    import psutil
except ImportError:
    psutil = None

try:
    import resource                      # hanya ada di Unix
except ImportError:
    resource = None

IS_WINDOWS = sys.platform.startswith("win")
HAS_PROC = os.path.isdir("/proc/self")


# --------------------------------------------------------------------------
# RSS (memori fisik yang benar-benar dipakai)
# --------------------------------------------------------------------------

def _rss_proc():
    """Linux: VmRSS dari /proc/self/status, satuan KB."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]), "proc"
    except OSError:
        pass
    return None, None


def _rss_psutil():
    if psutil is None:
        return None, None
    try:
        return psutil.Process().memory_info().rss // 1024, "psutil"
    except Exception:
        return None, None


def _rss_windows():
    """Windows tanpa psutil: GetProcessMemoryInfo dari psapi lewat ctypes."""
    if not IS_WINDOWS:
        return None, None
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),      # ini yang dipakai
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = PMC()
        counters.cb = ctypes.sizeof(PMC)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), counters.cb)
        if ok:
            return counters.WorkingSetSize // 1024, "ctypes"
    except Exception:
        pass
    return None, None


def _rss_rusage():
    """Cadangan terakhir. Nilainya PUNCAK sejak proses mulai, bukan saat ini,
    jadi selisih idle->peak bisa terlalu kecil. Ditandai supaya jelas."""
    if resource is None:
        return None, None
    try:
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux melaporkan KB, macOS/BSD melaporkan byte
        return (peak // 1024 if sys.platform == "darwin" else peak), "rusage-peak"
    except Exception:
        return None, None


def rss_kb():
    for fn in (_rss_proc, _rss_psutil, _rss_windows, _rss_rusage):
        val, src = fn()
        if val is not None:
            return val, src
    return None, None


# --------------------------------------------------------------------------
# Jumlah file descriptor / handle
# --------------------------------------------------------------------------

def fd_count():
    if HAS_PROC:
        try:
            return len(os.listdir("/proc/self/fd"))
        except OSError:
            pass
    if psutil is not None:
        try:
            p = psutil.Process()
            return p.num_fds() if hasattr(p, "num_fds") else p.num_handles()
        except Exception:
            pass
    return None


# --------------------------------------------------------------------------
# Snapshot lengkap
# --------------------------------------------------------------------------

def snapshot():
    """Kondisi proses saat ini.

    cpu_s memakai time.process_time(): waktu CPU (user + sistem) proses ini,
    tersedia di semua OS dan tidak terpengaruh waktu menunggu I/O.
    threads memakai threading.active_count(), yang justru lebih tepat untuk
    tugas ini daripada hitungan thread OS karena menghitung thread Python
    yang benar-benar dibuat program.
    """
    rss, src = rss_kb()
    return {
        "rss_kb": rss,
        "rss_source": src,
        "threads": threading.active_count(),
        "cpu_s": time.process_time(),
        "fds": fd_count(),
        "pid": os.getpid(),
        "wall": time.time(),
    }


def describe():
    """Satu baris keterangan dari mana angka memori diambil."""
    _, src = rss_kb()
    return {
        "proc": "/proc/self/status (Linux)",
        "psutil": "psutil",
        "ctypes": "Windows psapi (ctypes)",
        "rusage-peak": "resource.getrusage - NILAI PUNCAK, kurang akurat; "
                       "pasang psutil untuk hasil lebih baik",
    }.get(src, "tidak tersedia - pasang psutil (pip install psutil)")


if __name__ == "__main__":
    import json
    print(json.dumps(snapshot(), indent=2))
    print("sumber memori:", describe())
