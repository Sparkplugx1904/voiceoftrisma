"""
VoT Core Logger — Format warna ANSI, Server Logging, Interactive Console, & Stats Writer.
"""

import os
import sys
import time
import json
import queue
import signal
import random
import datetime
import threading
from typing import Optional

from core.config import WITA_TZ, TAG_WIDTH, STATS_FILE

# =============================================
# True Color ANSI (24-bit RGB)
# =============================================
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
    DIM        = "\033[2m"
else:
    TIME_COLOR = INFO_COLOR = WARN_COLOR = ERR_COLOR = UP_COLOR = TEXT_COLOR = RESET = BOLD = DIM = ""

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
    "[POOL]"        : "\033[38;2;56;189;166m",
    "[STATS]"       : "\033[38;2;56;189;166m",
}

def now_wita():
    """Waktu lokal WITA (UTC+8)."""
    return datetime.datetime.now(datetime.timezone.utc).astimezone(WITA_TZ)

# =============================================
# Randomized Headers
# =============================================
_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

_SESSION_HEADERS = {
    "User-Agent": random.choice(_UA_POOL),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

def get_headers():
    """Kembalikan headers session saat ini."""
    return _SESSION_HEADERS.copy()

# =============================================
# Interactive Console
# =============================================
class InteractiveConsole:
    def __init__(self):
        self._typing = threading.Event()
        self._buffer = queue.Queue()
        self.skip_stage = threading.Event()
        self._running = True
        self._lock = threading.Lock()

    def start(self):
        t = threading.Thread(target=self._input_loop, daemon=True, name="interactive-console")
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
                    if ch == "\x08":
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
                else:
                    time.sleep(0.05)
            except Exception:
                time.sleep(0.1)

    def _unix_loop(self):
        import select, termios, tty
        try:
            old_settings = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())
        except Exception:
            return
        buf = ""
        while self._running:
            try:
                r, _, _ = select.select([sys.stdin], [], [], 0.05)
                if r:
                    ch = sys.stdin.read(1)
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
                break
        try:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        except Exception:
            pass

    def _handle_command(self, cmd):
        log(f"[CMD] Input user: {cmd}")
        if cmd == "/skip-stage":
            log("[WARN] /skip-stage dipicu — melompati tahap saat ini.")
            self.skip_stage.set()
        elif cmd == "/help":
            print(f"\n  {INFO_COLOR}/help{RESET}           Tampilkan perintah")
            print(f"  {WARN_COLOR}/skip-stage{RESET}     Skip tahap yang sedang berjalan (wait/record)\n", flush=True)
        else:
            log(f"[WARN] Perintah tidak dikenal: {cmd}")

CONSOLE: Optional[InteractiveConsole] = None
GLOBAL_ARGS = None

def init_console(args=None):
    global CONSOLE, GLOBAL_ARGS
    GLOBAL_ARGS = args
    if sys.stdin.isatty() and not CONSOLE:
        try:
            CONSOLE = InteractiveConsole()
            CONSOLE.start()
        except Exception:
            CONSOLE = None

def log(msg: str):
    """Verbose Server Log dengan format warna ANSI dan timestamp WITA."""
    if GLOBAL_ARGS and getattr(GLOBAL_ARGS, "no_log", False):
        return

    matched_tag = None
    tag_color = INFO_COLOR
    for tag, color in TAG_COLORS.items():
        if msg.startswith(tag):
            matched_tag = tag
            tag_color = color
            break

    ts = now_wita().strftime("%Y-%m-%d %H:%M:%S")

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

    if CONSOLE and CONSOLE.is_typing():
        CONSOLE.buffer_log(line)
    else:
        print(line, flush=True)

# =============================================
# Background Stats Writer
# =============================================
_stats_queue = queue.Queue()
_stats_writer_stop = threading.Event()

def _stats_writer_loop():
    os.makedirs("recordings", exist_ok=True)
    _BATCH_SIZE = 10
    _FLUSH_EVERY = 5.0

    with open(STATS_FILE, "a", encoding="utf-8") as f:
        batch = []
        last_flush = time.monotonic()

        while not _stats_writer_stop.is_set() or not _stats_queue.empty():
            try:
                entry = _stats_queue.get(timeout=1)
            except queue.Empty:
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
                pass
            finally:
                _stats_queue.task_done()

            if len(batch) >= _BATCH_SIZE or (time.monotonic() - last_flush) >= _FLUSH_EVERY:
                try:
                    f.writelines(batch)
                    f.flush()
                except Exception:
                    pass
                batch.clear()
                last_flush = time.monotonic()

        if batch:
            try:
                f.writelines(batch)
                f.flush()
            except Exception:
                pass

def start_stats_writer():
    _stats_writer_stop.clear()
    t = threading.Thread(target=_stats_writer_loop, daemon=True, name="stats-writer")
    t.start()

def stop_stats_writer():
    _stats_writer_stop.set()

def record_stats_entry(entry: dict):
    _stats_queue.put(entry)

def print_help_banner(version: str = "v2.0"):
    banner = f"""
{BOLD}{INFO_COLOR}================================================================={RESET}
{BOLD}{INFO_COLOR}🎙️  VOT Radio Denpasar — Stream Recorder & Uploader {version}{RESET}
{TIME_COLOR}─────────────────────────────────────────────────────────────────{RESET}
{BOLD}{TEXT_COLOR}Penggunaan:{RESET}
  python main/record_{version}.py [options]

{BOLD}{INFO_COLOR}Opsi Umum:{RESET}
  {WARN_COLOR}-s, --suffix{RESET} {TEXT_COLOR}<str>{RESET}            Suffix pada nama file hasil rekaman
  {WARN_COLOR}-p, --position{RESET} {TEXT_COLOR}<int>{RESET}          Delay upload bertahap (position * 10 detik)
  {WARN_COLOR}--skip-check{RESET}                  Lewati verifikasi status siaran (langsung rekam)
  {WARN_COLOR}--no-upload{RESET}                   Hanya rekam audio tanpa upload ke Archive.org
  {WARN_COLOR}--duration{RESET} {TEXT_COLOR}<sec>{RESET}             Batasi durasi rekaman dalam detik
  {WARN_COLOR}--random-proxy{RESET} {TEXT_COLOR}<mode>{RESET}        Aktifkan proxy otomatis (stats | stream | all)
{BOLD}{INFO_COLOR}================================================================={RESET}
"""
    print(banner, flush=True)
