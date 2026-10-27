# 📖 Buku Panduan & Dokumentasi Arsitektur Voice of Trisma (VOT)

Selamat datang di dokumentasi resmi arsitektur sistem otomatisasi siaran radio, transkripsi AI, dan pengarsipan **Voice of Trisma (VOT) Denpasar**.

Dokumentasi ini disusun secara modular layaknya buku panduan (*handbook*) agar setiap pengembang (*human developer*) dapat memahami, mengoperasikan, dan merawat sistem ini secara mandiri tanpa bergantung pada model AI.

---

## 📚 Daftar Isi Buku Panduan

| Bab | Berkas Dokumen | Penjelasan Singkat |
| :---: | :--- | :--- |
| **01** | [**Arsitektur & Peta Alur Sistem**](01-arsitektur-sistem.md) | Diagram alur end-to-end dari Icecast Klikhost hingga publikasi di Archive.org dan website. |
| **02** | [**Mesin Perekaman Suara**](02-perekaman-suara.md) | Penjelasan mendalam `record_v1.py` vs `record_v2.py`, Subprocess Piping, dan batasan waktu 18:30 WITA. |
| **03** | [**Pipa Transkripsi & AI**](03-transkripsi-dan-ai.md) | Eksekusi Whisper.cpp native (`transcribe.py`), reduksi noise audio, dan pembuatan deskripsi via Gemini AI (`descriptor.py`). |
| **04** | [**Pengunggahan & Pengarsipan S3**](04-archive-org-s3.md) | Mekanisme S3 Archive.org (`archive.py`), kredensial `IA_ACCESS_KEY`/`IA_SECRET_KEY`, dan self-healing `guard.py`. |
| **05** | [**Sistem Proxy & Warm Pool**](05-sistem-proxy.md) | Logika `main/core/proxy.py`, format JSON Array `HTTP_PROXY`, concurrent fetching, dan warm pool rotation. |
| **06** | [**Infrastruktur Cloudflare**](06-cloudflare-worker-dan-pages.md) | Cloudflare Worker router (`/api`, `/stats`, `/archive`, `/metrics`), database D1, dan auto-deploy Pages (`voiceoftrisma.pages.dev`). |
| **07** | [**Panduan Pengembang (Local Runbook)**](07-panduan-developer.md) | Cara setup awal di komputer lokal (Windows/Linux), konfigurasi `.ENV`, uji coba tanpa upload, dan pemecahan masalah (*troubleshooting*). |

---

## 🧭 Prinsip Desain Proyek
1. **DRY (Don't Repeat Yourself)**: Seluruh fungsi bersama (log, uploader, proxy, timing, timezone) dipusatkan di dalam paket `main/core/`.
2. **Deterministic & Portable**: Menjalankan skrip Python dari root atau subfolder mana pun akan selalu memuat berkas konfigurasi `.ENV` di root secara otomatis.
3. **Resilience (Tahan Banting)**: Perekaman memiliki auto-reconnect, rotasi proxy otomatis, dan fail-safe bila jaringan terputus di tengah siaran.
