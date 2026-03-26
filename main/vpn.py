"""
vpn.py — Cloudflare WARP Auto-Connect
======================================
Ping stats_url, jika gagal 5x berturut-turut (interval 10 detik),
koneksi ke Cloudflare WARP. Jika sudah terkoneksi (IP 104.x.x.x),
restart koneksi WARP hingga dapat IP baru.
Program tetap hidup setelah berhasil re-connect.
"""

import requests
import urllib3
import subprocess
import time
import datetime
import signal
import sys
import os
import shutil

# Matikan warning SSL verify=False
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Zona waktu WITA (UTC+8)
WITA_TZ = datetime.timezone(datetime.timedelta(hours=8))

# =============================================
# True Color ANSI (24-bit RGB) — sama dengan record.py
# =============================================
TIME_COLOR = "\033[38;2;139;148;158m"   # #8b949e
INFO_COLOR = "\033[38;2;88;166;255m"    # #58a6ff
WARN_COLOR = "\033[38;2;210;153;34m"    # #d29922
ERR_COLOR  = "\033[38;2;248;81;73m"     # #f85149
UP_COLOR   = "\033[38;2;179;137;245m"   # #b389f5
TEXT_COLOR = "\033[38;2;201;209;217m"    # #c9d1d9
OK_COLOR   = "\033[38;2;63;185;80m"     # #3fb950 — hijau sukses
RESET      = "\033[0m"

TAG_WIDTH = 15

TAG_COLORS = {
    "[ERROR]"   : ERR_COLOR,
    "[FAIL]"    : ERR_COLOR,
    "[STOP]"    : ERR_COLOR,

    "[WARN]"    : WARN_COLOR,
    "[RETRY]"   : WARN_COLOR,
    "[WAIT]"    : WARN_COLOR,

    "[OK]"      : INFO_COLOR,
    "[DONE]"    : INFO_COLOR,
    "[START]"   : INFO_COLOR,
    "[INFO]"    : INFO_COLOR,
    "[SKIP]"    : INFO_COLOR,

    "[VPN]"     : UP_COLOR,
    "[IP]"      : UP_COLOR,
    "[WARP]"    : UP_COLOR,

    "[PING]"    : TIME_COLOR,
    "[ALIVE]"   : OK_COLOR,
}


# =============================================
# LOG — Verbose Server Log Style
# =============================================
def log(msg):
    matched_tag = None
    tag_color = INFO_COLOR
    for tag, color in TAG_COLORS.items():
        if msg.startswith(tag):
            matched_tag = tag
            tag_color = color
            break

    ts = datetime.datetime.now(WITA_TZ).strftime("%Y-%m-%d %H:%M:%S")

    if matched_tag:
        rest = msg[len(matched_tag):].lstrip()
        padding = " " * max(TAG_WIDTH - len(matched_tag), 1)
        line = (
            f"{TIME_COLOR}[{ts}]{RESET} "
            f"{tag_color}{matched_tag}{RESET}{padding}"
            f"{TEXT_COLOR}{rest}{RESET}"
        )
    else:
        padding = " " * TAG_WIDTH
        line = f"{TIME_COLOR}[{ts}]{RESET} {padding}{TEXT_COLOR}{msg}{RESET}"

    print(line, flush=True)


# =============================================
# KONFIGURASI
# =============================================
STATS_URL = "https://i.klikhost.com:8502/stats?json=1"
IP_CHECK_URL = "https://ipinfo.io/ip"
CLOUDFLARE_PREFIX = "104."

MAX_PING_RETRIES = 5       # Jumlah percobaan ping sebelum trigger VPN
PING_INTERVAL = 10         # Detik antar percobaan ping
MAX_WARP_RETRIES = 10      # Maks percobaan reconnect WARP untuk dapat IP baru
WARP_RECONNECT_WAIT = 5    # Detik tunggu setelah reconnect WARP
ALIVE_INTERVAL = 60        # Detik antar heartbeat saat idle

# Deteksi path warp-cli
WARP_CLI = shutil.which("warp-cli")
if not WARP_CLI:
    # Cek lokasi default Windows
    default_path = r"C:\Program Files\Cloudflare\Cloudflare WARP\warp-cli.exe"
    if os.path.isfile(default_path):
        WARP_CLI = default_path

if not WARP_CLI:
    # Fallback: asumsikan ada di PATH
    WARP_CLI = "warp-cli"


# =============================================
# HELPER FUNCTIONS
# =============================================
def run_warp(args, timeout=15):
    """Jalankan warp-cli dengan argumen tertentu."""
    cmd = [WARP_CLI] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.returncode == 0, result.stdout.strip()
    except FileNotFoundError:
        log(f"[ERROR] warp-cli tidak ditemukan di: {WARP_CLI}")
        log(f"[ERROR] Install Cloudflare WARP terlebih dahulu: https://1.1.1.1/")
        return False, ""
    except subprocess.TimeoutExpired:
        log(f"[WARN] warp-cli timeout ({timeout}s)")
        return False, ""
    except Exception as e:
        log(f"[ERROR] Gagal menjalankan warp-cli: {e}")
        return False, ""


def get_current_ip():
    """Dapatkan IP publik saat ini."""
    try:
        resp = requests.get(IP_CHECK_URL, timeout=10)
        if resp.status_code == 200:
            return resp.text.strip()
    except Exception:
        pass
    return None


def is_cloudflare_ip(ip):
    """Cek apakah IP adalah milik Cloudflare (awalan 104.)."""
    if ip and ip.startswith(CLOUDFLARE_PREFIX):
        return True
    return False


def ping_stats():
    """Ping stats_url, return True jika berhasil (HTTP 200)."""
    try:
        resp = requests.get(STATS_URL, timeout=5, verify=False)
        return resp.status_code == 200
    except Exception:
        return False


def warp_disconnect():
    """Putuskan koneksi WARP."""
    log("[WARP] Memutuskan koneksi WARP...")
    ok, out = run_warp(["disconnect"])
    if ok:
        log("[WARP] Koneksi WARP diputus.")
    else:
        log(f"[WARN] Gagal memutus WARP: {out}")
    time.sleep(2)
    return ok


def warp_connect():
    """Hubungkan ke WARP."""
    log("[WARP] Menghubungkan ke WARP...")
    ok, out = run_warp(["connect"])
    if ok:
        log("[WARP] Perintah connect berhasil dikirim.")
    else:
        log(f"[WARN] Gagal menghubungkan WARP: {out}")
    time.sleep(WARP_RECONNECT_WAIT)
    return ok


def warp_restart_for_new_ip(old_ip):
    """
    Restart koneksi WARP berulang kali sampai mendapat IP baru
    yang berbeda dari old_ip.
    """
    for attempt in range(1, MAX_WARP_RETRIES + 1):
        log(f"[WARP] Restart koneksi WARP (percobaan {attempt}/{MAX_WARP_RETRIES})...")

        warp_disconnect()
        warp_connect()

        new_ip = get_current_ip()

        if not new_ip:
            log(f"[WARN] Gagal mendapatkan IP setelah reconnect, coba lagi...")
            continue

        log(f"[IP] IP setelah reconnect: {new_ip}")

        if new_ip != old_ip:
            log(f"[OK] IP baru diperoleh! {old_ip} → {new_ip}")
            return True, new_ip
        else:
            log(f"[WARN] IP masih sama ({new_ip}), restart lagi...")

    log(f"[FAIL] Gagal mendapatkan IP baru setelah {MAX_WARP_RETRIES} percobaan.")
    return False, old_ip


# =============================================
# MAIN LOGIC
# =============================================
def main():
    log("[START] VPN Auto-Connect — Cloudflare WARP")
    log(f"[INFO] Stats URL: {STATS_URL}")
    log(f"[INFO] WARP CLI: {WARP_CLI}")
    log(f"[INFO] Ping retries: {MAX_PING_RETRIES}x, interval: {PING_INTERVAL}s")

    # ── FASE 1: Ping stats_url ──
    log(f"[PING] Memulai test ping ke stats server...")

    fail_count = 0
    for attempt in range(1, MAX_PING_RETRIES + 1):
        log(f"[PING] Percobaan {attempt}/{MAX_PING_RETRIES}...")

        if ping_stats():
            log(f"[OK] Stats server merespons normal (HTTP 200). VPN tidak diperlukan.")
            log(f"[DONE] Program selesai — server dapat dijangkau tanpa VPN.")
            # Tetap hidup
            stay_alive()
            return
        else:
            fail_count += 1
            log(f"[FAIL] Gagal ping ({fail_count}/{MAX_PING_RETRIES})")

            if fail_count < MAX_PING_RETRIES:
                log(f"[WAIT] Menunggu {PING_INTERVAL} detik sebelum percobaan berikutnya...")
                time.sleep(PING_INTERVAL)

    # ── FASE 2: Semua ping gagal — cek IP & koneksi WARP ──
    log(f"[ERROR] Stats server tidak merespons setelah {MAX_PING_RETRIES} percobaan.")
    log(f"[VPN] Memeriksa koneksi saat ini...")

    current_ip = get_current_ip()

    if current_ip:
        log(f"[IP] IP publik saat ini: {current_ip}")
    else:
        log(f"[WARN] Tidak dapat mendeteksi IP publik.")

    if current_ip and is_cloudflare_ip(current_ip):
        # Sudah pakai IP Cloudflare → restart untuk dapat IP baru
        log(f"[VPN] Sudah terkoneksi dengan IP Cloudflare ({current_ip}).")
        log(f"[VPN] Restart koneksi WARP untuk mendapatkan IP baru...")

        success, new_ip = warp_restart_for_new_ip(current_ip)

        if success:
            log(f"[OK] Berhasil mendapatkan IP baru: {new_ip}")
            verify_and_stay_alive(new_ip)
        else:
            log(f"[FAIL] Tidak bisa mendapatkan IP baru. Program tetap berjalan...")
            stay_alive()
    else:
        # Belum pakai Cloudflare → connect
        log(f"[VPN] Belum terkoneksi ke Cloudflare WARP. Menghubungkan...")

        if warp_connect():
            time.sleep(3)
            new_ip = get_current_ip()
            if new_ip:
                log(f"[IP] IP setelah connect: {new_ip}")

                if is_cloudflare_ip(new_ip):
                    log(f"[OK] Berhasil terkoneksi ke Cloudflare WARP!")
                    verify_and_stay_alive(new_ip)
                else:
                    log(f"[WARN] IP bukan Cloudflare ({new_ip}). WARP mungkin belum aktif sepenuhnya.")
                    stay_alive()
            else:
                log(f"[WARN] Tidak dapat mendeteksi IP setelah connect.")
                stay_alive()
        else:
            log(f"[FAIL] Gagal menghubungkan WARP.")
            stay_alive()


def verify_and_stay_alive(ip):
    """Verifikasi koneksi lalu masuk mode stay-alive."""
    log(f"[VPN] Verifikasi koneksi dengan ping stats server...")

    if ping_stats():
        log(f"[OK] Stats server dapat dijangkau melalui WARP (IP: {ip})")
    else:
        log(f"[WARN] Stats server masih tidak merespons meskipun sudah connect WARP.")
        log(f"[INFO] Program tetap berjalan — koneksi WARP aktif.")

    stay_alive()


def stay_alive():
    """
    Tetap hidup selamanya. Heartbeat setiap ALIVE_INTERVAL detik.
    Ctrl+C untuk berhenti.
    """
    log(f"[ALIVE] Program tetap berjalan (heartbeat setiap {ALIVE_INTERVAL}s). Ctrl+C untuk berhenti.")

    def signal_handler(sig, frame):
        log(f"[STOP] Sinyal interrupt diterima. Menutup program...")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    count = 0
    while True:
        try:
            time.sleep(ALIVE_INTERVAL)
            count += 1

            # Heartbeat log setiap 5 siklus (5 menit jika interval 60s)
            if count % 5 == 0:
                ip = get_current_ip()
                ip_str = ip if ip else "unknown"
                log(f"[ALIVE] Heartbeat #{count} — IP: {ip_str}")

        except KeyboardInterrupt:
            log(f"[STOP] Ctrl+C ditekan. Menutup program...")
            break
        except Exception as e:
            log(f"[ERROR] Error di stay_alive: {e}")
            time.sleep(10)


# =============================================
# ENTRY POINT
# =============================================
if __name__ == "__main__":
    main()