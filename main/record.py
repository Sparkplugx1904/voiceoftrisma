import requests
import urllib3
import socket
import subprocess
import time
import datetime
import signal
import sys
import os
import argparse
from internetarchive import upload
import threading
import re
import shutil

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

def log(msg):
    """Tambahkan timestamp + warna otomatis berdasarkan tag di awal pesan"""
    # ANSI color codes
    RED     = "\033[31m"
    GREEN   = "\033[32m"
    YELLOW  = "\033[33m"
    BLUE    = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN    = "\033[36m"
    WHITE   = "\033[37m"
    RESET   = "\033[0m"

    # Mapping tag -> warna pesan
    TAG_COLORS = {
        # Merah — error / berhenti paksa
        "[ ERROR ]"   : RED,
        "[ FAIL ]"    : RED,
        "[ STOP ]"    : RED,
        "[ CUT-OFF ]" : RED,

        # Kuning — peringatan / kondisi menunggu
        "[ WARN ]"    : YELLOW,
        "[ OFFLINE ]" : YELLOW,
        "[ RETRY ]"   : YELLOW,
        "[ DELAY ]"   : YELLOW,
        "[ WAIT ]"    : YELLOW,

        # Hijau — sukses / selesai
        "[ OK ]"      : GREEN,
        "[ DONE ]"    : GREEN,
        "[ START ]"   : GREEN,
        "[ END ]"     : GREEN,
        "[ ARCHIVE ]" : GREEN,
        "[ LINK ]"    : GREEN,
        "[ ITEM ]"    : GREEN,
        "[ ENV ]"     : GREEN,
        "[ CLEAN ]"   : GREEN,

        # Cyan — info proses aktif
        "[ RUN ]"         : CYAN,
        "[ SKIP ]"        : CYAN,
        "[ CODEC ]"       : CYAN,
        "[ UPLOAD-PREP ]" : CYAN,

        # Magenta — upload / transfer data
        "[ UPLOAD ]"  : MAGENTA,
        "[ MERGE ]"   : MAGENTA,

        # Putih redup — output eksternal / restart
        "[FFMPEG]"    : WHITE,
        "[ RESTART ]" : WHITE,

        # Merah — HTTP / JSON error
        "[ JSON ERR ]": RED,
    }

    # Cari tag di awal pesan (format "[ TAG ]" atau "[TAG]")
    tag_color = BLUE  # default warna jika tag tidak dikenal
    matched_tag = None
    for tag, color in TAG_COLORS.items():
        if msg.startswith(tag):
            tag_color = color
            matched_tag = tag
            break

    # Timestamp tetap biru, tag berwarna, teks setelah tag pakai warna default
    ts = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%H:%M:%S")
    if matched_tag:
        rest = msg[len(matched_tag):]
        print(f"\033[34m[{ts}]\033[0m {tag_color}{matched_tag}{RESET}{rest}", flush=True)
    else:
        print(f"\033[34m[{ts}]\033[0m {msg}", flush=True)

if "--no-upload" not in sys.argv:
    if not MY_ACCESS_KEY or not MY_SECRET_KEY:
        log("[ ERROR ] API key belum diatur. Pastikan MY_ACCESS_KEY dan MY_SECRET_KEY sudah ada di GitHub Secrets.")
        sys.exit(1)


def now_wita():
    """Waktu lokal WITA"""
    return datetime.datetime.now(datetime.UTC).astimezone(WITA_TZ)


def wait_for_stream(url):
    """
    Menunggu stream hingga benar-benar siap dengan mengecek JSON stats.
    URL yang digunakan: https://i.klikhost.com:8502/stats?json=1
    Logic: streamstatus == 1 -> ONLINE, streamstatus == 0 -> OFFLINE
    """
    stats_url = "https://i.klikhost.com:8502/stats?json=1"
    log(f"[ WAIT ] Menunggu siaran dimulai...")

    while True:
        try:
            response = requests.get(stats_url, timeout=5, verify=False)

            if response.status_code == 200:
                data = response.json()
                status = data.get("streamstatus", 0)

                if status == 1:
                    log(f"[ OK ] Siaran terdeteksi! Memulai perekaman...")
                    return
                else:
                    log(f"[ OFFLINE ] Server siap, siaran belum dimulai...")
            else:
                log(f"[ {response.status_code} ] Server tidak merespons dengan benar...")

        except requests.exceptions.RequestException:
            log(f"[ ERROR ] Tidak dapat menjangkau server...")
        except ValueError:
            log(f"[ JSON ERR ] Respons server tidak terbaca...")

        time.sleep(1.5)

# ---------------------
# Helper filename / chunk
# ---------------------
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
        log("[ MERGE ] Tidak ada chunk yang bisa digabung.")
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
        log(f"[ MERGE ] Menggabungkan semua bagian rekaman...")
        subprocess.run(cmd, check=True)

        shutil.move(temp_output, final_output)
        log(f"[ MERGE ] Berhasil digabung → {final_output}")

        for c in chunks:
            try:
                os.remove(c)
                log(f"[ CLEAN ] File sementara dihapus: {c}")
            except Exception as e:
                log(f"[ WARN ] Gagal menghapus file sementara {c}: {e}")

        try:
            os.remove(list_txt)
        except:
            pass

        return final_output

    except subprocess.CalledProcessError as e:
        log(f"[ ERROR ] Penggabungan rekaman gagal: {e}")
        try:
            if os.path.exists(temp_output):
                os.remove(temp_output)
        except:
            pass
        return None


# ---------------------
# core recording flow
# ---------------------
def run_ffmpeg(url, suffix="", position=0, no_upload=False):
    """Rekam stream audio dan upload"""
    date_str = now_wita().strftime("%d-%m-%y")
    os.makedirs("recordings", exist_ok=True)

    try:
        codec = subprocess.check_output([
            "./ffprobe", "-v", "error",
            "-timeout", "10000000",       # ← tambah ini
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_name",
            "-of", "default=nokey=1:noprint_wrappers=1", url
        ], timeout=15).decode().strip()   # ← tambah timeout Python juga
        log(f"[ CODEC ] Format audio: {codec}")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        codec = "mp3"
        log(f"[ CODEC ] Tidak dapat mendeteksi format, menggunakan mp3 sebagai default.")

    ext_map = {"aac": "aac", "mp3": "mp3", "opus": "opus", "vorbis": "ogg"}
    ext = ext_map.get(codec, "bin")

    base_no_ext = make_base_no_ext(date_str, suffix)
    filename = get_next_chunk_filename(base_no_ext, ext)

    cmd = [
        "./ffmpeg", "-y", "-hide_banner",
        "-reconnect", "1", "-reconnect_at_eof", "1", "-reconnect_streamed", "1",
        "-reconnect_delay_max", "0", "-reconnect_on_network_error", "1",
        "-reconnect_on_http_error", "4xx,5xx",
        "-timeout", "5000000",
        "-i", url,
        "-c", "copy",
        "-metadata", f"title=VOT Denpasar {date_str}",
        "-metadata", "artist=VOT Radio Denpasar",
        "-metadata", f"date={date_str}",
        filename
    ]

    log(f"[ RUN ] Rekaman dimulai → {filename}")
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)

    def log_ffmpeg(proc):
        for line in proc.stderr:
            log(f"[FFMPEG] {line.strip()}")

    threading.Thread(target=log_ffmpeg, args=(process,), daemon=True).start()

    cutoff_reached = False
    while True:
        now = now_wita()
        if now.hour == 18 and now.minute >= 30:
            cutoff_reached = True
            log(f"[ CUT-OFF ] Siaran berakhir pukul 18:30 WITA. Menghentikan perekaman...")
            try:
                process.send_signal(signal.SIGINT)
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
            break

        if process.poll() is not None:
                    now_check = now_wita()
                    if now_check.hour >= 18:
                        cutoff_reached = True
                        log("[ FAIL ] Perekaman berhenti — siaran sudah selesai.")
                    else:
                        log("[ FAIL ] Perekaman berhenti tiba-tiba. Akan segera dimulai ulang...")
                    break
        time.sleep(1)

    log(f"[ DONE ] Proses ffmpeg berhenti: {filename}")

    # Merge Logic
    filename_to_upload = filename
    if cutoff_reached:
        log("[ MERGE ] Melakukan merge semua chunk menjadi file base final...")
        merged = merge_chunks_to_base(base_no_ext, ext)
        if merged:
            filename_to_upload = merged
        else:
            log("[ WARN ] Merge gagal, akan upload chunk terakhir sebagai fallback.")

    log(f"[ UPLOAD-PREP ] Siap upload: {filename_to_upload}")

    if no_upload:
        log(f"[ SKIP ] Mode tanpa upload aktif. File tersimpan di {filename_to_upload}")
        return

    if position > 0:
        delay = position * 10
        log(f"[ DELAY ] Menunggu {delay} detik sebelum upload...")
        time.sleep(delay)

    archive_url, item_id = upload_to_archive(filename_to_upload)

    if archive_url and item_id:
        log(f"[ ARCHIVE ] File tersedia di {archive_url}")
        write_env_variables(archive_url, item_id)
    else:
        write_env_variables("None", "None")


def upload_to_archive(file_path, retries=5):
    """Upload file ke archive.org dan hasilkan URL langsung + item_id, retry jika gagal"""
    log(f"[ UPLOAD ] Mulai upload {file_path} ke archive.org...")
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
                access_key=MY_ACCESS_KEY,
                secret_key=MY_SECRET_KEY,
                verbose=True
            )

            details_url = f"https://archive.org/details/{item_identifier}"
            download_url = f"https://archive.org/download/{item_identifier}/{filename}"

            log(f"[ DONE ] Upload berhasil: {details_url}")
            log(f"[ LINK ] URL langsung: {download_url}")
            log(f"[ ITEM ] ID: {item_identifier}")

            return download_url, item_identifier

        except Exception as e:
            log(f"[ WARN ] Upload gagal percobaan {attempt}: {e}")
            if attempt < retries:
                log("[ RETRY ] Menunggu 10 detik sebelum mencoba lagi...")
                time.sleep(10)
            else:
                log("[ ERROR ] Semua percobaan upload gagal.")
                return None, None


def write_env_variables(url, item_id):
    """Kirim ARCHIVE_URL dan ITEM_ID langsung ke environment GitHub"""
    try:
        if "GITHUB_ENV" in os.environ:
            with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as env_file:
                env_file.write(f"ARCHIVE_URL={url}\n")
                env_file.write(f"ITEM_ID={item_id}\n")
                env_file.flush()
                log(f"[ ENV ] ARCHIVE_URL dan ITEM_ID dikirim ke environment GitHub.")
        else:
            log("[ WARN ] GITHUB_ENV tidak tersedia (mungkin bukan di workflow).")
    except Exception as e:
        log(f"[ ERROR ] Gagal menulis environment: {e}")


def main_recording():
    parser = argparse.ArgumentParser(description="Record stream and upload")
    parser.add_argument("-s", "--suffix", type=str, default="", help="Suffix di akhir nama file")
    parser.add_argument("-p", "--position", type=int, default=0, help="Posisi untuk delay upload (delay = position * 10 detik)")
    parser.add_argument("--skip-check", action="store_true", help="Lewati pengecekan stream, langsung mulai rekam")
    parser.add_argument("--no-upload", action="store_true", help="Rekam saja tanpa upload ke archive.org")
    args = parser.parse_args()

    stream_url = "http://i.klikhost.com:8502/stream"
    if args.skip_check:
        log("[ SKIP ] Pengecekan stream dilewati, langsung mulai rekam...")
    else:
        wait_for_stream(stream_url)
    run_ffmpeg(stream_url, args.suffix, args.position, no_upload=args.no_upload)
    log("[ DONE ] Semua tugas selesai.")
    return True


if __name__ == "__main__":
    log("[ START ] Memulai program recording dengan restart otomatis...")

    while True:
        now = now_wita()

        if now.hour >= 18:  # ← ubah dari > 18 ke >= 18
            log(f"[ STOP ] Sudah jam {now.strftime('%H:%M')} WITA, hentikan program.")
            break

        try:
            main_recording()
        except Exception as e:
            log(f"[ ERROR ] Terjadi error: {e}")

        now = now_wita()
        if now.hour >= 18:  # ← ubah dari > 18 ke >= 18
            log(f"[ STOP ] Setelah recording selesai, sudah jam {now.strftime('%H:%M')} WITA, hentikan program.")
            break
        else:
            log("[ RESTART ] Restarting recording loop...")
            continue

    log("[ END ] Program selesai.")