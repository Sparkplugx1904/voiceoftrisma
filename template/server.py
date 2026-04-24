import http.server
import socketserver
import threading
import atexit
import subprocess
import re
import socket
import tkinter as tk
from PIL import Image, ImageTk
import qrcode

# ===============================
# BAGIAN 1: Cloudflare Logic
# ===============================
def start_tunnel(port, on_url_update=None):
    print(f"⌛ [Cloudflare] Meminta lorong rahasia untuk port {port}...")
    command = ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{port}"]
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    )
    url_pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")

    def monitor_output():
        for line in iter(process.stdout.readline, ''):
            match = url_pattern.search(line)
            if match:
                url = match.group(0)
                if on_url_update:
                    on_url_update(url)
    
    threading.Thread(target=monitor_output, daemon=True).start()
    return process

# ===============================
# BAGIAN 2: UI QR & Server Logic
# ===============================
def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except: IP = '127.0.0.1'
    finally: s.close()
    return IP

class ServerApp:
    def __init__(self, port):
        self.port = port
        self.root = tk.Tk()
        self.root.title("Server Manager")
        self.root.geometry("350x450")
        
        self.lbl_source = tk.Label(self.root, text="Menyiapkan...", font=("Arial", 10, "bold"))
        self.lbl_source.pack(pady=10)
        self.lbl_qr = tk.Label(self.root)
        self.lbl_qr.pack()
        self.lbl_url = tk.Label(self.root, text="", fg="blue")
        self.lbl_url.pack(pady=10)

    def update_qr(self, url, source):
        self.lbl_source.config(text=source)
        self.lbl_url.config(text=url)
        qr = qrcode.make(url)
        img = ImageTk.PhotoImage(qr.resize((300, 300)))
        self.lbl_qr.config(image=img)
        self.lbl_qr.image = img

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    PORT = 8080
    app = ServerApp(PORT)
    
    # Jalankan HTTP Server
    def run_http():
        with socketserver.TCPServer(("", PORT), http.server.SimpleHTTPRequestHandler) as httpd:
            httpd.serve_forever()
    
    threading.Thread(target=run_http, daemon=True).start()
    
    # Jalankan Cloudflare
    tunnel = start_tunnel(PORT, lambda url: app.update_qr(url, "🌐 Public URL (Cloudflare)"))
    
    # Tampilkan default IP lokal jika belum ada cloudflare
    app.update_qr(f"http://{get_local_ip()}:{PORT}", "📡 Local Network")
    
    atexit.register(tunnel.terminate)
    app.run()