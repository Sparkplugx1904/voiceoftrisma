# 02 — Mesin Perekaman Suara

Dokumen ini membedah arsitektur perekam suara otomatis Voice of Trisma, perbedaan mendasar antara **v1.0** dan **v2.0**, argumen CLI yang didukung, serta logika *cut-off* waktu siaran.

---

## ⚖️ Perbandingan Arsitektur: v1.0 vs v2.0

Kedua script menggunakan modul bersama di `main/core/`, tetapi memiliki perbedaan mendasar pada cara berkomunikasi dengan FFmpeg:

| Parameter | Versi 1.0 (`record_v1.py`) | Versi 2.0 (`record_v2.py`) |
| :--- | :--- | :--- |
| **Arsitektur Utama** | **Direct FFmpeg Subprocess + Chunk Concat** | **Python Subprocess Piping (Stdin Pipe)** |
| **Penanganan Disconnect** | Jika koneksi putus, proses FFmpeg mati $\rightarrow$ membuka proses FFmpeg baru $\rightarrow$ menghasilkan chunk `_1.mp3`, `_2.mp3`. | Python membaca audio via `requests` lalu menyalurkan byte ke `process.stdin` FFmpeg yang terus hidup. |
| **Proses Penggabungan** | Memerlukan tahap **Merge Concat** di akhir siaran untuk menggabungkan seluruh chunk. | **Tidak ada merge sama sekali**. File yang dihasilkan dari awal sudah utuh 1 berkas. |
| **Kelebihan** | Sederhana, mengandalkan koneksi native internal FFmpeg. | Sangat tahan banting terhadap *micro-drop* koneksi; tidak ada jeda pemotongan audio. |
| **Status di Produksi** | Engine Cadangan (*Fallback Redundancy*). | **Engine Utama (*Primary Recorder*)**. |

---

## 🛠️ Argumen Baris Perintah (CLI Options)

Kedua script dapat dijalankan dengan berbagai flag parameter:

```bash
python main/record_v2.py [OPTIONS]
```

### Tabel Parameter

| Parameter | Tipe Data | Nilai Default | Penjelasan |
| :--- | :---: | :---: | :--- |
| **`-s, --suffix`** | `str` | `""` | Menambahkan teks akhiran di akhir nama file rekaman (contoh: `-s s1p1`). |
| **`-p, --position`** | `int` | `0` | Delay waktu upload bertahap (`position * 10 detik`) untuk menghindari benturan upload jika berjalan multi-job. |
| **`--skip-check`** | `flag` | `False` | Melewati tahap tunggu siaran (*wait for stream*); perekaman langsung dimulai seketika. Cocok untuk pengujian lokal. |
| **`--no-upload`** | `flag` | `False` | Hanya merekam dan menyimpan audio di lokal, **tanpa** mengunggah ke Archive.org. |
| **`--no-record`** | `flag` | `False` | Melewati proses FFmpeg. Hanya menjalankan alur verifikasi koneksi dan proxy. |
| **`--duration`** | `int` | `None` | Membatasi durasi rekaman dalam satuan detik (contoh: `--duration 60` untuk merekam 1 menit). |
| **`--no-timer-limit`**| `flag` | `False` | Mengabaikan batasan cut-off waktu 18:30 WITA. Rekaman akan terus berjalan hingga dihentikan manual atau durasi habis. |
| **`--stream-url`** | `str` | Dari `.ENV` | Menentukan URL stream audio khusus selain default Klikhost. |
| **`-o, --output-name`**| `str` | `None` | Menentukan nama file keluaran khusus di folder `recordings/`. |
| **`--proxy`** | `str` | `None` | Menggunakan proxy manual tertentu (contoh: `--proxy 103.48.71.6:8080`). |
| **`--random-proxy`** | `choice` | `None` | Memilih mode auto-proxy pool (`stats`, `stream`, atau `all`). |
| **`--start-time`** | `str` | `None` | Menunggu hingga waktu WITA tertentu sebelum mulai (format: `hh`, `hh:mm`, `hh:mm:ss`). |
| **`--no-log`** | `flag` | `False` | Menonaktifkan seluruh pencetakan log di terminal (*silent mode*). |
| **`--no-save`** | `flag` | `False` | Menghapus berkas rekaman segera setelah selesai dieksekusi. |
| **`--no-merge-chunks`**| `flag` | `False` | *(Hanya di v1.0)* Melewati proses concat merge chunk di akhir siaran. |
| **`--with-raw`** | `flag` | `False` | *(Hanya di v1.0)* Menyimpan chunk mentah di subfolder `recordings/raw/`. |

---

## ⏰ Batasan Waktu & Aturan Cut-off (18:30 WITA)

* Siaran radio reguler VOT Denpasar berakhir pukul **18:30 WITA**.
* Script recorder memiliki pengecekan internal setiap detik:
  ```python
  now = now_wita()
  if not no_timer and now.hour == 18 and now.minute >= 30:
      log("[CUT-OFF] Siaran berakhir pukul 18:30 WITA. Menghentikan perekaman...")
      break
  ```
* Saat jam mencapai 18:30 WITA:
  1. Stream pipe ke FFmpeg ditutup secara teratur (*graceful close*).
  2. FFmpeg menyelesaikan penulisan header MP3 (*muxing*).
  3. Berkas rekaman diverifikasi.
  4. Pengunggahan ke Archive.org dimulai.

---

## ⌨️ Perintah Interaktif (Interactive Console)

Saat script berjalan di terminal yang mendukung input TTY (misalnya di komputer lokal):
* Tekan **`/skip-stage`** lalu **Enter**: Langsung melompati tahap yang sedang aktif (misalnya melompati tahap tunggu siaran atau menghentikan perekaman di tengah jalan secara aman).
* Tekan **`/help`**: Menampilkan daftar bantuan perintah terminal langsung.
* Input pengguna tidak akan merusak tampilan log, karena sistem memiliki *buffer lock* di `main/core/logger.py`.
