"""
VoT Core Uploader — Pengunggahan Rekaman & Metadata ke Archive.org S3 API.
"""

import os
import time
from typing import Optional, Tuple
from internetarchive import upload

from core.config import IA_ACCESS_KEY, IA_SECRET_KEY, STATS_FILE
from core.logger import log, now_wita

def upload_to_archive(
    file_path: str,
    access_key: Optional[str] = None,
    secret_key: Optional[str] = None,
    retries: int = 5,
    with_raw: bool = False
) -> Tuple[Optional[str], Optional[str]]:
    """
    Unggah file rekaman ke Archive.org.
    Menyertakan stats.json dan folder raw/ jika tersedia.
    """
    acc_key = access_key or IA_ACCESS_KEY
    sec_key = secret_key or IA_SECRET_KEY

    if not acc_key or not sec_key:
        log("[ERROR] IA_ACCESS_KEY atau IA_SECRET_KEY tidak tersedia untuk upload.")
        return None, None

    log(f"[UPLOAD] Mulai upload {file_path} ke archive.org...")
    item_identifier = f"vot-denpasar-{now_wita().strftime('%Y%m%d-%H%M%S')}"
    filename = os.path.basename(file_path)

    files_to_upload = {filename: file_path}

    # Sertakan stats.json jika ada
    if os.path.exists(STATS_FILE):
        files_to_upload[os.path.basename(STATS_FILE)] = STATS_FILE
        log("[STATS] stats.json akan ikut diupload bersama rekaman.")

    # Sertakan chunk mentah dari folder raw/ jika ada
    if with_raw:
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
                    "mediatype": "audio",
                    "title": filename,
                    "creator": "VOT Radio Denpasar"
                },
                access_key=acc_key,
                secret_key=sec_key,
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

    return None, None

def write_env_variables(url: str, item_id: str):
    """Kirim ARCHIVE_URL dan ITEM_ID langsung ke environment GitHub Actions."""
    try:
        github_env = os.environ.get("GITHUB_ENV")
        if github_env:
            with open(github_env, "a", encoding="utf-8") as f:
                f.write(f"ARCHIVE_URL={url}\n")
                f.write(f"ITEM_ID={item_id}\n")
                f.flush()
            log("[ENV] ARCHIVE_URL dan ITEM_ID dikirim ke environment GitHub.")
        else:
            log("[WARN] GITHUB_ENV tidak tersedia (mungkin bukan di workflow Actions).")
    except Exception as e:
        log(f"[ERROR] Gagal menulis environment: {e}")
