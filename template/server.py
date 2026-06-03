import os
import sys

# =====================================================================
# PENGAMAN 1: Kunci direktori kerja ke tempat skrip ini berada SEJAK AWAL
# =====================================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(SCRIPT_DIR)

import time
import socket
import threading
import http.server
import socketserver
import subprocess
import re
import html
import urllib.parse
import tkinter as tk
from PIL import Image, ImageTk
import qrcode
from http.server import ThreadingHTTPServer

# =====================================================================
# PENGAMAN 2 & UI: Custom Handler Tampilan Folder Google Material Design 3
# =====================================================================
class LockedDirectoryHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=SCRIPT_DIR, **kwargs)

    def list_directory(self, path):
        """Mengubah tampilan folder menjadi komponen Material Design 3 yang ramah Android."""
        try:
            dir_list = os.listdir(path)
        except os.error:
            self.send_error(http.HTTPStatus.NOT_FOUND, "Akses direktori ditolak")
            return None
        
        dir_list.sort(key=lambda a: (not os.path.isdir(os.path.join(path, a)), a.lower()))
        displaypath = html.escape(urllib.parse.unquote(self.path, errors='surrogatepass'))
        
        r = []
        enc = sys.getfilesystemencoding()
        title = f"Berkas di {displaypath}"
        
        r.append('<!DOCTYPE html>')
        r.append('<html lang="id">')
        r.append('<head>')
        r.append('  <meta charset="UTF-8">')
        r.append('  <meta name="viewport" content="width=device-width, initial-scale=1.0">')
        r.append(f'  <title>{title}</title>')
        # Menggunakan Tailwind CSS untuk mempermudah pewarnaan & komponen Material 3
        r.append('  <script src="https://cdn.tailwindcss.com"></script>')
        # Google Fonts (Roboto) & Google Material Icons (Font icon resmi Android)
        r.append('  <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">')
        r.append('  <link href="https://fonts.googleapis.com/icon?family=Material+Icons+Outlined" rel="stylesheet">')
        r.append('  <style>')
        r.append('    body { font-family: "Roboto", sans-serif; -webkit-tap-highlight-color: transparent; }')
        r.append('  </style>')
        r.append('</head>')
        r.append('<body class="bg-[#f8f9ff] text-[#1a1c1e] min-h-screen pb-12">') # Warna bg Material M3 Light
        
        # Top App Bar (Material 3 Style)
        r.append('  <div class="sticky top-0 z-50 bg-[#fffbfe] border-b border-[#e1e2ec] px-4 py-4 shadow-sm flex items-center space-x-4">')
        if displaypath != '/':
            r.append('    <a href="../" class="w-12 h-12 rounded-full flex items-center justify-center text-[#435e91] hover:bg-[#e1e2ec] active:bg-[#c4d6ff] transition-all">')
            r.append('      <span class="material-icons-outlined text-2xl">arrow_back</span>')
            r.append('    </a>')
        else:
            r.append('    <div class="w-12 h-12 rounded-full bg-[#dfe2eb] flex items-center justify-center text-[#435e91]">')
            r.append('      <span class="material-icons-outlined text-2xl">folder_shared</span>')
            r.append('    </div>')
        r.append('    <div class="flex-1 min-w-0">')
        r.append(f'     <h1 class="text-xl font-medium tracking-tight text-[#1a1c1e] truncate">{displaypath if displaypath != "/" else "Drive Utama"}</h1>')
        r.append('    </div>')
        r.append('  </div>')
        
        # Main Content Area
        r.append('  <div class="max-w-md mx-auto px-4 mt-6">')
        r.append('    <div class="text-xs font-medium text-[#435e91] mb-3 px-1 uppercase tracking-wider">Semua Berkas & Folder</div>')
        r.append('    <div class="space-y-2">') # Jarak antar kartu berkas
        
        if not dir_list and displaypath == '/':
            r.append('      <div class="bg-white rounded-2xl border border-[#e1e2ec] p-8 text-center text-[#435e91]">')
            r.append('        <span class="material-icons-outlined text-4xl mb-2 text-[#74777f]">folder_off</span>')
            r.append('        <p class="text-sm font-medium">Tidak ada berkas di dalam folder ini</p>')
            r.append('      </div>')

        # Iterasi Berkas & Folder
        for name in dir_list:
            fullname = os.path.join(path, name)
            displayname = linkname = name
            
            if os.path.isdir(fullname):
                displayname = name + "/"
                linkname = name + "/"
                icon = "folder"
                icon_bg = "bg-[#fef3c7]"      # Warna latar folder (kuning lembut)
                icon_color = "text-[#d97706]" # Warna ikon folder
                size_text = "Folder"
            else:
                icon = "description"
                icon_bg = "bg-[#e0f2fe]"      # Default file (biru lembut)
                icon_color = "text-[#0284c7]"
                
                ext = os.path.splitext(name)[1].lower()
                if ext in ['.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp']:
                    icon = "image"
                    icon_bg = "bg-[#f3e8ff]"
                    icon_color = "text-[#9333ea]"
                elif ext in ['.mp4', '.mkv', '.avi', '.mov']:
                    icon = "movie"
                    icon_bg = "bg-[#ffe4e6]"
                    icon_color = "text-[#e11d48]"
                elif ext in ['.mp3', '.wav', '.flac']:
                    icon = "audiotrack"
                    icon_bg = "bg-[#dcfce7]"
                    icon_color = "text-[#16a34a]"
                elif ext in ['.zip', '.rar', '.7z', '.tar', '.gz']:
                    icon = "inventory_2"
                    icon_bg = "bg-[#ffedd5]"
                    icon_color = "text-[#ea580c]"
                
                try:
                    size = os.path.getsize(fullname)
                    if size < 1024: size_text = f"{size} B"
                    elif size < 1048576: size_text = f"{size/1024:.1f} KB"
                    else: size_text = f"{size/1048576:.1f} MB"
                except OSError:
                    size_text = "Berkas"

            url_encoded = urllib.parse.quote(linkname, errors="surrogatepass")
            
            # Kartu Elemen List (Sekali Ketuk/Tap Langsung Membuka atau Mengunduh)
            r.append(f'    <a href="{url_encoded}" {"download" if icon != "folder" else ""} class="flex items-center p-3 bg-[#ffffff] rounded-2xl border border-[#e1e2ec] hover:bg-[#f3f4f9] active:scale-[0.98] active:bg-[#e1e2ec] transition-all shadow-sm group">')
            # Bagian Kiri: Lingkaran Ikon (Touch target besar)
            r.append(f'      <div class="w-12 h-12 rounded-xl {icon_bg} flex items-center justify-center {icon_color} flex-shrink-0">')
            r.append(f'        <span class="material-icons-outlined text-2xl">{icon}</span>')
            r.append('      </div>')
            # Bagian Tengah: Nama File dan Ukuran
            r.append('      <div class="flex-1 min-w-0 ml-4 pr-2">')
            r.append(f'        <div class="text-sm font-medium text-[#1a1c1e] truncate group-hover:text-[#435e91]">{html.escape(displayname)}</div>')
            r.append(f'        <div class="text-xs text-[#74777f] font-medium mt-0.5">{size_text}</div>')
            r.append('      </div>')
            # Bagian Kanan: Petunjuk Aksi Tersirat Android (Chevron / Panah halus)
            r.append('      <div class="text-[#c4c6cf] flex-shrink-0">')
            r.append(f'        <span class="material-icons-outlined text-xl">{"chevron_right" if icon == "folder" else "file_download"}</span>')
            r.append('      </div>')
            r.append('    </a>')
            
        r.append('    </div>')
        r.append('  </div>')
        r.append('</body>')
        r.append('</html>')
        
        encoded = '\n'.join(r).encode(enc, 'surrogateescape')
        f = io.BytesIO() if 'io' in locals() else __import__('io').BytesIO()
        f.write(encoded)
        f.seek(0)
        self.send_response(http.HTTPStatus.OK)
        self.send_header("Content-type", f"text/html; charset={enc}")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        return f

# =====================================================================
# BAGIAN 1: Cloudflare Tunnel Logic
# =====================================================================
def start_tunnel(port, on_url_update=None):
    print(f"⌛ [Cloudflare] Meminta lorong rahasia untuk port {port}...")
    command = ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{port}"]
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        creationflags=creationflags
    )
    url_pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
    def monitor_output():
        try:
            for line in iter(process.stdout.readline, ''):
                match = url_pattern.search(line)
                if match:
                    url = match.group(0)
                    if on_url_update:
                        threading.Timer(1.5, lambda: on_url_update(url)).start()
        except Exception:
            pass
    threading.Thread(target=monitor_output, daemon=True).start()
    return process

# =====================================================================
# BAGIAN 2: Utilitas Jaringan & Antarmuka QR (Tkinter)
# =====================================================================
def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()

def run_http_server(host: str, port: int):
    socketserver.TCPServer.allow_reuse_address = True
    srv = ThreadingHTTPServer((host, port), LockedDirectoryHandler)
    print(f"  [Server] Listening on http://{host}:{port}", flush=True)
    try:
        srv.serve_forever()
    except Exception:
        pass

class QRPopupApp:
    def __init__(self, port: int, url_state: dict, tunnel_process):
        self.port = port
        self.url_state = url_state
        self.tunnel = tunnel_process
        self._last_shown = ""
        self._tk_img = None

        self.root = tk.Tk()
        self.root.title("Server Manager — QR Access")
        self.root.geometry("380x500")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="white")

        self.root.bind("<Escape>", self.shutdown)
        self.root.bind("<Control-c>", self.shutdown)
        self.root.protocol("WM_DELETE_WINDOW", self.shutdown)

        tk.Label(self.root, text="SERVER TERMINAL", font=("Arial", 14, "bold"), bg="white", fg="#222").pack(pady=(14, 2))
        self.lbl_status = tk.Label(self.root, text="Menghubungkan ke Cloudflare...", font=("Arial", 9, "bold"), bg="white", fg="#888")
        self.lbl_status.pack(pady=(2, 8))

        self.lbl_qr = tk.Label(self.root, bg="white", text="...", font=("Arial", 10))
        self.lbl_qr.pack()

        self.lbl_url = tk.Label(self.root, text="", font=("Courier", 9, "bold"), bg="white", fg="blue", wraplength=340, cursor="hand2")
        self.lbl_url.pack(pady=(8, 2))
        self.lbl_url.bind("<Button-1>", self._copy_to_clipboard)

        self.lbl_local = tk.Label(self.root, text="", font=("Courier", 8), bg="white", fg="#888", wraplength=340)
        self.lbl_local.pack(pady=(0, 4))

        tk.Label(self.root, text="(klik URL untuk menyalin / Esc untuk keluar)", font=("Arial", 8), bg="white", fg="#aaa").pack()

        self._render(f"http://{get_local_ip()}:{port}", "Jaringan Lokal (Cloudflare menghubungkan...)", "#666")
        self._poll()

    def _copy_to_clipboard(self, _evt=None):
        self.root.clipboard_clear()
        self.root.clipboard_append(self._last_shown)
        old = self.lbl_status.cget("text")
        old_color = self.lbl_status.cget("fg")
        self.lbl_status.config(text="URL tersalin ke clipboard!", fg="#0066cc")
        self.root.after(2000, lambda: self.lbl_status.config(text=old, fg=old_color))

    def _render(self, url: str, status_text: str, status_color: str):
        if url == self._last_shown:
            return
        self._last_shown = url
        self.lbl_status.config(text=status_text, fg=status_color)
        self.lbl_url.config(text=url, fg=status_color)
        
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img = img.resize((280, 280), Image.Resampling.LANCZOS)
        
        self._tk_img = ImageTk.PhotoImage(img)
        self.lbl_qr.config(image=self._tk_img, text="")
        self.lbl_local.config(text=f"Lokal: http://{get_local_ip()}:{self.port}")

    def _poll(self):
        with self.url_state["lock"]:
            pub = self.url_state["value"]
        if pub:
            self._render(pub, "Public URL (Cloudflare) - ONLINE", "#0066cc")
        self.root.after(1000, self._poll)

    def shutdown(self, event=None):
        print("\n💥 [Opsi Nuklir] Menghancurkan seluruh proses server seketika...")
        if self.tunnel:
            try:
                if os.name == 'nt':
                    subprocess.run(f"taskkill /F /T /PID {self.tunnel.pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    self.tunnel.kill()
            except Exception:
                pass
        os._exit(0)

    def run(self):
        self.root.mainloop()

# =====================================================================
# BAGIAN 3: Main Execution Loop
# =====================================================================
def main():
    host = "127.0.0.1"
    port = int(os.environ.get("PORT", "8080"))

    url_state = {"value": None, "lock": threading.Lock()}

    def on_public_url(url: str):
        with url_state["lock"]:
            url_state["value"] = url
        print(f"\n========================================================")
        print(f"  SERVER TEMPLATE ONLINE DI INTERNET")
        print(f"========================================================")
        print(f"  URL Publik : {url}")
        print(f"  URL Lokal  : http://{get_local_ip()}:{port}")
        print(f"========================================================")
        print(f"  Tekan Ctrl+C di terminal atau ESC di UI untuk keluar.")
        print(f"========================================================\n", flush=True)

    print(f"========================================================")
    print(f"  SERVER STANDALONE + CLOUDFLARE TUNNEL")
    print(f"========================================================")
    print(f"  Folder Terkunci : {SCRIPT_DIR}")
    print(f"  Local IP        : {get_local_ip()}")
    print(f"  Port Server     : {port}")
    print(f"========================================================", flush=True)

    server_thread = threading.Thread(target=run_http_server, args=(host, port), daemon=True)
    server_thread.start()
    time.sleep(0.5)

    tunnel = start_tunnel(port=port, on_url_update=on_public_url)

    app = QRPopupApp(port, url_state, tunnel)
    app.run()

if __name__ == "__main__":
    main()