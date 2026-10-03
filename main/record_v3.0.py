#!/usr/bin/env python3
"""
record_v3.0.py — perekam estafet (pekerja ke pekerja, tanpa wasit awan).

Bahasa awam:
  Pekerja A merekam sampai umur 5 jam 20 menit (bukan 6 jam, kasih jeda).
  PENTING: A membangunkan B *sebelum* A berhenti — selisih (tumpang +
  margin) detik — lalu A LANJUT merekam sampai batas umur. Hanya dengan
  cara ini B sempat mendengar ekor A (siaran langsung tidak bisa diulang).
  Pemicu dikirim A sendiri lewat API GitHub pakai GH_TOKEN (tanpa wasit
  awan). B merekam, lalu belakangan mengunduh ekor A dan membuktikan
  sambungan lewat ISI suara (lihat main/sidik_suara.py), bukan lewat jam.
  Rantai selalu satu garis lurus 1->2->3.

  Anti banjir: A hanya boleh memicu tepat 1 penerus, sekali saja, dan
  batal kalau: sudah memicu, penerus sudah hidup, jam tutup 18:30,
  atau ada perintah BERHENTI.

  Cara berhenti (untuk penerus yang bukan programmer):
    1) buat berkas bernama recordings/BERHENTI (isi bebas), atau
    2) jalankan ulang workflow v3 dengan isian perintah=berhenti, atau
    3) batalkan lari dari tombol GitHub (hanya menghentikan lari itu;
       rantai ikut berhenti bila B belum sempat dibangunkan, karena A
       batal mengetuk saat mati).

Hanya butuh: requests (+ internetarchive saat unggah). Tanpa numpy.
Transkriptor TIDAK dijalankan di sini (laptop tidak kuat).
"""

import argparse
import datetime
import hashlib
import hmac as _hmac_mod
import json
import os
import shutil
import signal
import subprocess
import sys
import time

try:
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    print("[ERROR] butuh pustaka 'requests'. Pasang: pip install requests", flush=True)
    sys.exit(2)

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# Bersihkan HTTP_PROXY / HTTPS_PROXY bila berisi URL daftar (warisan v2.0):
# requests secara otomatis memakai env var ini sebagai proxy server, sehingga
# bila berisi daftar raw.githubusercontent.com maka semua koneksi HTTP akan
# diarahkan ke github sebagai proxy dan gagal (404 / connection error).
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    _v = os.environ.get(_k, "")
    if _v.startswith("[") or "raw.githubusercontent.com" in _v:
        os.environ.pop(_k, None)

try:
    from sidik_suara import (audio_ke_sidik, cari_kembaran, sidik_ke_detik,
                             durasi_detik, potong_pangkal_seamless)
except ImportError:
    try:
        from main.sidik_suara import (audio_ke_sidik, cari_kembaran, sidik_ke_detik,
                                      durasi_detik, potong_pangkal_seamless)
    except ImportError:
        # fallback: tambah folder main ke path (saat dijalankan dari akar repo)
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
        from sidik_suara import (audio_ke_sidik, cari_kembaran, sidik_ke_detik,
                                 durasi_detik, potong_pangkal_seamless)

try:
    from tunnel_client import (init_tunnel, push_tunnel_log, set_tunnel_status,
                               get_instance_id, cek_runner_kembar_via_worker)
except ImportError:
    try:
        from main.tunnel_client import (init_tunnel, push_tunnel_log, set_tunnel_status,
                                       get_instance_id, cek_runner_kembar_via_worker)
    except ImportError:
        def push_tunnel_log(x): pass
        def set_tunnel_status(x): pass
        def get_instance_id(): return "vm-local"
        def init_tunnel(*args, **kwargs): pass
        def cek_runner_kembar_via_worker(*args, **kwargs): return False

# ---------- waktu ----------
WITA = datetime.timezone(datetime.timedelta(hours=8))
PEMILIK_JATUH = "Sparkplugx1904"
REPO_JATUH    = "voiceoftrisma"
BERKAS_ALUR_V3 = "main+transcript_v3.0.yml"

# URL Cloudflare Worker relay — override via env CLOUDFLARE_RELAY_URL
CLOUDFLARE_RELAY_URL = os.environ.get(
    "CLOUDFLARE_RELAY_URL",
    "https://voiceoftrisma.anandapradnyana68.workers.dev"
).rstrip("/")

# Tahap cek sopan milikmu (jangan dihujani): (mulai, selesai, jeda)
TAHAP = [
    (0, 120, 3),
    (120, 180, 5),
    (180, 300, 10),
    (300, 600, 15),
    (600, 1800, 30),
    (1800, 3000, 60),
    (3000, 3600, 90),
]

ARGS = None


def log(tag_msg):
    if ARGS is not None and getattr(ARGS, "tanpa_log", False):
        return
    cap = datetime.datetime.now(datetime.UTC).astimezone(WITA).strftime("%Y-%m-%d %H:%M:%S")
    baris = f"[{cap}] {tag_msg}"
    print(baris, flush=True)
    push_tunnel_log(baris)


def kini_wita():
    return datetime.datetime.now(datetime.UTC).astimezone(WITA)


def repo_tujuan():
    """Ambil owner/repo dari env GitHub, jatuh ke bawaan bila lokal."""
    slug = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if "/" in slug:
        pemilik, repo = slug.split("/", 1)
        if pemilik and repo:
            return pemilik, repo
    return PEMILIK_JATUH, REPO_JATUH


def ambil_token():
    return (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()


def ambil_relay_secret():
    """Ambil RELAY_SECRET — kunci bersama untuk HMAC antar runner dan Cloudflare."""
    return (os.environ.get("GH_RELAY_SECRET") or os.environ.get("RELAY_SECRET") or "").strip()


# ---------- HMAC-SHA256 relay (keamanan antar VM) ----------

def buat_tanda_hmac(rahasia: str, data: str) -> str:
    """Buat tanda tangan HMAC-SHA256 format 'sha256=<hex>'.

    data bisa berupa body JSON (POST) atau canonical query string (GET).
    """
    return "sha256=" + _hmac_mod.new(
        rahasia.encode("utf-8"),
        data.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# ---------- Waktu boot sesungguhnya ----------

def waktu_boot_sesungguhnya() -> float:
    """Ambil epoch seconds saat runner pertama kali dibuat.

    Prioritas:
    1. Env VM_BOOT_EPOCH (dicatat pada detik pertama langkah workflow GHA, 100% akurat & instan)
    2. GitHub API GET /runs/{run_id} -> created_at
    3. time.time() sebagai fallback
    """
    # 1. Cek VM_BOOT_EPOCH dari workflow step
    epoch_env = os.environ.get("VM_BOOT_EPOCH", "").strip()
    if epoch_env:
        try:
            epoch = float(epoch_env)
            log(f"[BOOT] waktu boot dari VM_BOOT_EPOCH: {int(epoch)} ({int(time.time() - epoch)}d lalu, akurat tanpa API)")
            return epoch
        except ValueError:
            pass

    # 2. Cek GitHub API
    token = ambil_token()
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if not token or not run_id:
        log("[BOOT] tanpa token/run-id — gunakan waktu sekarang sebagai fallback.")
        return time.time()
    pemilik, repo = repo_tujuan()
    try:
        r = requests.get(
            f"https://api.github.com/repos/{pemilik}/{repo}/actions/runs/{run_id}",
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json",
                     "User-Agent": "voiceoftrisma-estafet/3.0"},
            timeout=15,
        )
        if r.status_code == 200:
            created_at = r.json().get("created_at", "")
            # format ISO 8601: "2026-10-03T07:26:26Z"
            dt = datetime.datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ")
            dt = dt.replace(tzinfo=datetime.timezone.utc)
            epoch = dt.timestamp()
            log(f"[BOOT] waktu boot dari GitHub API: {created_at} ({int(time.time() - epoch)}d lalu)")
            return epoch
        log(f"[BOOT] GitHub API HTTP {r.status_code}, fallback ke waktu sekarang.")
    except Exception as e:
        log(f"[BOOT] gagal ambil waktu boot ({type(e).__name__}), fallback.")
    return time.time()


# ---------- Relay via Cloudflare Worker ----------

def picu_penerus_via_worker(rantai_id, nomor_saya, tumpang, margin, sesi, induk_run_id="",
                            target_rantai=None, nomor_target=None):
    """Minta Cloudflare Worker dispatch Runner penerus ke GitHub Actions.

    Lebih aman dari panggilan langsung ke GitHub API karena GITHUB_TOKEN
    hanya ada di Cloudflare secrets, bukan di env runner.
    Kembalikan: "terpicu" | "gagal" | "tanpa-secret"
    """
    rahasia = ambil_relay_secret()
    if not rahasia:
        log("[RELAY] GH_RELAY_SECRET kosong — tidak bisa picu via Worker. Cek GitHub Secrets.")
        return "tanpa-secret"

    nomor_b = int(nomor_target) if nomor_target is not None else (int(nomor_saya) + 1)
    r_id = str(target_rantai) if target_rantai is not None else str(rantai_id)
    run_id = os.environ.get("GITHUB_RUN_ID", induk_run_id)
    badan = {
        "rantai_id":    r_id,
        "nomor_b":      nomor_b,
        "tumpang":      int(tumpang),
        "margin":       int(margin),
        "sesi":         int(sesi),
        "induk_run_id": str(run_id),
    }
    badan_json = json.dumps(badan, separators=(",", ":"), sort_keys=True)
    tanda = buat_tanda_hmac(rahasia, badan_json)

    url = f"{CLOUDFLARE_RELAY_URL}/relay/trigger"
    for coba in range(1, 4):
        try:
            r = requests.post(
                url,
                data=badan_json,
                headers={"Content-Type": "application/json",
                         "X-Relay-Sig": tanda,
                         "User-Agent": "voiceoftrisma-estafet/3.0"},
                timeout=20,
            )
            if r.status_code == 200:
                log(f"[RELAY] Runner #{nomor_b} berhasil dipicu via Cloudflare (coba {coba}).")
                return "terpicu"
            if r.status_code in (401, 403):
                log(f"[RELAY] Ditolak HTTP {r.status_code} — RELAY_SECRET mungkin beda. Batal.")
                return "gagal"
            log(f"[RELAY] Trigger coba {coba} HTTP {r.status_code}, ulang...")
        except Exception as e:
            log(f"[RELAY] Trigger coba {coba} galat {type(e).__name__}, ulang...")
        time.sleep(5 * coba)
    log("[RELAY] Semua coba trigger gagal.")
    return "gagal"


def sinyal_siap_ke_worker(rantai_id, nomor):
    """Umumkan ke Cloudflare Worker bahwa runner ini sudah mulai merekam.

    Dipanggil oleh Runner B begitu chunk pertama selesai.
    Runner A akan polling /relay/status dan segera cut saat menerima sinyal ini.
    """
    rahasia = ambil_relay_secret()
    if not rahasia:
        log("[RELAY-SIAP] GH_RELAY_SECRET kosong — sinyal siap tidak terkirim.")
        return False

    run_id = os.environ.get("GITHUB_RUN_ID", "")
    badan = {"rantai_id": str(rantai_id), "nomor": int(nomor), "run_id": run_id}
    badan_json = json.dumps(badan, separators=(",", ":"), sort_keys=True)
    tanda = buat_tanda_hmac(rahasia, badan_json)

    url = f"{CLOUDFLARE_RELAY_URL}/relay/ready"
    for coba in range(1, 4):
        try:
            r = requests.post(
                url,
                data=badan_json,
                headers={"Content-Type": "application/json",
                         "X-Relay-Sig": tanda,
                         "User-Agent": "voiceoftrisma-estafet/3.0"},
                timeout=15,
            )
            if r.status_code == 200:
                log(f"[RELAY-SIAP] Sinyal 'Runner #{nomor} siap merekam' terkirim ke Cloudflare.")
                return True
            log(f"[RELAY-SIAP] HTTP {r.status_code} coba {coba}, ulang...")
        except Exception as e:
            log(f"[RELAY-SIAP] Galat coba {coba}: {type(e).__name__}, ulang...")
        time.sleep(3 * coba)
    log("[RELAY-SIAP] Gagal kirim sinyal siap setelah 3x.")
    return False


def tunggu_penerus_siap(rantai_id, nomor_b, batas_tunggu=900):
    """Poll Cloudflare Worker sampai Runner B mengumumkan dirinya siap merekam.

    Dipanggil Runner A setelah mempicu Runner B.
    batas_tunggu=900 detik (15 menit) — jika B tidak konfirmasi dalam 15 menit,
    A cut saja (daripada terus merekam sia-sia melewati batas VM).
    Kembalikan: True (B siap), False (timeout/gagal).
    """
    rahasia = ambil_relay_secret()
    if not rahasia:
        log("[RELAY-POLL] GH_RELAY_SECRET kosong — tidak bisa poll. Asumsikan B siap.")
        return True  # fail-open: jangan blokir A selamanya

    data_sign = f"rantai_id={rantai_id}&nomor={nomor_b}"
    tanda = buat_tanda_hmac(rahasia, data_sign)
    url = (f"{CLOUDFLARE_RELAY_URL}/relay/status"
           f"?rantai_id={rantai_id}&nomor={nomor_b}")

    mulai = time.monotonic()
    putaran = 0
    while time.monotonic() - mulai < batas_tunggu:
        putaran += 1
        try:
            r = requests.get(
                url,
                headers={"X-Relay-Sig": tanda,
                         "User-Agent": "voiceoftrisma-estafet/3.0"},
                timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                if data.get("siap"):
                    log(f"[RELAY-POLL] Penerus #{nomor_b} SIAP merekam! "
                        f"(putaran {putaran}, {int(time.monotonic()-mulai)}d menunggu)")
                    return True
                alasan = data.get("alasan", "?")
                if putaran % 6 == 1:  # log tiap ~30 detik
                    log(f"[RELAY-POLL] #{putaran} penerus #{nomor_b} belum siap ({alasan}), "
                        f"tunggu 5d... ({int(time.monotonic()-mulai)}d/{batas_tunggu}d)")
            else:
                log(f"[RELAY-POLL] HTTP {r.status_code}, coba lagi 5d...")
        except Exception as e:
            log(f"[RELAY-POLL] Galat ({type(e).__name__}), coba lagi 5d...")
        time.sleep(5)

    log(f"[RELAY-POLL] Batas tunggu {batas_tunggu}d habis — penerus #{nomor_b} tidak konfirmasi. "
        f"A akan cut dan upload sekarang.")
    return False


# ---------- berhenti / cutoff ----------
def sudah_diminta_berhenti(map_rekaman="recordings", perintah="jalan"):
    """True bila ada perintah berhenti dari mana pun."""
    if str(perintah).strip().lower() == "berhenti":
        return True
    if os.environ.get("ESTAFET_BERHENTI", "").strip() == "1":
        return True
    for nama in ("BERHENTI", "BERHENTI.txt", "STOP"):
        if os.path.exists(os.path.join(map_rekaman, nama)):
            return True
    return False


def detik_ke_tengah_malam():
    """Berapa detik tersisa menuju 23:59:59 WITA hari ini."""
    now = kini_wita()
    tengah_malam = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    sisa = (tengah_malam - now).total_seconds()
    return max(0.0, sisa)


def sudah_lewat_tengah_malam():
    """True bila waktu WITA sudah mencapai atau melewati 23:59:59."""
    now = kini_wita()
    return now.hour == 23 and now.minute == 59 and now.second >= 59


def lewat_jam_tutup(batas="23:59:59", abaikan=False):
    """True bila jam WITA sudah >= batas (format HH:MM atau HH:MM:SS)."""
    if abaikan:
        return False
    try:
        parts = [int(x) for x in str(batas).split(":")]
        now = kini_wita()
        if len(parts) == 3:
            return (now.hour, now.minute, now.second) >= (parts[0], parts[1], parts[2])
        return (now.hour, now.minute) >= (parts[0], parts[1])
    except Exception:
        return False


def ruang_cukup(map_rekaman="recordings", butuh_mb=100):
    """Cek ruang disk. False bila sempit (skenario tak terduga: disk penuh)."""
    try:
        total, dipakai, bebas = shutil.disk_usage(map_rekaman if os.path.exists(map_rekaman) else ".")
        return bebas >= butuh_mb * 1024 * 1024
    except Exception:
        return True  # tidak bisa cek -> jangan halangi, tapi catat


# ---------- tunggu siaran (tahap sopan) ----------
def jeda_tahap(detik_berlalu):
    for awal, akhir, jeda in TAHAP:
        if awal <= detik_berlalu < akhir:
            return jeda
    return 90


def tunggu_siaran(url_stats, url_stream, mulai=None, titik_picu=None, max_umur=None,
                  rantai_id=None, nomor=1, tumpang=900, margin=300,
                  perintah="jalan", jam_tutup="23:59:59", tanpa_batas_waktu=False,
                  sesi=1, sudah_picu=False, status_picu="belum-waktunya",
                  picu_ulang_pada=0.0, sudah_picu_hari_esok=False,
                  picu_esok_ulang_pada=0.0):
    """Polling sopan sampai ON-AIR dengan dukungan estafet standby 24 jam.

    Timer umur diukur dari t_mulai yang dikirim pemanggil — idealnya epoch
    waktu boot sesungguhnya (dari GitHub API), bukan time.monotonic() baru.

    Kembalikan tuple (status, sudah_picu, status_picu, picu_ulang_pada, sudah_picu_hari_esok, picu_esok_ulang_pada):
      status: "on-air" | "selesai" (umur habis) | "tengah-malam" | "batal"
    """
    set_tunnel_status("standby")
    log(f"[TUNGGU] menunggu siaran — cek ke: {url_stats}")
    awal_jam = kini_wita().hour
    putaran = 0
    # t_mulai: epoch seconds (bukan monotonic) agar bisa dibandingkan dengan time.time()
    t_mulai = mulai if mulai is not None else time.time()
    t_picu  = titik_picu if titik_picu is not None else 18600  # 5j10m dari boot
    t_max   = max_umur if max_umur is not None else 19800      # 5j30m dari boot
    r_id    = rantai_id or kini_wita().strftime("%Y-%m-%d")

    while True:
        if ARGS and sudah_diminta_berhenti(perintah=getattr(ARGS, "perintah", perintah)):
            log("[BERHENTI] perintah berhenti saat menunggu. Batal.")
            return "batal", sudah_picu, status_picu, picu_ulang_pada, sudah_picu_hari_esok, picu_esok_ulang_pada

        # 0. Cek batas tengah malam / jam tutup harian
        sisa_hari_ini = detik_ke_tengah_malam()
        if not sudah_picu_hari_esok and sisa_hari_ini <= 900 and time.time() >= picu_esok_ulang_pada:
            besok_str = (kini_wita().date() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            log(f"[TRANSISI HARI STANDBY] Waktu {kini_wita().strftime('%H:%M:%S')} WITA (<=15m ke tengah malam). "
                f"Picu Runner #1 Hari Esok ({besok_str}) via Cloudflare Worker...")
            status_esok = picu_penerus_via_worker(
                besok_str, nomor_saya=0, tumpang=int(tumpang), margin=int(margin), sesi=1,
                target_rantai=besok_str, nomor_target=1
            )
            log(f"[TRANSISI HARI STANDBY] status picu hari esok: {status_esok}")
            if status_esok == "terpicu":
                sudah_picu_hari_esok = True
            elif status_esok in ("gagal", "tanpa-secret"):
                picu_esok_ulang_pada = time.time() + 120
            else:
                sudah_picu_hari_esok = True

        if sudah_lewat_tengah_malam() or (ARGS and lewat_jam_tutup(getattr(ARGS, "jam_tutup", jam_tutup),
                                                                  getattr(ARGS, "tanpa_batas_waktu", tanpa_batas_waktu))):
            log("[TRANSISI HARI STANDBY] Pukul 23:59:59 WITA tercapai saat standby. Menutup hari ini untuk merge final.")
            return "tengah-malam", sudah_picu, status_picu, picu_ulang_pada, sudah_picu_hari_esok, picu_esok_ulang_pada

        now = kini_wita()
        if now.hour != awal_jam:
            awal_jam = now.hour  # reset ke tahap cepat tiap jam :00
        detik = now.minute * 60 + now.second
        jeda = jeda_tahap(detik)
        putaran += 1

        # umur dihitung dari epoch time (bisa bandingkan dengan waktu boot aktual)
        umur = time.time() - t_mulai

        # 1. Cek apakah sudah waktunya membangunkan runner penerus
        if not sudah_picu and umur >= t_picu and time.time() >= picu_ulang_pada:
            log(f"[ESTAFET STANDBY] umur #{nomor} {int(umur)}d >= titik_picu {t_picu}d. "
                f"Picu penerus #{nomor + 1} via Cloudflare Worker...")
            status_picu = picu_penerus_via_worker(
                r_id, int(nomor), int(tumpang), int(margin), int(sesi)
            )
            log(f"[ESTAFET STANDBY] status picu penerus: {status_picu}")
            if status_picu == "terpicu":
                sudah_picu = True
            elif status_picu in ("gagal", "tanpa-secret"):
                # Coba ulang 2 menit kemudian
                picu_ulang_pada = time.time() + 120
            else:
                sudah_picu = True

        # 2. Cek apakah umur runner sudah habis saat standby
        if umur >= t_max:
            log(f"[ESTAFET STANDBY] umur #{nomor} {int(umur)}d >= batas {t_max}d. "
                f"Selesai giliran standby, serahkan ke penerus.")
            return "selesai", sudah_picu, status_picu, picu_ulang_pada, sudah_picu_hari_esok, picu_esok_ulang_pada

        # 3. Cek apakah pemancar sudah ON-AIR
        try:
            r = requests.get(url_stats, timeout=8,
                             headers={"User-Agent": "voiceoftrisma-estafet/3.0"},
                             verify=False)
            if r.status_code == 200:
                bersih = r.text.replace(" ", "")
                if '"streamstatus":1' in bersih:
                    log("[OK] siaran ON-AIR — mulai merekam detik ini juga!")
                    set_tunnel_status("recording")
                    return "on-air", sudah_picu, status_picu, picu_ulang_pada, sudah_picu_hari_esok, picu_esok_ulang_pada
                log(f"[TUNGGU] #{putaran} OFF-AIR (umur {int(umur)}d/{t_max}d), cek lagi {jeda}d.")
            else:
                log(f"[TUNGGU] #{putaran} stats HTTP {r.status_code}, cek lagi {jeda}d.")
        except Exception as e:
            log(f"[TUNGGU] #{putaran} gagal jangkau stats ({type(e).__name__}), cek lagi {jeda}d.")

        sisa = 3600 - (now.minute * 60 + now.second)
        time.sleep(min(jeda, sisa + 1))


# ---------- berkas: sha, manifest, ffmpeg ----------
def cari_ffmpeg():
    p = shutil.which("ffmpeg")
    if p:
        return p
    for c in ("./ffmpeg", "bin/ffmpeg"):
        if os.path.exists(c):
            return c
    return "ffmpeg"


def sha256_berkas(lintas):
    h = hashlib.sha256()
    with open(lintas, "rb") as f:
        for blok in iter(lambda: f.read(1024 * 1024), b""):
            h.update(blok)
    return h.hexdigest()


def _hentikan_ffmpeg(proc):
    """Hentikan proses ffmpeg secara anggun agar buffer tersimpan rapi."""
    try:
        if sys.platform != "win32":
            proc.send_signal(signal.SIGINT)
        else:
            proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def rekam_satu_bagian(url, keluar, detik, stats_url=""):
    """Rekam satu potongan sepanjang `detik` via ffmpeg secara responsif.

    Memantau status siaran setiap 3 detik. Jika pemancar resmi OFF-AIR atau
    ada sinyal berhenti, ffmpeg segera dihentikan secara anggun agar audio
    yang sudah terekam tetap tersimpan utuh tanpa harus menunggu 10 menit.

    Kembalikan tuple (ok: bool, off_air_terdeteksi: bool).
    """
    if not ruang_cukup(os.path.dirname(keluar) or "."):
        log("[ERROR] ruang disk sempit (<100MB). Batal merekam bagian ini.")
        return False, False
    ff = cari_ffmpeg()
    # Tanpa -reconnect_at_eof agar bila server menutup koneksi, ffmpeg tidak looping
    cmd = [
        ff, "-y", "-hide_banner", "-v", "error",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-timeout", "5000000",
        "-i", url,
        "-t", str(int(detik)),
        "-c", "copy",
        "-metadata", "artist=VOT Radio Denpasar",
        keluar,
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception as e:
        log(f"[ERROR] gagal menjalankan ffmpeg: {e}")
        return False, False

    mulai = time.time()
    off_air_terdeteksi = False
    tengah_malam_tercapai = False

    while proc.poll() is None:
        time.sleep(3)
        # 1. Cek apakah ada sinyal henti manual
        if ARGS and sudah_diminta_berhenti(getattr(ARGS, "map_rekaman", "recordings"),
                                           getattr(ARGS, "perintah", "jalan")):
            log("[BERHENTI] sinyal henti diterima saat merekam. Menghentikan ffmpeg...")
            _hentikan_ffmpeg(proc)
            break

        # 2. Cek apakah batas tengah malam (23:59:59 WITA) / jam tutup tercapai
        if ARGS and lewat_jam_tutup(getattr(ARGS, "jam_tutup", "23:59:59"),
                                    getattr(ARGS, "tanpa_batas_waktu", False)):
            log("[TRANSISI HARI] Pukul 23:59:59 WITA tercapai saat merekam. "
                "Menutup potongan audio ini secara presisi...")
            tengah_malam_tercapai = True
            _hentikan_ffmpeg(proc)
            break

        # 3. Cek apakah pemancar sudah resmi OFF-AIR di server stats
        if stats_url and (time.time() - mulai >= 6):
            try:
                r = requests.get(stats_url, timeout=3,
                                 headers={"User-Agent": "voiceoftrisma-estafet/3.0"},
                                 verify=False)
                if r.status_code == 200 and '"streamstatus":0' in r.text.replace(" ", ""):
                    log("[OFF-AIR] Server siaran resmi OFF-AIR di tengah perekaman. Menutup bagian ini secara rapi...")
                    off_air_terdeteksi = True
                    _hentikan_ffmpeg(proc)
                    break
            except Exception:
                pass

        # 4. Batas waktu maksimum (detik + 60s)
        if time.time() - mulai > int(detik) + 60:
            log(f"[WARN] ffmpeg melebihi batas waktu {detik}s + 60s. Menghentikan.")
            _hentikan_ffmpeg(proc)
            break

    ada = os.path.exists(keluar)
    ukuran = os.path.getsize(keluar) if ada else 0
    # Berkas valid jika ada data audio (min 16KB bila OFF-AIR atau tengah malam di tengah jalan)
    min_byte = 16384 if (off_air_terdeteksi or tengah_malam_tercapai) else max(1024, int(min(detik, 10) * 2000))
    if ada and ukuran >= min_byte:
        return True, off_air_terdeteksi, tengah_malam_tercapai
    return False, off_air_terdeteksi, tengah_malam_tercapai


def tulis_ekor(bagian_terakhir, map_rekaman, rantai_id, nomor, tumpang_detik):
    """Potong ekor (tumpang+30 detik) dari bagian terakhir + sidiknya.

    Kembalikan dict info ekor (untuk artifact + warisan B).
    Gagal -> raise (jangan diam-diam tanpa ekor saat estafet).
    """
    os.makedirs(map_rekaman, exist_ok=True)
    ff = cari_ffmpeg()
    ekor_mp3 = os.path.join(map_rekaman, f"ekor_{rantai_id}_{nomor}.mp3")
    durasi_minta = int(tumpang_detik) + 30
    cmd = [ff, "-y", "-hide_banner", "-v", "error",
           "-sseof", f"-{durasi_minta}", "-i", bagian_terakhir,
           "-c", "copy", ekor_mp3]
    try:
        subprocess.check_call(cmd, timeout=120)
    except Exception:
        # fallback: salin utuh bila berkas lebih pendek dari minta
        shutil.copyfile(bagian_terakhir, ekor_mp3)
    sidik = audio_ke_sidik(ekor_mp3)
    info = {
        "rantai_id": rantai_id, "nomor": nomor,
        "dari_berkas": os.path.basename(bagian_terakhir),
        "ekor_berkas": os.path.basename(ekor_mp3),
        "sha256": sha256_berkas(ekor_mp3),
        "byte": os.path.getsize(ekor_mp3),
        "sidik": [[round(v, 4) for v in pasang] for pasang in sidik],
        "dibuat": kini_wita().strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(os.path.join(map_rekaman, f"ekor_{rantai_id}_{nomor}.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    return info


def verifikasi_warisan(map_warisan, bagian_kepala_sendiri, jendela_cari=30000):
    """Bandingkan ekor pewaris (A) vs kepala sendiri (B) via sidik angka.

    jendela_cari besar (1500 dtk) karena jeda antrean membuat titik temu
    bisa jauh dari ujung. Tidak pernah raise (gagal = ragu, lanjut).
    """
    try:
        calon = [f for f in os.listdir(map_warisan) if f.startswith("ekor_") and f.endswith(".json")]
    except OSError:
        return {"ketemu": False, "ragu": True, "cara": "tanpa-warisan"}
    if not calon:
        return {"ketemu": False, "ragu": True, "cara": "tanpa-warisan"}
    if not (bagian_kepala_sendiri and os.path.exists(bagian_kepala_sendiri)):
        return {"ketemu": False, "ragu": True, "cara": "belum-ada-kepala-sendiri"}
    try:
        with open(os.path.join(map_warisan, sorted(calon)[0]), encoding="utf-8") as f:
            sidik_waris = json.load(f).get("sidik", [])
        if not sidik_waris:
            return {"ketemu": False, "ragu": True, "cara": "warisan-tanpa-sidik"}
        sidik_saya = audio_ke_sidik(bagian_kepala_sendiri)
        temu = cari_kembaran(sidik_waris, sidik_saya, jendela_cari=jendela_cari)
        if temu is None:
            return {"ketemu": False, "ragu": True, "cara": "sidik-tidak-ketemu"}
        _, _, panjang = temu
        return {"ketemu": True, "ragu": False, "cara": "sidik-angka",
                "tumpang_terukur": round(sidik_ke_detik(panjang), 2)}
    except Exception as e:
        return {"ketemu": False, "ragu": True, "cara": f"galat-verifikasi:{type(e).__name__}"}


# ---------- pemicu penerus (langsung dari Python, tanpa Cloudflare) ----------
def penerus_sudah_hidup(pemilik, repo, token, rantai_id, nomor_lanjut,
                        get_fn=None, run_saya=""):
    """Cek apakah penerus bernomor itu sudah antre/jalan. True bila ada.

    Dibaca dari nama lari: workflow v3 menamai lari
    'estafet {rantai_id} #{nomor}' (lihat run-name di yml).
    get_fn untuk uji (suntik palsu). Gagal API -> False + peringatan
    (jangan blokir estafet hanya karena API down).
    """
    get_fn = get_fn or requests.get
    kunci = f"{rantai_id} #{nomor_lanjut}"
    try:
        r = get_fn(
            f"https://api.github.com/repos/{pemilik}/{repo}/actions/workflows/{BERKAS_ALUR_V3}/runs",
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json",
                     "User-Agent": "voiceoftrisma-estafet/3.0"},
            params={"per_page": 30, "event": "workflow_dispatch"},
            timeout=20,
        )
        if r.status_code != 200:
            log(f"[WARN] cek kembar gagal HTTP {r.status_code} — lanjut (jangan blokir).")
            return False
        for lari in r.json().get("workflow_runs", []):
            nama = str(lari.get("name", "")) + str(lari.get("display_title", ""))
            status = str(lari.get("status", ""))
            if kunci in nama and status in ("queued", "in_progress", "waiting", "requested"):
                if run_saya and str(lari.get("id", "")) == str(run_saya):
                    continue
                log(f"[KEMBAR] penerus #{nomor_lanjut} sudah hidup (lari {lari.get('id')}). Batal ketuk.")
                return True
        return False
    except Exception as e:
        log(f"[WARN] cek kembar galat ({type(e).__name__}) — lanjut (jangan blokir).")
        return False


def picu_penerus(rantai_id, nomor_saya, tumpang_detik, induk_info="",
                 map_rekaman="recordings", perintah="jalan",
                 jam_tutup="18:30", tanpa_batas_waktu=False,
                 margin="120", sesi=1, post_fn=None, get_fn=None):
    """Bangunkan tepat 1 penerus. Idempoten + anti banjir.

    Kembalikan status: terpicu | sudah-ada | sudah-pernah |
    dilewat-berhenti | dilewat-tutup | mode-lokal-tanpa-token | gagal-api
    """
    nomor_lanjut = int(nomor_saya) + 1
    if sudah_diminta_berhenti(map_rekaman, perintah):
        log("[PICU] batal: ada perintah BERHENTI.")
        return "dilewat-berhenti"
    if lewat_jam_tutup(jam_tutup, tanpa_batas_waktu):
        log("[PICU] batal: sudah jam tutup, tidak bangunkan penerus.")
        return "dilewat-tutup"
    os.makedirs(map_rekaman, exist_ok=True)
    cap_memicu = os.path.join(map_rekaman, f".sudah_memicu_{rantai_id}_{nomor_lanjut}")
    if os.path.exists(cap_memicu):
        log(f"[PICU] sudah pernah memicu #{nomor_lanjut} (ada cap). Batal ketuk ganda.")
        return "sudah-pernah"
    token = ambil_token()
    if not token:
        log("[PICU] mode lokal: tanpa GH_TOKEN, tidak ketuk GitHub (bukan gagal).")
        return "mode-lokal-tanpa-token"
    pemilik, repo = repo_tujuan()
    run_saya = os.environ.get("GITHUB_RUN_ID", "")
    if penerus_sudah_hidup(pemilik, repo, token, rantai_id, nomor_lanjut,
                           get_fn=get_fn, run_saya=run_saya):
        open(cap_memicu, "w").write("kembar-sudah-hidup")
        return "sudah-ada"
    post_fn = post_fn or requests.post
    url = (f"https://api.github.com/repos/{pemilik}/{repo}"
           f"/actions/workflows/{BERKAS_ALUR_V3}/dispatches")
    badan = {"ref": "main", "inputs": {
        "rantai_id": str(rantai_id), "nomor": str(nomor_lanjut),
        "tumpang": str(int(tumpang_detik)), "margin": str(margin),
        "induk_run_id": str(run_saya or induk_info),
        "perintah": "jalan", "mau_transkrip": "false",
        "sesi": str(sesi),
    }}
    galat_akhir = None
    for coba in range(1, 4):
        try:
            r = post_fn(url, headers={"Authorization": f"Bearer {token}",
                                      "Accept": "application/vnd.github+json",
                                      "User-Agent": "voiceoftrisma-estafet/3.0"},
                        json=badan, timeout=20)
            if r.status_code in (201, 204):
                open(cap_memicu, "w").write(f"terpicu-coba-{coba}")
                log(f"[PICU] penerus #{nomor_lanjut} terbangun (coba {coba}).")
                return "terpicu"
            if r.status_code in (401, 403, 404):
                log(f"[ERROR] ketuk ditolak HTTP {r.status_code}: {r.text[:200]}. "
                    f"Cek GH_TOKEN punya izin Actions:write.")
                return "gagal-api"
            log(f"[WARN] ketuk coba {coba} HTTP {r.status_code}, ulang...")
            galat_akhir = f"HTTP {r.status_code}"
        except Exception as e:
            log(f"[WARN] ketuk coba {coba} galat {type(e).__name__}, ulang...")
            galat_akhir = type(e).__name__
        time.sleep(5 * coba)
    log(f"[ERROR] gagal membangunkan penerus setelah 3x ({galat_akhir}). "
        f"Tulis handover manual, A perpanjang dikit.")
    # tulis petunjuk manual agar operator bisa ketuk via tombol
    with open(os.path.join(map_rekaman, f"HANDOVER_{rantai_id}_{nomor_lanjut}.txt"),
              "w", encoding="utf-8") as f:
        f.write(f"ketuk manual: workflow {BERKAS_ALUR_V3}\n"
                f"rantai_id={rantai_id} nomor={nomor_lanjut} "
                f"tumpang={tumpang_detik} induk_run_id={run_saya}\n")
    return "gagal-api"


# ---------- unggah & master harian ----------
def format_item_identifier(rantai_id):
    """Identifier kanonik V3 di Archive.org: TEPAT 1 identifier per tanggal (YYYYMMDD).

    Tidak boleh ada suffix sesi (_1, _2), nomor runner, maupun timestamp,
    agar seluruh segmen dalam satu hari kalender bergabung ke dalam
    satu item arsip harian yang sama tanpa duplikasi.
    """
    bersih = str(rantai_id).replace("-", "").strip()
    return f"vot-denpasar-{bersih}"


def unduh_master_harian(ident, nama_file, tujuan):
    """Coba unduh master harian dari archive.org untuk disambung."""
    # Daftar fallback jika sebelumnya ada item/file bertanda sesi _1
    calon_urls = [
        f"https://archive.org/download/{ident}/{nama_file}",
        f"https://archive.org/download/{ident}_1/{nama_file.replace('.mp3', '_1.mp3')}",
        f"https://archive.org/download/{ident}_1/{nama_file}",
        f"https://archive.org/download/{ident}/{nama_file.replace('.mp3', '_1.mp3')}",
    ]
    for url in calon_urls:
        try:
            r = requests.get(url, timeout=120, stream=True)
            if r.status_code == 200:
                with open(tujuan, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
                log(f"[MASTER] Berhasil unduh master sebelumnya ({os.path.getsize(tujuan)} byte) dari: {url}")
                return True
        except Exception as e:
            log(f"[WARN] gagal unduh master harian dari {url} ({type(e).__name__}): {e}")
    log(f"[MASTER] Master belum ada di archive.org (akan dibuat baru di item {ident}).")
    return False


def unggah_ke_archive(rantai_id, nomor, part_berkas, master_berkas=None,
                      sesi=1, map_rekaman="recordings", kunci=None, rahasia=None, coba=4):
    """Unggah part ke raw/ dan master harian ke root di archive.org.

    Struktur di Archive.org (Tepat 1 Identifier per Hari):
      - VOT-Denpasar_DD-MM-YYYY.mp3 (di root luar folder raw, langsung diputar web player)
      - raw/VOT-Denpasar_..._partX.mp3 (semua part mentah di dalam folder raw/)
      - raw/manifest_nX.json (metadata teknis)
      - raw/ekor_*.json (sidik suara estafet)
    """
    try:
        from internetarchive import upload
    except ImportError:
        log("[ERROR] pustaka internetarchive belum dipasang.")
        return None, None
    ident = format_item_identifier(rantai_id)
    tanggal_str = kini_wita().strftime("%d %B %Y")

    # 1. Part mentah milik runner ini masuk ke folder raw/
    nama_part = os.path.basename(part_berkas)
    paket = {f"raw/{nama_part}": part_berkas}

    # 2. Metadata manifest dan ekor json juga dirapikan ke raw/
    manif = os.path.join(map_rekaman, "manifest.json")
    if os.path.exists(manif):
        paket[f"raw/manifest_n{nomor}.json"] = manif
    ekor_json = os.path.join(map_rekaman, f"ekor_{rantai_id}_{nomor}.json")
    if os.path.exists(ekor_json):
        paket[f"raw/ekor_{rantai_id}_{nomor}.json"] = ekor_json

    # 3. File master harian diletakkan di luar folder raw/ (root)
    #    agar langsung diputar oleh pemutar web Archive.org
    nama_master = None
    if master_berkas and os.path.exists(master_berkas):
        nama_master = os.path.basename(master_berkas)
        paket[nama_master] = master_berkas

    for i in range(1, coba + 1):
        try:
            upload(ident, files=paket,
                   metadata={"mediatype": "audio",
                             "title": f"VOT Radio Denpasar - {tanggal_str}",
                             "creator": "VOT Radio Denpasar",
                             "date": kini_wita().strftime("%Y-%m-%d")},
                   access_key=kunci, secret_key=rahasia, verbose=False)
            url_detail = f"https://archive.org/details/{ident}"
            url_stream = f"https://archive.org/download/{ident}/{nama_master or nama_part}"
            log(f"[UNGGAH] ok: {url_detail}")
            return url_stream, ident
        except Exception as e:
            log(f"[WARN] unggah coba {i} gagal: {type(e).__name__}. Tunggu 10d...")
            time.sleep(10)
    log("[ERROR] semua coba unggah gagal.")
    return None, None


# ---------- unduh ekor induk (B mengambil bekal A belakangan) ----------
def unduh_ekor_induk(map_warisan, rantai_id, induk_run_id, batas_tunggu=720):
    """Unduh ekor A dari artifact lari induk. Kembalikan path json / None.

    Dipanggil B di AKHIR tugasnya (bukan di awal) karena ekor final A baru
    ada setelah A benar-benar berhenti — B sendiri mulai lebih dulu agar
    ada tumpang tindih isi. Cek map lokal dulu (uji / unduhan berkas
    alur), baru API. Gagal -> None (B lanjut dengan ragu, jangan mati).
    """
    import glob
    sudah = sorted(glob.glob(os.path.join(map_warisan, "ekor_*.json")))
    if sudah:
        log(f"[WARISAN] ekor sudah ada lokal: {os.path.basename(sudah[0])}")
        return sudah[0]
    token = ambil_token()
    if not token or not induk_run_id:
        log("[WARISAN] tanpa token/induk-run-id — lewati unduh (mode lokal).")
        return None
    pemilik, repo = repo_tujuan()
    kepala = {"Authorization": f"Bearer {token}",
              "Accept": "application/vnd.github+json",
              "User-Agent": "voiceoftrisma-estafet/3.0"}
    mulai = time.monotonic()
    while time.monotonic() - mulai < batas_tunggu:
        try:
            d = requests.get(
                f"https://api.github.com/repos/{pemilik}/{repo}"
                f"/actions/runs/{induk_run_id}/artifacts",
                headers=kepala, params={"per_page": 30}, timeout=20).json()
            cocok = [a for a in d.get("artifacts", [])
                     if str(a.get("name", "")).startswith("estafet-ekor-")]
            if cocok:
                art = cocok[0]
                log(f"[WARISAN] artifact ketemu: {art['name']} — unduh...")
                z = requests.get(
                    f"https://api.github.com/repos/{pemilik}/{repo}"
                    f"/actions/artifacts/{art['id']}/zip",
                    headers=kepala, timeout=120)
                z.raise_for_status()
                import io
                import zipfile
                with zipfile.ZipFile(io.BytesIO(z.content)) as zh:
                    for nama in zh.namelist():
                        if nama.endswith(".json") or nama.endswith(".mp3"):
                            zh.extract(nama, map_warisan)
                lagi = sorted(glob.glob(os.path.join(map_warisan, "ekor_*.json")))
                if lagi:
                    log(f"[WARISAN] ekor terunduh: {os.path.basename(lagi[0])}")
                    return lagi[0]
                return None
            log("[WARISAN] artifact induk belum ada, tunggu 30d...")
        except Exception as e:
            log(f"[WARISAN] galat unduh ({type(e).__name__}), coba lagi 30d...")
        time.sleep(30)
    log("[WARISAN] batas tunggu habis, induk tak menitip ekor. Lanjut ragu.")
    return None


# ---------- ambil awalan (kepala B untuk verifikasi) ----------
def ambil_awalan(bagian_daftar, detik_butuh, map_rekaman):
    """Gabung potongan-potongan AWAL sampai mencakup `detik_butuh`.

    Isi kembar selalu mulai di detik 0 milik B, jadi verifikasi cukup
    melihat awalan sepanjang (tumpang+margin+lebih), bukan seluruh rekaman
    berjam-jam (hemat waktu workers 5 jam sekalipun).
    Kembalikan path berkas sementara (pemanggil boleh hapus).
    """
    terkumpul, ambil = 0, []
    for b in bagian_daftar:
        ambil.append(b)
        try:
            terkumpul += durasi_bagian(b)
        except Exception:
            terkumpul += 600
        if terkumpul >= detik_butuh:
            break
    keluar = os.path.join(map_rekaman, "__verif_awalan__.mp3")
    if len(ambil) == 1:
        shutil.copyfile(ambil[0], keluar)
    else:
        gabung_internal_concat(ambil, keluar)
    return keluar


def durasi_bagian(lintas):
    """Baca durasi satu potongan via ffprobe."""
    return durasi_detik(lintas)


# ---------- alur utama ----------
def gabung_internal_concat(bagian_daftar, keluar):
    """Gabung bagian SE-RUNNER yang bersambung (tanpa tumpang) via concat.

    Coba salin-mentah dulu (cepat, tanpa susut). Kalau gagal (misal
    potongan beda kepala wadah karena putus-sambung) -> encode ulang
    via filter concat (lambat dikit, tapi selamat, jangan buang suara).
    """
    daftar = os.path.join(os.path.dirname(keluar) or ".", "concat_list.txt")
    with open(daftar, "w", encoding="utf-8") as f:
        for b in bagian_daftar:
            f.write(f"file '{os.path.abspath(b)}'\n")
    ff = cari_ffmpeg()
    dasar, ekst = os.path.splitext(keluar)
    tmp = dasar + ".__tmp__" + ekst  # ekstensi wajib dipertahankan (ffmpeg menebak format dari nama)

    def _jalan(cmd):
        p = subprocess.run(cmd, timeout=600,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if p.returncode != 0:
            raise subprocess.CalledProcessError(
                p.returncode, cmd, output=p.stdout, stderr=p.stderr)
    try:
        _jalan([ff, "-y", "-hide_banner", "-v", "error",
                "-f", "concat", "-safe", "0", "-i", daftar,
                "-c", "copy", tmp])
    except subprocess.CalledProcessError as e:
        log(f"[WARN] gabung salin-mentah gagal ({str(e.stderr[:160])!r}) — coba encode ulang...")
        n = len(bagian_daftar)
        masukan, saring = [], ""
        for i, b in enumerate(bagian_daftar):
            masukan += ["-i", b]
            saring += f"[{i}:a]"
        saring += f"concat=n={n}:v=0:a=1[out]"
        _jalan([ff, "-y", "-hide_banner", "-v", "error",
                *masukan, "-filter_complex", saring,
                "-map", "[out]", "-ac", "1", "-ar", "44100",
                "-codec:a", "libmp3lame", "-b:a", "64k", tmp])
    os.replace(tmp, keluar)
    try:
        os.remove(daftar)
    except OSError:
        pass
    return keluar


def jalan_utama():
    global ARGS
    urai = argparse.ArgumentParser(description="VOT perekam estafet v3")
    urai.add_argument("--rantai-id", default=kini_wita().strftime("%Y-%m-%d"))
    urai.add_argument("--nomor", type=int, default=1)
    urai.add_argument("--sesi", type=int, default=1, help="Nomor sesi tayang siaran pada hari itu (1, 2, ...)")
    urai.add_argument("--induk-run-id", default="")
    urai.add_argument("--tumpang", type=int, default=900)   # 15 menit overlap
    urai.add_argument("--margin", type=int, default=300)    # 5 menit toleransi antrean
    urai.add_argument("--bagian", type=int, default=600)    # chunk 10 menit
    urai.add_argument("--max-umur", type=int, default=19800)  # 5j30m dari boot
    urai.add_argument("--jam-tutup", default="23:59:59")
    urai.add_argument("--tanpa-batas-waktu", action="store_true")
    urai.add_argument("--stream-url", default=os.environ.get("STREAM_URL", ""))
    urai.add_argument("--stats-url", default="https://i.klikhost.com:8502/stats?json=1")
    urai.add_argument("--lewati-cek", action="store_true")
    urai.add_argument("--tanpa-unggah", action="store_true")
    urai.add_argument("--tanpa-log", action="store_true")
    urai.add_argument("--durasi", type=int, default=0)
    urai.add_argument("--perintah", default="jalan")
    urai.add_argument("--map-rekaman", default="recordings")
    urai.add_argument("--map-warisan", default="recordings/warisan")
    urai.add_argument("--proxy", default="")
    ARGS = urai.parse_args()

    if sudah_diminta_berhenti(ARGS.map_rekaman, ARGS.perintah):
        log("[BERHENTI] perintah berhenti sejak awal. Keluar tanpa merekam.")
        return 0
    if lewat_jam_tutup(ARGS.jam_tutup, ARGS.tanpa_batas_waktu):
        log("[TUTUP] sudah lewat jam tutup sejak awal. Keluar tanpa merekam.")
        return 0
    if not ARGS.stream_url:
        log("[ERROR] STREAM_URL kosong. Isi --stream-url atau env STREAM_URL.")
        return 2
    if not ARGS.tanpa_unggah:
        if not (os.environ.get("MY_ACCESS_KEY") and os.environ.get("MY_SECRET_KEY")):
            log("[ERROR] kunci archive.org kosong. Isi MY_ACCESS_KEY+MY_SECRET_KEY atau tambah --tanpa-unggah.")
            return 2

    os.makedirs(ARGS.map_rekaman, exist_ok=True)
    os.makedirs(ARGS.map_warisan, exist_ok=True)

    # ===== INISIALISASI TUNNEL WEBSOCKET & LOG HUB =====
    instance_id = get_instance_id()
    mulai_boot = waktu_boot_sesungguhnya()
    init_tunnel(CLOUDFLARE_RELAY_URL, instance_id, ARGS.rantai_id, int(ARGS.nomor), boot_epoch=mulai_boot)
    log(f"[TUNNEL] Terhubung ke Cloudflare WebSocket Hub — Instance ID: {instance_id}")

    # Anti kembar saat bangun via Cloudflare Worker (tanpa GitHub API / tanpa token)
    if cek_runner_kembar_via_worker(CLOUDFLARE_RELAY_URL, ARGS.rantai_id, int(ARGS.nomor), instance_id):
        log("[MUNDUR] Runner kembar aktif bernomor sama sudah ada di Cloudflare Worker. Mundur.")
        set_tunnel_status("cancelled_twin")
        return 0

    elapsed_saat_mulai = time.time() - mulai_boot
    log(f"[BOOT] elapsed sejak boot: {int(elapsed_saat_mulai)}d "
        f"(install+checkout sudah memakan {int(elapsed_saat_mulai)}d dari quota {ARGS.max_umur}d)")

    tanggal = kini_wita().strftime("%d-%m-%Y")
    dasar = f"VOT-Denpasar_{tanggal}_rantai{ARGS.rantai_id}_n{ARGS.nomor}"
    bagian_daftar, manifest = [], []
    batas_total = ARGS.durasi or None
    sudah_rekam = 0
    idx = 0

    # Titik bangunkan penerus: tumpang + margin SEBELUM batas max_umur dari boot
    titik_picu = max(0, int(ARGS.max_umur) - int(ARGS.tumpang) - int(ARGS.margin))
    sudah_picu, status_picu = False, "belum-waktunya"
    picu_ulang_pada = 0.0
    sudah_picu_hari_esok = False
    picu_esok_ulang_pada = 0.0
    log(f"[ESTAFET] titik_picu: {titik_picu}d dari boot, batas: {ARGS.max_umur}d dari boot.")

    if not ARGS.lewati_cek:
        status_tunggu, sudah_picu, status_picu, picu_ulang_pada, sudah_picu_hari_esok, picu_esok_ulang_pada = tunggu_siaran(
            ARGS.stats_url, ARGS.stream_url,
            mulai=mulai_boot,          # epoch seconds — titik nol dari boot
            titik_picu=titik_picu,
            max_umur=ARGS.max_umur, rantai_id=ARGS.rantai_id, nomor=int(ARGS.nomor),
            tumpang=int(ARGS.tumpang), margin=int(ARGS.margin), perintah=ARGS.perintah,
            jam_tutup=ARGS.jam_tutup, tanpa_batas_waktu=ARGS.tanpa_batas_waktu,
            sesi=ARGS.sesi, sudah_picu=sudah_picu, status_picu=status_picu,
            picu_ulang_pada=picu_ulang_pada,
            sudah_picu_hari_esok=sudah_picu_hari_esok,
            picu_esok_ulang_pada=picu_esok_ulang_pada
        )
        if status_tunggu in ("selesai", "tengah-malam"):
            log("[ESTAFET STANDBY] umur standby habis atau tengah malam tercapai. Keluar sukses.")
            return 0
        elif status_tunggu == "batal":
            log("[BERHENTI] tunggu siaran dibatalkan (perintah/jam tutup).")
            return 0
            return 0
    else:
        log("[LEWAT] cek siaran dilewati.")

    off_air_selesai = False
    sudah_kirim_sinyal_siap = False  # (untuk runner B: sudah bilang siap ke A?)
    while True:
        if sudah_diminta_berhenti(ARGS.map_rekaman, ARGS.perintah):
            log("[BERHENTI] berhenti di antara bagian.")
            break
        # 0. Cek batas tengah malam / jam tutup harian
        sisa_hari_ini = detik_ke_tengah_malam()
        if not sudah_picu_hari_esok and sisa_hari_ini <= 900 and time.time() >= picu_esok_ulang_pada:
            besok_str = (kini_wita().date() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            log(f"[TRANSISI HARI] Waktu {kini_wita().strftime('%H:%M:%S')} WITA (<=15m ke tengah malam). "
                f"Picu Runner #1 Hari Esok ({besok_str}) via Cloudflare Worker...")
            status_esok = picu_penerus_via_worker(
                besok_str, nomor_saya=0, tumpang=int(ARGS.tumpang), margin=int(ARGS.margin), sesi=1,
                target_rantai=besok_str, nomor_target=1
            )
            log(f"[TRANSISI HARI] Status pemicu Runner Hari Esok: {status_esok}")
            if status_esok == "terpicu":
                sudah_picu_hari_esok = True
            elif status_esok in ("gagal", "tanpa-secret"):
                picu_esok_ulang_pada = time.time() + 120
            else:
                sudah_picu_hari_esok = True

        if sudah_lewat_tengah_malam() or lewat_jam_tutup(ARGS.jam_tutup, ARGS.tanpa_batas_waktu):
            log("[TRANSISI HARI] Pukul 23:59:59 WITA tercapai — hari ini resmi selesai! "
                "Melakukan cut rekaman dan persiapan merge harian final.")
            break

        # Umur dihitung dari waktu boot sesungguhnya (epoch)
        umur = time.time() - mulai_boot
        if umur >= ARGS.max_umur:
            log(f"[ESTAFET] umur {int(umur)}d >= maks {ARGS.max_umur}d. Setop, tulis ekor.")
            break

        # ═══ PICU RUNNER PENERUS LEWAT CLOUDFLARE (saat umur >= titik_picu) ═══
        if not sudah_picu and umur >= titik_picu and time.time() >= picu_ulang_pada:
            status_picu = picu_penerus_via_worker(
                ARGS.rantai_id, int(ARGS.nomor), int(ARGS.tumpang),
                int(ARGS.margin), int(ARGS.sesi)
            )
            log(f"[ESTAFET] status picu penerus: {status_picu}")
            if status_picu == "terpicu":
                sudah_picu = True
            elif status_picu in ("gagal", "tanpa-secret"):
                picu_ulang_pada = time.time() + 120  # coba ulang 2 menit lagi
            else:
                sudah_picu = True

        sisa = None
        if batas_total is not None:
            sisa = batas_total - sudah_rekam
            if sisa <= 0:
                break
        panjang = ARGS.bagian if sisa is None else min(ARGS.bagian, sisa)
        berkas = os.path.join(ARGS.map_rekaman, f"{dasar}_{idx:03d}.mp3")
        log(f"[REKAM] bagian {idx} ({panjang}d) -> {berkas}")
        ok, off_air_di_tengah, tengah_malam_tercapai = rekam_satu_bagian(
            ARGS.stream_url, berkas, panjang, stats_url=ARGS.stats_url
        )

        if not ok:
            # Jika gagal karena OFF-AIR atau tengah malam
            if off_air_di_tengah or tengah_malam_tercapai:
                if tengah_malam_tercapai or sudah_lewat_tengah_malam():
                    log("[TRANSISI HARI] Tengah malam 23:59:59 WITA tercapai. Menutup hari ini untuk merge harian.")
                    break
                # Jika OFF-AIR di siang hari: Runner TETAP AKTIF di VM ini (standby)!
                log(f"[STANDBY] Siaran OFF-AIR di siang hari. Runner #{ARGS.nomor} TETAP AKTIF di VM ini "
                    f"(sisa umur: {int(ARGS.max_umur - umur)}d). Menunggu siaran ON-AIR kembali...")
                status_tunggu, sudah_picu, status_picu, picu_ulang_pada, sudah_picu_hari_esok, picu_esok_ulang_pada = tunggu_siaran(
                    ARGS.stats_url, ARGS.stream_url,
                    mulai=mulai_boot, titik_picu=titik_picu, max_umur=ARGS.max_umur,
                    rantai_id=ARGS.rantai_id, nomor=int(ARGS.nomor),
                    tumpang=int(ARGS.tumpang), margin=int(ARGS.margin), perintah=ARGS.perintah,
                    jam_tutup=ARGS.jam_tutup, tanpa_batas_waktu=ARGS.tanpa_batas_waktu,
                    sesi=ARGS.sesi, sudah_picu=sudah_picu, status_picu=status_picu,
                    picu_ulang_pada=picu_ulang_pada,
                    sudah_picu_hari_esok=sudah_picu_hari_esok,
                    picu_esok_ulang_pada=picu_esok_ulang_pada
                )
                if status_tunggu == "on-air":
                    log("[OK] Siaran ON-AIR kembali — melanjutkan perekaman di runner ini!")
                    continue
                elif status_tunggu in ("selesai", "tengah-malam", "batal"):
                    break
            else:
                # Retry jika error jaringan sementara
                for jeda_coba, tunggu in enumerate([5, 15, 30, 60, 90], 1):
                    log(f"[WARN] bagian gagal (coba {jeda_coba}), tunggu {tunggu}d lalu ulangi.")
                    time.sleep(tunggu)
                    ok, off_air_di_tengah, tengah_malam_tercapai = rekam_satu_bagian(
                        ARGS.stream_url, berkas, panjang, stats_url=ARGS.stats_url
                    )
                    if ok or off_air_di_tengah or tengah_malam_tercapai:
                        break
                if not ok:
                    if off_air_di_tengah or tengah_malam_tercapai:
                        if tengah_malam_tercapai or sudah_lewat_tengah_malam():
                            break
                        continue
                    log("[ERROR] bagian gagal 5x beruntun tapi pemancar masih ON-AIR. Lewati bagian ini, lanjut rekam.")
                    idx += 1
                    continue

        try:
            info = {"berkas": os.path.basename(berkas), "sha256": sha256_berkas(berkas),
                    "byte": os.path.getsize(berkas),
                    "mulai": kini_wita().strftime("%H:%M:%S")}
        except OSError as e:
            log(f"[ERROR] berkas hilang setelah rekam: {e}. Hentikan.")
            break
        bagian_daftar.append(berkas)
        manifest.append(info)
        sudah_rekam += panjang

        # Jika chunk ini dipotong karena tengah malam
        if tengah_malam_tercapai or sudah_lewat_tengah_malam():
            log("[TRANSISI HARI] Pukul 23:59:59 WITA tercapai. Bagian terakhir hari ini tersimpan rapi.")
            break

        # Jika chunk ini dipotong karena OFF-AIR di siang hari
        if off_air_di_tengah:
            log(f"[STANDBY] Siaran jeda setelah bagian {idx}. Runner #{ARGS.nomor} TETAP AKTIF di VM ini "
                f"(sisa umur: {int(ARGS.max_umur - umur)}d). Menunggu siaran ON-AIR kembali...")
            status_tunggu, sudah_picu, status_picu, picu_ulang_pada, sudah_picu_hari_esok, picu_esok_ulang_pada = tunggu_siaran(
                ARGS.stats_url, ARGS.stream_url,
                mulai=mulai_boot, titik_picu=titik_picu, max_umur=ARGS.max_umur,
                rantai_id=ARGS.rantai_id, nomor=int(ARGS.nomor),
                tumpang=int(ARGS.tumpang), margin=int(ARGS.margin), perintah=ARGS.perintah,
                jam_tutup=ARGS.jam_tutup, tanpa_batas_waktu=ARGS.tanpa_batas_waktu,
                sesi=ARGS.sesi, sudah_picu=sudah_picu, status_picu=status_picu,
                picu_ulang_pada=picu_ulang_pada,
                sudah_picu_hari_esok=sudah_picu_hari_esok,
                picu_esok_ulang_pada=picu_esok_ulang_pada
            )
            if status_tunggu == "on-air":
                log("[OK] Siaran ON-AIR kembali — melanjutkan perekaman di runner ini!")
                idx += 1
                continue
            elif status_tunggu in ("selesai", "tengah-malam", "batal"):
                break

        # ═══ SINYAL "SIAP" UNTUK RUNNER SEBELUMNYA (Runner B) ═══
        # B mengirimkan sinyal ke Cloudflare begitu chunk pertama selesai.
        # Runner A yang sedang menunggu akan segera cut dan upload setelah menerima ini.
        if int(ARGS.nomor) > 1 and not sudah_kirim_sinyal_siap and idx == 0:
            sudah_kirim_sinyal_siap = sinyal_siap_ke_worker(ARGS.rantai_id, int(ARGS.nomor))

        # ═══ TUNGGU RUNNER PENERUS SIAP (Runner A) ═══
        # Setelah A memicu B, A terus rekam tapi mulai poll apakah B sudah siap.
        # Begitu B konfirmasi, A selesaikan chunk ini lalu cut dan upload.
        if sudah_picu and not (int(ARGS.nomor) > 1 and not sudah_kirim_sinyal_siap):
            # Hanya poll jika A (nomor=1 dst) dan B sudah dipicu
            if int(ARGS.nomor) == 1 or (int(ARGS.nomor) > 1 and sudah_kirim_sinyal_siap):
                nomor_b = int(ARGS.nomor) + 1
                log(f"[RELAY-POLL] Cek apakah penerus #{nomor_b} sudah siap merekam...")
                if tunggu_penerus_siap(ARGS.rantai_id, nomor_b, batas_tunggu=900):
                    log(f"[ESTAFET] Penerus #{nomor_b} siap! A segera selesaikan chunk ini dan upload.")
                    break  # A keluar dari loop rekam, lanjut ke upload

        # denyut hidup untuk tombol BERHENTI manual
        try:
            open(os.path.join(ARGS.map_rekaman, f".denyut_{ARGS.rantai_id}_{ARGS.nomor}"), "w").write(
                f"{kini_wita().isoformat()} bagian={idx}")
        except OSError:
            pass
        idx += 1

    if not bagian_daftar:
        log("[INFO] tidak ada bagian yang berhasil direkam.")
        return 0

    with open(os.path.join(ARGS.map_rekaman, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"rantai_id": ARGS.rantai_id, "nomor": ARGS.nomor,
                   "bagian": manifest}, f, ensure_ascii=False, indent=1)

    tutup = lewat_jam_tutup(ARGS.jam_tutup, ARGS.tanpa_batas_waktu) or sudah_diminta_berhenti(
        ARGS.map_rekaman, ARGS.perintah)
    # Estafet V3: 1 tanggal = 1 master arsip harian utuh (selalu sesi 1, tanpa pemisahan identifier)
    sesi_berikutnya = 1
    if not tutup and not sudah_picu:
        # Lari pendek / pergantian sesi: picu penerus lewat Cloudflare Worker
        status_picu = picu_penerus_via_worker(
            ARGS.rantai_id, int(ARGS.nomor), int(ARGS.tumpang),
            int(ARGS.margin), int(sesi_berikutnya)
        )
        log(f"[ESTAFET] status pemicu akhir: {status_picu}")
    elif tutup:
        log("[ESTAFET] rantai selesai (tutup/berhenti). Tidak memicu penerus.")

    # Tulis ekor final (mencakup seluruh tumpang+margin) untuk B.
    try:
        ekor = tulis_ekor(bagian_daftar[-1], ARGS.map_rekaman,
                          ARGS.rantai_id, ARGS.nomor,
                          int(ARGS.tumpang) + int(ARGS.margin))
        log(f"[EKOR] {ekor['ekor_berkas']} sha={ekor['sha256'][:12]}.. angka={len(ekor['sidik'])}")
    except Exception as e:
        log(f"[ERROR] gagal tulis ekor: {type(e).__name__}: {e}")
        return 1

    # B membuktikan sambung: unduh ekor induk (kalau ada), bandingkan ISI
    # melawan AWALAN sendiri (isi kembar selalu mulai di detik 0 milik B).
    butuh_trim_seamless = (int(ARGS.nomor) > 1) or (int(ARGS.nomor) == 1 and bool(ARGS.induk_run_id))
    if butuh_trim_seamless:
        unduh_ekor_induk(ARGS.map_warisan, ARGS.rantai_id, ARGS.induk_run_id,
                         batas_tunggu=(0 if not ambil_token() else 720))
        try:
            awalan = ambil_awalan(bagian_daftar, int(ARGS.tumpang) + int(ARGS.margin) + 60,
                                  ARGS.map_rekaman)
            hasil = verifikasi_warisan(ARGS.map_warisan, awalan)
            log(f"[WARISAN] verifikasi vs awalan sendiri: {hasil}")
            try:
                os.remove(awalan)
            except OSError:
                pass
        except Exception as e:
            log(f"[WARISAN] verifikasi gagal jalan: {type(e).__name__}: {e}")

    # 1. Gabung internal semua potongan milik runner ini menjadi satu part utuh
    part_mentah = os.path.join(ARGS.map_rekaman, f"{dasar}_part{ARGS.nomor}.mp3")
    try:
        if len(bagian_daftar) == 1:
            shutil.copyfile(bagian_daftar[0], part_mentah)
        else:
            gabung_internal_concat(bagian_daftar, part_mentah)
        log(f"[GABUNG] internal part {ARGS.nomor} ok -> {part_mentah}")
    except Exception as e:
        log(f"[ERROR] gabung internal gagal: {type(e).__name__}: {e}")
        part_mentah = bagian_daftar[-1]

    # 2. Seamless Trimming: Jika Runner > 1 ATAU Runner == 1 dengan induk_run_id (transisi tengah malam),
    #    potong pangkal audio sendiri agar mulai tepat di mana Runner sebelumnya berhenti (anti-gap, anti-dobel).
    part_final = part_mentah
    if butuh_trim_seamless:
        json_warisan = unduh_ekor_induk(ARGS.map_warisan, ARGS.rantai_id, ARGS.induk_run_id,
                                        batas_tunggu=(0 if not ambil_token() else 720))
        ekor_mp3 = None
        if json_warisan and os.path.exists(json_warisan):
            try:
                with open(json_warisan, encoding="utf-8") as f:
                    data_w = json.load(f)
                    nama_ekor_mp3 = data_w.get("ekor_berkas", "")
                calon = os.path.join(ARGS.map_warisan, nama_ekor_mp3)
                if os.path.exists(calon):
                    ekor_mp3 = calon
            except Exception:
                pass
        # Fallback jika nama berkas tidak cocok persis: cari berkas .mp3 di map_warisan
        if not ekor_mp3:
            import glob
            calon_mp3 = sorted(glob.glob(os.path.join(ARGS.map_warisan, "ekor_*.mp3")))
            if calon_mp3:
                ekor_mp3 = calon_mp3[0]

        if ekor_mp3 and os.path.exists(ekor_mp3):
            part_seamless = os.path.join(ARGS.map_rekaman, f"{dasar}_part{ARGS.nomor}_seamless.mp3")
            info_potong = potong_pangkal_seamless(ekor_mp3, part_mentah, part_seamless)
            log(f"[SEAMLESS] Hasil potong pangkal: {info_potong}")
            if not info_potong.get("ragu") and os.path.exists(part_seamless):
                part_final = part_seamless
            else:
                log("[SEAMLESS] Deteksi ragu, menggunakan audio utuh agar tidak ada suara hilang.")
        else:
            log("[SEAMLESS] Berkas ekor mp3 tidak ditemukan, menggunakan audio utuh.")

    # 3. Master Harian di luar folder raw/ (root Archive.org):
    #    - Runner 1: master harian awal adalah part 1 itu sendiri
    #    - Runner > 1: download master harian sebelumnya dari Archive.org, lalu concat part_final
    nama_master = f"VOT-Denpasar_{tanggal}.mp3"
    master_final = os.path.join(ARGS.map_rekaman, nama_master)
    ident_harian = format_item_identifier(ARGS.rantai_id)

    if int(ARGS.nomor) == 1:
        shutil.copyfile(part_final, master_final)
    else:
        master_lama = os.path.join(ARGS.map_rekaman, "__master_sebelumnya__.mp3")
        if not ARGS.tanpa_unggah and unduh_master_harian(ident_harian, nama_master, master_lama):
            try:
                gabung_internal_concat([master_lama, part_final], master_final)
                log(f"[MASTER] Berhasil sambung master harian + part {ARGS.nomor} -> {master_final}")
                try:
                    os.remove(master_lama)
                except OSError:
                    pass
            except Exception as e:
                log(f"[WARN] gagal sambung ke master lama: {e}. Gunakan part sendiri sebagai master.")
                shutil.copyfile(part_final, master_final)
        else:
            shutil.copyfile(part_final, master_final)

    if ARGS.tanpa_unggah:
        log(f"[LEWAT] tanpa unggah. Part di {part_final}, master di {master_final}. Pemicu={status_picu}")
        set_tunnel_status("done")
        return 0

    set_tunnel_status("uploading")
    url, iid = unggah_ke_archive(ARGS.rantai_id, ARGS.nomor, part_final,
                                 master_berkas=master_final,
                                 sesi=ARGS.sesi,
                                 map_rekaman=ARGS.map_rekaman,
                                 kunci=os.environ.get("MY_ACCESS_KEY"),
                                 rahasia=os.environ.get("MY_SECRET_KEY"))
    if url and "GITHUB_ENV" in os.environ:
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as f:
            f.write(f"ARCHIVE_URL={url}\nITEM_ID={iid}\n")
    set_tunnel_status("done" if url else "error_upload")
    return 0 if url else 1


if __name__ == "__main__":
    # Ctrl+C yang sopan: jangan rusak berkas setengah jadi
    def _tangan(signum, bingkai):
        log("[BERHENTI] sinyal henti diterima. Selesaikan bagian ini lalu keluar.")
        try:
            open(os.path.join(getattr(ARGS, "map_rekaman", "recordings") if ARGS else "recordings",
                              "BERHENTI"), "w").write("sinyal")
        except OSError:
            pass
    try:
        signal.signal(signal.SIGINT, _tangan)
        signal.signal(signal.SIGTERM, _tangan)
    except Exception:
        pass
    sys.exit(jalan_utama())
