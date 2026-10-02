#!/usr/bin/env python3
"""
sidik_suara.py — alat baca isi suara + sambung halus.

Tujuan (bahasa awam):
  Dua rekaman dari dua pekerja (A dan B) TIDAK PERNAH sejajar kalau hanya
  mengandalkan jam. A lancar, B macet 2-3 detik, itu biasa. Maka titik
  gunting harus dibaca dari ISI suaranya, bukan dari jam.

Cara kerja (sengaja sederhana, tanpa kamus huruf):
  1. Suara disederhanakan jadi 8000 cuplik/detik, satu jalur (mono).
  2. Tiap 20 mili-detik dihitung sepasang angka mentah [keras, kasar].
     5 menit tumpang = ~15000 pasang. Inilah sidiknya, data mentah apa adanya.
  3. Cari tumpang: ambil contoh 4 detik dari kepala B, geser di sepanjang
     ekor A (kasar lalu halus), pilih yang perpanjangannya terpanjang.
  4. Rapatkan via korelasi silang sampai 1 cuplik (0,125 mdetik), potong A
     di penyeberangan-nol (anti klik) + silang pendek adaptif via ffmpeg.

Hanya pakai pustaka bawaan Python + ffmpeg. Tanpa numpy agar mudah
dijalankan penerus di laptop kentang maupun di runner GitHub.

Dipakai oleh: main/record_v3.0.py dan main/uji_estafet.py
"""

import math
import os
import shutil
import struct
import subprocess

JENDELA_MS = 20          # tiap angka sidik mewakili 20 mili-detik
CONTOH_SR = 8000         # cuplik per detik setelah disederhanakan


def cari_ffmpeg():
    """Cari perintah ffmpeg yang bisa dipakai (PATH dulu, lalu lokal)."""
    p = shutil.which("ffmpeg")
    if p:
        return p
    for calon in ("./ffmpeg", "bin/ffmpeg", "ffmpeg.exe"):
        if os.path.exists(calon):
            return calon
    return "ffmpeg"


def cari_ffprobe():
    """Cari perintah ffprobe."""
    p = shutil.which("ffprobe")
    if p:
        return p
    for calon in ("./ffprobe", "bin/ffprobe", "ffprobe.exe"):
        if os.path.exists(calon):
            return calon
    return "ffprobe"


def decode_ke_raw(lintas_berkas, sr=CONTOH_SR):
    """Ubah berkas audio apa pun jadi byte mentah s16 mono.

    Kembalikan bytes. Gagal -> raise RuntimeError (jangan diam-diam).
    """
    ff = cari_ffmpeg()
    cmd = [
        ff, "-hide_banner", "-v", "error",
        "-i", lintas_berkas,
        "-ac", "1", "-ar", str(sr), "-f", "s16le", "-acodec", "pcm_s16le",
        "pipe:1",
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=120)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"gagal decode {lintas_berkas}: {e.output[:300]!r}")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"decode {lintas_berkas} macet >120 detik")
    if len(out) < sr * 2:  # kurang dari 1 detik
        raise RuntimeError(f"hasil decode {lintas_berkas} terlalu pendek ({len(out)} byte)")
    return out


def raw_ke_sidik(raw, sr=CONTOH_SR, jendela_ms=JENDELA_MS):
    """Ubah byte mentah s16 mono jadi daftar pasangan angka [keras, kasar].

    Tiap jendela 20 mili-detik -> satu pasangan:
      keras 0,0-1,0 : energi rata-rata (0,0 sunyi - 1,0 pecah)
      kasar 0,0-1,0 : seberapa sering gelombang menyeberang nol
                      (rendah = nada rendah, tinggi = nada tinggi / desis)
    Data mentah, tanpa dipetakan ke huruf. Dua dimensi ini perlu karena
    nada murni kerasnya datar terus (uji lokal membuktikan energi saja
    tidak bisa menemukan posisi yang benar).
    """
    tiap_jendela = int(sr * jendela_ms / 1000)
    n_contoh = len(raw) // 2
    sidik = []
    for awal in range(0, n_contoh - tiap_jendela + 1, tiap_jendela):
        potong = raw[awal * 2:(awal + tiap_jendela) * 2]
        nilai = struct.unpack("<%dh" % tiap_jendela, potong)
        jumlah2 = 0
        ganti = 0
        sebelum = 0 if nilai[0] >= 0 else 1
        for k in range(len(nilai)):
            s = nilai[k]
            jumlah2 += s * s
            if k:
                kini = 0 if s >= 0 else 1
                if kini != sebelum:
                    ganti += 1
                    sebelum = kini
        keras = math.sqrt(jumlah2 / tiap_jendela) / 32768.0
        kasar = ganti / tiap_jendela
        sidik.append([keras, kasar])
    return sidik


def audio_ke_sidik(lintas_berkas, sr=CONTOH_SR, jendela_ms=JENDELA_MS):
    """Jalan pintas: berkas audio -> daftar angka sidik. Gagal -> raise."""
    return raw_ke_sidik(decode_ke_raw(lintas_berkas, sr=sr), sr=sr, jendela_ms=jendela_ms)


def sidik_ke_detik(panjang, jendela_ms=JENDELA_MS):
    """Ubah jumlah angka sidik jadi detik."""
    return panjang * jendela_ms / 1000.0


def _saluran(deret_pasang, idx):
    """Ambil satu saluran (0=keras, 1=kasar) dari daftar pasangan."""
    return [p[idx] for p in deret_pasang]


def _skor_saluran(a, b):
    """Korelasi satu saluran; sunyi-vs-sunyi yang sama = cocok (1,0)."""
    n = min(len(a), len(b))
    if n == 0:
        return -1.0
    a, b = a[:n], b[:n]
    ra = sum(a) / n
    rb = sum(b) / n
    va = sum((x - ra) ** 2 for x in a) / n
    vb = sum((x - rb) ** 2 for x in b) / n
    if va < 1e-12 and vb < 1e-12:
        return 1.0 if abs(ra - rb) < 0.02 else 0.0
    if va < 1e-12 or vb < 1e-12:
        return 0.0
    sa, sb = math.sqrt(va), math.sqrt(vb)
    return sum((x - ra) * (y - rb) for x, y in zip(a, b)) / (n * sa * sb)


def _mirip(a, b):
    """Kemiripan -1..1 antara dua potongan sidik (rata-rata 2 saluran)."""
    return (_skor_saluran(_saluran(a, 0), _saluran(b, 0))
            + _skor_saluran(_saluran(a, 1), _saluran(b, 1))) / 2.0


def _kasarkan(sidik, tiap=5):
    """Rata-ratakan tiap N pasangan (untuk cari cepat di resolusi kasar)."""
    keluar = []
    for i in range(0, len(sidik), tiap):
        potong = sidik[i:i + tiap]
        n = len(potong)
        keluar.append([sum(p[0] for p in potong) / n,
                       sum(p[1] for p in potong) / n])
    return keluar


def _kepala_berisi(sidik, dari=0, butuh=150, ambang=0.01):
    """Cari awal potongan yang bersuara (lewati sunyi pembuka)."""
    i = max(0, dari)
    while i + butuh <= len(sidik):
        if sum(p[0] for p in sidik[i:i + butuh]) / butuh > ambang:
            return i
        i += 25
    return -1


def cari_kembaran(sidik_a, sidik_b, min_panjang=150, bibit=200, jendela_cari=6000):
    """Cari tumpang tindih: ekor A yang muncul di awal B.

    Kembalikan (a_mulai, b_mulai, panjang) dalam satuan angka sidik,
    atau None bila tidak ketemu sepanjang min_panjang.

    Cara: ambil contoh dari kepala B yang bersuara, geser di sepanjang
    ekor A (resolusi kasar dulu, lalu rapatkan), perpanjang ke dua arah
    selama masih mirip. Diulang untuk 3 contoh — jangkar benar adalah yang
    perpanjangannya TERPANJANG (jangkar saingan periodik mati muda).
    """
    if not sidik_a or not sidik_b:
        return None
    ekor_a = sidik_a[-jendela_cari:] if len(sidik_a) > jendela_cari else sidik_a
    geser_a = len(sidik_a) - len(ekor_a)
    kepala_b = sidik_b[:jendela_cari] if len(sidik_b) > jendela_cari else sidik_b
    kasar_a = _kasarkan(ekor_a)
    tiap = 5

    # 1) kumpulkan contoh dari kepala B (3 titik awal yang bersuara)
    daftar_contoh = []
    for awal_coba in (0, 400, 800):
        m = _kepala_berisi(kepala_b, dari=awal_coba, butuh=bibit)
        if m >= 0:
            daftar_contoh.append((m, kepala_b[m:m + bibit]))
    if not daftar_contoh:
        return None  # kepala B sunyi total -> tidak bisa jangkar

    terbaik = None
    for m0, probe in daftar_contoh:
        temu = _jangkar_satu(ekor_a, kepala_b, kasar_a, probe, m0,
                             bibit, tiap, min_panjang)
        if temu is not None and (terbaik is None or temu[2] > terbaik[2]):
            terbaik = temu
    if terbaik is None:
        return None
    a_mulai, b_mulai, panjang = terbaik
    return (a_mulai + geser_a, b_mulai, panjang)


def _jangkar_satu(ekor_a, kepala_b, kasar_a, probe, m0, bibit, tiap, min_panjang):
    """Jangkar + perpanjang untuk SATU contoh. Kembalikan (a,b,panjang)
    relatif ekor/kepala, atau None."""
    kasar_p = _kasarkan(probe)

    # 2) geser contoh di ekor A — resolusi kasar dulu (100 ms/langkah).
    #    Ambang bibit RENDAH (0,30): bibit hanya kandidat, pembuktian ada
    #    di tahap halus + perpanjangan. Ambil 3 terbaik yang berjauhan agar
    #    puncak kembar periodik tidak mengalahkan posisi benar.
    calon_kasar = []
    for k in range(0, len(kasar_a) - len(kasar_p) + 1, 2):
        s = _mirip(kasar_a[k:k + len(kasar_p)], kasar_p)
        calon_kasar.append((s, k))
    calon_kasar.sort(reverse=True)
    pilihan = []
    for s, k in calon_kasar:
        if s < 0.40:
            break
        if all(abs(k - q) >= 10 for _, q in pilihan):
            pilihan.append((s, k))
        if len(pilihan) >= 3:
            break
    if not pilihan:
        return None

    # 3) rapatkan di resolusi penuh (+-40 angka) sekitar tiap kandidat,
    #    ambil yang terbaik. Syarat halus 0,60.
    skor_baik, k_halus = -2.0, -1
    for _, k_kasar in pilihan:
        tengah = k_kasar * tiap
        awal = max(0, tengah - 40)
        akhir = min(len(ekor_a) - bibit, tengah + 40)
        for k in range(awal, akhir + 1):
            s = _mirip(ekor_a[k:k + bibit], probe)
            if s > skor_baik:
                skor_baik, k_halus = s, k
    if k_halus < 0 or skor_baik < 0.60:
        return None

    # 4) perpanjang ke kanan per blok 25 angka (0,5 dtk), berhenti setelah
    #    2 blok jelek (<0,70) beruntun; pangkas ke blok bagus terakhir.
    #    Syarat tambahan: NILAI RATA-RATA semua blok >= 0,75 — jangkar
    #    saingan dari lagu beda biasanya hanya 0,5-0,7 putus-putus.
    blok = 25
    BATAS_BLOK, BATAS_RATA = 0.70, 0.75
    pa, pb = k_halus + bibit, m0 + bibit
    kanan_ok = (pa, pb)
    jelek = 0
    # nilai bibit dihitung sebagai 8 blok agar rata-rata adil
    jumlah_skor, jumlah_blok = skor_baik * 8, 8
    while pa + blok <= len(ekor_a) and pb + blok <= len(kepala_b):
        skor = _mirip(ekor_a[pa:pa + blok], kepala_b[pb:pb + blok])
        jumlah_skor += skor
        jumlah_blok += 1
        if skor >= BATAS_BLOK:
            pa += blok
            pb += blok
            kanan_ok = (pa, pb)
            jelek = 0
        else:
            jelek += 1
            if jelek >= 2:
                break
            pa += blok
            pb += blok
    pa, pb = kanan_ok

    # 5) perpanjang ke kiri dengan aturan sama (mundur per blok)
    la, lb = k_halus - 1, m0 - 1
    a_mulai, b_mulai = k_halus, m0
    jelek = 0
    while la - blok + 1 >= 0 and lb - blok + 1 >= 0:
        skor = _mirip(ekor_a[la - blok + 1:la + 1], kepala_b[lb - blok + 1:lb + 1])
        jumlah_skor += skor
        jumlah_blok += 1
        if skor >= BATAS_BLOK:
            a_mulai, b_mulai = la - blok + 1, lb - blok + 1
            la -= blok
            lb -= blok
            jelek = 0
        else:
            jelek += 1
            if jelek >= 2:
                break
            la -= blok
            lb -= blok

    a_mulai = max(0, a_mulai)
    b_mulai = max(0, b_mulai)
    # Region sejajar: [a_mulai, pa) dengan [b_mulai, pb)
    panjang = min(pa - a_mulai, pb - b_mulai)
    if panjang < min_panjang:
        return None
    if jumlah_blok > 0 and (jumlah_skor / jumlah_blok) < BATAS_RATA:
        return None  # mirip putus-putus -> bukan kembaran sejati
    return (a_mulai, b_mulai, panjang)  # relatif ekor/kepala; pemanggil yang menggeser


def durasi_detik(lintas_berkas):
    """Baca durasi via ffprobe. Gagal -> raise."""
    fp = cari_ffprobe()
    cmd = [
        fp, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nokey=1:noprint_wrappers=1", lintas_berkas,
    ]
    out = subprocess.check_output(cmd, timeout=30).decode().strip()
    return float(out)


def _korelasi_ternormalisasi(x, y):
    """Korelasi -1..1 dua daftar cuplik (buang rata-rata dulu).

    Kebal beda volume — yang dibandingkan BENTUK gelombang, bukan kerasnya.
    Ini metode paling tepat untuk geseran murni (hasil riset: NIST).
    """
    n = min(len(x), len(y))
    if n < 8:
        return -2.0
    x, y = x[:n], y[:n]
    rx = sum(x) / n
    ry = sum(y) / n
    atas = sum((a - rx) * (b - ry) for a, b in zip(x, y))
    bawah = math.sqrt(sum((a - rx) ** 2 for a in x)
                      * sum((b - ry) ** 2 for b in y))
    if bawah < 1e-9:
        return 1.0 if abs(rx - ry) < 50 else 0.0
    return atas / bawah


def _kotakkasar(seg, tiap=8):
    """Rata-rata tiap N cuplik (lowpass kotak sebelum desimasi).

    Desimasi mentah ([::8]) melipat frekuensi tinggi jadi pola palsu
    (aliasing) sehingga puncak sejati kalah oleh puncak saingan — terbukti
    di uji lokal (skor 0,52 vs 0,99). Rata-rata dulu = puncak sejati utuh.
    """
    return [sum(seg[i:i + tiap]) / min(tiap, len(seg) - i)
            for i in range(0, len(seg), tiap)]


def korelasi_halus(lintas_a, lintas_b, kira_a_detik, kira_b_detik,
                   sr=CONTOH_SR, jangkau_detik=0.2):
    """Rapatkan titik temu sampai ketepatan 1 cuplik via korelasi silang.

    kira_a/b_detik = tebakan kasar (dari sidik, teliti ~20 mdetik).
    Kembalikan (geser_contoh, skor): geser yang harus DITAMBAH ke posisi B
    agar sejajar A (dalam cuplik @sr; 1 cuplik = 0,125 mdetik @8kHz),
    skor -1..1 (makin dekat 1 makin identik).
    Dua tahap (kasar decimasi-8 lalu halus langkah-1) agar cepat tanpa numpy.
    Gagal -> (0, -2.0), jangan crash.
    """
    try:
        raw_a = decode_ke_raw(lintas_a, sr=sr)
        raw_b = decode_ke_raw(lintas_b, sr=sr)
    except Exception:
        return 0, -2.0
    va = struct.unpack("<%dh" % (len(raw_a) // 2), raw_a)
    vb = struct.unpack("<%dh" % (len(raw_b) // 2), raw_b)
    pa = int(kira_a_detik * sr)
    pb = int(kira_b_detik * sr)
    bentang = int(sr * 1.0)  # 1 detik sekitar tebakan
    a0 = max(0, pa - bentang // 2)
    b0 = max(0, pb - bentang // 2)
    seg_a = va[a0:a0 + bentang]
    seg_b = vb[b0:b0 + bentang]
    if len(seg_a) < 64 or len(seg_b) < 64:
        return 0, -2.0
    jangkau = int(sr * jangkau_detik)
    # Tahap kasar: desimasi-8 DENGAN lowpass kotak, langkah 2
    # (cepat, teliti ~2 mdetik, tanpa pola palsu aliasing)
    da = _kotakkasar(seg_a)
    db = _kotakkasar(seg_b)
    jk = max(1, jangkau // 8)
    skor_baik, g_kasar = -2.0, 0
    for g in range(-jk, jk + 1, 2):
        if g >= 0:
            s = _korelasi_ternormalisasi(da, db[g:g + len(da)])
        else:
            s = _korelasi_ternormalisasi(da[-g:-g + len(db)], db)
        if s > skor_baik:
            skor_baik, g_kasar = s, g
    # Tahap halus: resolusi penuh +-24 cuplik di sekitar tebakan kasar
    g0 = g_kasar * 8
    skor_baik, g_halus = -2.0, g0
    for g in range(g0 - 24, g0 + 25):
        if abs(g) > jangkau:
            continue
        if g >= 0:
            s = _korelasi_ternormalisasi(seg_a, seg_b[g:g + len(seg_a)])
        else:
            s = _korelasi_ternormalisasi(seg_a[-g:-g + len(seg_b)], seg_b)
        if s > skor_baik:
            skor_baik, g_halus = s, g
    return g_halus, skor_baik


def jepit_nol(lintas_berkas, kira_detik, sr=CONTOH_SR, jangkau_contoh=64):
    """Geser titik potong ke penyeberangan-nol terdekat (anti bunyi klik).

    Memotong tepat saat gelombang menyentuh nol = tidak ada loncatan
    tegangan = tidak ada bunyi 'tuk'. Kembalikan posisi detik final.
    Gagal -> tebakan awal (jangan crash).
    """
    try:
        raw = decode_ke_raw(lintas_berkas, sr=sr)
        v = struct.unpack("<%dh" % (len(raw) // 2), raw)
        tengah = int(kira_detik * sr)
        for d in range(0, jangkau_contoh + 1):
            for pos in (tengah + d, tengah - d):
                if 0 <= pos < len(v) - 1 and (v[pos] >= 0) != (v[pos + 1] >= 0):
                    return pos / float(sr)
        return kira_detik
    except Exception:
        return kira_detik


def rapatkan_presisi(lintas_a, lintas_b, a_sidik, b_sidik,
                     sr=CONTOH_SR, jendela_ms=JENDELA_MS, jangkau_contoh=400):
    """Jaga nama lama: kini memakai korelasi silang ternormalisasi.

    Kembalikan geser_contoh (ditambah ke posisi B). Presisi 1 cuplik.
    """
    tiap = sr * jendela_ms / 1000.0
    geser, _skor = korelasi_halus(
        lintas_a, lintas_b, (a_sidik + 1) * tiap / sr, (b_sidik + 1) * tiap / sr,
        sr=sr, jangkau_detik=max(0.05, jangkau_contoh / float(sr)))
    return geser


def sambung_halus(lintas_a, lintas_b, keluar, silang=0.03,
                  sr=CONTOH_SR, jendela_ms=JENDELA_MS, min_panjang=150):
    """Sambung A + B jadi satu berkas mulus berdasar ISI, bukan jam.

    Resep presisi (sesuai praktik siaran):
      sidik kasar (20 mdetik) -> korelasi silang sampai 1 cuplik
      (0,125 mdetik) -> potong A di penyeberangan-nol -> silang pendek.
    Kembalikan info dict:
      {keluar, tumpang_terukur, a_potong_detik, b_lewat_detik,
       geser_halus_contoh, sisa_mdetik, skor_korelasi, ragu, cara}
    sisa_mdetik = koreksi cuplik yang diterapkan (residu sesudahnya
    <= 1 cuplik = 0,125 mdetik karena pencarian langkah-1 + potong 6 desimal).
    ragu=True berarti sidik tidak ketemu -> sambung kasar darurat (jangan
    diam-diam buang suara, tapi tandai).
    """
    sidik_a = audio_ke_sidik(lintas_a, sr=sr, jendela_ms=jendela_ms)
    sidik_b = audio_ke_sidik(lintas_b, sr=sr, jendela_ms=jendela_ms)
    temu = cari_kembaran(sidik_a, sidik_b, min_panjang=min_panjang)
    dur_a = durasi_detik(lintas_a)
    dur_b = durasi_detik(lintas_b)
    detik_per_angka_a = dur_a / max(1, len(sidik_a))
    detik_per_angka_b = dur_b / max(1, len(sidik_b))

    ff = cari_ffmpeg()
    if temu is None:
        cmd = [
            ff, "-y", "-hide_banner", "-v", "error",
            "-i", lintas_a, "-i", lintas_b,
            "-filter_complex",
            f"[0:a][1:a]acrossfade=d={silang}[out]",
            "-map", "[out]", "-ac", "1", "-ar", "44100",
            keluar,
        ]
        subprocess.check_call(cmd, timeout=300)
        return {
            "keluar": keluar, "tumpang_terukur": 0.0,
            "a_potong_detik": dur_a, "b_lewat_detik": 0.0,
            "geser_halus_contoh": 0, "sisa_mdetik": None,
            "skor_korelasi": None, "ragu": True, "cara": "darurat-acrossfade-penuh",
        }

    a_mulai, b_mulai, panjang = temu
    # Ujung B dalam waktu-A: kalau B seluruhnya di dalam A (kasus uji /
    # rekaman ganda), keluaran yang benar = A UTUH (B tak menambah apa-apa).
    b_akhir_dalam_a = (a_mulai * detik_per_angka_a
                       - b_mulai * detik_per_angka_b + dur_b)
    if b_akhir_dalam_a <= dur_a + 0.05:
        shutil.copyfile(lintas_a, keluar)
        return {
            "keluar": keluar, "tumpang_terukur": round(dur_b, 3),
            "a_potong_detik": round(dur_a, 6), "b_lewat_detik": round(dur_b, 6),
            "geser_halus_contoh": 0, "sisa_mdetik": 0.0,
            "skor_korelasi": 1.0, "ragu": False,
            "cara": "B-di-dalam-A,keluar-A-utuh",
        }
    # Bagian kembar diambil dari A saja (hindari dobel): A dipotong di
    # AKHIR kembaran, B dilewat sampai AKHIR kembaran.
    kira_potong = (a_mulai + panjang) * detik_per_angka_a
    kira_lewat = (b_mulai + panjang) * detik_per_angka_b
    geser, skor = korelasi_halus(lintas_a, lintas_b, kira_potong, kira_lewat, sr=sr)
    if skor < 0.5:
        # Korelasi tak yakin -> jangan sok presisi; darurat + tandai.
        cmd = [
            ff, "-y", "-hide_banner", "-v", "error",
            "-i", lintas_a, "-i", lintas_b,
            "-filter_complex",
            f"[0:a][1:a]acrossfade=d={max(silang, 0.05)}[out]",
            "-map", "[out]", "-ac", "1", "-ar", "44100",
            keluar,
        ]
        subprocess.check_call(cmd, timeout=300)
        return {
            "keluar": keluar, "tumpang_terukur": round(panjang * detik_per_angka_a, 3),
            "a_potong_detik": round(kira_potong, 3),
            "b_lewat_detik": round(kira_lewat, 3),
            "geser_halus_contoh": int(geser),
            "sisa_mdetik": None, "skor_korelasi": round(skor, 4),
            "ragu": True, "cara": "darurat-korelasi-rendah",
        }
    kira_lewat += geser / float(sr)
    # Potong A tepat di penyeberangan-nol (anti klik); B cukup dilewat
    # presisi-cuplik karena silang pendek menutup sisanya.
    a_potong = jepit_nol(lintas_a, kira_potong, sr=sr)
    b_lewat = max(0.0, min(kira_lewat, dur_b - 0.05))
    a_potong = max(0.05, min(a_potong, dur_a - 0.05))
    tumpang = panjang * detik_per_angka_a  # durasi kembaran (sisi A)
    # Silang adaptif: kembar identik -> sangat pendek (tak terdengar);
    # korelasi meragukan -> lebih panjang menutup beda.
    silang_pakai = 0.02 if skor >= 0.97 else max(silang, 0.05)

    cmd = [
        ff, "-y", "-hide_banner", "-v", "error",
        "-i", lintas_a, "-i", lintas_b,
        "-filter_complex",
        f"[0:a]atrim=0:{a_potong:.6f},asetpts=PTS-STARTPTS[a0];"
        f"[1:a]atrim=start={b_lewat:.6f},asetpts=PTS-STARTPTS[b0];"
        f"[a0][b0]acrossfade=d={silang_pakai}[out]",
        "-map", "[out]", "-ac", "1", "-ar", "44100",
        keluar,
    ]
    subprocess.check_call(cmd, timeout=300)
    return {
        "keluar": keluar, "tumpang_terukur": round(tumpang, 3),
        "a_potong_detik": round(a_potong, 6), "b_lewat_detik": round(b_lewat, 6),
        "geser_halus_contoh": int(geser),
        "sisa_mdetik": round(abs(geser) / float(sr) * 1000.0, 3),
        "skor_korelasi": round(skor, 4), "ragu": False,
        "cara": "sidik+korelasi-1cuplik+titik-nol",
    }


def potong_pangkal_seamless(lintas_ekor_a, lintas_b, keluar, sr=CONTOH_SR, min_panjang=150):
    """Cari di detik berapa ekor A berakhir di dalam B, potong B mulai dari titik itu.

    Hasil: B baru yang dimulai TEPAT di titik di mana A berhenti (tanpa celah, tanpa dobel).
    """
    sidik_a = audio_ke_sidik(lintas_ekor_a, sr=sr)
    sidik_b = audio_ke_sidik(lintas_b, sr=sr)
    dur_a = durasi_detik(lintas_ekor_a)
    dur_b = durasi_detik(lintas_b)
    detik_per_angka_a = dur_a / max(1, len(sidik_a))
    detik_per_angka_b = dur_b / max(1, len(sidik_b))

    temu = cari_kembaran(sidik_a, sidik_b, min_panjang=min_panjang)
    if temu is None:
        # Fallback jika tidak ketemu: jangan buang suara, salin utuh dengan catatan ragu
        shutil.copyfile(lintas_b, keluar)
        return {
            "keluar": keluar, "titik_potong_detik": 0.0,
            "durasi_asli": round(dur_b, 3), "durasi_setelah_potong": round(dur_b, 3),
            "skor_korelasi": None, "ragu": True, "cara": "darurat-tanpa-potong",
        }

    a_mulai, b_mulai, panjang = temu
    kira_potong = (a_mulai + panjang) * detik_per_angka_a
    kira_lewat = (b_mulai + panjang) * detik_per_angka_b

    # Rapatkan presisi tingkat cuplik (0.125 ms)
    geser, skor = korelasi_halus(lintas_ekor_a, lintas_b, kira_potong, kira_lewat, sr=sr)
    if skor >= 0.5:
        kira_lewat += geser / float(sr)

    # Cari penyeberangan nol di B agar bebas bunyi klik
    b_lewat = jepit_nol(lintas_b, kira_lewat, sr=sr)
    b_lewat = max(0.0, min(b_lewat, dur_b - 0.05))

    ff = cari_ffmpeg()
    cmd = [
        ff, "-y", "-hide_banner", "-v", "error",
        "-ss", f"{b_lewat:.6f}", "-i", lintas_b,
        "-c", "copy", keluar,
    ]
    subprocess.check_call(cmd, timeout=300)
    return {
        "keluar": keluar,
        "titik_potong_detik": round(b_lewat, 6),
        "durasi_asli": round(dur_b, 3),
        "durasi_setelah_potong": round(dur_b - b_lewat, 3),
        "skor_korelasi": round(skor, 4),
        "ragu": False,
        "cara": "potong-pangkal-presisi-cuplik",
    }
