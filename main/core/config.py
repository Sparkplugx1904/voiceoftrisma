"""
VoT Core Config — Konfigurasi, Environment, dan Timezone.
"""

import os
import sys
import datetime
from pathlib import Path

# Matikan warning SSL verify=False agar log tidak terganggu
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    pass

# Resolusi path root repositori untuk memuat .ENV
ROOT_DIR = Path(__file__).resolve().parent.parent.parent

try:
    from dotenv import load_dotenv
    for _candidate in [ROOT_DIR / ".ENV", ROOT_DIR / ".env", Path(".ENV"), Path(".env")]:
        if _candidate.is_file():
            load_dotenv(dotenv_path=_candidate)
            break
except ImportError:
    pass

# Zona waktu WITA (UTC+8)
WITA_OFFSET = datetime.timedelta(hours=8)
WITA_TZ = datetime.timezone(WITA_OFFSET)

# Kredensial Archive.org & Stream URL
IA_ACCESS_KEY = os.environ.get("IA_ACCESS_KEY") or os.environ.get("MY_ACCESS_KEY")
IA_SECRET_KEY = os.environ.get("IA_SECRET_KEY") or os.environ.get("MY_SECRET_KEY")
STREAM_URL = os.environ.get("STREAM_URL") or "http://i.klikhost.com:8502/stream"

# File statistik siaran
STATS_FILE = "recordings/stats.json"

TAG_WIDTH = 15
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def validate_credentials(args=None):
    """
    Validasi awal keberadaan IA_ACCESS_KEY dan IA_SECRET_KEY.
    Bisa dilewati bila mode --no-upload aktif atau key diberikan lewat CLI arg.
    """
    if "--no-upload" in sys.argv or (args and getattr(args, "no_upload", False)):
        return True

    has_env_keys = bool(IA_ACCESS_KEY and IA_SECRET_KEY)
    has_cli_keys = False
    if args:
        has_cli_keys = bool(getattr(args, "archive_access", None) and getattr(args, "archive_secret", None))
    else:
        has_cli_keys = any(a in sys.argv for a in [
            "--archive-access", "--archive-acc", "--archive-secret", "--archive-sec"
        ])

    if not has_env_keys and not has_cli_keys:
        print("[ERROR] API key belum diatur. Pastikan IA_ACCESS_KEY dan IA_SECRET_KEY sudah ada di GitHub Secrets atau .ENV.")
        print("[ERROR] Atau gunakan --archive-access dan --archive-secret untuk menyediakan key.")
        sys.exit(1)
    return True
