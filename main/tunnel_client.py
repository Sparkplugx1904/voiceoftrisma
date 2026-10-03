#!/usr/bin/env python3
"""
tunnel_client.py — Klien WebSocket Tunnel & Log Relay untuk Runner VM.

Fungsi:
  1. Menghasilkan ID unik acak per-VM (misal: vm-a4e92b1c).
  2. Terhubung ke WebSocket Tunnel Hub Cloudflare Worker secara real-time.
  3. Mengalirkan semua baris log terminal dari VM ke Cloudflare Worker.
  4. Mengirimkan heartbeat status berkala (standby, recording, uploaded, dll).
  5. Fallback otomatis ke HTTP POST /tunnel/push bila WebSocket terputus.
  6. Memeriksa keberadaan runner kembar via Cloudflare Worker (tanpa GitHub API).
"""

import base64
import json
import os
import queue
import secrets
import socket
import ssl
import sys
import threading
import time
import urllib.parse

try:
    import requests
except ImportError:
    requests = None

# Instance ID acak unik per runtime VM ini
_INSTANCE_ID = f"vm-{secrets.token_hex(4)}"

_client_instance = None
_lock = threading.Lock()


def get_instance_id() -> str:
    """Kembalikan ID unik per VM ini."""
    return _INSTANCE_ID


# ── RFC 6455 Minimal WebSocket Client (Murni Library Standar Python) ─────────

def _make_ws_frame(data_or_text, opcode: int = 0x1) -> bytes:
    """Buat frame WebSocket RFC 6455 dengan masking client."""
    if isinstance(data_or_text, str):
        data = data_or_text.encode("utf-8")
    else:
        data = bytes(data_or_text)
    length = len(data)
    mask = os.urandom(4)
    first_byte = 0x80 | (opcode & 0x0F)
    if length <= 125:
        header = bytes([first_byte, 0x80 | length])
    elif length <= 65535:
        header = bytes([first_byte, 0x80 | 126]) + length.to_bytes(2, "big")
    else:
        header = bytes([first_byte, 0x80 | 127]) + length.to_bytes(8, "big")
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return header + mask + masked


class TunnelClient:
    """Klien tunnel background yang mengalirkan log ke Cloudflare Worker."""

    def __init__(self, relay_url: str, instance_id: str, rantai_id: str,
                 nomor: int, boot_epoch: float = 0.0):
        self.relay_url = relay_url.rstrip("/")
        self.instance_id = instance_id
        self.rantai_id = rantai_id
        self.nomor = int(nomor)
        self.boot_epoch = boot_epoch or time.time()
        self.status = "standby"

        self.log_queue = queue.Queue(maxsize=1000)
        self.is_running = True
        self.ws_connected = False
        self.sock = None

        # Parse host & path untuk WebSocket
        p = urllib.parse.urlparse(self.relay_url)
        self.host = p.hostname or "voiceoftrisma.anandapradnyana68.workers.dev"
        self.port = p.port or (443 if p.scheme == "https" else 80)
        self.is_ssl = p.scheme in ("https", "wss")

        # Query param WebSocket
        query = urllib.parse.urlencode({
            "role": "runner",
            "instance_id": self.instance_id,
            "rantai_id": str(self.rantai_id),
            "nomor": str(self.nomor),
            "boot_epoch": str(int(self.boot_epoch)),
        })
        self.ws_path = f"/tunnel/ws?{query}"

        # Mulai worker thread background
        self.worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        self.worker_thread.start()

    def push_log(self, line: str):
        """Kirim satu baris log ke antrean tunnel."""
        if not self.is_running:
            return
        try:
            self.log_queue.put_nowait(line)
        except queue.Full:
            # Jika antrean penuh, buang yang tertua
            try:
                self.log_queue.get_nowait()
                self.log_queue.put_nowait(line)
            except Exception:
                pass

    def set_status(self, new_status: str):
        """Ubah status operasional runner (misal: standby, recording, uploaded)."""
        self.status = new_status
        if self.ws_connected and self.sock:
            try:
                frame = _make_ws_frame(json.dumps({"type": "status", "status": new_status}))
                self.sock.sendall(frame)
            except Exception:
                pass

    def _connect_ws(self) -> bool:
        """Koneksi dan handshake WebSocket RFC 6455 via raw socket + TLS."""
        try:
            raw_sock = socket.create_connection((self.host, self.port), timeout=10)
            if self.is_ssl:
                ctx = ssl.create_default_context()
                self.sock = ctx.wrap_socket(raw_sock, server_hostname=self.host)
            else:
                self.sock = raw_sock

            # Handshake HTTP Upgrade
            sec_key = base64.b64encode(os.urandom(16)).decode()
            req = (
                f"GET {self.ws_path} HTTP/1.1\r\n"
                f"Host: {self.host}\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {sec_key}\r\n"
                f"Sec-WebSocket-Version: 13\r\n"
                f"User-Agent: voiceoftrisma-tunnel/{self.instance_id}\r\n\r\n"
            )
            self.sock.sendall(req.encode("utf-8"))

            # Baca response header
            resp = b""
            while b"\r\n\r\n" not in resp:
                chunk = self.sock.recv(1024)
                if not chunk:
                    break
                resp += chunk

            if b"101 " in resp or b"Switching Protocols" in resp:
                self.ws_connected = True
                self.sock.settimeout(20.0)
                # Jalankan pembaca frame di background thread agar PING dibalas PONG
                threading.Thread(target=self._read_loop, args=(self.sock,), daemon=True).start()
                return True
            else:
                self._close_sock()
                return False
        except Exception:
            self._close_sock()
            return False

    def _read_loop(self, sock):
        """Baca frame masuk dari server: tangani PING -> balas PONG."""
        while self.is_running and self.ws_connected and self.sock is sock:
            try:
                head = sock.recv(2)
                if not head or len(head) < 2:
                    break
                opcode = head[0] & 0x0F
                has_mask = bool(head[1] & 0x80)
                length = head[1] & 0x7F
                if length == 126:
                    ext = sock.recv(2)
                    length = int.from_bytes(ext, "big")
                elif length == 127:
                    ext = sock.recv(8)
                    length = int.from_bytes(ext, "big")
                mask = sock.recv(4) if has_mask else None
                payload = b""
                while len(payload) < length:
                    chunk = sock.recv(min(length - len(payload), 4096))
                    if not chunk:
                        break
                    payload += chunk
                if mask:
                    payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

                if opcode == 0x9:  # PING
                    # Balas PONG segera dengan payload yang sama
                    pong = _make_ws_frame(payload, opcode=0x0A)
                    sock.sendall(pong)
                elif opcode == 0x8:  # CLOSE
                    break
            except (socket.timeout, TimeoutError):
                continue
            except Exception:
                break
        if self.sock is sock:
            self._close_sock()

    def _close_sock(self):
        self.ws_connected = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def _run_loop(self):
        """Loop utama background: tangani koneksi WS, pengiriman log, dan heartbeat."""
        last_heartbeat = 0
        backoff = 3

        while self.is_running:
            now = time.time()

            # 1. Pastikan WebSocket terhubung
            if not self.ws_connected:
                ok = self._connect_ws()
                if not ok:
                    # Fallback kirim antrean via HTTP POST jika WS gagal
                    self._flush_via_http()
                    time.sleep(backoff)
                    backoff = min(backoff * 1.5, 30)
                    continue
                else:
                    backoff = 3

            # 2. Kirim Heartbeat setiap 10 detik
            if now - last_heartbeat >= 10:
                last_heartbeat = now
                try:
                    hb = json.dumps({
                        "type": "heartbeat",
                        "instance_id": self.instance_id,
                        "status": self.status,
                        "uptime": int(now - self.boot_epoch),
                    })
                    self.sock.sendall(_make_ws_frame(hb))
                except Exception:
                    self._close_sock()
                    continue

            # 3. Kirim antrean log
            try:
                # Tunggu maksimal 1 detik agar heartbeat tetap jalan
                line = self.log_queue.get(timeout=1.0)
                msg = json.dumps({"type": "log", "msg": line})
                self.sock.sendall(_make_ws_frame(msg))
            except queue.Empty:
                pass
            except Exception:
                self._close_sock()

    def _flush_via_http(self):
        """Kirim tumpukan log ke HTTP POST /tunnel/push sebagai fallback."""
        if not requests:
            return
        batch = []
        while not self.log_queue.empty() and len(batch) < 50:
            try:
                batch.append(self.log_queue.get_nowait())
            except Exception:
                break
        if not batch:
            return
        url = f"{self.relay_url}/tunnel/push"
        payload = {
            "instance_id": self.instance_id,
            "rantai_id": self.rantai_id,
            "nomor": self.nomor,
            "status": self.status,
            "logs": batch,
        }
        try:
            requests.post(url, json=payload, headers={"User-Agent": "voiceoftrisma-tunnel/3.0"}, timeout=5)
        except Exception:
            # Kembalikan ke antrean jika gagal
            for item in reversed(batch):
                try:
                    self.log_queue.put_nowait(item)
                except Exception:
                    pass

    def stop(self):
        """Hentikan tunnel client secara bersih."""
        self.is_running = False
        # Kirim status disconnected sebelum tutup
        if self.ws_connected and self.sock:
            try:
                msg = json.dumps({"type": "status", "status": "disconnected"})
                self.sock.sendall(_make_ws_frame(msg))
            except Exception:
                pass
        self._close_sock()


# ── Global Functions ─────────────────────────────────────────────────────────

def init_tunnel(relay_url: str, instance_id: str, rantai_id: str,
                nomor: int, boot_epoch: float = 0.0) -> TunnelClient:
    """Inisialisasi tunnel singleton untuk proses ini."""
    global _client_instance
    with _lock:
        if _client_instance is None:
            _client_instance = TunnelClient(relay_url, instance_id, rantai_id, nomor, boot_epoch)
        return _client_instance


def push_tunnel_log(line: str):
    """Kirim log ke tunnel jika sudah diinisialisasi."""
    if _client_instance:
        _client_instance.push_log(line)


def set_tunnel_status(status: str):
    """Perbarui status runner ke tunnel."""
    if _client_instance:
        _client_instance.set_status(status)


def cek_runner_kembar_via_worker(relay_url: str, rantai_id: str, nomor: int,
                                instance_id: str) -> bool:
    """Cek apakah ada runner bernomor sama yang aktif di Cloudflare Worker.

    Menggantikan panggilan GitHub API yang sering 401 karena token expired.
    Kembalikan True bila kembar aktif ditemukan.
    """
    if not requests:
        return False
    url = f"{relay_url.rstrip('/')}/tunnel/active"
    params = {
        "rantai_id": str(rantai_id),
        "nomor": str(nomor),
        "exclude_instance": str(instance_id),
    }
    try:
        r = requests.get(url, params=params, headers={"User-Agent": "voiceoftrisma-tunnel/3.0"}, timeout=5)
        if r.status_code == 200:
            data = r.json()
            if data.get("active"):
                vm = data.get("active_vm") or {}
                print(f"[KEMBAR WORKER] Ditemukan VM kembar aktif: {vm.get('instance_id')} "
                      f"(idle {vm.get('idle_seconds', 0)}d). Mundur.", flush=True)
                return True
        return False
    except Exception as e:
        print(f"[WARN] Gagal cek kembar via Cloudflare Worker ({type(e).__name__}): lanjut.", flush=True)
        return False
