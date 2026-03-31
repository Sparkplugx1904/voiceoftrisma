import requests
import urllib3
import socket
import subprocess
import time
import datetime
import signal
import sys
import os
import json
import argparse
from internetarchive import upload
import threading
import re
import shutil
import queue
import random
import concurrent.futures

# --- Setup dasar ---
os.system("chmod +x ffmpeg ffprobe")

# Matikan warning SSL verify=False agar log tidak terganggu
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Zona waktu WITA (UTC+8)
WITA_OFFSET = datetime.timedelta(hours=8)
WITA_TZ = datetime.timezone(WITA_OFFSET)

# Ambil MY_ACCESS_KEY dan MY_SECRET_KEY dari environment variable (GitHub Secrets)
MY_ACCESS_KEY = os.environ.get("MY_ACCESS_KEY")
MY_SECRET_KEY = os.environ.get("MY_SECRET_KEY")

# --- Global state ---
ARGS = None
TAG_WIDTH = 15

# =============================================
# True Color ANSI (24-bit RGB)
# =============================================
TIME_COLOR = "\033[38;2;139;148;158m"   # #8b949e — abu-abu kebiruan (timestamp)
INFO_COLOR = "\033[38;2;88;166;255m"    # #58a6ff — biru (info/sukses)
WARN_COLOR = "\033[38;2;210;153;34m"    # #d29922 — kuning/oranye (peringatan)
ERR_COLOR  = "\033[38;2;248;81;73m"     # #f85149 — merah (error)
UP_COLOR   = "\033[38;2;179;137;245m"   # #b389f5 — ungu (upload/merge)
TEXT_COLOR = "\033[38;2;201;209;217m"    # #c9d1d9 — abu-abu terang (teks utama)
RESET      = "\033[0m"

TAG_COLORS = {
    # Red — error / berhenti paksa
    "[ERROR]"       : ERR_COLOR,
    "[FAIL]"        : ERR_COLOR,
    "[STOP]"        : ERR_COLOR,
    "[CUT-OFF]"     : ERR_COLOR,
    "[JSON-ERR]"    : ERR_COLOR,

    # Yellow/Orange — peringatan / kondisi menunggu
    "[WARN]"        : WARN_COLOR,
    "[OFFLINE]"     : WARN_COLOR,
    "[RETRY]"       : WARN_COLOR,
    "[DELAY]"       : WARN_COLOR,
    "[WAIT]"        : WARN_COLOR,

    # Blue — info, sukses, status positif
    "[OK]"          : INFO_COLOR,
    "[DONE]"        : INFO_COLOR,
    "[START]"       : INFO_COLOR,
    "[END]"         : INFO_COLOR,
    "[ARCHIVE]"     : INFO_COLOR,
    "[LINK]"        : INFO_COLOR,
    "[ITEM]"        : INFO_COLOR,
    "[ENV]"         : INFO_COLOR,
    "[CLEAN]"       : INFO_COLOR,
    "[RUN]"         : INFO_COLOR,
    "[SKIP]"        : INFO_COLOR,
    "[CODEC]"       : INFO_COLOR,
    "[UPLOAD-PREP]" : INFO_COLOR,
    "[CMD]"         : INFO_COLOR,
    "[PROXY-SEARCH]": INFO_COLOR,
    "[PROXY-OK]"    : INFO_COLOR,

    # Purple — upload / transfer data
    "[UPLOAD]"      : UP_COLOR,
    "[MERGE]"       : UP_COLOR,

    # Gray — output eksternal / restart
    "[FFMPEG]"      : TIME_COLOR,
    "[RESTART]"     : TIME_COLOR,
    "[PING]"        : TIME_COLOR,

    # Added proxy fail to warning/error based logging
    "[PROXY-FAIL]"  : ERR_COLOR,
}


# =============================================
# HTTP FINGERPRINT — Randomized Headers
# =============================================
_UA_POOL = [
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Chrome macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Firefox Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    # Firefox Linux
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    # Edge
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    # Safari macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]

_ACCEPT_POOL = [
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
]

_LANG_POOL = [
    "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    "en-US,en;q=0.9,id;q=0.8",
    "id,en-US;q=0.9,en;q=0.8",
    "en-GB,en;q=0.9,id;q=0.8",
]

_REFERER_POOL = [
    "https://www.google.com/",
    "https://www.google.co.id/",
    "https://www.bing.com/",
    "",  # Kadang tidak ada referrer
]

# Satu set header yang di-generate per-run (konsisten dalam satu sesi)
def _generate_headers():
    ua = random.choice(_UA_POOL)
    headers = {
        "User-Agent": ua,
        "Accept": random.choice(_ACCEPT_POOL),
        "Accept-Language": random.choice(_LANG_POOL),
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "DNT": random.choice(["1", "0"]),
        "Cache-Control": random.choice(["no-cache", "max-age=0"]),
    }
    ref = random.choice(_REFERER_POOL)
    if ref:
        headers["Referer"] = ref
    return headers

# Header default — di-generate sekali saat startup
_SESSION_HEADERS = _generate_headers()

def get_headers():
    """Kembalikan headers session saat ini."""
    return _SESSION_HEADERS.copy()


# =============================================
# Interactive Console — live commands saat runtime
# =============================================
class InteractiveConsole:
    """
    Background thread yang membaca input user secara karakter-per-karakter.
    Selama user mengetik, log di-buffer. Saat Enter ditekan,
    input user ditampilkan sebagai [CMD] log, lalu buffer di-flush.
    """

    def __init__(self):
        self._typing = threading.Event()
        self._buffer = queue.Queue()
        self.skip_stage = threading.Event()
        self._running = True
        self._lock = threading.Lock()

    def start(self):
        t = threading.Thread(target=self._input_loop, daemon=True)
        t.start()

    def is_typing(self):
        return self._typing.is_set()

    def buffer_log(self, formatted_line):
        self._buffer.put(formatted_line)

    def flush(self):
        with self._lock:
            while not self._buffer.empty():
                try:
                    line = self._buffer.get_nowait()
                    print(line, flush=True)
                except queue.Empty:
                    break

    def stop(self):
        self._running = False

    def _input_loop(self):
        if sys.platform == "win32":
            self._win_loop()
        else:
            self._unix_loop()

    def _win_loop(self):
        import msvcrt
        buf = ""
        while self._running:
            try:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()

                    # Ctrl+C
                    if ch == "\x03":
                        os.kill(os.getpid(), signal.SIGINT)
                        break

                    # Enter
                    if ch in ("\r", "\n"):
                        self._typing.clear()
                        # Hapus baris user dari terminal
                        sys.stdout.write("\r" + " " * (len(buf) + 2) + "\r")
                        sys.stdout.flush()
                        if buf.strip():
                            self._handle_command(buf.strip())
                        self.flush()
                        buf = ""
                        continue

                    # Backspace
                    if ch == "\x08":
                        if buf:
                            buf = buf[:-1]
                            sys.stdout.write("\b \b")
                            sys.stdout.flush()
                        if not buf:
                            self._typing.clear()
                            self.flush()
                        continue

                    # Karakter biasa — mulai typing mode
                    if not self._typing.is_set():
                        self._typing.set()
                    buf += ch
                    sys.stdout.write(ch)
                    sys.stdout.flush()
                else:
                    time.sleep(0.05)
            except Exception:
                time.sleep(0.1)

    def _unix_loop(self):
        try:
            import select
            import termios
            import tty
        except ImportError:
            # Jika termios tidak tersedia (bukan TTY / GitHub Actions), fallback ke input()
            self._fallback_loop()
            return

        try:
            old_settings = termios.tcgetattr(sys.stdin)
        except termios.error:
            self._fallback_loop()
            return

        try:
            tty.setcbreak(sys.stdin.fileno())
            buf = ""
            while self._running:
                if select.select([sys.stdin], [], [], 0.05)[0]:
                    ch = sys.stdin.read(1)

                    if ch == "\x03":
                        os.kill(os.getpid(), signal.SIGINT)
                        break

                    if ch in ("\r", "\n"):
                        self._typing.clear()
                        sys.stdout.write("\r" + " " * (len(buf) + 2) + "\r")
                        sys.stdout.flush()
                        if buf.strip():
                            self._handle_command(buf.strip())
                        self.flush()
                        buf = ""
                        continue

                    if ch in ("\x7f", "\x08"):
                        if buf:
                            buf = buf[:-1]
                            sys.stdout.write("\b \b")
                            sys.stdout.flush()
                        if not buf:
                            self._typing.clear()
                            self.flush()
                        continue

                    if not self._typing.is_set():
                        self._typing.set()
                    buf += ch
                    sys.stdout.write(ch)
                    sys.stdout.flush()
        except Exception:
            pass
        finally:
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
            except Exception:
                pass

    def _fallback_loop(self):
        """Fallback untuk non-TTY (GitHub Actions, pipe, dll)"""
        while self._running:
            try:
                line = input()
                if line.strip():
                    self._handle_command(line.strip())
            except EOFError:
                break
            except Exception:
                time.sleep(0.5)

    def _handle_command(self, cmd):
        """Proses perintah user dan tampilkan sebagai [CMD] log"""
        log(f"[CMD] {cmd}")

        if cmd == "/trigger-warn":
            trigger_all_tags()
        elif cmd == "/skip-stage":
            self.skip_stage.set()
            log("[SKIP] Stage skip diminta oleh user...")
        elif cmd == "/help":
            show_commands_help()
        else:
            log(f"[WARN] Perintah tidak dikenal: {cmd}. Ketik /help untuk daftar perintah.")


# Global console instance (akan diinisialisasi di __main__ jika stdin = TTY)
CONSOLE = None


def trigger_all_tags():
    """Trigger satu log untuk setiap jenis tag — demo semua warna sekaligus."""
    demo_tags = [
        "[ERROR]", "[FAIL]", "[STOP]", "[CUT-OFF]", "[JSON-ERR]",
        "[WARN]", "[OFFLINE]", "[RETRY]", "[DELAY]", "[WAIT]",
        "[OK]", "[DONE]", "[START]", "[END]", "[ARCHIVE]",
        "[LINK]", "[ITEM]", "[ENV]", "[CLEAN]", "[RUN]",
        "[SKIP]", "[CODEC]", "[UPLOAD-PREP]",
        "[UPLOAD]", "[MERGE]",
        "[FFMPEG]", "[RESTART]",
    ]
    for tag in demo_tags:
        log(f"{tag} Test trigger — demo tag")


def show_commands_help():
    """Tampilkan daftar perintah interaktif yang tersedia."""
    lines = [
        "",
        f"  {INFO_COLOR}/help{RESET}           {TEXT_COLOR}Tampilkan daftar perintah ini{RESET}",
        f"  {WARN_COLOR}/trigger-warn{RESET}   {TEXT_COLOR}Trigger log demo untuk semua tag (tes warna){RESET}",
        f"  {ERR_COLOR}/skip-stage{RESET}     {TEXT_COLOR}Skip tahap yang sedang berjalan (wait/record){RESET}",
        "",
    ]
    for l in lines:
        print(l, flush=True)


# =============================================
# LOG FUNCTION — Verbose Server Log Style
# =============================================
def log(msg):
    """
    Verbose Server Log — true color 24-bit, timestamp [YYYY-MM-DD HH:MM:SS],
    tag berwarna dengan padding fixed-width 15 karakter.
    Jika user sedang mengetik, log di-buffer hingga Enter ditekan.
    """
    if ARGS and ARGS.no_log:
        return

    # Deteksi tag di awal pesan
    matched_tag = None
    tag_color = INFO_COLOR
    for tag, color in TAG_COLORS.items():
        if msg.startswith(tag):
            matched_tag = tag
            tag_color = color
            break

    # Timestamp lengkap WITA
    ts = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=8))
    ).strftime("%Y-%m-%d %H:%M:%S")

    if matched_tag:
        rest = msg[len(matched_tag):].lstrip()
        # Padding: lebar tag mentah (tanpa ANSI) tepat TAG_WIDTH karakter
        padding = " " * max(TAG_WIDTH - len(matched_tag), 1)
        line = (
            f"{TIME_COLOR}[{ts}]{RESET} "
            f"{tag_color}{matched_tag}{RESET}{padding}"
            f"{TEXT_COLOR}{rest}{RESET}"
        )
    else:
        padding = " " * TAG_WIDTH
        line = f"{TIME_COLOR}[{ts}]{RESET} {padding}{TEXT_COLOR}{msg}{RESET}"

    # Jika user sedang mengetik, buffer log
    if CONSOLE and CONSOLE.is_typing():
        CONSOLE.buffer_log(line)
    else:
        print(line, flush=True)


# =============================================
# HELP BANNER
# =============================================
def print_help_banner():
    """Tampilkan banner help dengan semua args yang tersedia"""
    BOLD = "\033[1m"
    DIM  = "\033[2m"

    banner = f"""
{INFO_COLOR}╔══════════════════════════════════════════════════════════════════╗
║                  {BOLD}VOT Radio Denpasar Recorder{RESET}{INFO_COLOR}                     ║
╠══════════════════════════════════════════════════════════════════╣{RESET}
{TEXT_COLOR}  Rekam stream audio dan upload otomatis ke archive.org{RESET}                 
{INFO_COLOR}╚══════════════════════════════════════════════════════════════════╝{RESET}

{BOLD}{INFO_COLOR}📋 Opsi Tersedia:{RESET}
{TIME_COLOR}─────────────────────────────────────────────────────────────────{RESET}

  {WARN_COLOR}-s, --suffix{RESET} {TEXT_COLOR}<TEXT>{RESET}         Suffix di akhir nama file
  {WARN_COLOR}-p, --position{RESET} {TEXT_COLOR}<INT>{RESET}        Posisi untuk delay upload (delay = N * 10s)
  {WARN_COLOR}-o{RESET} {TEXT_COLOR}<FILENAME>{RESET}               Nama output file (tanpa path, tanpa ext)
  {WARN_COLOR}--duration{RESET} {TEXT_COLOR}<SECONDS>{RESET}        Durasi rekaman dalam detik (misal: 10 = 10 detik)

{BOLD}{INFO_COLOR}🌐 Stream & Format:{RESET}
{TIME_COLOR}─────────────────────────────────────────────────────────────────{RESET}

  {WARN_COLOR}--stream-url{RESET} {TEXT_COLOR}<URL>{RESET}          URL stream kustom
  {WARN_COLOR}--stream-file-format{RESET} {TEXT_COLOR}<EXT>{RESET}  Format file stream (mp3, wav, aac, dll)
                               {TIME_COLOR}→ skip ffprobe auto-detection{RESET}

{BOLD}{INFO_COLOR}🔑 Archive.org Credentials:{RESET}
{TIME_COLOR}─────────────────────────────────────────────────────────────────{RESET}

  {WARN_COLOR}--archive-access, --archive-acc{RESET} {TEXT_COLOR}<KEY>{RESET}  Archive.org access key
  {WARN_COLOR}--archive-secret, --archive-sec{RESET} {TEXT_COLOR}<KEY>{RESET}  Archive.org secret key

{BOLD}{INFO_COLOR}🌐 Proxy Settings:{RESET}
{TIME_COLOR}─────────────────────────────────────────────────────────────────{RESET}
  {WARN_COLOR}--proxy{RESET} {TEXT_COLOR}<URL>{RESET}                 Manual HTTP proxy (e.g., http://... atau ip:port)
  {WARN_COLOR}--random-proxy{RESET}                 Cari dan aktifkan proxy gratis otomatis

{BOLD}{INFO_COLOR}⏭️  Skip / Disable:{RESET}
{TIME_COLOR}─────────────────────────────────────────────────────────────────{RESET}

  {WARN_COLOR}--skip-check{RESET}               Lewati pengecekan stream online
  {WARN_COLOR}--no-timer-limit{RESET}           Abaikan batasan waktu (jam 18:30, dll)
  {WARN_COLOR}--no-record{RESET}                Skip perekaman ffmpeg
  {WARN_COLOR}--no-merge-chunks{RESET}          Skip penggabungan chunk rekaman
  {WARN_COLOR}--no-log{RESET}                   Nonaktifkan semua log output
  {WARN_COLOR}--no-save{RESET}                  Tidak menyimpan file rekaman
  {WARN_COLOR}--no-upload{RESET}                Rekam saja tanpa upload ke archive.org

{BOLD}{INFO_COLOR}⌨️  Perintah Interaktif (saat runtime):{RESET}
{TIME_COLOR}─────────────────────────────────────────────────────────────────{RESET}

  {INFO_COLOR}/help{RESET}                      Tampilkan daftar perintah
  {WARN_COLOR}/trigger-warn{RESET}              Trigger log demo untuk semua tag
  {ERR_COLOR}/skip-stage{RESET}                Skip tahap yang sedang berjalan
"""
    print(banner, flush=True)


# =============================================
# EARLY VALIDATION (sebelum argparse)
# =============================================
if "--no-upload" not in sys.argv:
    if not MY_ACCESS_KEY or not MY_SECRET_KEY:
        has_custom_keys = any(a in sys.argv for a in [
            "--archive-access", "--archive-acc", "--archive-secret", "--archive-sec"
        ])
        if not has_custom_keys:
            log("[ERROR] API key belum diatur. Pastikan MY_ACCESS_KEY dan MY_SECRET_KEY sudah ada di GitHub Secrets.")
            log("[ERROR] Atau gunakan --archive-access dan --archive-secret untuk menyediakan key.")
            sys.exit(1)


# =============================================
# WAKTU
# =============================================
def now_wita():
    """Waktu lokal WITA"""
    return datetime.datetime.now(datetime.UTC).astimezone(WITA_TZ)

# =============================================
# PROXY AUTO-FINDER
# =============================================
SELECTED_PROXY = None

# =============================================
# PROXY SOURCE LIST
# Diambil dari GitHub Secrets via environment variable HTTP_PROXY.
# Nilai HTTP_PROXY harus berupa JSON array string, contoh:
#   ["https://url1.txt", "https://url2.txt", ...]
# Semua URL dalam daftar akan digunakan sekaligus (bukan hanya satu).
# =============================================
def _load_proxy_sources():
    raw = os.environ.get("HTTP_PROXY", "").strip()
    if not raw:
        log("[WARN] Environment variable HTTP_PROXY tidak ditemukan atau kosong. "
            "Proxy source list akan kosong.")
        return []
    try:
        sources = json.loads(raw)
        if not isinstance(sources, list):
            log("[WARN] HTTP_PROXY bukan JSON array yang valid. Proxy source list akan kosong.")
            return []
        sources = [s for s in sources if isinstance(s, str) and s.strip()]
        log(f"[ENV] HTTP_PROXY dimuat: {len(sources)} sumber proxy ditemukan.")
        return sources
    except json.JSONDecodeError as e:
        log(f"[WARN] Gagal parse HTTP_PROXY sebagai JSON: {e}. Proxy source list akan kosong.")
        return []

PROXY_SOURCES = _load_proxy_sources()

def get_proxies_dict(proxy_url):
    """Kembalikan dict proxy untuk library requests.
    Menerima format 'ip:port' atau 'http://ip:port'."""
    if not proxy_url:
        return None
    # Pastikan ada prefix http:// untuk requests
    if not proxy_url.startswith("http"):
        proxy_url = f"http://{proxy_url}"
    return {"http": proxy_url, "https": proxy_url}

def find_working_proxy():
    """
    Mencari proxy secara otomatis dari PROXY_SOURCES array secara berurutan.
    Setiap proxy divalidasi dua kali: tes stats endpoint + tes stream ping.
    Berhenti pada proxy pertama yang lulus kedua tes.
    """
    target_stats = "http://i.klikhost.com:8502/stats?json=1"
    target_stream = "http://i.klikhost.com:8502/stream"

    log("[PROXY-SEARCH] Mengunduh daftar proxy terbaru dari semua sumber...")
    proxies_raw = []

    if not PROXY_SOURCES:
        log("[PROXY-FAIL] PROXY_SOURCES kosong. Pastikan HTTP_PROXY sudah diset di GitHub Secrets.")
        return None

    # Iterasi SEMUA PROXY_SOURCES dan gabungkan seluruh hasilnya
    for idx, source_url in enumerate(PROXY_SOURCES):
        try:
            log(f"[PROXY-SEARCH] Mengunduh sumber [{idx}]: {source_url}")
            req = requests.get(source_url, timeout=10, headers=get_headers())
            if req.status_code == 200:
                lines = req.text.strip().split('\n')
                raw_list = [p.strip() for p in lines if p.strip()]
                if raw_list:
                    log(f"[PROXY-SEARCH] Sumber [{idx}]: {len(raw_list)} proxy ditemukan, digabung ke pool.")
                    proxies_raw.extend(raw_list)
                else:
                    log(f"[WARN] Sumber [{idx}] berhasil diunduh tapi daftar kosong.")
            else:
                log(f"[WARN] Sumber [{idx}] merespons HTTP {req.status_code}, dilewati.")
        except Exception as e:
            log(f"[WARN] Sumber [{idx}] gagal: {e}, dilewati.")

    log(f"[PROXY-SEARCH] Total proxy mentah dari semua sumber: {len(proxies_raw)} entri.")

    if not proxies_raw:
        log("[PROXY-FAIL] Semua sumber proxy gagal diambil.")
        return None

    # -----------------------------------------------
    # NORMALISASI FORMAT PROXY
    # Handle dua format yang mungkin ada:
    #   "103.48.71.6:83"            → simpan apa adanya
    #   "http://49.156.44.115:8080" → strip prefix http://
    # Output akhir selalu berformat: "ip:port"
    # -----------------------------------------------
    def normalize_proxy(raw):
        p = raw.strip()
        for prefix in ("https://", "http://"):
            if p.lower().startswith(prefix):
                p = p[len(prefix):]
                break
        if ':' not in p:
            return None
        parts = p.rsplit(':', 1)
        if len(parts) != 2:
            return None
        host, port_str = parts
        if not port_str.isdigit():
            return None
        port = int(port_str)
        if not (1 <= port <= 65535):
            return None
        if not host:
            return None
        return f"{host}:{port}"

    proxies_clean = []
    for raw in proxies_raw:
        normalized = normalize_proxy(raw)
        if normalized:
            proxies_clean.append(normalized)

    if not proxies_clean:
        log("[PROXY-FAIL] Tidak ada proxy valid setelah normalisasi.")
        return None

    log(f"[PROXY-SEARCH] {len(proxies_clean)} proxy valid. Memulai pengecekan paralel (Batch: 50)...")

    found_proxy = None
    stop_event = threading.Event()

    def check_proxy(px):
        if stop_event.is_set():
            return None

        # px sudah dalam format "ip:port" (bersih, tanpa prefix)
        px_url = f"http://{px}"
        proxies_dict = {"http": px_url, "https": px_url}

        # ── TES 1: Endpoint stats (cek konektivitas dasar) ──
        try:
            res = requests.get(
                target_stats,
                proxies=proxies_dict,
                timeout=5,
                verify=False
            )
            if res.status_code not in (200, 401):
                if not stop_event.is_set():
                    log(f"[PROXY-FAIL] [GAGAL] Proxy: {px} | Stats HTTP {res.status_code}")
                return None
        except requests.exceptions.Timeout:
            if not stop_event.is_set():
                log(f"[PROXY-FAIL] [GAGAL] Proxy: {px} | Timeout saat tes stats")
            return None
        except requests.exceptions.RequestException as e:
            if not stop_event.is_set():
                log(f"[PROXY-FAIL] [GAGAL] Proxy: {px} | {type(e).__name__} saat tes stats")
            return None

        if stop_event.is_set():
            return None

        # ── TES 2: Ping stream URL (mirip apa yang akan dilakukan ffmpeg) ──
        try:
            ping = requests.get(
                target_stream,
                proxies=proxies_dict,
                stream=True,
                timeout=6,
                verify=False
            )
            ping.close()

            if ping.status_code in (200, 206, 401):
                if not stop_event.is_set():
                    stop_event.set()
                    log(f"[PROXY-OK] [BERHASIL] Proxy: {px} | Stats OK, Stream: HTTP {ping.status_code}")
                    return px
            else:
                if not stop_event.is_set():
                    log(f"[PROXY-FAIL] [GAGAL] Proxy: {px} | Stream HTTP {ping.status_code} (bukan 200/401)")
        except requests.exceptions.Timeout:
            if not stop_event.is_set():
                log(f"[PROXY-FAIL] [GAGAL] Proxy: {px} | Timeout saat tes stream ping")
        except requests.exceptions.RequestException as e:
            if not stop_event.is_set():
                log(f"[PROXY-FAIL] [GAGAL] Proxy: {px} | {type(e).__name__} saat tes stream ping")

        return None

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=50)
    futures = [executor.submit(check_proxy, p) for p in proxies_clean]

    for future in concurrent.futures.as_completed(futures):
        res = future.result()
        if res:
            found_proxy = res
            stop_event.set()
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                executor.shutdown(wait=False)
            break

    if found_proxy:
        log(f"[PROXY-OK] Selesai. Proxy terpilih: {found_proxy}")
    else:
        log("[PROXY-FAIL] Tidak ada proxy yang merespons 200 OK.")
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except:
            executor.shutdown(wait=False)

    return found_proxy


# =============================================
# GIST AUTO-PUBLISH (non-blocking, parallel)
# =============================================
GIST_TOKEN    = os.environ.get("GIST_TOKEN")
GIST_ID       = "0d0b5d5e6f1184cadd7da69c74b753c9"
GIST_FILENAME = "voiceoftrisma-server-status.json"
GIST_STATS_URL = "https://i.klikhost.com:8502/stats?json=1"

def _gist_worker():
    """
    Worker yang berjalan di daemon thread terpisah.
    Mengambil data dari stats endpoint dan mendorongnya ke GitHub Gist.
    Tidak pernah raise exception ke caller — semua error di-handle internal.
    """
    if not GIST_TOKEN:
        log("[WARN] GIST_TOKEN tidak tersedia — auto-publish Gist dilewati.")
        return

    log("[PING] Gist worker: mengambil data stats untuk auto-publish...")

    try:
        resp = requests.get(
            GIST_STATS_URL,
            headers=get_headers(),
            timeout=10,
            verify=False,
            proxies=get_proxies_dict(SELECTED_PROXY)
        )
        resp.raise_for_status()
        radio_data = resp.json()
        log("[PING] Gist worker: data stats berhasil diambil.")
    except Exception as e:
        log(f"[WARN] Gist worker: gagal ambil stats — {e}. Menggunakan fallback offline.")
        radio_data = {
            "status": "offline",
            "message": "Server radio tidak dapat dijangkau saat ini.",
            "error_detail": str(e)
        }

    try:
        api_url = f"https://api.github.com/gists/{GIST_ID}"
        gist_headers = {
            "Authorization": f"token {GIST_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        }
        payload = {
            "files": {
                GIST_FILENAME: {
                    "content": json.dumps(radio_data, indent=2)
                }
            }
        }
        patch_res = requests.patch(api_url, headers=gist_headers, json=payload, timeout=10)

        if patch_res.status_code == 200:
            gist_raw_url = f"https://gist.githubusercontent.com/Sparkplugx1904/{GIST_ID}/raw/{GIST_FILENAME}"
            log(f"[OK] Gist worker: GitHub Gist berhasil diperbarui → {gist_raw_url}")
        else:
            log(f"[WARN] Gist worker: GitHub menolak request. HTTP {patch_res.status_code}")
    except Exception as e:
        log(f"[WARN] Gist worker: koneksi ke GitHub gagal — {e}")


def push_gist_background():
    """
    Luncurkan _gist_worker() di daemon thread terpisah.
    Return SEGERA tanpa menunggu — tidak memblokir caller.
    """
    t = threading.Thread(target=_gist_worker, daemon=True, name="gist-publisher")
    t.start()
    log("[PING] Gist publisher diluncurkan di background (non-blocking).")
    return t


# =============================================
# WAIT FOR STREAM
# =============================================
def wait_for_stream(url):
    """
    Strategi polling bertahap berbasis offset sejak jam :00.
    Tahap 1:  0– 2 menit → setiap   3 detik
    Tahap 2:  2– 3 menit → setiap   5 detik
    Tahap 3:  3– 5 menit → setiap  15 detik
    Tahap 4:  5–10 menit → setiap  30 detik
    Tahap 5: 10–30 menit → setiap  60 detik
    Tahap 6: 30–50 menit → setiap  90 detik
    Tahap 7: 50–60 menit → setiap 120 detik

    Saat jam :00, offset otomatis kembali ke 0 sehingga
    polling kembali ke Tahap 1 yang cepat.

    Metode deteksi: ping langsung ke stream URL (bukan stats endpoint).
    Mirip fetch() no-cors — koneksi dibuka, header diterima, langsung ditutup.
    Kalau server merespons tanpa error = stream hidup, langsung mulai rekam.
    """
    STAGES = [
        (   0,  120,   3),
        ( 120,  180,   5),
        ( 180,  300,  15),
        ( 300,  600,  30),
        ( 600, 1800,  60),
        (1800, 3000,  90),
        (3000, 3600, 120),
    ]

    def get_interval(elapsed_seconds):
        for start, end, interval in STAGES:
            if start <= elapsed_seconds < end:
                return interval
        return 120

    def seconds_since_last_hour():
        now = now_wita()
        return now.minute * 60 + now.second

    stage_labels = {
        3: "0–2 mnt", 5: "2–3 mnt", 15: "3–5 mnt",
        30: "5–10 mnt", 60: "10–30 mnt", 90: "30–50 mnt", 120: "50–60 mnt",
    }
    last_interval = None
    last_err = None
    last_seen_hour = now_wita().hour

    log(f"[WAIT] Menunggu siaran — ping langsung ke stream: {url}")

    while True:
        # /skip-stage: skip waiting
        if CONSOLE and CONSOLE.skip_stage.is_set():
            CONSOLE.skip_stage.clear()
            log("[SKIP] Wait stage dilewati oleh user.")
            return

        current_hour = now_wita().hour
        elapsed = seconds_since_last_hour()

        # Deteksi pergantian jam (:00) — log reset, offset otomatis kembali ke 0
        if current_hour != last_seen_hour:
            log(f"[WAIT] Reset polling — jam :{current_hour:02d}:00 "
                f"(kembali ke Tahap 1, interval 3s)")
            last_interval = None
            last_seen_hour = current_hour

        interval = get_interval(elapsed)
        mm, ss = divmod(elapsed, 60)

        # Hanya tampilkan saat transisi tahap
        if interval != last_interval:
            log(f"[WAIT] Tahap berubah → +{mm}m {ss:02d}s — interval ping: {interval}s ({stage_labels.get(interval, '?')})")
            last_interval = interval

        try:
            # Ping ke stream URL langsung — stream=True agar tidak membaca body audio
            # Cukup tunggu response headers saja, lalu tutup koneksi
            response = requests.get(
                url,
                stream=True,
                timeout=5,
                verify=False,
                headers=get_headers(),
                proxies=get_proxies_dict(SELECTED_PROXY)
            )
            response.close()

            if response.status_code in (200, 206):
                # Stream aktif — luncurkan Gist publisher di background, lalu langsung rekam
                log(f"[OK] Stream merespons (HTTP {response.status_code}) — memulai perekaman...")
                push_gist_background()
                return
            elif response.status_code == 401:
                # Server terjangkau tapi siaran belum on-air
                # 401 berarti IP kita tidak diblokir — polling terus sampai siaran mulai
                log(f"[OFFLINE] Server terjangkau (HTTP 401) — siaran belum aktif, menunggu...")
                last_err = None  # reset agar error berikutnya tetap terlog
            else:
                if last_err != f"http_{response.status_code}":
                    log(f"[OFFLINE] Server merespons HTTP {response.status_code} — siaran belum aktif")
                    last_err = f"http_{response.status_code}"

        except requests.exceptions.RequestException:
            log("[ERROR] Tidak dapat menjangkau server — kemungkinan IP diblokir atau server down.")
            last_err = None

        time.sleep(interval)


# =============================================
# HELPER FILENAME / CHUNK
# =============================================
def make_base_no_ext(date_str, suffix):
    return f"recordings/VOT-Denpasar_{date_str}{('-' + suffix) if suffix else ''}"


def get_next_chunk_filename(base_no_ext, ext):
    dirpath = os.path.dirname(base_no_ext) or '.'
    base_name = os.path.basename(base_no_ext)
    pattern = re.compile(r'^' + re.escape(base_name) + r'(?:_(\d+))?\.' + re.escape(ext) + r'$')

    max_index = -1
    found_any = False

    for f in os.listdir(dirpath):
        m = pattern.match(f)
        if m:
            found_any = True
            if m.group(1):
                try:
                    idx = int(m.group(1))
                    if idx > max_index:
                        max_index = idx
                except:
                    pass
            else:
                if max_index < 0:
                    max_index = 0

    if not found_any:
        return f"{base_no_ext}.{ext}"
    else:
        if max_index < 0:
            next_idx = 1
        else:
            next_idx = max_index + 1
        return f"{base_no_ext}_{next_idx}.{ext}"


def list_chunks_ordered(base_no_ext, ext):
    dirpath = os.path.dirname(base_no_ext) or '.'
    base_name = os.path.basename(base_no_ext)
    pattern = re.compile(r'^' + re.escape(base_name) + r'(?:_(\d+))?\.' + re.escape(ext) + r'$')

    files = []
    for f in os.listdir(dirpath):
        if pattern.match(f):
            full = os.path.join(dirpath, f)
            try:
                mtime = os.path.getmtime(full)
            except:
                mtime = 0
            files.append((mtime, full))
    files.sort(key=lambda x: x[0])
    return [f for _, f in files]


def merge_chunks_to_base(base_no_ext, ext):
    chunks = list_chunks_ordered(base_no_ext, ext)
    if not chunks:
        log("[MERGE] Tidak ada chunk yang bisa digabung.")
        return None

    list_txt = os.path.join("recordings", "concat_list.txt")
    with open(list_txt, "w", encoding="utf-8") as f:
        for c in chunks:
            safe_path = c.replace("'", "'\"'\"'")
            f.write(f"file '{safe_path}'\n")

    temp_output = os.path.join("recordings", "__merged_temp__." + ext)
    final_output = f"{base_no_ext}.{ext}"

    try:
        cmd = [
            "./ffmpeg", "-hide_banner", "-f", "concat", "-safe", "0",
            "-i", list_txt, "-c", "copy", temp_output
        ]
        log("[MERGE] Menggabungkan semua bagian rekaman...")
        subprocess.run(cmd, check=True)

        shutil.move(temp_output, final_output)
        log(f"[MERGE] Berhasil digabung → {final_output}")

        for c in chunks:
            try:
                os.remove(c)
                log(f"[CLEAN] File sementara dihapus: {c}")
            except Exception as e:
                log(f"[WARN] Gagal menghapus file sementara {c}: {e}")

        try:
            os.remove(list_txt)
        except:
            pass

        return final_output

    except subprocess.CalledProcessError as e:
        log(f"[ERROR] Penggabungan rekaman gagal: {e}")
        try:
            if os.path.exists(temp_output):
                os.remove(temp_output)
        except:
            pass
        return None


# =============================================
# CORE RECORDING FLOW
# =============================================
def run_ffmpeg(url, suffix="", position=0, no_upload=False):
    """Rekam stream audio dan upload"""

    # --no-record: skip seluruh proses ffmpeg
    if ARGS and ARGS.no_record:
        log("[SKIP] --no-record aktif. Perekaman ffmpeg dilewati.")
        return

    date_str = now_wita().strftime("%d-%m-%y")
    os.makedirs("recordings", exist_ok=True)

    # Tentukan format/ext file
    if ARGS and ARGS.stream_file_format:
        ext = ARGS.stream_file_format.lower().strip(".")
        log(f"[CODEC] Format file stream (manual): {ext}")
    else:
        try:
            # Bangun command ffprobe dengan -http_proxy native jika proxy tersedia
            ffprobe_cmd = [
                "./ffprobe", "-v", "error",
                "-timeout", "10000000",
            ]
            if SELECTED_PROXY:
                proxy_for_ffmpeg = SELECTED_PROXY if SELECTED_PROXY.startswith("http://") else f"http://{SELECTED_PROXY}"
                ffprobe_cmd.extend(["-http_proxy", proxy_for_ffmpeg])
                log(f"[PROXY-OK] FFprobe menggunakan HTTP proxy native: {proxy_for_ffmpeg}")
            ffprobe_cmd.extend([
                "-select_streams", "a:0",
                "-show_entries", "stream=codec_name",
                "-of", "default=nokey=1:noprint_wrappers=1", url
            ])
            codec = subprocess.check_output(
                ffprobe_cmd, timeout=15
            ).decode().strip()
            log(f"[CODEC] Format audio: {codec}")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            codec = "mp3"
            log("[CODEC] Tidak dapat mendeteksi format, menggunakan mp3 sebagai default.")

        ext_map = {"aac": "aac", "mp3": "mp3", "opus": "opus", "vorbis": "ogg"}
        ext = ext_map.get(codec, "bin")

    # Tentukan nama file output
    if ARGS and ARGS.output_name:
        base_no_ext = f"recordings/{ARGS.output_name}"
        filename = get_next_chunk_filename(base_no_ext, ext)
    else:
        base_no_ext = make_base_no_ext(date_str, suffix)
        filename = get_next_chunk_filename(base_no_ext, ext)

    # Bangun command ffmpeg dengan -http_proxy native jika proxy tersedia
    cmd = [
        "./ffmpeg", "-y", "-hide_banner",
        "-reconnect", "1", "-reconnect_at_eof", "1", "-reconnect_streamed", "1",
        "-reconnect_delay_max", "0", "-reconnect_on_network_error", "1",
        "-reconnect_on_http_error", "4xx,5xx",
        "-timeout", "5000000",
    ]
    if SELECTED_PROXY:
        proxy_for_ffmpeg = SELECTED_PROXY if SELECTED_PROXY.startswith("http://") else f"http://{SELECTED_PROXY}"
        cmd.extend(["-http_proxy", proxy_for_ffmpeg])
        log(f"[PROXY-OK] FFmpeg menggunakan HTTP proxy native: {proxy_for_ffmpeg}")
    cmd.extend([
        "-i", url,
        "-c", "copy",
        "-metadata", f"title=VOT Denpasar {date_str}",
        "-metadata", "artist=VOT Radio Denpasar",
        "-metadata", f"date={date_str}",
    ])

    # --duration: batasi durasi rekaman
    if ARGS and ARGS.duration:
        cmd.extend(["-t", str(ARGS.duration)])
        log(f"[RUN] Durasi rekaman dibatasi: {ARGS.duration} detik")

    cmd.append(filename)

    log(f"[RUN] Rekaman dimulai → {filename}")
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)

    def log_ffmpeg(proc):
        for line in proc.stderr:
            log(f"[FFMPEG] {line.strip()}")

    threading.Thread(target=log_ffmpeg, args=(process,), daemon=True).start()

    no_timer = ARGS and ARGS.no_timer_limit
    cutoff_reached = False

    while True:
        # /skip-stage: hentikan rekaman
        if CONSOLE and CONSOLE.skip_stage.is_set():
            CONSOLE.skip_stage.clear()
            log("[SKIP] Rekaman dihentikan oleh user (/skip-stage).")
            try:
                process.send_signal(signal.SIGINT)
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
            break

        now = now_wita()

        # Cek cutoff waktu hanya jika --no-timer-limit TIDAK aktif
        if not no_timer:
            if now.hour == 18 and now.minute >= 30:
                cutoff_reached = True
                log("[CUT-OFF] Siaran berakhir pukul 18:30 WITA. Menghentikan perekaman...")
                try:
                    process.send_signal(signal.SIGINT)
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                break

        if process.poll() is not None:
            if not no_timer:
                now_check = now_wita()
                if now_check.hour >= 18:
                    cutoff_reached = True
                    log("[FAIL] Perekaman berhenti — siaran sudah selesai.")
                else:
                    log("[FAIL] Perekaman berhenti tiba-tiba. Akan segera dimulai ulang...")
            else:
                log("[DONE] Perekaman selesai.")
            break

        time.sleep(1)

    log(f"[DONE] Proses ffmpeg berhenti: {filename}")

    # --no-save: hapus file yang baru direkam
    if ARGS and ARGS.no_save:
        log(f"[SKIP] --no-save aktif. Menghapus file rekaman: {filename}")
        try:
            os.remove(filename)
        except Exception as e:
            log(f"[WARN] Gagal menghapus file: {e}")
        return

    # Merge Logic (skip jika --no-merge-chunks aktif)
    no_merge = ARGS and ARGS.no_merge_chunks
    filename_to_upload = filename

    if cutoff_reached and not no_merge:
        log("[MERGE] Melakukan merge semua chunk menjadi file base final...")
        merged = merge_chunks_to_base(base_no_ext, ext)
        if merged:
            filename_to_upload = merged
        else:
            log("[WARN] Merge gagal, akan upload chunk terakhir sebagai fallback.")
    elif cutoff_reached and no_merge:
        log("[SKIP] --no-merge-chunks aktif. Merge chunk dilewati.")

    log(f"[UPLOAD-PREP] Siap upload: {filename_to_upload}")

    if no_upload:
        log(f"[SKIP] Mode tanpa upload aktif. File tersimpan di {filename_to_upload}")
        return

    if position > 0:
        delay = position * 10
        log(f"[DELAY] Menunggu {delay} detik sebelum upload...")
        time.sleep(delay)

    archive_url, item_id = upload_to_archive(filename_to_upload)

    if archive_url and item_id:
        log(f"[ARCHIVE] File tersedia di {archive_url}")
        write_env_variables(archive_url, item_id)
    else:
        write_env_variables("None", "None")


# =============================================
# UPLOAD
# =============================================
def upload_to_archive(file_path, retries=5):
    """Upload file ke archive.org"""

    access_key = MY_ACCESS_KEY
    secret_key = MY_SECRET_KEY
    if ARGS:
        if ARGS.archive_access:
            access_key = ARGS.archive_access
        if ARGS.archive_secret:
            secret_key = ARGS.archive_secret

    log(f"[UPLOAD] Mulai upload {file_path} ke archive.org...")
    item_identifier = f"vot-denpasar-{now_wita().strftime('%Y%m%d-%H%M%S')}"
    filename = os.path.basename(file_path)

    for attempt in range(1, retries + 1):
        try:
            upload(
                item_identifier,
                files=[file_path],
                metadata={
                    'mediatype': 'audio',
                    'title': filename,
                    'creator': 'VOT Radio Denpasar'
                },
                access_key=access_key,
                secret_key=secret_key,
                verbose=True
            )

            details_url = f"https://archive.org/details/{item_identifier}"
            download_url = f"https://archive.org/download/{item_identifier}/{filename}"

            log(f"[DONE] Upload berhasil: {details_url}")
            log(f"[LINK] URL langsung: {download_url}")
            log(f"[ITEM] ID: {item_identifier}")

            return download_url, item_identifier

        except Exception as e:
            log(f"[WARN] Upload gagal percobaan {attempt}: {e}")
            if attempt < retries:
                log("[RETRY] Menunggu 10 detik sebelum mencoba lagi...")
                time.sleep(10)
            else:
                log("[ERROR] Semua percobaan upload gagal.")
                return None, None


def write_env_variables(url, item_id):
    """Kirim ARCHIVE_URL dan ITEM_ID langsung ke environment GitHub"""
    try:
        if "GITHUB_ENV" in os.environ:
            with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as env_file:
                env_file.write(f"ARCHIVE_URL={url}\n")
                env_file.write(f"ITEM_ID={item_id}\n")
                env_file.flush()
                log("[ENV] ARCHIVE_URL dan ITEM_ID dikirim ke environment GitHub.")
        else:
            log("[WARN] GITHUB_ENV tidak tersedia (mungkin bukan di workflow).")
    except Exception as e:
        log(f"[ERROR] Gagal menulis environment: {e}")


# =============================================
# MAIN RECORDING
# =============================================
def main_recording():
    global ARGS, MY_ACCESS_KEY, MY_SECRET_KEY

    parser = argparse.ArgumentParser(
        description="VOT Radio Denpasar — Stream Recorder & Uploader",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("-s", "--suffix", type=str, default="", help="Suffix di akhir nama file")
    parser.add_argument("-p", "--position", type=int, default=0, help="Posisi untuk delay upload (delay = position * 10 detik)")
    parser.add_argument("--skip-check", action="store_true", help="Lewati pengecekan stream, langsung mulai rekam")
    parser.add_argument("--no-upload", action="store_true", help="Rekam saja tanpa upload ke archive.org")
    parser.add_argument("--hide-banner", action="store_true", help="Sembunyikan banner help saat startup")

    parser.add_argument("--no-timer-limit", action="store_true", help="Abaikan batasan waktu (jam 18:30 cutoff, dll)")
    parser.add_argument("--no-record", action="store_true", help="Skip perekaman ffmpeg")
    parser.add_argument("--no-merge-chunks", action="store_true", help="Skip penggabungan chunk (jika ada multiple record)")
    parser.add_argument("--no-log", action="store_true", help="Nonaktifkan semua log output")
    parser.add_argument("--no-save", action="store_true", help="Tidak menyimpan file rekaman (hapus setelah selesai)")

    parser.add_argument("--archive-access", "--archive-acc", type=str, default=None, dest="archive_access",
                        help="Archive.org access key (override env var)")
    parser.add_argument("--archive-secret", "--archive-sec", type=str, default=None, dest="archive_secret",
                        help="Archive.org secret key (override env var)")

    parser.add_argument("--stream-url", type=str, default=None, help="URL stream kustom")
    parser.add_argument("-o", type=str, default=None, dest="output_name",
                        help="Nama output file (tanpa path/ext, gunakan bersama --stream-url)")
    parser.add_argument("--stream-file-format", type=str, default=None,
                        help="Format file stream (mp3, wav, aac, dll) — skip ffprobe")
    parser.add_argument("--duration", type=int, default=None,
                        help="Durasi rekaman dalam detik (misal: 10 = 10 detik)")
                        
    parser.add_argument("--proxy", type=str, default=None,
                        help="Gunakan manual HTTP proxy (misal: http://192.168.1.1:8080 atau 192.168.1.1:8080)")
    parser.add_argument("--random-proxy", action="store_true",
                        help="Jalankan proxy finder otomatis dan gunakan proxy gratis yang bekerja")

    ARGS = parser.parse_args()

    if ARGS.archive_access:
        MY_ACCESS_KEY = ARGS.archive_access
    if ARGS.archive_secret:
        MY_SECRET_KEY = ARGS.archive_secret

    stream_url = ARGS.stream_url if ARGS.stream_url else "http://i.klikhost.com:8502/stream"

    # Evaluasi Proxy
    global SELECTED_PROXY
    if ARGS.random_proxy:
        log("[PROXY-SEARCH] Opsi --random-proxy diaktifkan. Mencari proxy...")
        found = find_working_proxy()
        if found:
            SELECTED_PROXY = found
        else:
            log("[WARN] auto-proxy gagal menemukan (atau koneksi habis), fallback ke koneksi langsung.")
            SELECTED_PROXY = None
    elif ARGS.proxy:
        raw_manual = ARGS.proxy.strip()
        for prefix in ("https://", "http://"):
            if raw_manual.lower().startswith(prefix):
                raw_manual = raw_manual[len(prefix):]
                break
        SELECTED_PROXY = raw_manual
        log(f"[PROXY-OK] Menggunakan manual proxy: {SELECTED_PROXY}")
    else:
        SELECTED_PROXY = None

    if ARGS.skip_check:
        log("[SKIP] Pengecekan stream dilewati, langsung mulai rekam...")
    else:
        wait_for_stream(stream_url)

    run_ffmpeg(stream_url, ARGS.suffix, ARGS.position, no_upload=ARGS.no_upload)
    log("[DONE] Semua tugas selesai.")
    return True


# =============================================
# ENTRY POINT
# =============================================
if __name__ == "__main__":
    # Pre-parse untuk flag yang dibutuhkan sebelum main loop
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--no-timer-limit", action="store_true", default=False)
    pre_parser.add_argument("--no-log", action="store_true", default=False)
    pre_parser.add_argument("--hide-banner", action="store_true", default=False)
    pre_args, _ = pre_parser.parse_known_args()

    if not pre_args.hide_banner:
        print_help_banner()

    # Set ARGS sementara agar log() bisa cek --no-log sebelum main_recording()
    class _TempArgs:
        no_log = pre_args.no_log
        no_timer_limit = pre_args.no_timer_limit
        no_record = False
        stream_file_format = None
        output_name = None
        duration = None
        no_save = False
        no_merge_chunks = False
        archive_access = None
        archive_secret = None
    ARGS = _TempArgs()

    # Mulai interactive console jika stdin adalah TTY
    if sys.stdin.isatty():
        CONSOLE = InteractiveConsole()
        CONSOLE.start()

    log("[START] Memulai program recording dengan restart otomatis...")

    while True:
        now = now_wita()

        if not pre_args.no_timer_limit:
            if now.hour >= 18:
                log(f"[STOP] Sudah jam {now.strftime('%H:%M')} WITA, hentikan program.")
                break

        try:
            main_recording()
        except Exception as e:
            log(f"[ERROR] Terjadi error: {e}")

        now = now_wita()
        if not pre_args.no_timer_limit:
            if now.hour >= 18:
                log(f"[STOP] Setelah recording selesai, sudah jam {now.strftime('%H:%M')} WITA, hentikan program.")
                break
            else:
                log("[RESTART] Restarting recording loop...")
                continue
        else:
            if ARGS and hasattr(ARGS, 'duration') and ARGS.duration:
                log("[DONE] Rekaman dengan durasi terbatas selesai.")
                break
            log("[RESTART] Restarting recording loop (no timer limit)...")
            continue

    log("[END] Program selesai.")