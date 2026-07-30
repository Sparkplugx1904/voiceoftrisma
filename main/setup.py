#!/usr/bin/env python3
"""
Voice of Trisma — Setup utility.

Menggantikan baris-baris curl/pip/apt di GitHub Actions workflows
dengan satu perintah Python.

Usage:
  python setup.py record              # Setup environment recording (default: record_v1.0.py)
  python setup.py record record_v2.0.py  # Setup dengan script kustom
  python setup.py transcript           # Setup environment transkripsi
  python setup.py hybrid               # Setup record + transkripsi (default: record_v1.0.py)
  python setup.py hybrid record_v2.0.py  # Hybrid dengan script kustom
"""

import argparse
import os
import shutil
import stat
import subprocess
import sys
import time
import urllib.request
from urllib.error import HTTPError, URLError

RAW_BASE = "https://raw.githubusercontent.com/Sparkplugx1904/voiceoftrisma/main"
GH_BASE = "https://github.com/Sparkplugx1904/voiceoftrisma/raw/refs/heads/main"


# ── helpers ────────────────────────────────────────────────────────────────


def _format_size(n_bytes: int) -> str:
    """Format bytes → KB / MB / GB."""
    for unit in ("B", "KB", "MB", "GB"):
        if n_bytes < 1024:
            return f"{n_bytes:.1f}{unit}"
        n_bytes /= 1024
    return f"{n_bytes:.1f}TB"


def _progress_hook(label: str):
    """
    Return a reporthook closure untuk urlretrieve.

    Render::
        ↓ filename  ████████░░░░░░░░ 50%
    """
    last_pct = [0]

    def hook(block_num: int, block_size: int, total_size: int):
        if total_size <= 0:
            return
        pct = int(block_num * block_size * 100 / total_size)
        if pct == last_pct[0]:
            return
        last_pct[0] = pct
        filled = pct * 16 // 100
        bar = "█" * filled + "░" * (16 - filled)
        print(f"\r  ↓  {label}  {bar} {pct}%", end="", flush=True)
        if pct >= 100:
            print()

    return hook


def download(url: str, dest: str, max_retries: int = 3) -> None:
    """
    Download *url* ke *dest* dengan retry, progress bar, dan validasi.

    Raises RuntimeError jika gagal setelah semua percobaan atau file kosong.
    """
    for attempt in range(1, max_retries + 1):
        try:
            urllib.request.urlretrieve(url, dest, _progress_hook(dest))

            # Validasi file hasil download
            if not os.path.exists(dest):
                raise RuntimeError("file tidak ditemukan setelah download")
            size = os.path.getsize(dest)
            if size == 0:
                raise RuntimeError("file kosong (0 B) — kemungkinan gagal download")

            print(f"  ✓  {dest} ({_format_size(size)})")
            return

        except HTTPError as exc:
            reason = f"HTTP {exc.code} {exc.reason}"
            print(f"  ⚠  download {dest} attempt {attempt}/{max_retries} — {reason}")
        except URLError as exc:
            reason = f"Network error: {exc.reason}"
            print(f"  ⚠  download {dest} attempt {attempt}/{max_retries} — {reason}")
        except Exception as exc:
            reason = getattr(exc, "reason", str(exc))
            print(f"  ⚠  download {dest} attempt {attempt}/{max_retries} — {reason}")

        if attempt < max_retries:
            time.sleep(2)

    raise RuntimeError(
        f"Gagal download {dest} setelah {max_retries}× percobaan.\n"
        f"  URL: {url}\n"
        f"  Cek koneksi internet atau coba manual."
    )


def run(args: list[str], **kwargs):
    """Print dan jalankan command; exit pada failure."""
    print(f"  $  {' '.join(args)}")
    subprocess.check_call(args, **kwargs)


# ── record ─────────────────────────────────────────────────────────────────


def setup_record(script: str):
    """
    Setup untuk job "Record Stream and Upload".

    Download script rekaman, ffmpeg, ffprobe, requirements.txt,
    chmod +x, pip install.

    Semua download punya retry + validasi otomatis.
    Jika gagal → pesan jelas, bukan traceback mentah.
    """
    print(f"\n── Setup record ({script}) ──\n")

    # ── Download yang dibutuhkan ──
    # (record script di-download ke CWD, ffmpeg/ffprobe dari rilis — repo checkout sudah punya requirements/)
    try:
        download(f"{RAW_BASE}/main/{script}", script)
        download(f"{GH_BASE}/bin/ffmpeg", "ffmpeg")
        download(f"{GH_BASE}/bin/ffprobe", "ffprobe")
    except RuntimeError as exc:
        print(f"\n❌  Record setup gagal: {exc}")
        print("  ·  Job tidak bisa lanjut tanpa file-file ini.")
        print("  ·  Coba: pastikan koneksi internet stabil, lalu jalankan ulang.\n")
        sys.exit(1)

    # ── chmod binaries ──
    for bin_name in ("ffmpeg", "ffprobe"):
        if os.path.exists(bin_name):
            os.chmod(bin_name, os.stat(bin_name).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
            print(f"  ✓  chmod +x {bin_name}")

    # ── pip install (dari requirements/ di checkout repo) ──
    try:
        run([sys.executable, "-m", "pip", "install", "-r", "requirements/record.txt"])
    except subprocess.CalledProcessError:
        print("\n❌  pip install gagal.")
        print("  ·  Coba manual: pip install -r requirements/record.txt\n")
        sys.exit(1)

    print(f"\n✓  Setup record ({script}) selesai.\n")


# ── ffmpeg installer (menggantikan 3x Setup FFmpeg di YAML) ────────────────


def install_ffmpeg(max_retries: int = 3, timeout: int = 120):
    """
    Install FFmpeg via apt dengan retry & timeout handling.

    Mirip behavior 3x FedericoCarboni/setup-ffmpeg@v3 + continue-on-error:
      - Cek ffmpeg di PATH
      - Install via apt-get
      - Retry max_retries kali jika gagal/timeout
      - Jika tetap gagal → warning + lanjut (tidak exit)
    """
    if shutil.which("ffmpeg"):
        print("  ·  ffmpeg already available in PATH")
        return True

    for attempt in range(1, max_retries + 1):
        print(f"  ·  ffmpeg install attempt {attempt}/{max_retries} ...")
        try:
            subprocess.run(
                ["sudo", "apt-get", "update", "-qq"],
                timeout=timeout, check=False, capture_output=True,
            )
            subprocess.run(
                ["sudo", "apt-get", "install", "-y", "--no-install-recommends", "ffmpeg"],
                timeout=timeout, check=True, capture_output=True,
            )
            if shutil.which("ffmpeg"):
                print("  ✓  ffmpeg installed successfully")
                return True
        except subprocess.TimeoutExpired:
            print(f"  ⚠  ffmpeg attempt {attempt} timed out (> {timeout}s)")
        except subprocess.CalledProcessError as exc:
            print(f"  ⚠  ffmpeg attempt {attempt} failed (exit {exc.returncode})")

        if attempt < max_retries:
            time.sleep(3)

    print("  ⚠  ffmpeg installation failed after all retries — continuing anyway")
    return False


# ── transcript ─────────────────────────────────────────────────────────────

WHISPER_BINS = [
    "bench", "command", "lsp", "main", "quantize", "stream",
    "test-vad", "test-vad-full", "vad-speech-segments", "wchess",
    "whisper-bench", "whisper-cli", "whisper-command", "whisper-server",
    "whisper-stream", "whisper-talk-llama",
]


def setup_transcript():
    """
    Setup untuk job "Transcribe and Push to Archive".

    Install FFmpeg (retry 3x, continue-on-error),
    chmod +x binary Whisper-CPP di bin/,
    pip install dependencies transkripsi,
    sudo apt install -y internetarchive.
    """
    print("\n── Setup transcript ──\n")

    # 1. FFmpeg (retry 3x, continue-on-error)
    install_ffmpeg()

    # 2. chmod +x semua binary Whisper-CPP di bin/
    for binfile in WHISPER_BINS:
        dest = os.path.join("bin", binfile)
        if os.path.exists(dest):
            os.chmod(dest, os.stat(dest).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        else:
            print(f"  ·  {dest} not found, skipping.")

    # 3. pip install dependencies untuk transkripsi
    req = os.path.join(os.path.dirname(__file__), "..", "requirements", "transcript.txt")
    if os.path.exists(req):
        run([sys.executable, "-m", "pip", "install", "-r", req])
    else:
        print(f"  ·  {req} not found, skipping pip install.")

    # 4. Install archive tool
    run(["sudo", "apt", "install", "-y", "internetarchive"])

    print("\n✓  Setup transcript selesai.\n")


# ── CLI ────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="VoT setup utility — install dependencies untuk recording / transkripsi.",
    )
    parser.add_argument(
        "mode",
        choices=["record", "transcript", "hybrid"],
        help="Mode: 'record' (rekam), 'transcript' (transkripsi), atau 'hybrid' (keduanya)",
    )
    parser.add_argument(
        "record_script",
        nargs="?",
        default="record_v1.0.py",
        help="Nama file script record (default: record_v1.0.py). Contoh: record_v2.0.py",
    )

    args = parser.parse_args()

    if args.mode == "record":
        setup_record(args.record_script)
    elif args.mode == "transcript":
        setup_transcript()
    elif args.mode == "hybrid":
        setup_record(args.record_script)
        setup_transcript()


if __name__ == "__main__":
    main()
