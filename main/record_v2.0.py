import requests
from collections import deque
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
import itertools
import concurrent.futures
from dotenv import load_dotenv
load_dotenv()

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
STREAM_URL = os.environ.get("STREAM_URL")

# --- Global state ---
ARGS = None
TAG_WIDTH = 15

# =============================================
# True Color ANSI (24-bit RGB)
# Dinonaktifkan jika stdout bukan TTY (misal GitHub Actions)
# =============================================
# isatty() = False di GitHub Actions (stdout adalah pipe), tapi GitHub Actions
# tetap support ANSI color. Aktifkan juga jika GITHUB_ACTIONS atau FORCE_COLOR di-set.
_USE_COLOR = (
    sys.stdout.isatty()
    or bool(os.environ.get("GITHUB_ACTIONS"))
    or bool(os.environ.get("FORCE_COLOR"))
)

if _USE_COLOR:
    TIME_COLOR = "\033[38;2;139;148;158m"   # #8b949e — abu-abu kebiruan (timestamp)
    INFO_COLOR = "\033[38;2;88;166;255m"    # #58a6ff — biru (info/sukses)
    WARN_COLOR = "\033[38;2;210;153;34m"    # #d29922 — kuning/oranye (peringatan)
    ERR_COLOR  = "\033[38;2;248;81;73m"     # #f85149 — merah (error)
    UP_COLOR   = "\033[38;2;179;137;245m"   # #b389f5 — ungu (upload/merge)
    TEXT_COLOR = "\033[38;2;201;209;217m"   # #c9d1d9 — abu-abu terang (teks utama)
    RESET      = "\033[0m"
    BOLD       = "\033[1m"
else:
    TIME_COLOR = INFO_COLOR = WARN_COLOR = ERR_COLOR = UP_COLOR = TEXT_COLOR = RESET = BOLD = ""

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

    # Teal — warm proxy pool management
    "[POOL]"        : "\033[38;2;56;189;166m",   # #38bda6
    "[STATS]"       : "\033[38;2;56;189;166m",
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
                    self._last_typing_time = time.time()
                    buf += ch
                    sys.stdout.write(ch)
                    sys.stdout.flush()
                else:
                    # Auto-clear typing mode jika sudah 10 detik tidak ada input
                    if self._typing.is_set() and (time.time() - self._last_typing_time > 10):
                        self._typing.clear()
                        # Jika ada buffer, flush agar user tidak bingung
                        if not self._buffer.empty():
                            sys.stdout.write("\r" + " " * (len(buf) + 2) + "\r")
                            sys.stdout.flush()
                            self.flush()
                            # Tampilkan kembali buffer ketikan (jika ada) agar user bisa lanjut
                            if buf:
                                sys.stdout.write(buf)
                                sys.stdout.flush()
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
    DIM  = "\033[2m" if _USE_COLOR else ""

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
  {WARN_COLOR}--random-proxy{RESET} {TEXT_COLOR}<MODE>{RESET}        Cari proxy otomatis (opsi: stats, stream, all)

{BOLD}{INFO_COLOR}⏭️  Skip / Disable:{RESET}
{TIME_COLOR}─────────────────────────────────────────────────────────────────{RESET}

  {WARN_COLOR}--skip-check{RESET}               Lewati pengecekan stream online
  {WARN_COLOR}--no-timer-limit{RESET}           Abaikan batasan waktu (jam 18:30, dll)
  {WARN_COLOR}--no-record{RESET}                Skip perekaman ffmpeg
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

# Pool lengkap semua proxy yang sudah dinormalisasi — diisi saat find_working_proxy() pertama kali dipanggil.
# wait_for_stream() akan iterasi dari sini tanpa perlu re-download.
PROXY_POOL       = []   # list of "ip:port"
PROXY_POOL_INDEX = 0    # index proxy yang sedang aktif

# =============================================
# PROXY WARM POOL — 16 proxy siap pakai
# Diisi secara paralel di latar belakang saat menunggu siaran.
# Saat stream ON-AIR → pakai ordo-1. Disconnect → ordo-2, dst.
# Jika pool < 16, warmer thread otomatis refill dari PROXY_POOL.
# =============================================
PROXY_WARM_POOL    = deque()              # deque of validated "ip:port" — popleft() = O(1)
PROXY_WARM_LOCK    = threading.Lock()
PROXY_WARM_TARGET  = 16
_proxy_warmer_stop = threading.Event()

# =============================================
# STATS — Pencatat setiap ping ke stats.json
# Ditulis secara async via queue agar tidak
# mem-bottleneck loop ping utama.
# =============================================
STATS_FILE         = os.path.join("recordings", "stats.json")
_stats_queue: "queue.Queue[dict]" = queue.Queue()
_stats_writer_stop = threading.Event()


def search_working_proxy_in_pool(start_idx):
    """
    Mencari proxy aktif (200 OK & mengandung 'streamstatus') secara paralel
    (concurrent) dari PROXY_POOL mulai dari index start_idx.
    Fungsi ini menghindari spam log saat mencari proxy pengganti.
    """
    global PROXY_POOL
    if not PROXY_POOL or start_idx >= len(PROXY_POOL):
        return None

    target_stats = "https://i.klikhost.com:8502/stats?json=1"
    found_proxy = None
    stop_event = threading.Event()

    def check_proxy(px_info):
        idx, px = px_info
        if stop_event.is_set():
            return None

        px_url = f"http://{px}"
        proxies_dict = {"http": px_url, "https": px_url}
        try:
            res = requests.get(
                target_stats,
                proxies=proxies_dict,
                timeout=5,  # Timeout singkat agar cepat berlalu jika mati
                verify=False
            )
            if res.status_code == 200 and "streamstatus" in res.text:
                if not stop_event.is_set():
                    stop_event.set()
                    return (idx, px)
        except:
            pass  # Abaikan semua error secara diam-diam untuk mencegah log spam
        return None

    # Gunakan sistem chunk (500 proxy per batch) agar tidak membebani RAM
    chunk_size = 500
    for chunk_start in range(start_idx, len(PROXY_POOL), chunk_size):
        chunk_end = min(chunk_start + chunk_size, len(PROXY_POOL))
        current_chunk = list(enumerate(PROXY_POOL[chunk_start:chunk_end], start=chunk_start))

        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(check_proxy, item) for item in current_chunk]
            for future in concurrent.futures.as_completed(futures):
                res = future.result()
                if res:
                    found_proxy = res
                    try:
                        executor.shutdown(wait=False, cancel_futures=True)
                    except TypeError:
                        executor.shutdown(wait=False)
                    break

        if found_proxy:
            break

    return found_proxy


def next_proxy_from_pool():
    """
    Mencari dan menggeser SELECTED_PROXY ke proxy berikutnya yang *sudah divalidasi*.
    Prioritas: ambil dari PROXY_WARM_POOL dulu (instant), baru scan dari PROXY_POOL.
    """
    global SELECTED_PROXY, PROXY_POOL_INDEX, PROXY_POOL

    # --- Coba ambil dari warm pool dulu (O(1), tidak perlu scan) ---
    with PROXY_WARM_LOCK:
        if PROXY_WARM_POOL:
            SELECTED_PROXY = PROXY_WARM_POOL.popleft()
            remaining = len(PROXY_WARM_POOL)
            log(f"[POOL] Beralih ke proxy hangat berikutnya: {SELECTED_PROXY} "
                f"(sisa di warm pool: {remaining}/{PROXY_WARM_TARGET})")
            return SELECTED_PROXY

    # --- Warm pool kosong: fallback ke scan biasa ---
    if not PROXY_POOL:
        SELECTED_PROXY = None
        return None

    log("[PROXY-SEARCH] Warm pool kosong. Mencari proxy aktif berikutnya secara paralel...")

    result = search_working_proxy_in_pool(PROXY_POOL_INDEX + 1)

    if result:
        PROXY_POOL_INDEX, SELECTED_PROXY = result
        log(f"[PROXY-OK] Berhasil beralih ke proxy [{PROXY_POOL_INDEX}/{len(PROXY_POOL)-1}]: {SELECTED_PROXY}")
        return SELECTED_PROXY
    else:
        log("[PROXY-FAIL] Semua proxy dalam pool sudah dicoba/habis. Fallback ke koneksi langsung.")
        SELECTED_PROXY = None
        return None


# =============================================
# PROXY WARMER — background thread
# =============================================
def _proxy_warmer_loop():
    """
    Background thread: terus mengisi PROXY_WARM_POOL hingga PROXY_WARM_TARGET.
    Berjalan bersamaan dengan wait_for_stream() sehingga waktu tunggu
    siaran dimanfaatkan untuk memvalidasi proxy.
    """
    global PROXY_POOL_INDEX

    target_stats = "https://i.klikhost.com:8502/stats?json=1"
    scan_idx = PROXY_POOL_INDEX + 1   # mulai setelah proxy aktif saat ini

    while not _proxy_warmer_stop.is_set():
        with PROXY_WARM_LOCK:
            current_size = len(PROXY_WARM_POOL)

        if current_size >= PROXY_WARM_TARGET:
            # Pool sudah penuh — tidur sebentar lalu cek lagi
            _proxy_warmer_stop.wait(timeout=10)
            continue

        needed = PROXY_WARM_TARGET - current_size
        if not PROXY_POOL or scan_idx >= len(PROXY_POOL):
            # Pool utama habis — reset scan dari awal
            scan_idx = 0

        stop_event = threading.Event()
        found_batch = []
        found_lock  = threading.Lock()

        def _check(px_info):
            if stop_event.is_set():
                return
            idx, px = px_info
            px_url = f"http://{px}"
            try:
                res = requests.get(
                    target_stats,
                    proxies={"http": px_url, "https": px_url},
                    timeout=5,
                    verify=False,
                )
                if res.status_code == 200 and "streamstatus" in res.text:
                    with found_lock:
                        found_batch.append((idx, px))
                        if len(found_batch) >= needed:
                            stop_event.set()
            except Exception:
                pass

        chunk_end = min(scan_idx + 500, len(PROXY_POOL))
        chunk = list(enumerate(PROXY_POOL[scan_idx:chunk_end], start=scan_idx))
        scan_idx = chunk_end

        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as ex:
            futs = [ex.submit(_check, item) for item in chunk]
            concurrent.futures.wait(futs, timeout=15)

        if found_batch:
            with PROXY_WARM_LOCK:
                for _, px in found_batch[:needed]:
                    if px not in PROXY_WARM_POOL:
                        PROXY_WARM_POOL.append(px)
                new_size = len(PROXY_WARM_POOL)
            log(f"[POOL] Warm pool diisi +{len(found_batch[:needed])} proxy → "
                f"{new_size}/{PROXY_WARM_TARGET} siap")

        # Jeda singkat antar batch agar tidak membebani CPU
        if not _proxy_warmer_stop.is_set():
            _proxy_warmer_stop.wait(timeout=2)


def start_proxy_warmer():
    """Mulai background thread proxy warmer."""
    _proxy_warmer_stop.clear()
    t = threading.Thread(target=_proxy_warmer_loop, daemon=True, name="proxy-warmer")
    t.start()
    log(f"[POOL] Proxy warmer dimulai — target {PROXY_WARM_TARGET} proxy siap di latar belakang.")


def stop_proxy_warmer():
    """Hentikan proxy warmer thread."""
    _proxy_warmer_stop.set()


# =============================================
# PARALLEL PROXY SELECTOR
# Digunakan saat reconnect agar tidak membuang
# waktu test proxy satu-per-satu secara sekuensial.
# Mengambil batch dari warm pool, test serentak,
# dan memakai yang pertama berhasil.
# =============================================
def next_proxy_parallel_from_pool(target_url=None, max_batch=8, timeout=5):
    """
    Test hingga max_batch proxy dari warm pool secara parallel.
    Proxy yang tidak lulus (termasuk yang kalah lomba) dibuang dari pool.
    Kembalikan proxy pemenang, atau fallback ke next_proxy_from_pool() jika semua gagal.
    """
    global SELECTED_PROXY

    if not target_url:
        target_url = "https://i.klikhost.com:8502/stats?json=1"

    # Ambil kandidat dari warm pool
    with PROXY_WARM_LOCK:
        batch = list(itertools.islice(PROXY_WARM_POOL, max_batch))

    if not batch:
        # Warm pool kosong — fallback ke sequential scan
        return next_proxy_from_pool()

    winner_holder = [None]
    winner_lock   = threading.Lock()
    stop_event    = threading.Event()

    def _check(px):
        if stop_event.is_set():
            return
        px_url = f"http://{px}"
        try:
            res = requests.get(
                target_url,
                proxies={"http": px_url, "https": px_url},
                timeout=timeout,
                verify=False,
            )
            if res.status_code == 200 and "streamstatus" in res.text:
                with winner_lock:
                    if winner_holder[0] is None:
                        winner_holder[0] = px
                        stop_event.set()
        except Exception:
            pass

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(batch)) as ex:
        futs = [ex.submit(_check, px) for px in batch]
        concurrent.futures.wait(futs, timeout=timeout + 2)

    winner = winner_holder[0]

    # Hapus semua kandidat batch dari warm pool (pemenang sudah dipakai, losers sudah mati)
    with PROXY_WARM_LOCK:
        for px in batch:
            try:
                PROXY_WARM_POOL.remove(px)
            except ValueError:
                pass
        remaining = len(PROXY_WARM_POOL)

    if winner:
        SELECTED_PROXY = winner
        log(f"[POOL] Parallel pick: {winner} terpilih dari {len(batch)} kandidat "
            f"(sisa warm pool: {remaining}/{PROXY_WARM_TARGET})")
        return winner

    # Semua kandidat batch gagal — cari lagi dari sisa pool
    log(f"[PROXY-FAIL] Seluruh batch {len(batch)} proxy gagal. Mencari dari sisa pool...")
    return next_proxy_from_pool()


# =============================================
# STATS WRITER — async queue-based
# =============================================
def _stats_writer_loop():
    """
    Background thread: flush _stats_queue ke STATS_FILE (NDJSON).
    Setiap baris ditulis dalam dua grup:
      - "connection": info koneksi proxy (ping_no, proxy, status_code, dll)
      - "stats"     : response body JSON dari stats endpoint

    Optimasi v2.0: File dibuka SATU KALI di luar loop.
    Data dikumpulkan ke batch, lalu ditulis sekaligus setiap 10 entri
    atau setiap 5 detik — mengurangi drastis overhead buka/kunci/tutup file OS.
    """
    os.makedirs("recordings", exist_ok=True)
    _BATCH_SIZE  = 10
    _FLUSH_EVERY = 5.0   # detik

    with open(STATS_FILE, "a", encoding="utf-8") as f:
        batch      = []
        last_flush = time.monotonic()

        while not _stats_writer_stop.is_set() or not _stats_queue.empty():
            try:
                entry = _stats_queue.get(timeout=1)
            except queue.Empty:
                # Flush sisa batch jika sudah lebih dari _FLUSH_EVERY detik
                if batch and (time.monotonic() - last_flush) >= _FLUSH_EVERY:
                    f.writelines(batch)
                    f.flush()
                    batch.clear()
                    last_flush = time.monotonic()
                continue

            try:
                formatted = {
                    "time": entry.get("time"),
                    "connection": {
                        "ping_no"      : entry.get("ping_no"),
                        "proxy"        : entry.get("proxy"),
                        "status_code"  : entry.get("status_code"),
                        "stream_on_air": entry.get("stream_on_air"),
                        "response_ms"  : entry.get("response_ms"),
                        "error"        : entry.get("error"),
                    },
                    "stats": entry.get("stats_body"),
                }
                batch.append(json.dumps(formatted, ensure_ascii=False) + "\n")
            except Exception:
                pass   # Jangan crash program hanya karena stats gagal tulis
            finally:
                _stats_queue.task_done()

            # Flush jika batch sudah penuh atau waktunya
            if len(batch) >= _BATCH_SIZE or (time.monotonic() - last_flush) >= _FLUSH_EVERY:
                try:
                    f.writelines(batch)
                    f.flush()
                except Exception:
                    pass
                batch.clear()
                last_flush = time.monotonic()

        # Flush sisa entri terakhir sebelum thread mati
        if batch:
            try:
                f.writelines(batch)
                f.flush()
            except Exception:
                pass


def start_stats_writer():
    """Mulai background thread stats writer."""
    _stats_writer_stop.clear()
    t = threading.Thread(target=_stats_writer_loop, daemon=True, name="stats-writer")
    t.start()


def stop_stats_writer():
    """Sinyal stats writer untuk berhenti (setelah queue kosong)."""
    _stats_writer_stop.set()


def append_stat(entry: dict):
    """
    Tambahkan satu entri ping ke antrean stats (non-blocking).
    entry harus berupa dict yang bisa di-serialize ke JSON.
    """
    _stats_queue.put_nowait(entry)

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

PROXY_SOURCES = []  # Dimuat lazy hanya saat --random-proxy diaktifkan

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
    Setiap proxy divalidasi: tes stats endpoint harus HTTP 200 + mengandung 'streamstatus'.
    Berhenti pada proxy pertama yang lulus.
    Seluruh daftar proxy yang sudah dinormalisasi disimpan ke PROXY_POOL global
    agar wait_for_stream() bisa beralih ke proxy berikutnya tanpa re-download.
    """
    global PROXY_POOL, PROXY_POOL_INDEX, PROXY_SOURCES
    target_stats = "https://i.klikhost.com:8502/stats?json=1"

    # Muat sumber proxy secara lazy — hanya saat fungsi ini benar-benar dipanggil
    if not PROXY_SOURCES:
        PROXY_SOURCES = _load_proxy_sources()

    log("[PROXY-SEARCH] Mengunduh daftar proxy terbaru dari semua sumber...")
    proxies_raw = []

    if not PROXY_SOURCES:
        log("[PROXY-FAIL] PROXY_SOURCES kosong. Pastikan HTTP_PROXY sudah diset di GitHub Secrets.")
        return None

    # Unduh SEMUA PROXY_SOURCES secara bersamaan (concurrent) — v2.0 optimization
    # Sebelumnya: sekuensial (URL ke-2 harus menunggu URL ke-1 selesai).
    # Sekarang: semua URL dilempar ke ThreadPoolExecutor sekaligus.
    results_lock = threading.Lock()

    def _fetch_source(args):
        idx, source_url = args
        try:
            req = requests.get(source_url, timeout=10, headers=get_headers())
            if req.status_code == 200:
                lines    = req.text.strip().split('\n')
                raw_list = [p.strip() for p in lines if p.strip()]
                if raw_list:
                    log(f"[PROXY-SEARCH] Sumber [{idx}]: {len(raw_list)} proxy ditemukan, digabung ke pool.")
                    return raw_list
                else:
                    log(f"[WARN] Sumber [{idx}] berhasil diunduh tapi daftar kosong.")
            else:
                log(f"[WARN] Sumber [{idx}] merespons HTTP {req.status_code}, dilewati.")
        except Exception as e:
            log(f"[WARN] Sumber [{idx}] gagal: {e}, dilewati.")
        return []

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(PROXY_SOURCES)) as ex:
        future_map = {
            ex.submit(_fetch_source, (i, url)): i
            for i, url in enumerate(PROXY_SOURCES)
        }
        for future in concurrent.futures.as_completed(future_map):
            try:
                proxies_raw.extend(future.result())
            except Exception:
                pass

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

    # Simpan seluruh pool ke global
    global PROXY_POOL, PROXY_POOL_INDEX
    PROXY_POOL = proxies_clean
    PROXY_POOL_INDEX = 0

    log(f"[PROXY-SEARCH] {len(proxies_clean)} proxy valid. Memulai pencarian proxy aktif...")

    result = search_working_proxy_in_pool(0)

    if result:
        PROXY_POOL_INDEX, found_proxy = result
        log(f"[PROXY-OK] Selesai. Proxy terpilih [{PROXY_POOL_INDEX}/{len(PROXY_POOL)-1}]: {found_proxy}")
        return found_proxy
    else:
        log("[PROXY-FAIL] Tidak ada proxy yang merespons 200 OK.")
        return None



# =============================================
# WAIT FOR STREAM
# =============================================
def wait_for_stream(url):
    """
    Strategi polling bertahap berbasis offset sejak jam :00.
    Tahap 1:  0– 2 menit → setiap   3 detik
    Tahap 2:  2– 3 menit → setiap   5 detik
    Tahap 3:  3– 5 menit → setiap  10 detik
    Tahap 4:  5–10 menit → setiap  15 detik
    Tahap 5: 10–30 menit → setiap  30 detik
    Tahap 6: 30–50 menit → setiap  60 detik
    Tahap 7: 50–60 menit → setiap  90 detik

    Saat jam :00, offset otomatis kembali ke 0 sehingga
    polling kembali ke Tahap 1 yang cepat.

    Metode deteksi: ping langsung ke stream URL (bukan stats endpoint).
    Mirip fetch() no-cors — koneksi dibuka, header diterima, langsung ditutup.
    Kalau server merespons tanpa error = stream hidup, langsung mulai rekam.
    """
    global SELECTED_PROXY
    STAGES = [
        (   0,  120,   3),
        ( 120,  180,   5),
        ( 180,  300,  10),
        ( 300,  600,  15),
        ( 600, 1800,  30),
        (1800, 3000,  60),
        (3000, 3600,  90),
    ]

    def get_interval(elapsed_seconds):
        for start, end, interval in STAGES:
            if start <= elapsed_seconds < end:
                return interval
        return 90

    def seconds_since_last_hour():
        now = now_wita()
        return now.minute * 60 + now.second

    stage_labels = {
        3: "0–2 mnt", 5: "2–3 mnt", 10: "3–5 mnt",
        15: "5–10 mnt", 30: "10–30 mnt", 60: "30–50 mnt", 90: "50–60 mnt",
    }
    last_interval = None
    last_err = None
    last_seen_hour = now_wita().hour
    ping_count = 0

    target_stats = "https://i.klikhost.com:8502/stats?json=1"
    log(f"[WAIT] Menunggu siaran — request ke stats: {target_stats}")

    # Mulai mengumpulkan 16 proxy siap di latar belakang
    # sambil menunggu siaran — waktu tunggu tidak terbuang sia-sia.
    random_proxy_mode = getattr(ARGS, 'random_proxy', None) if ARGS else None
    
    if random_proxy_mode and PROXY_POOL:
        start_proxy_warmer()

    while True:
        # /skip-stage: skip waiting
        if CONSOLE and CONSOLE.skip_stage.is_set():
            CONSOLE.skip_stage.clear()
            log("[SKIP] Wait stage dilewati oleh user.")
            stop_proxy_warmer()
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

        ping_count += 1
        ping_time   = now_wita()
        t_start     = time.monotonic()
        stat_entry  = {
            "time"       : ping_time.strftime("%Y-%m-%d %H:%M:%S"),
            "ping_no"    : ping_count,
            "proxy"      : SELECTED_PROXY,
            "status_code": None,
            "stream_on_air": None,
            "response_ms": None,
            "error"      : None,
            "stats_body" : None,   # ← response body JSON dari stats endpoint
        }

        try:
            use_proxy_for_stats = False
            if ARGS:
                if ARGS.proxy:
                    use_proxy_for_stats = True
                if getattr(ARGS, 'random_proxy', None) in ['stats', 'all']:
                    use_proxy_for_stats = True
            
            stats_proxy = get_proxies_dict(SELECTED_PROXY) if use_proxy_for_stats else None

            response = requests.get(
                target_stats,
                timeout=5,
                verify=False,
                headers=get_headers(),
                proxies=stats_proxy
            )
            stat_entry["response_ms"]  = round((time.monotonic() - t_start) * 1000)
            stat_entry["status_code"]  = response.status_code

            # Simpan response body asli sebagai stats_body (parse JSON jika bisa)
            if response.status_code == 200:
                try:
                    stat_entry["stats_body"] = response.json()
                except Exception:
                    stat_entry["stats_body"] = response.text[:500]  # fallback: teks mentah (max 500 char)

            if response.status_code == 200:
                res_clean = response.text.replace(" ", "")

                # Proxy mengembalikan konten tapi sama sekali tidak ada key "streamstatus"
                # → proxy ngawur/intercept, ganti proxy
                if "streamstatus" not in res_clean:
                    stat_entry["stream_on_air"] = None
                    stat_entry["error"] = "invalid_content"
                    if use_proxy_for_stats:
                        log(f"[PROXY-FAIL] #{ping_count} | Proxy {SELECTED_PROXY} merespons tapi konten tidak valid (tanpa 'streamstatus'). Ganti proxy...")
                        if random_proxy_mode:
                            next_proxy_from_pool()
                    else:
                        log(f"[ERROR] #{ping_count} | Endpoint langsung merespons tapi konten tidak valid (tanpa 'streamstatus').")
                    last_err = None

                elif '"streamstatus":1' in res_clean:
                    stat_entry["stream_on_air"] = True
                    append_stat(stat_entry)
                    log(f"[OK] Stream ON-AIR — memulai perekaman...")
                    stop_proxy_warmer()
                    # Gunakan proxy ordo-1 dari warm pool untuk recording
                    with PROXY_WARM_LOCK:
                        if PROXY_WARM_POOL and random_proxy_mode:
                            SELECTED_PROXY = PROXY_WARM_POOL.popleft()
                            log(f"[POOL] Proxy dari warm pool disiapkan untuk recording: {SELECTED_PROXY} "
                                f"(sisa warm pool: {len(PROXY_WARM_POOL)})")
                    return
                else:
                    stat_entry["stream_on_air"] = False
                    if last_err != "offline":
                        log(f"[OFFLINE] Server merespons tapi siaran OFF-AIR — menunggu...")
                        last_err = "offline"
                    else:
                        log(f"[PING] #{ping_count} | Status: OFF-AIR | Interval: {interval}s")
            else:
                stat_entry["stream_on_air"] = False
                # Non-200: penerusan gagal atau blokir
                if use_proxy_for_stats and random_proxy_mode:
                    log(f"[PROXY-FAIL] #{ping_count} | Proxy {SELECTED_PROXY} merespons HTTP {response.status_code} — ganti proxy...")
                    next_proxy_from_pool()
                    last_err = None
                else:
                    if last_err != f"http_{response.status_code}":
                        log(f"[OFFLINE] Server merespons HTTP {response.status_code} — menunggu...")
                        last_err = f"http_{response.status_code}"
                    else:
                        log(f"[PING] #{ping_count} | Status: HTTP {response.status_code} | Interval: {interval}s")

        except requests.exceptions.ProxyError as e:
            stat_entry["error"] = f"ProxyError:{type(e).__name__}"
            stat_entry["response_ms"] = round((time.monotonic() - t_start) * 1000)
            log(f"[PROXY-FAIL] #{ping_count} | Proxy {SELECTED_PROXY} tidak dapat digunakan: {type(e).__name__}")
            if random_proxy_mode:
                # Gunakan parallel selector agar tidak membuang ~5 detik per proxy secara sekuensial
                next_proxy_parallel_from_pool()
                append_stat(stat_entry)
                time.sleep(1)  # jeda singkat sebelum retry
                continue       # skip time.sleep(interval) di bawah
            else:
                log("[WARN] Proxy gagal, fallback ke koneksi langsung.")
                SELECTED_PROXY = None
            last_err = None

        except requests.exceptions.RequestException as e:
            stat_entry["error"] = f"RequestException:{type(e).__name__}"
            stat_entry["response_ms"] = round((time.monotonic() - t_start) * 1000)
            if use_proxy_for_stats:
                log(f"[PROXY-FAIL] #{ping_count} | Gagal menjangkau server via proxy: {type(e).__name__}")
                if random_proxy_mode:
                    # Parallel di sini juga — RequestException biasanya timeout, lebih cepat batch
                    next_proxy_parallel_from_pool()
                    append_stat(stat_entry)
                    time.sleep(1)  # jeda singkat sebelum retry
                    continue       # skip time.sleep(interval) di bawah
            else:
                log(f"[ERROR] #{ping_count} | Gagal menjangkau server langsung: {type(e).__name__}")
            last_err = None

        # Catat stat secara async (non-blocking)
        append_stat(stat_entry)

        # Tidur, tapi jangan melewati pergantian jam (:00).
        # Kalau interval lebih panjang dari sisa detik ke jam berikutnya,
        # bangun tepat saat :00 agar reset ke Tahap 1 terjadi on-time.
        now_dt = now_wita()
        secs_to_next_hour = 3600 - (now_dt.minute * 60 + now_dt.second)
        sleep_duration = min(interval, secs_to_next_hour + 1)
        time.sleep(sleep_duration)


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
        # Chunk pertama selalu pakai suffix _0 agar tidak bentrok
        # dengan nama final output (base_no_ext.ext) yang dipakai setelah merge.
        return f"{base_no_ext}_0.{ext}"
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

    # final_output adalah file hasil merge — jangan ikutkan sebagai chunk
    # agar tidak ter-merge ulang pada restart berikutnya
    final_output_abs = os.path.abspath(f"{base_no_ext}.{ext}")

    files = []
    for f in os.listdir(dirpath):
        if pattern.match(f):
            full = os.path.join(dirpath, f)
            if os.path.abspath(full) == final_output_abs:
                continue  # skip: ini file merged hasil sesi sebelumnya
            try:
                mtime = os.path.getmtime(full)
            except:
                mtime = 0
            # sort primer: mtime; sekunder: nama file (hindari ambiguitas saat mtime sama)
            files.append((mtime, f, full))
    files.sort(key=lambda x: (x[0], x[1]))
    return [full for _, _, full in files]


def merge_chunks_to_base(base_no_ext, ext, keep_raw=False):
    chunks = list_chunks_ordered(base_no_ext, ext)
    if not chunks:
        log("[MERGE] Tidak ada chunk yang bisa digabung.")
        return None

    list_txt = os.path.join("recordings", "concat_list.txt")
    with open(list_txt, "w", encoding="utf-8") as f:
        for c in chunks:
            abs_path = os.path.abspath(c)
            safe_path = abs_path.replace("'", "'\"'\"'")
            f.write(f"file '{safe_path}'\n")

    temp_output = os.path.join("recordings", "__merged_temp__." + ext)
    final_output = f"{base_no_ext}.{ext}"

    # Hapus sisa temp merge dari run sebelumnya yang crash
    if os.path.exists(temp_output):
        log(f"[WARN] File temp merge lama ditemukan, menghapus: {temp_output}")
        try:
            os.remove(temp_output)
        except Exception as e:
            log(f"[WARN] Gagal menghapus temp lama: {e}")

    try:
        cmd = [
            "./ffmpeg", "-hide_banner", "-f", "concat", "-safe", "0",
            "-i", list_txt, "-c", "copy", temp_output
        ]
        log("[MERGE] Menggabungkan semua bagian rekaman...")
        subprocess.run(cmd, check=True)

        shutil.move(temp_output, final_output)
        log(f"[MERGE] Berhasil digabung → {final_output}")

        if keep_raw:
            # Pindah semua chunk ke subfolder raw/ agar tidak ter-merge ulang
            raw_dir = os.path.join(os.path.dirname(base_no_ext) or "recordings", "raw")
            os.makedirs(raw_dir, exist_ok=True)
            for c in chunks:
                try:
                    dest = os.path.join(raw_dir, os.path.basename(c))
                    shutil.move(c, dest)
                    log(f"[CLEAN] Raw chunk dipindah ke: {dest}")
                except Exception as e:
                    log(f"[WARN] Gagal memindah raw chunk {c}: {e}")
        else:
            for c in chunks:
                try:
                    if os.path.abspath(c) == os.path.abspath(final_output):
                        log(f"[CLEAN] Melewati chunk final (sudah jadi output): {c}")
                        continue
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
    """
    Rekam stream audio dan upload.

    Arsitektur v2.0 — Subprocess Piping:
      Python (requests) ──stdin pipe──► FFmpeg (background, hidup terus)
      Outer Loop menangani reconnect; FFmpeg TIDAK direstart saat koneksi putus.
      Hasil akhir: SATU file utuh tanpa proses merge sama sekali.
    """

    # --no-record: skip seluruh proses
    if ARGS and ARGS.no_record:
        log("[SKIP] --no-record aktif. Perekaman ffmpeg dilewati.")
        return

    # ─────────────────────────────────────────────────────────
    # LANGKAH 1: Persiapan nama file & command FFmpeg
    # Lewati ffprobe — gunakan --stream-file-format atau default mp3.
    # ─────────────────────────────────────────────────────────
    date_str = now_wita().strftime("%d-%m-%y")
    os.makedirs("recordings", exist_ok=True)

    if ARGS and ARGS.stream_file_format:
        ext = ARGS.stream_file_format.lower().strip(".")
        log(f"[CODEC] Format file stream (manual): {ext}")
    else:
        ext = "mp3"
        log("[CODEC] Menggunakan format default: mp3 (pipe mode, ffprobe dilewati)")

    # Nama file output final (SATU file, bukan chunk)
    if ARGS and ARGS.output_name:
        filename = f"recordings/{ARGS.output_name}.{ext}"
    else:
        base_no_ext = make_base_no_ext(date_str, suffix)
        filename    = f"{base_no_ext}.{ext}"

    # Susun command FFmpeg untuk pipe mode
    cmd = [
        "./ffmpeg", "-y", "-hide_banner",
        "-i", "pipe:0",          # terima byte audio mentah dari stdin Python
        "-c", "copy",            # salin stream tanpa re-encode
        "-metadata", f"title=VOT Denpasar {date_str}",
        "-metadata", "artist=VOT Radio Denpasar",
        "-metadata", f"date={date_str}",
    ]

    # --duration: batasi durasi rekaman via FFmpeg
    if ARGS and ARGS.duration:
        cmd.extend(["-t", str(ARGS.duration)])
        log(f"[RUN] Durasi rekaman dibatasi: {ARGS.duration} detik")

    cmd.append(filename)
    log(f"[RUN] Rekaman dimulai (pipe mode) → {filename}")

    # ─────────────────────────────────────────────────────────
    # LANGKAH 2: Inisialisasi Subprocess (HANYA 1 KALI)
    # FFmpeg menerima byte dari process.stdin, bukan dari URL langsung.
    # ─────────────────────────────────────────────────────────
    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    # Thread daemon membaca stderr FFmpeg secara real-time → log [FFMPEG]
    # Wajib agar buffer memori subprocess tidak penuh dan program tidak hang.
    def _log_ffmpeg_stderr(proc):
        try:
            for raw_line in proc.stderr:
                line = raw_line.decode(errors="replace").strip()
                if line:
                    log(f"[FFMPEG] {line}")
        except Exception:
            pass

    threading.Thread(
        target=_log_ffmpeg_stderr,
        args=(process,),
        daemon=True,
        name="ffmpeg-stderr-reader",
    ).start()

    no_timer       = ARGS and ARGS.no_timer_limit
    stop_flag      = False        # sinyal keluar baik dari waktu maupun duration
    _duration_start = time.monotonic() if (ARGS and ARGS.duration) else None

    # ─────────────────────────────────────────────────────────
    # LANGKAH 3: Outer Loop — Penahan Reconnect
    # FFmpeg TIDAK dimatikan/restart di dalam loop ini.
    # ─────────────────────────────────────────────────────────
    while not stop_flag:

        # /skip-stage: hentikan rekaman dari user
        if CONSOLE and CONSOLE.skip_stage.is_set():
            CONSOLE.skip_stage.clear()
            log("[SKIP] Rekaman dihentikan oleh user (/skip-stage).")
            stop_flag = True
            break

        # ─────────────────────────────────────────────────────
        # LANGKAH 4: Konfigurasi Headers & Request
        # ─────────────────────────────────────────────────────
        headers = {"Icy-MetaData": "0"}    # byte audio murni, tanpa sisipan metadata ICY
        headers.update(get_headers())

        use_proxy_for_stream = False
        if ARGS:
            if ARGS.proxy:
                use_proxy_for_stream = True
            if getattr(ARGS, "random_proxy", None) in ["stream", "all"]:
                use_proxy_for_stream = True

        proxies = get_proxies_dict(SELECTED_PROXY) if use_proxy_for_stream else None
        if proxies:
            log(f"[PROXY-OK] Stream menggunakan proxy: {SELECTED_PROXY}")

        # ─────────────────────────────────────────────────────
        # LANGKAH 5 & 6: Inner Loop (Chunking & Piping) + Exception Handling
        # ─────────────────────────────────────────────────────
        try:
            with requests.get(
                url,
                headers=headers,
                proxies=proxies,
                stream=True,
                timeout=10,
            ) as r:
                r.raise_for_status()
                log(f"[OK] Koneksi stream berhasil (HTTP {r.status_code}). Meng-pipe ke FFmpeg...")

                # LANGKAH 5: Inner Loop — baca chunk & tulis ke stdin FFmpeg
                for chunk in r.iter_content(chunk_size=8192):
                    if not chunk:
                        continue

                    # Tulis byte audio langsung ke stdin FFmpeg
                    try:
                        process.stdin.write(chunk)
                        # flush() opsional — memastikan FFmpeg segera memproses
                        # tanpa harus menunggu buffer OS penuh
                        process.stdin.flush()
                    except (BrokenPipeError, OSError):
                        # FFmpeg mati tak terduga (bukan karena koneksi, tapi proses crash)
                        log("[FAIL] Pipe ke FFmpeg terputus. FFmpeg mungkin crash.")
                        stop_flag = True
                        break

                    # Pengecekan waktu cutoff 18:30 WITA
                    now = now_wita()
                    if not no_timer and now.hour == 18 and now.minute >= 30:
                        log("[CUT-OFF] Siaran berakhir pukul 18:30 WITA. Menghentikan perekaman...")
                        stop_flag = True
                        break

                    # Pengecekan --duration
                    if _duration_start and (time.monotonic() - _duration_start) >= ARGS.duration:
                        log(f"[DONE] Durasi {ARGS.duration}s tercapai. Menghentikan perekaman...")
                        stop_flag = True
                        break

        except (
            requests.exceptions.RequestException,
            requests.exceptions.ChunkedEncodingError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            ConnectionError,
            OSError,
        ) as e:
            # ─────────────────────────────────────────────────
            # LANGKAH 6: Exception Handling — Mekanisme Tahan Banting
            # FFmpeg di background TETAP DIBIARKAN HIDUP dan menunggu byte baru.
            # ─────────────────────────────────────────────────
            log(f"[WARN] Koneksi stream terputus: {type(e).__name__}. Mencari proxy pengganti...")

            # Ganti proxy secara paralel dari warm pool
            use_proxy_for_stream = False
            if ARGS:
                if ARGS.proxy:
                    use_proxy_for_stream = True
                if getattr(ARGS, "random_proxy", None) in ["stream", "all"]:
                    use_proxy_for_stream = True

            if use_proxy_for_stream:
                next_proxy_parallel_from_pool()

            time.sleep(2)   # jeda singkat untuk mencegah spam reconnect
            continue         # kembali ke awal Outer Loop, buka requests.get baru

    # ─────────────────────────────────────────────────────────
    # LANGKAH 7: Graceful Shutdown
    # Tutup stdin agar FFmpeg tahu tidak ada data lagi,
    # lalu tunggu FFmpeg selesai muxing struktur header file.
    # ─────────────────────────────────────────────────────────
    log("[DONE] Menutup pipe ke FFmpeg — menunggu FFmpeg menyelesaikan muxing...")
    try:
        process.stdin.close()
    except Exception:
        pass

    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        log("[WARN] FFmpeg timeout saat shutdown, memaksa berhenti...")
        process.kill()
        process.wait()

    log(f"[DONE] File rekaman selesai → {filename}")

    # --no-save: hapus file yang baru direkam
    if ARGS and ARGS.no_save:
        log(f"[SKIP] --no-save aktif. Menghapus file rekaman: {filename}")
        try:
            os.remove(filename)
        except Exception as e:
            log(f"[WARN] Gagal menghapus file: {e}")
        return

    # ─────────────────────────────────────────────────────────
    # Upload — file sudah utuh, tidak ada merge
    # ─────────────────────────────────────────────────────────
    log(f"[UPLOAD-PREP] Siap upload: {filename}")

    if no_upload:
        log(f"[SKIP] Mode tanpa upload aktif. File tersimpan di {filename}")
        return

    if position > 0:
        delay = position * 10
        log(f"[DELAY] Menunggu {delay} detik sebelum upload...")
        time.sleep(delay)

    archive_url, item_id = upload_to_archive(filename)

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

    # Sertakan stats.json jika ada
    # Gunakan dict {remote_path: local_path} agar raw/ bisa masuk sebagai subfolder di archive
    files_to_upload = {os.path.basename(file_path): file_path}
    if os.path.exists(STATS_FILE):
        # Tunggu sebentar agar queue stats sempat ter-flush
        _stats_queue.join() if not _stats_queue.empty() else None
        files_to_upload[os.path.basename(STATS_FILE)] = STATS_FILE
        log(f"[STATS] stats.json akan ikut diupload bersama rekaman.")

    # Sertakan chunk mentah dari folder raw/ jika ada (aktif dengan --with-raw)
    raw_dir = os.path.join("recordings", "raw")
    if os.path.exists(raw_dir):
        raw_files = sorted(f for f in os.listdir(raw_dir) if os.path.isfile(os.path.join(raw_dir, f)))
        if raw_files:
            for rf in raw_files:
                files_to_upload[f"raw/{rf}"] = os.path.join(raw_dir, rf)
            log(f"[MERGE] {len(raw_files)} chunk mentah dari folder raw/ akan diupload.")

    for attempt in range(1, retries + 1):
        try:
            upload(
                item_identifier,
                files=files_to_upload,
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
# START-TIME HELPER
# =============================================
def parse_start_time(value):
    """
    Parse string waktu ke objek datetime WITA hari ini.
    Format yang diterima: hh:mm:ss | hh:mm | hh
    Kembalikan datetime, atau raise ValueError jika format tidak valid.
    """
    parts = value.strip().split(":")
    if len(parts) == 1:
        h = int(parts[0])
        m, s = 0, 0
    elif len(parts) == 2:
        h, m = int(parts[0]), int(parts[1])
        s = 0
    elif len(parts) == 3:
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
    else:
        raise ValueError(f"Format waktu tidak valid: '{value}'. Gunakan hh, hh:mm, atau hh:mm:ss")

    if not (0 <= h <= 23 and 0 <= m <= 59 and 0 <= s <= 59):
        raise ValueError(f"Nilai waktu di luar rentang valid: '{value}'")

    now = now_wita()
    return now.replace(hour=h, minute=m, second=s, microsecond=0)


def wait_until_start_time(start_dt):
    """
    Tunggu hingga waktu start_dt (datetime dengan timezone WITA).
    Menampilkan countdown setiap 30 detik.
    Bisa di-skip dengan /skip-stage.
    """
    log(f"[WAIT] Menunggu hingga jam {start_dt.strftime('%H:%M:%S')} WITA sebelum mulai...")
    last_log = None

    while True:
        # /skip-stage: lewati waiting
        if CONSOLE and CONSOLE.skip_stage.is_set():
            CONSOLE.skip_stage.clear()
            log("[SKIP] Start-time wait dilewati oleh user.")
            return

        now = now_wita()
        remaining = (start_dt - now).total_seconds()

        if remaining <= 0:
            log(f"[OK] Waktu mulai tercapai ({start_dt.strftime('%H:%M:%S')} WITA). Melanjutkan...")
            return

        # Log countdown setiap 30 detik (atau saat pertama kali)
        bucket = int(remaining // 30)
        if bucket != last_log:
            last_log = bucket
            mins, secs = divmod(int(remaining), 60)
            hrs, mins = divmod(mins, 60)
            if hrs:
                countdown_str = f"{hrs}j {mins}m {secs:02d}d"
            elif mins:
                countdown_str = f"{mins}m {secs:02d}d"
            else:
                countdown_str = f"{secs}d"
            log(f"[WAIT] Menunggu start-time — sisa: {countdown_str} hingga jam {start_dt.strftime('%H:%M:%S')} WITA")

        time.sleep(min(5, remaining))


# =============================================
# MAIN RECORDING
# =============================================
def main_recording():
    global ARGS, MY_ACCESS_KEY, MY_SECRET_KEY, PROXY_POOL_INDEX

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
    parser.add_argument("--start-time", type=str, default=None, dest="start_time",
                        help="Waktu mulai rekaman WITA. Format: hh | hh:mm | hh:mm:ss (misal: 07, 07:30, 07:30:00)")

    parser.add_argument("--proxy", type=str, default=None,
                        help="Gunakan manual HTTP proxy (misal: http://192.168.1.1:8080 atau 192.168.1.1:8080)")
    parser.add_argument("--random-proxy", type=str, choices=["stats", "stream", "all"],
                        help="Jalankan proxy finder otomatis dan tetapkan target proxy (opsi: stats, stream, all)")

    ARGS = parser.parse_args()

    if ARGS.archive_access:
        MY_ACCESS_KEY = ARGS.archive_access
    if ARGS.archive_secret:
        MY_SECRET_KEY = ARGS.archive_secret

    stream_url = ARGS.stream_url if ARGS.stream_url else STREAM_URL

    # Mulai stats writer di background (hanya sekali per session)
    if not _stats_writer_stop.is_set() or _stats_queue.empty():
        start_stats_writer()

    # Evaluasi Proxy
    global SELECTED_PROXY
    if ARGS.random_proxy:
        if PROXY_POOL:
            # Restart ke-N: pool sudah ada, tidak perlu re-download 468k proxy.
            # Validasi ulang proxy aktif saat ini; jika mati, ambil dari warm pool.
            log("[PROXY-SEARCH] Pool sudah tersedia — revalidasi proxy aktif...")
            with PROXY_WARM_LOCK:
                warm_available = len(PROXY_WARM_POOL)
            if warm_available > 0:
                found = next_proxy_from_pool()
            else:
                found = search_working_proxy_in_pool(PROXY_POOL_INDEX)
                if found:
                    PROXY_POOL_INDEX, found = found
            if found:
                SELECTED_PROXY = found
                log(f"[PROXY-OK] Proxy aktif untuk sesi ini: {SELECTED_PROXY}")
            else:
                log("[WARN] Tidak ada proxy tersedia, fallback ke koneksi langsung.")
                SELECTED_PROXY = None
        else:
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

    # --start-time: tunggu hingga waktu yang ditentukan
    if ARGS.start_time:
        try:
            start_dt = parse_start_time(ARGS.start_time)
            now = now_wita()
            if start_dt <= now:
                log(f"[WARN] --start-time {ARGS.start_time} sudah terlewat ({start_dt.strftime('%H:%M:%S')} WITA). Langsung lanjut.")
            else:
                wait_until_start_time(start_dt)
        except ValueError as e:
            log(f"[ERROR] --start-time: {e}")

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
    pre_parser.add_argument("--start-time", type=str, default=None, dest="start_time")
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
        start_time = pre_args.start_time
        no_save = False
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

    # Beri sedikit waktu agar background thread selesai sebelum program benar-benar mati
    stop_stats_writer()
    time.sleep(2) 
    log("[END] Program selesai.")