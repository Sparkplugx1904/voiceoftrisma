#!/usr/bin/env python3
"""
uji_estafet.py — latihan lokal SEBELUM git push (tanpa transkriptor).

Menjalankan 5 uji berurutan, berhenti di gagal pertama (kode keluar 1):
  [1] sidik_akurasi   : A digeser 2,5 detik + jeda 0,3s tetap ketemu jangkar
  [2] sambung_halus   : durasi hasil = A+B-tumpang (+-1,5s toleransi alat)
  [3] rekam_pura      : siaran_pura lokal -> record_v3 20 detik -> manifest ok
  [4] picu_kering     : ketuk GitHub PALSU (tanpa internet): sekali saja,
                        cap cegah ganda, BERHENTI memblokir, tanpa token aman
  [5] skenario_aneh   : audio beda total -> ragu (jangan hapus suara);
                        cutoff memblokir picu; warisan kosong -> ragu

Cara pakai:  python main/uji_estafet.py
Waktu: ~1-2 menit di laptop. TIDAK menjalankan transkriptor.
"""

import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import wave

EKSPERIMENTAL = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.dirname(EKSPERIMENTAL)
AKAR = os.path.dirname(MAIN)
for p in (AKAR, MAIN, EKSPERIMENTAL):
    if p not in sys.path:
        sys.path.insert(0, p)

GAGAL = []


def lapor(nama, ok, ket=""):
    print(f"[UJI-{nama}] {'LULUS' if ok else 'GAGAL'} {ket}", flush=True)
    if not ok:
        GAGAL.append(nama)


def buat_nada(lintas, detik=30, sr=22050, seed=7):
    """Musik pura-pura: akor + melodi + ketukan tak berulang.

    Sengaja dibuat kaya corak seperti radio asli (musik+omongan), BUKAN
    nada murni berulang — nada murni membuat banyak posisi kembar yang
    sama persis sehingga uji tidak mewakili dunia nyata.
    Deterministik (seed tetap) agar latihan bisa diulang.
    """
    import random
    rnd = random.Random(seed)
    nada_dasar = [196.0, 220.0, 246.9, 261.6, 293.7, 329.6, 349.2, 392.0, 440.0]
    seg = 2.0  # ganti akor tiap 2 detik
    n_seg = max(1, int(detik / seg) + 1)
    akor = [[rnd.choice(nada_dasar) * m for m in (1.0, 1.25, 1.5)] for _ in range(n_seg)]
    ketuk = sorted(rnd.uniform(0, detik) for _ in range(detik * 2))
    melodi = 500.0
    n = sr * detik
    # Amplop ketukan dihitung SEKALI di depan (bukan di tiap cuplik):
    # tiap ketukan = ledakan 0,03 dtk yang meluruh. Ini memangkas
    # ~40 juta perulangan jadi ~40 ribu agar uji tak macet.
    amplop = [0.0] * n
    lebark = int(sr * 0.03)
    for tk in ketuk:
        i0 = int(tk * sr)
        for i in range(i0, min(n, i0 + lebark)):
            amplop[i] += 1.0 - (i - i0) / lebark
    fase_m = 0.0
    fase_a = [0.0, 0.0, 0.0]
    with wave.open(lintas, "wb") as w:
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


def wav_ke_mp3(wav, mp3):
    ff = shutil.which("ffmpeg") or "ffmpeg"
    subprocess.check_call([ff, "-y", "-hide_banner", "-v", "error",
                           "-i", wav, "-codec:a", "libmp3lame",
                           "-b:a", "64k", mp3], timeout=60)


def uji_1_sidik(tmp):
    try:
        from sidik_suara import audio_ke_sidik, cari_kembaran, sidik_ke_detik
    except ImportError:
        from main.sidik_suara import audio_ke_sidik, cari_kembaran, sidik_ke_detik
    a_wav = os.path.join(tmp, "a.wav")
    b_wav = os.path.join(tmp, "b.wav")
    buat_nada(a_wav, detik=30)
    # B = A digeser 2,5 detik (buang 2,5 dtk awal) + sisip sunyi 0,3 dtk di tengah
    sr = 22050
    with wave.open(a_wav, "rb") as w:
        bingkai = w.readframes(w.getnframes())
    buang = int(sr * 2.5) * 2
    potong = bingkai[buang:]
    jeda = b"\x00" * int(sr * 0.3) * 2
    tengah = len(potong) // 2
    b_data = potong[:tengah] + jeda + potong[tengah:tengah + int(sr * 20) * 2]
    with wave.open(b_wav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b_data)
    ha = audio_ke_sidik(a_wav)
    hb = audio_ke_sidik(b_wav)
    print(f"   contoh sidik A: {[[round(v, 3) for v in p] for p in ha[500:504]]}", flush=True)
    print(f"   contoh sidik B: {[[round(v, 3) for v in p] for p in hb[100:104]]}", flush=True)
    temu = cari_kembaran(ha, hb, min_panjang=40)
    if temu is None:
        lapor("1-sidik", False, "jangkar tidak ketemu")
        return
    _, _, panjang = temu
    tumpang = sidik_ke_detik(panjang)
    # harapan: kembaran >= 10 detik (B 20 dtk berasal dari A)
    lapor("1-sidik", tumpang >= 10.0, f"tumpang_terukur={tumpang:.1f}d")


def uji_2_sambung(tmp):
    try:
        from sidik_suara import sambung_halus, durasi_detik
    except ImportError:
        from main.sidik_suara import sambung_halus, durasi_detik
    a_wav = os.path.join(tmp, "s_a.wav")
    buat_nada(a_wav, detik=25, seed=11)
    # B = 15 dtk terakhir A + 10 dtk baru: buat dengan menempel manual
    # Sederhana: B = A[10:25] + nada baru 10 dtk (total 25 dtk, tumpang 15 dtk)
    a_mp3 = os.path.join(tmp, "s_a.mp3")
    b_mp3 = os.path.join(tmp, "s_b.mp3")
    wav_ke_mp3(a_wav, a_mp3)
    # potong A[10:] lalu sambung nada baru via ffmpeg concat
    ff = shutil.which("ffmpeg") or "ffmpeg"
    a_tail = os.path.join(tmp, "a_tail.mp3")
    baru_wav = os.path.join(tmp, "baru.wav")
    baru_mp3 = os.path.join(tmp, "baru.mp3")
    buat_nada(baru_wav, detik=10, seed=23)
    wav_ke_mp3(baru_wav, baru_mp3)
    subprocess.check_call([ff, "-y", "-hide_banner", "-v", "error",
                           "-ss", "10", "-i", a_mp3, "-c", "copy", a_tail], timeout=60)
    daftar = os.path.join(tmp, "dl.txt")
    with open(daftar, "w") as f:
        f.write(f"file '{a_tail}'\nfile '{baru_mp3}'\n")
    subprocess.check_call([ff, "-y", "-hide_banner", "-v", "error",
                           "-f", "concat", "-safe", "0", "-i", daftar,
                           "-c:a", "libmp3lame", "-b:a", "64k", b_mp3], timeout=60)
    keluar = os.path.join(tmp, "gabung.mp3")
    info = sambung_halus(a_mp3, b_mp3, keluar)
    da, db, dg = durasi_detik(a_mp3), durasi_detik(b_mp3), durasi_detik(keluar)
    harap = da + db - info["tumpang_terukur"]
    # toleransi 2 detik (alat encode + silang)
    ok = abs(dg - harap) <= 2.0 and os.path.getsize(keluar) > 20000 and not info["ragu"]
    lapor("2-sambung", ok, f"A={da:.1f} B={db:.1f} tumpang={info['tumpang_terukur']:.1f} "
                           f"hasil={dg:.1f} harap~{harap:.1f} skor={info.get('skor_korelasi')} "
                           f"sisa={info.get('sisa_mdetik')}mdetik cara={info['cara']}")


def port_bebas():
    """Cari port localhost yang bebas (hindari rebutan sisa proses lama)."""
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def tunggu_stats(port, batas=180):
    """Tunggu /stats menjawab ON-AIR. False bila keburu batas."""
    import urllib.request
    mulai = time.monotonic()
    while time.monotonic() - mulai < batas:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/stats?json=1", timeout=5) as r:
                if '"streamstatus":1' in r.read().decode():
                    return True
        except Exception:
            pass
        time.sleep(3)
    return False


def uji_3_rekam_pura(tmp):
    rec = os.path.join(tmp, "rec")
    os.makedirs(rec, exist_ok=True)
    port = port_bebas()
    srv = subprocess.Popen(
        [sys.executable, os.path.join(EKSPERIMENTAL, "siaran_pura.py"),
         "--port", str(port), "--tunda", "1", "--lama", "300"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        if not tunggu_stats(port):
            srv.terminate()
            lapor("3-rekam", False, "pemancar tak siap-siap")
            return
        cmd = [sys.executable, os.path.join(MAIN, "record_v3.0.py"),
               "--stream-url", f"http://127.0.0.1:{port}/stream",
               "--stats-url", f"http://127.0.0.1:{port}/stats?json=1",
               "--durasi", "20", "--bagian", "8", "--max-umur", "300",
               "--tanpa-unggah", "--tanpa-batas-waktu",
               "--map-rekaman", rec, "--map-warisan", os.path.join(rec, "warisan"),
               "--rantai-id", "uji", "--nomor", "1"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=360)
        potongan = sorted(f for f in os.listdir(rec) if f.endswith(".mp3")
                          and not f.startswith("ekor_"))
        manif = os.path.join(rec, "manifest.json")
        ok = r.returncode == 0 and len(potongan) >= 2 and os.path.exists(manif)
        print("   log rekam (5 baris akhir):", flush=True)
        for baris in (r.stdout or "").strip().splitlines()[-5:]:
            print(f"   | {baris}", flush=True)
        if r.returncode != 0:
            print(f"   STDERR: {(r.stderr or '')[:500]}", flush=True)
        lapor("3-rekam", ok, f"return={r.returncode} bagian={len(potongan)}")
    except subprocess.TimeoutExpired:
        lapor("3-rekam", False, "macet >120d")
    finally:
        try:
            srv.terminate()
            srv.wait(timeout=5)
        except Exception:
            srv.kill()


def muat_perekam():
    """Muat main/record_v3.0.py lewat jalur berkas (namanya bertitik)."""
    import importlib.util
    lintas = os.path.join(MAIN, "record_v3.0.py")
    spec = importlib.util.spec_from_file_location("perekam_v3", lintas)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def uji_4_picu_kering(tmp):
    rec = muat_perekam()
    rec_dir = os.path.join(tmp, "picu")
    os.makedirs(rec_dir, exist_ok=True)
    os.environ.pop("GH_TOKEN", None)
    os.environ.pop("GITHUB_TOKEN", None)
    # 4a: tanpa token -> mode lokal, bukan gagal
    s = rec.picu_penerus("uji", 1, 20, map_rekaman=rec_dir,
                         tanpa_batas_waktu=True)
    ok_a = (s == "mode-lokal-tanpa-token")
    # 4b: token palsu + API palsu -> terpicu sekali, kedua kali diblokir cap
    os.environ["GH_TOKEN"] = "palsu-untuk-uji"
    calls = {"post": 0}

    class Resp:
        def __init__(self, kode, teks=""):
            self.status_code = kode
            self.text = teks

        def json(self):
            return {"workflow_runs": []}

    def get_kosong(*a, **k):
        return Resp(200)

    def post_ok(*a, **k):
        calls["post"] += 1
        badan = k.get("json", {})
        assert badan["inputs"]["rantai_id"] == "uji"
        assert badan["inputs"]["nomor"] == "2"
        assert "dispatches" in a[0] and "v3.0" in a[0]
        return Resp(204)

    s1 = rec.picu_penerus("uji", 1, 20, map_rekaman=rec_dir,
                          tanpa_batas_waktu=True, post_fn=post_ok, get_fn=get_kosong)
    s2 = rec.picu_penerus("uji", 1, 20, map_rekaman=rec_dir,
                          tanpa_batas_waktu=True, post_fn=post_ok, get_fn=get_kosong)
    ok_b = (s1 == "terpicu" and s2 == "sudah-pernah" and calls["post"] == 1)
    # 4c: BERHENTI memblokir walau token ada
    open(os.path.join(rec_dir, "BERHENTI"), "w").write("uji")
    s3 = rec.picu_penerus("uji", 9, 20, map_rekaman=rec_dir,
                          tanpa_batas_waktu=True, post_fn=post_ok, get_fn=get_kosong)
    ok_c = (s3 == "dilewat-berhenti" and calls["post"] == 1)
    os.remove(os.path.join(rec_dir, "BERHENTI"))
    # 4d: penerus sudah hidup -> jangan ketuk ganda
    def get_ada(*a, **k):
        class R:
            status_code = 200
            text = ""
            def json(self):
                return {"workflow_runs": [
                    {"id": 111, "name": "estafet uji #5",
                     "display_title": "", "status": "in_progress"}]}
        return R()
    rec_dir2 = os.path.join(tmp, "picu2")
    os.makedirs(rec_dir2, exist_ok=True)
    s4 = rec.picu_penerus("uji", 4, 20, map_rekaman=rec_dir2,
                          tanpa_batas_waktu=True, post_fn=post_ok, get_fn=get_ada)
    ok_d = (s4 == "sudah-ada" and calls["post"] == 1)
    os.environ.pop("GH_TOKEN", None)
    lapor("4-picu", ok_a and ok_b and ok_c and ok_d,
          f"lokal={s} picu1={s1} picu2={s2} berhenti={s3} kembar={s4}")


def uji_5_aneh(tmp):
    try:
        from sidik_suara import sambung_halus, audio_ke_sidik
    except ImportError:
        from main.sidik_suara import sambung_halus, audio_ke_sidik
    rec = muat_perekam()
    # 5a: dua audio beda total -> JANGAN hapus suara, tandai ragu
    x_wav = os.path.join(tmp, "x.wav")
    y_wav = os.path.join(tmp, "y.wav")
    buat_nada(x_wav, detik=12, seed=31)
    buat_nada(y_wav, detik=12, seed=77)  # seed beda = lagu beda (harus ragu)
    x_mp3 = os.path.join(tmp, "x.mp3")
    y_mp3 = os.path.join(tmp, "y.mp3")
    wav_ke_mp3(x_wav, x_mp3)
    wav_ke_mp3(y_wav, y_mp3)
    keluar = os.path.join(tmp, "beda.mp3")
    try:
        info = sambung_halus(x_mp3, y_mp3, keluar, min_panjang=200)
        ok_a = info["ragu"] is True and os.path.exists(keluar)
        ket_a = f"ragu={info['ragu']} cara={info['cara']}"
    except Exception as e:
        ok_a, ket_a = False, f"harusnya fallback ragu, malah raise {type(e).__name__}"
    # 5b: jam tutup memblokir picu (tanpa sentuh API)
    d = os.path.join(tmp, "cut")
    os.makedirs(d, exist_ok=True)
    s = rec.picu_penerus("uji", 1, 20, map_rekaman=d, jam_tutup="00:00",
                         tanpa_batas_waktu=False)
    ok_b = (s == "dilewat-tutup")
    # 5c: warisan kosong -> ragu, tidak crash
    w = rec.verifikasi_warisan(os.path.join(tmp, "tak-ada"), None)
    ok_c = (w["ragu"] is True)
    # 5d: warisan ADA + kepala sendiri seisi -> ketemu (simulasi B vs ekor A)
    import shutil as _sh
    wdir = os.path.join(tmp, "waris")
    os.makedirs(wdir, exist_ok=True)
    import json as _json
    sidik_x = audio_ke_sidik(x_mp3)
    with open(os.path.join(wdir, "ekor_uji_1.json"), "w", encoding="utf-8") as f:
        _json.dump({"sidik": sidik_x}, f)
    w2 = rec.verifikasi_warisan(wdir, x_mp3)  # kepala B = isi sama persis
    ok_d = (w2["ketemu"] is True and w2["ragu"] is False)
    lapor("5-aneh", ok_a and ok_b and ok_c and ok_d,
          f"beda[{ket_a}] tutup={s} warisan={w['cara']} warisan-ada={w2}")


def uji_6_serah_terima(tmp):
    """Simulasi estafet A->B: A merekam, ekornya dititip ke warisan B,
    B merekam belakangan dari siaran loop yang sama -> verifikasi B
    HARUS ketemu (kepastian isi, bukan jam)."""
    rec = os.path.join(tmp, "serah")
    a_dir = os.path.join(rec, "A")
    b_dir = os.path.join(rec, "B")
    w_dir = os.path.join(b_dir, "warisan")
    os.makedirs(w_dir, exist_ok=True)
    port = port_bebas()
    srv = subprocess.Popen(
        [sys.executable, os.path.join(EKSPERIMENTAL, "siaran_pura.py"),
         "--port", str(port), "--tunda", "1", "--lama", "600"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        if not tunggu_stats(port):
            srv.terminate()
            lapor("6-serah", False, "pemancar tak siap-siap")
            return
        dasar = [sys.executable, os.path.join(MAIN, "record_v3.0.py"),
                 "--stream-url", f"http://127.0.0.1:{port}/stream",
                 "--stats-url", f"http://127.0.0.1:{port}/stats?json=1",
                 "--tanpa-unggah", "--tanpa-batas-waktu",
                 "--tumpang", "10", "--margin", "5"]
        ra = subprocess.run(
            dasar + ["--durasi", "25", "--bagian", "25", "--max-umur", "300",
                     "--rantai-id", "serah", "--nomor", "1",
                     "--map-rekaman", a_dir, "--map-warisan", os.path.join(a_dir, "warisan")],
            capture_output=True, text=True, timeout=120)
        ekor_files = [f for f in os.listdir(a_dir) if f.startswith("ekor_")]
        if ra.returncode != 0 or not ekor_files:
            lapor("6-serah", False, f"A gagal return={ra.returncode} ekor={ekor_files}")
            return
        for ef in ekor_files:
            shutil.copyfile(os.path.join(a_dir, ef), os.path.join(w_dir, ef))
        rb = subprocess.run(
            dasar + ["--durasi", "45", "--bagian", "45", "--max-umur", "300",
                     "--rantai-id", "serah", "--nomor", "2",
                     "--map-rekaman", b_dir, "--map-warisan", w_dir],
            capture_output=True, text=True, timeout=180)
        baris = [b for b in (rb.stdout or "").splitlines() if "[WARISAN]" in b]
        ok = rb.returncode == 0 and any("'ketemu': True" in b for b in baris)
        lapor("6-serah", ok, f"return={rb.returncode} warisan={baris[-1:] if baris else 'TANPA-LOG'}")
    except subprocess.TimeoutExpired:
        lapor("6-serah", False, "macet (timeout)")
    finally:
        try:
            srv.terminate()
            srv.wait(timeout=5)
        except Exception:
            srv.kill()


def uji_7_presisi(tmp):
    """Presisi mendekati 0: B dipotong TEPAT-cuplik dari A (offset 2,537 dtk
    yang tidak pas jendela 20 mdetik) -> tumpang terukur harus 15,0 +-0,1,
    korelasi ~= 1, durasi hasil = 20,0 +-0,1."""
    try:
        from sidik_suara import sambung_halus, durasi_detik
    except ImportError:
        from main.sidik_suara import sambung_halus, durasi_detik
    sr = 22050
    a_wav = os.path.join(tmp, "p_a.wav")
    buat_nada(a_wav, detik=20, seed=55)
    with wave.open(a_wav, "rb") as w:
        fr = w.readframes(w.getnframes())
    mulai_cuplik = 55941  # = 2,5370068 dtk (sengaja tidak pas 20 mdetik)
    b_data = fr[mulai_cuplik * 2:(mulai_cuplik + sr * 15) * 2]
    b_wav = os.path.join(tmp, "p_b.wav")
    with wave.open(b_wav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b_data)
    a_mp3 = os.path.join(tmp, "p_a.mp3")
    b_mp3 = os.path.join(tmp, "p_b.mp3")
    wav_ke_mp3(a_wav, a_mp3)
    wav_ke_mp3(b_wav, b_mp3)
    keluar = os.path.join(tmp, "presisi.mp3")
    info = sambung_halus(a_mp3, b_mp3, keluar)
    dg = durasi_detik(keluar)
    ok = (not info["ragu"] and 14.9 <= info["tumpang_terukur"] <= 15.1
          and (info["skor_korelasi"] or 0) >= 0.9 and 19.9 <= dg <= 20.1)
    lapor("7-presisi", ok,
          f"tumpang={info['tumpang_terukur']} (harap 15,0) "
          f"skor={info.get('skor_korelasi')} sisa={info.get('sisa_mdetik')}mdetik "
          f"hasil={dg:.2f}d (harap 20,0) cara={info['cara']}")


def utama():
    print("=== UJI ESTAFET LOKAL (tanpa transkriptor) ===", flush=True)
    tmp = tempfile.mkdtemp(prefix="uji_estafet_")
    print(f"map uji: {tmp}", flush=True)
    try:
        uji_1_sidik(tmp)
        if not GAGAL:
            uji_2_sambung(tmp)
        if not GAGAL:
            uji_3_rekam_pura(tmp)
        if not GAGAL:
            uji_4_picu_kering(tmp)
        if not GAGAL:
            uji_5_aneh(tmp)
        if not GAGAL:
            uji_6_serah_terima(tmp)
        if not GAGAL:
            uji_7_presisi(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("=== HASIL: " + ("SEMUA LULUS" if not GAGAL else f"GAGAL di {GAGAL}") + " ===",
          flush=True)
    return 0 if not GAGAL else 1


if __name__ == "__main__":
    sys.exit(utama())
