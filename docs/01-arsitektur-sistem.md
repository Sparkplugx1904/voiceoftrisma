# 01 — Arsitektur & Peta Alur Sistem

Dokumen ini menjelaskan arsitektur menyeluruh dari sistem Voice of Trisma (VOT), bagaimana data audio mengalir dari radio siaran langsung hingga tersimpan sebagai arsip digital publik.

---

## 🗺️ Diagram Alur Menyeluruh (End-to-End Pipeline)

```mermaid
flowchart TD
    subgraph Stream_Source ["1. Sumber Siaran"]
        Icecast["Server Icecast/Shoutcast Klikhost\n(http://i.klikhost.com:8502/stream)"]
    end

    subgraph Trigger ["2. Penjadwalan & Pemicu"]
        CFCron["Cloudflare Worker Cron\n(Setiap 07:00 UTC / ~15:00 WITA)"]
        GHA["GitHub Actions Workflows\n(main+transcript_v1 / v2)"]
    end

    subgraph Core_Pipeline ["3. Pipeline Eksekusi Runner"]
        Recorder["Mesin Perekam (record_v2.py / record_v1.py)\n- Deteksi status siaran via polling\n- FFmpeg capture audio feed (.mp3)\n- Cutoff pukul 18:30 WITA"]
        Whisper["Transkripsi AI (transcribe.py)\n- Whisper.cpp native engine\n- Hasil: transcript.srt & transcript.txt"]
        Gemini["AI Descriptor (descriptor.py)\n- Gemini API memproses transkrip\n- Hasil: description.json (judul & topik)"]
    end

    subgraph Storage ["4. Penyimpanan Permanen"]
        ArchiveOrg["Archive.org S3 Storage\n- Item: vot-denpasar-YYYYMMDD-HHMMSS\n- File: .mp3, .srt, description.json, stats.json"]
    end

    subgraph Presentation ["5. Presentasi & Akses Publik"]
        CFWorker["Cloudflare Worker (voiceoftrisma)\n- /stats : Proxy CORS live status\n- /archive : Cache katalog arsip\n- /api : Kelola jadwal siaran (D1)"]
        PagesWeb["Web Player Publik (PWA)\n- https://voiceoftrisma.pages.dev/\n- https://voiceoftrisma.github.io/"]
    end

    CFCron -->|Dispatch API| GHA
    GHA --> Recorder
    Icecast -->|Stream Audio Feed| Recorder
    Recorder -->|Output File MP3| Storage
    Recorder -->|Trigger| Whisper
    Whisper -->|Hasil SRT| Gemini
    Gemini -->|Upload Metadata & Transkrip via archive.py| ArchiveOrg
    ArchiveOrg -->|Data Sync| CFWorker
    CFWorker --> PagesWeb
```

---

## 🧩 Komponen-Komponen Utama

### 1. Sumber Audio Siaran (Klikhost Icecast)
* **Endpoint Audio**: `http://i.klikhost.com:8502/stream`
* **Endpoint Status**: `http://i.klikhost.com:8502/stats?json=1`
* Server streaming berbasis Shoutcast/Icecast yang memancarkan siaran radio secara analog/digital.

### 2. Orkestrasi Pemicu (Cloudflare Cron Trigger)
* Setiap hari pukul **07:00 UTC** (~15:00 WITA), Worker `voiceoftrisma` mengeksekusi modul `workflow.ts` untuk memicu (*dispatch*) alur kerja GitHub Actions:
  * `main+transcript_v1.0.yml` (Engine Chunking - Cadangan)
  * `main+transcript_v2.0.yml` (Engine Stdin Piping - Utama)

### 3. Mesin Perekam (`main/record_v1.py` & `main/record_v2.py`)
* Menggunakan paket modular `main/core/`.
* Polling status siaran adaptif (3 detik hingga 90 detik).
* Menghentikan rekaman secara otomatis tepat pukul **18:30 WITA**.

### 4. Transkripsi AI (`main/transcribe.py`)
* Menjalankan biner native `whisper-cli` berarsitektur `x86-64`.
* Mengubah rekaman audio menjadi teks berwaktu (*timestamped subtitle*) berformat `.srt` dan `.txt`.

### 5. AI Descriptor (`main/descriptor.py`)
* Membaca berkas `.srt`, lalu mengirimkannya ke Google Gemini API.
* Gemini menghasilkan ringkasan siaran, topik pembicaraan, dan metadata dalam bentuk `description.json`.

### 6. Pengarsipan S3 Archive.org (`main/archive.py`)
* Menggunakan library `internetarchive` dengan kredensial S3 `IA_ACCESS_KEY` dan `IA_SECRET_KEY`.
* Membuat item baru di Archive.org dengan format identitas:
  `vot-denpasar-YYYYMMDD-HHMMSS`

### 7. Distribusi Web & CDN (Cloudflare Worker & Pages)
* **Cloudflare Worker** bertindak sebagai backend API, proxy CORS, penyimpan jadwal di Cloudflare D1, dan cache katalog rekaman.
* **Cloudflare Pages** (`voiceoftrisma.pages.dev`) dan **GitHub Pages** (`voiceoftrisma.github.io`) menyajikan antarmuka pengguna berbasis web statis dan Progressive Web App (PWA).
