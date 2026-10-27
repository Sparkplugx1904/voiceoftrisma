#!/usr/bin/env python3
"""
VOT Radio Denpasar — Stream Recorder v1.0 (Direct Subprocess & Chunk Merge Architecture).
"""

import os
import re
import sys
import time
import shutil
import signal
import argparse
import subprocess
import threading
from pathlib import Path

# Pastikan folder main ada di sys.path
_MAIN_DIR = Path(__file__).resolve().parent
if str(_MAIN_DIR) not in sys.path:
    sys.path.insert(0, str(_MAIN_DIR))

from core.config import IA_ACCESS_KEY, IA_SECRET_KEY, STREAM_URL, validate_credentials
from core.logger import (
    log, now_wita, print_help_banner, start_stats_writer, init_console, CONSOLE
)
import core.proxy as proxy_module
from core.timing import (
    wait_for_stream, make_base_no_ext, get_next_chunk_filename,
    parse_start_time, wait_until_start_time
)
from core.uploader import upload_to_archive, write_env_variables

ARGS = None


def list_chunks_ordered(base_no_ext, ext):
    dirpath = os.path.dirname(base_no_ext) or "."
    base_name = os.path.basename(base_no_ext)
    pattern = re.compile(r"^" + re.escape(base_name) + r"(?:_(\d+))?\." + re.escape(ext) + r"$")
    final_output_abs = os.path.abspath(f"{base_no_ext}.{ext}")

    files = []
    if os.path.exists(dirpath):
        for f in os.listdir(dirpath):
            if pattern.match(f):
                full = os.path.join(dirpath, f)
                if os.path.abspath(full) == final_output_abs:
                    continue
                try:
                    mtime = os.path.getmtime(full)
                except Exception:
                    mtime = 0
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

    temp_output = f"{base_no_ext}_temp_merged.{ext}"
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

        if keep_raw:
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
                        log(f"[CLEAN] Melewati chunk final: {c}")
                        continue
                    os.remove(c)
                    log(f"[CLEAN] File sementara dihapus: {c}")
                except Exception as e:
                    log(f"[WARN] Gagal menghapus file sementara {c}: {e}")

        try:
            os.remove(list_txt)
        except Exception:
            pass

        return final_output

    except subprocess.CalledProcessError as e:
        log(f"[ERROR] Penggabungan rekaman gagal: {e}")
        if os.path.exists(temp_output):
            try:
                os.remove(temp_output)
            except Exception:
                pass
        return None


def run_ffmpeg(url, suffix="", position=0, no_upload=False):
    """Rekam stream audio v1.0 menggunakan direct ffmpeg subprocess."""
    if ARGS and ARGS.no_record:
        log("[SKIP] --no-record aktif. Perekaman ffmpeg dilewati.")
        return

    date_str = now_wita().strftime("%d-%m-%y")
    os.makedirs("recordings", exist_ok=True)

    if ARGS and ARGS.stream_file_format:
        ext = ARGS.stream_file_format.lower().strip(".")
        log(f"[CODEC] Format file stream (manual): {ext}")
    else:
        try:
            ffprobe_cmd = ["./ffprobe", "-v", "error", "-timeout", "10000000"]
            use_proxy_for_stream = False
            if ARGS:
                if ARGS.proxy or getattr(ARGS, "random_proxy", None) in ["stream", "all"]:
                    use_proxy_for_stream = True

            if proxy_module.SELECTED_PROXY and use_proxy_for_stream:
                px = proxy_module.SELECTED_PROXY if proxy_module.SELECTED_PROXY.startswith("http://") else f"http://{proxy_module.SELECTED_PROXY}"
                ffprobe_cmd.extend(["-http_proxy", px])
                log(f"[PROXY-OK] FFprobe menggunakan HTTP proxy: {px}")

            ffprobe_cmd.extend([
                "-select_streams", "a:0",
                "-show_entries", "stream=codec_name",
                "-of", "default=nokey=1:noprint_wrappers=1", url
            ])
            codec = subprocess.check_output(ffprobe_cmd, timeout=15).decode().strip()
            log(f"[CODEC] Format audio: {codec}")
        except Exception:
            codec = "mp3"
            log("[CODEC] Tidak dapat mendeteksi format, menggunakan mp3 sebagai default.")

        ext_map = {"aac": "aac", "mp3": "mp3", "opus": "opus", "vorbis": "ogg"}
        ext = ext_map.get(codec, "bin")

    if ARGS and ARGS.output_name:
        base_no_ext = f"recordings/{ARGS.output_name}"
    else:
        base_no_ext = make_base_no_ext(date_str, suffix)
    filename = get_next_chunk_filename(base_no_ext, ext)

    cmd = [
        "./ffmpeg", "-y", "-hide_banner",
        "-reconnect", "1", "-reconnect_at_eof", "1", "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5", "-reconnect_on_network_error", "1",
        "-reconnect_on_http_error", "4xx,5xx",
        "-timeout", "15000000",
    ]
    use_proxy_for_stream = False
    if ARGS:
        if ARGS.proxy or getattr(ARGS, "random_proxy", None) in ["stream", "all"]:
            use_proxy_for_stream = True

    if proxy_module.SELECTED_PROXY and use_proxy_for_stream:
        px = proxy_module.SELECTED_PROXY if proxy_module.SELECTED_PROXY.startswith("http://") else f"http://{proxy_module.SELECTED_PROXY}"
        cmd.extend(["-http_proxy", px])
        log(f"[PROXY-OK] FFmpeg menggunakan HTTP proxy: {px}")

    cmd.extend([
        "-i", url,
        "-c", "copy",
        "-metadata", f"title=VOT Denpasar {date_str}",
        "-metadata", "artist=VOT Radio Denpasar",
        "-metadata", f"date={date_str}",
    ])

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
        if not no_timer and now.hour == 18 and now.minute >= 30:
            cutoff_reached = True
            log("[CUT-OFF] Siaran berakhir pukul 18:30 WITA. Menghentikan perekaman...")
            try:
                process.send_signal(signal.SIGINT)
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
            break

        if process.poll() is not None:
            if not no_timer and now_wita().hour >= 18:
                cutoff_reached = True
                log("[FAIL] Perekaman berhenti — siaran sudah selesai.")
            else:
                log("[DONE] Perekaman berhenti.")
            break

        time.sleep(1)

    log(f"[DONE] Proses ffmpeg berhenti: {filename}")

    if ARGS and ARGS.no_save:
        log(f"[SKIP] --no-save aktif. Menghapus file rekaman: {filename}")
        try:
            os.remove(filename)
        except Exception as e:
            log(f"[WARN] Gagal menghapus file: {e}")
        return

    no_merge = ARGS and ARGS.no_merge_chunks
    keep_raw = bool(ARGS and getattr(ARGS, "with_raw", False))
    filename_to_upload = filename

    if cutoff_reached and not no_merge:
        log("[MERGE] Melakukan merge semua chunk menjadi file base final...")
        merged = merge_chunks_to_base(base_no_ext, ext, keep_raw=keep_raw)
        if merged:
            filename_to_upload = merged
        else:
            log("[WARN] Merge gagal, akan upload chunk terakhir sebagai fallback.")

    if not cutoff_reached and not no_timer:
        log(f"[SKIP] Chunk tersimpan: {filename_to_upload} — upload ditunda hingga jam 18:30 WITA.")
        return

    log(f"[UPLOAD-PREP] Siap upload: {filename_to_upload}")
    if no_upload:
        log(f"[SKIP] Mode tanpa upload aktif. File tersimpan di {filename_to_upload}")
        return

    if position > 0:
        delay = position * 10
        log(f"[DELAY] Menunggu {delay} detik sebelum upload...")
        time.sleep(delay)

    archive_url, item_id = upload_to_archive(
        filename_to_upload,
        access_key=getattr(ARGS, "archive_access", None),
        secret_key=getattr(ARGS, "archive_secret", None),
        with_raw=keep_raw
    )

    if archive_url and item_id:
        log(f"[ARCHIVE] File tersedia di {archive_url}")
        write_env_variables(archive_url, item_id)
    else:
        write_env_variables("None", "None")


def main_recording():
    global ARGS
    validate_credentials()

    parser = argparse.ArgumentParser(
        description="VOT Radio Denpasar — Stream Recorder & Uploader v1.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-s", "--suffix", type=str, default="", help="Suffix nama file")
    parser.add_argument("-p", "--position", type=int, default=0, help="Delay upload bertahap")
    parser.add_argument("--skip-check", action="store_true", help="Lewati pengecekan stream")
    parser.add_argument("--no-upload", action="store_true", help="Rekam tanpa upload")
    parser.add_argument("--hide-banner", action="store_true", help="Sembunyikan banner help")
    parser.add_argument("--no-timer-limit", action="store_true", help="Abaikan batasan waktu")
    parser.add_argument("--no-record", action="store_true", help="Skip perekaman ffmpeg")
    parser.add_argument("--no-merge-chunks", action="store_true", help="Skip penggabungan chunk")
    parser.add_argument("--no-log", action="store_true", help="Nonaktifkan log output")
    parser.add_argument("--no-save", action="store_true", help="Hapus file setelah selesai")
    parser.add_argument("--with-raw", action="store_true", dest="with_raw", help="Simpan chunk mentah di raw/")
    parser.add_argument("--archive-access", "--archive-acc", type=str, default=None, dest="archive_access")
    parser.add_argument("--archive-secret", "--archive-sec", type=str, default=None, dest="archive_secret")
    parser.add_argument("--stream-url", type=str, default=None, help="URL stream kustom")
    parser.add_argument("-o", type=str, default=None, dest="output_name", help="Nama output file")
    parser.add_argument("--stream-file-format", type=str, default=None, help="Format file audio")
    parser.add_argument("--duration", type=int, default=None, help="Durasi rekaman dalam detik")
    parser.add_argument("--start-time", type=str, default=None, dest="start_time", help="Waktu mulai WITA")
    parser.add_argument("--proxy", type=str, default=None, help="Manual HTTP proxy")
    parser.add_argument("--random-proxy", type=str, choices=["stats", "stream", "all"])

    ARGS = parser.parse_args()
    init_console(ARGS)

    if not ARGS.hide_banner:
        print_help_banner("v1.0")

    stream_url = ARGS.stream_url if ARGS.stream_url else STREAM_URL
    start_stats_writer()

    if ARGS.random_proxy:
        found = proxy_module.find_working_proxy()
        proxy_module.SELECTED_PROXY = found
    elif ARGS.proxy:
        px = ARGS.proxy.strip()
        for prefix in ("https://", "http://"):
            if px.lower().startswith(prefix):
                px = px[len(prefix):]
                break
        proxy_module.SELECTED_PROXY = px
        log(f"[PROXY-OK] Menggunakan manual proxy: {proxy_module.SELECTED_PROXY}")
    else:
        proxy_module.SELECTED_PROXY = None

    if ARGS.start_time:
        try:
            start_dt = parse_start_time(ARGS.start_time)
            if start_dt > now_wita():
                wait_until_start_time(start_dt)
        except Exception as e:
            log(f"[ERROR] --start-time: {e}")

    if not ARGS.skip_check:
        wait_for_stream(stream_url, ARGS)
    else:
        log("[SKIP] Pengecekan stream dilewati, langsung mulai rekam...")

    run_ffmpeg(stream_url, ARGS.suffix, ARGS.position, no_upload=ARGS.no_upload)
    log("[DONE] Semua tugas selesai.")
    return True


if __name__ == "__main__":
    main_recording()
