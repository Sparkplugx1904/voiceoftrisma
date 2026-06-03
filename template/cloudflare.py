import subprocess
import threading
import re
import os

def start_tunnel(port, on_url_update=None):
    """
    Menjalankan Cloudflare Quick Tunnel dengan isolasi proses.
    """
    print(f"⌛ [Cloudflare] Meminta lorong rahasia untuk port {port}...")

    command = ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{port}"]

    # Mencegah cloudflared ikut menerima sinyal Ctrl+C dari terminal utama
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        creationflags=creationflags
    )

    url_pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")

    def monitor_output():
        try:
            for line in iter(process.stdout.readline, ''):
                # Opsional: Jika ingin melihat log asli cloudflared, uncoment baris di bawah ini
                # print(f"[Cloudflare Log] {line.strip()}")
                
                match = url_pattern.search(line)
                if match:
                    url = match.group(0)
                    
                    # Beri jeda 1.5 detik agar DNS Cloudflare benar-benar siap secara global
                    if on_url_update:
                        threading.Timer(1.5, lambda: on_url_update(url)).start()
        except Exception:
            pass

    monitor_thread = threading.Thread(target=monitor_output, daemon=True)
    monitor_thread.start()

    return process