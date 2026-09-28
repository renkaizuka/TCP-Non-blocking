"""
protocol.py - Protokol layer aplikasi buatan sendiri (framing) di atas TCP.

TCP adalah byte-stream: tidak ada batas pesan (message boundary). Karena itu
setiap pesan dibungkus dalam FRAME dengan header fixed-length 5 byte:

    +--------+--------------------------+----------------------+
    | TYPE   | LENGTH (unsigned, 4 byte)| PAYLOAD (LENGTH byte)|
    | 1 byte | big-endian / network     |                      |
    +--------+--------------------------+----------------------+

Penerima selalu membaca TEPAT 5 byte header, lalu TEPAT LENGTH byte payload
(fungsi recv_exact), sehingga pesan yang tergabung (coalesced) maupun yang
terpotong (partial read) tetap dapat dipisahkan dengan benar.

Modul ini dipakai bersama oleh TCPServer.py (threading) dan
TCPServerAsync.py (asyncio) agar format frame keduanya identik.
"""

import json
import struct

HEADER = struct.Struct("!BI")        # ! = network byte order, B = 1 byte, I = 4 byte
HEADER_SIZE = HEADER.size            # 5 byte
MAX_PAYLOAD = 50 * 1024 * 1024       # batas 50 MB untuk mencegah alokasi berlebihan

# Jenis frame
T_TEXT = 1    # pesan chat
T_FILE = 2    # transfer file (metadata + isi)
T_CMD = 3     # perintah ke server (/list, /get, ...)
T_INFO = 4    # informasi / ACK dari server
T_ERROR = 5   # pesan error dari server

TYPE_NAMES = {T_TEXT: "TEXT", T_FILE: "FILE", T_CMD: "CMD",
              T_INFO: "INFO", T_ERROR: "ERROR"}


# ---------- API berbasis soket blocking (dipakai versi threading) ----------

def send_frame(sock, mtype: int, payload: bytes) -> None:
    """Kirim satu frame utuh. sendall() menjamin seluruh byte terkirim."""
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"Payload terlalu besar ({len(payload)} byte)")
    sock.sendall(HEADER.pack(mtype, len(payload)) + payload)


def recv_exact(sock, n: int) -> bytes:
    """Baca TEPAT n byte. recv() boleh mengembalikan lebih sedikit dari n,
    jadi harus diulang sampai terpenuhi."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:                       # b'' = peer menutup koneksi (FIN)
            raise ConnectionError("Koneksi ditutup oleh peer")
        buf.extend(chunk)
    return bytes(buf)


def recv_frame(sock):
    """Baca satu frame utuh -> (type, payload)."""
    mtype, length = HEADER.unpack(recv_exact(sock, HEADER_SIZE))
    if length > MAX_PAYLOAD:
        raise ValueError(f"Frame terlalu besar ({length} byte)")
    payload = recv_exact(sock, length) if length else b""
    return mtype, payload


# ---------- API berbasis asyncio (dipakai versi event-driven) ----------

def build_frame(mtype: int, payload: bytes) -> bytes:
    """Bentuk byte frame tanpa mengirimnya (untuk StreamWriter.write)."""
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"Payload terlalu besar ({len(payload)} byte)")
    return HEADER.pack(mtype, len(payload)) + payload


async def recv_frame_async(reader):
    """Versi asinkron recv_frame(). readexactly() sudah menjamin jumlah byte."""
    header = await reader.readexactly(HEADER_SIZE)
    mtype, length = HEADER.unpack(header)
    if length > MAX_PAYLOAD:
        raise ValueError(f"Frame terlalu besar ({length} byte)")
    payload = await reader.readexactly(length) if length else b""
    return mtype, payload


# ---------- Payload file ----------

def pack_file(filename: str, data: bytes) -> bytes:
    """Payload FILE = [2 byte panjang metadata][metadata JSON][isi file]."""
    meta = json.dumps({"name": filename, "size": len(data)}).encode()
    return struct.pack("!H", len(meta)) + meta + data


def unpack_file(payload: bytes):
    (meta_len,) = struct.unpack("!H", payload[:2])
    meta = json.loads(payload[2:2 + meta_len].decode())
    data = payload[2 + meta_len:]
    if len(data) != meta["size"]:
        raise ValueError("Ukuran file tidak sesuai metadata")
    return meta["name"], data
