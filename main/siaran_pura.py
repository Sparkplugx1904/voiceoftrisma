#!/usr/bin/env python3
"""
siaran_pura.py — pemancar siaran PURA-PURA untuk latihan lokal.

Bahasa awam: sebelum berani memakai radio asli / kuota GitHub, kita latihan
dulu dengan radio buatan sendiri di laptop. Program ini memancar dari
http://127.0.0.1:PORT/stream (mp3 diulang-ulang) dan menjawab status di
http://127.0.0.1:PORT/stats?json=1 seperti server radio asli.

Skenario tak terduga yang bisa dilatih:
  --goyang MS   : jeda acak antar potongan (simulasi internet macet,
                  B lebih lambat dari A)
  --putus N     : tiap N detik koneksi diputus paksa (simulasi putus)
  --tunda D     : siaran baru ON-AIR setelah D detik (latih TAHAP sopan)
  --lama D      : siaran mati setelah D detik (latih OFF-AIR + tutup)

Contoh:
  python main/siaran_pura.py --port 8901 --tunda 3 --lama 120
  # lalu di terminal lain:
  python main/record_v3.0.py --stream-url http://127.0.0.1:8901/stream ^
    --stats-url "http://127.0.0.1:8901/stats?json=1" --lewati-cek ^
    --durasi 20 --bagian 8 --max-umur 60 --tanpa-unggah --tanpa-batas-waktu

Hanya pustaka bawaan + ffmpeg (untuk membuat mp3 latihan sekali saja).
Berhenti: Ctrl+C.
"""

import argparse
import math
import os
import random
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

MULAI_ONAIR = None
MULAI_SIARAN = None  # patokan waktu hidup (loop jalan terus lintas sambungan)
ARGS = None
MP3_LATIH = None
DETIK_LOOP = 30  # panjang satu putaran nada latihan


def buat_mp3_latih(detik=30):
    """Buat nada latihan yang kaya corak (musik pura-pura, tak berulang).

    Duplikat kecil dari buat_nada di uji_estafet.py — sengaja ditulis
    ulang di sini agar tiap berkas bisa dibaca sendiri oleh penerus.
    Deterministik (seed tetap) agar latihan bisa diulang.
    """
    import random
    tmp = tempfile.mkdtemp(prefix="siaran_pura_")
    wav = os.path.join(tmp, "latih.wav")
    rnd = random.Random(7)
    nada_dasar = [196.0, 220.0, 246.9, 261.6, 293.7, 329.6, 349.2, 392.0, 440.0]
    seg = 2.0
    n_seg = max(1, int(detik / seg) + 1)
    akor = [[rnd.choice(nada_dasar) * m for m in (1.0, 1.25, 1.5)] for _ in range(n_seg)]
    ketuk = sorted(rnd.uniform(0, detik) for _ in range(detik * 2))
    sr = 22050
    melodi = 500.0
    n = sr * detik
    # Amplop ketukan dihitung SEKALI di depan (bukan di tiap cuplik).
    amplop = [0.0] * n
    lebark = int(sr * 0.03)
    for tk in ketuk:
        i0 = int(tk * sr)
        for i in range(i0, min(n, i0 + lebark)):
            amplop[i] += 1.0 - (i - i0) / lebark
    fase_m = 0.0
    fase_a = [0.0, 0.0, 0.0]
    with wave.open(wav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        for i in range(n):
            t = i / sr
            g = min(int(t / seg), n_seg - 1)
            melodi += rnd.uniform(-6, 6)
            melodi = max(200.0, min(1200.0, melodi))
            fase_m += 2 * math.pi * melodi / sr
            v = 3500 * math.sin(fase_m)
            for j in range(3):
                fase_a[j] += 2 * math.pi * akor[g][j] / sr
                v += 2200 * math.sin(fase_a[j])
            if amplop[i]:
                v += 7000 * amplop[i] * math.sin(2 * math.pi * 2000 * t)
            v *= 0.7 + 0.3 * math.sin(2 * math.pi * t / 1.7 + 0.5)
            w.writeframes(struct.pack("<h", max(-32768, min(32767, int(v)))))
    mp3 = os.path.join(tmp, "latih.mp3")
    ff = shutil.which("ffmpeg") or "ffmpeg"
    subprocess.check_call([ff, "-y", "-hide_banner", "-v", "error",
                           "-i", wav, "-codec:a", "libmp3lame",
                           "-b:a", "64k", "-ar", "22050", mp3],
                          timeout=60)
    with open(mp3, "rb") as f:
        data = f.read()
    print(f"[SIARAN] mp3 latihan {len(data)} byte ({detik}d).", flush=True)
    return data


class Tangan(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        pass  # jangan berisik

    def do_GET(self):
        pisah = urlparse(self.path)
        if pisah.path.startswith("/stats"):
            onair = time.monotonic() >= (MULAI_ONAIR or 0)
            if ARGS.lama > 0 and (time.monotonic() - (MULAI_ONAIR or 0)) > ARGS.lama:
                onair = False
            badan = ('{"streamstatus":1,"listeners":3}' if onair
                     else '{"streamstatus":0,"listeners":0}')
            data = badan.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if pisah.path.startswith("/stream"):
            # tunggu sampai on-air
            while time.monotonic() < (MULAI_ONAIR or 0):
                time.sleep(0.2)
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                # Posisi hidup: radio asli jalan terus, sambungan baru
                # mendapat isi JAM SEKARANG, bukan dari awal putaran.
                laju = len(MP3_LATIH) / DETIK_LOOP  # byte per detik audio
                geser = int((time.monotonic() - MULAI_SIARAN) * laju) % len(MP3_LATIH)
                kirim = 0
                putus_tiap = max(1, int(ARGS.putus * 8)) if ARGS.putus > 0 else 0
                # throttle real-time: mp3 64kbps ~= 8192 byte/detik.
                # Kirim 1024 byte tiap 0,125 detik agar 10 detik audio
                # memakan 10 detik waktu (seperti radio asli).
                while True:
                    # putus paksa tiap N detik (simulasi): tutup koneksi
                    if putus_tiap and kirim > 0 and (kirim // 8192) % putus_tiap == 0:
                        break
                    ujung = min(geser + 1024, len(MP3_LATIH))
                    try:
                        self.wfile.write(MP3_LATIH[geser:ujung])
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                        break  # pendengar menutup duluan — wajar tiap potong selesai
                    kirim += ujung - geser
                    geser = ujung if ujung < len(MP3_LATIH) else 0
                    # goyang: internet macet acak di atas irama dasar
                    jeda = 0.125
                    if ARGS.goyang > 0:
                        jeda += random.uniform(0, ARGS.goyang / 1000.0)
                    time.sleep(jeda)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass  # putus di tengah jalan = skenario latihan yang sah
            return
        self.send_response(404)
        self.end_headers()


def utama():
    global ARGS, MULAI_ONAIR, MULAI_SIARAN, MP3_LATIH, DETIK_LOOP
    urai = argparse.ArgumentParser(description="Pemancar pura-pura untuk latihan")
    urai.add_argument("--port", type=int, default=8901)
    urai.add_argument("--tunda", type=float, default=2)
    urai.add_argument("--lama", type=float, default=0)
    urai.add_argument("--goyang", type=float, default=0)
    urai.add_argument("--putus", type=float, default=0)
    urai.add_argument("--detik-latih", type=int, default=30)
    ARGS = urai.parse_args()
    MP3_LATIH = buat_mp3_latih(ARGS.detik_latih)
    DETIK_LOOP = ARGS.detik_latih
    MULAI_SIARAN = time.monotonic()
    MULAI_ONAIR = MULAI_SIARAN + ARGS.tunda
    srv = ThreadingHTTPServer(("127.0.0.1", ARGS.port), Tangan)
    print(f"[SIARAN] dengar di http://127.0.0.1:{ARGS.port}/stream "
          f"(on-air {ARGS.tunda}d lagi). Ctrl+C berhenti.", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("[SIARAN] berhenti.", flush=True)


if __name__ == "__main__":
    utama()
