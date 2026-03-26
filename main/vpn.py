"""
vpn.py — Standalone VPN checker (wrapper)
==========================================
Jalankan langsung:  python vpn.py
Atau dari record.py: vpn_check() dipanggil otomatis sebelum wait_for_stream().
"""

import sys
import os

# Tambahkan parent dir ke path agar bisa import record
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from record import vpn_check, log

if __name__ == "__main__":
    log("[START] VPN Auto-Connect — Cloudflare WARP (standalone)")
    vpn_check()
    log("[DONE] VPN check selesai.")