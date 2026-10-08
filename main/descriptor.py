import os
import re
import sys
import time
import json
import argparse
from typing import List, Optional
from pydantic import BaseModel
from google import genai
from google.genai import types
from google.genai.errors import APIError

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

MIN_PERFORMANCE_VERSION = (2, 5)  # Hanya model Flash modern (>= 2.5), tolak model low-perform (1.5, 1.0, lite)

def parse_model_score(name: str) -> tuple:
    """
    Ekstrak versi numerik dari nama model.
    Hanya model 'flash' yang akan dipertimbangkan.
    Contoh:
      'gemini-3.8-flash' -> (3, 8)
      'gemini-3.0-flash' -> (3, 0)
      'gemini-2.5-flash' -> (2, 5)
    """
    match = re.search(r'gemini-(\d+)(?:\.(\d+))?-flash', name.lower())
    if match:
        major = int(match.group(1))
        minor = int(match.group(2)) if match.group(2) else 0
        return (major, minor)
    return (-1, -1)


def get_high_performance_flash_models(client, preferred_model: Optional[str] = None) -> List[str]:
    """
    Ambil daftar model Gemini Flash dengan performa tinggi (>= versi 2.5).
    Model berkinerja rendah (1.5, 1.0, lite, nano) dan eksperimental difilter keluar.
    """
    models = []
    if preferred_model:
        clean_name = preferred_model.split("/", 1)[1] if preferred_model.startswith("models/") else preferred_model
        models.append(clean_name)

    try:
        available_models = list(client.models.list())
        valid_models = []
        for m in available_models:
            n = m.name.lower()
            # Tolak model low-perform, non-text, atau preview/exp
            if any(x in n for x in ['exp', 'lite', 'nano', 'preview', 'tts', 'audio', 'vision', 'embedding']):
                continue
            
            score = parse_model_score(m.name)
            # Filter ketat: Hanya model high-performance (>= 2.5)
            if score >= MIN_PERFORMANCE_VERSION:
                clean_name = m.name.split("/", 1)[1] if m.name.startswith("models/") else m.name
                if clean_name not in valid_models:
                    valid_models.append(clean_name)

        if valid_models:
            valid_models.sort(key=parse_model_score, reverse=True)
            for vm in valid_models:
                if vm not in models:
                    models.append(vm)
            print(f"[INFO] Model high-performance terdeteksi: {models}")
            return models

    except Exception as e:
        print(f"[WARNING] Gagal mengambil daftar model dinamis: {e}")

    # Fallback default: hanya model high-performance yang terbukti tangguh
    default_fallbacks = ["gemini-3.0-flash", "gemini-2.5-flash"]
    for fb in default_fallbacks:
        if fb not in models:
            models.append(fb)
    print(f"[INFO] Menggunakan model high-performance fallback: {models}")
    return models


def is_transient_error(err: Exception) -> bool:
    """Cek apakah error bersifat sementara (503, 429, 500, overload, timeout)."""
    err_str = str(err).lower()
    if isinstance(err, APIError):
        if err.code in [503, 429, 500, 502, 504]:
            return True
        if err.status in ['UNAVAILABLE', 'RESOURCE_EXHAUSTED', 'INTERNAL']:
            return True
    transient_indicators = [
        '503', '429', 'unavailable', 'resource_exhausted',
        'high demand', 'spikes in demand', 'temporarily unavailable',
        'try again later', 'rate limit', 'overloaded', 'timeout', '502', '504'
    ]
    return any(ind in err_str for ind in transient_indicators)


def resolve_output_path(output_arg: str) -> str:
    """
    -o selalu diperlakukan sebagai path file tujuan secara langsung.
    Direktori induknya dibuat otomatis jika belum ada.
    """
    output_dir = os.path.dirname(output_arg)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    return output_arg


def generate_description(
    srt_file_path: str,
    output_arg: str,
    model_arg: Optional[str] = None,
    max_retries: int = 4,
    initial_delay: float = 5.0
):
    if not GEMINI_API_KEY:
        print("[ERROR] GEMINI_API_KEY tidak ditemukan di environment variable!")
        sys.exit(1)

    client = genai.Client(api_key=GEMINI_API_KEY)

    try:
        with open(srt_file_path, 'r', encoding='utf-8') as f:
            transcript_content = f.read()
    except Exception as e:
        print(f"[ERROR] Gagal membaca file '{srt_file_path}': {e}")
        sys.exit(1)

    # Schema Pydantic
    class Program(BaseModel):
        program: str
        announcer: str
        timestamp: str
        topic: str
        description: str

    class SiaranRadio(BaseModel):
        title: str
        description: str
        programs: List[Program]

    sys_instruct = """
    
    ATURAN MUTLAK PENGGUNAAN TRANSKRIP & KOREKSI TYPO:
1. SUMBER FAKTA TUNGGAL: Anda DILARANG KERAS menggunakan pengetahuan eksternal atau mengarang informasi (seperti kota, frekuensi radio, tahun berdiri, dll). Anda HANYA boleh merangkum fakta, nama, tempat, dan topik yang ada di dalam transkrip.
2. KONTEKS JANGKAR (ANCHOR): Ketahuilah bahwa transkrip ini berasal dari stasiun radio sekolah bernama "Voice of Trisma" (atau VOT Radio), yang merupakan milik SMA Negeri 3 Denpasar, Bali. 
3. TOLERANSI TYPO (KOREKSI FONETIK): Transkrip ini dihasilkan oleh mesin (Speech-to-Text), sehingga pasti banyak kata yang salah dengar (typo). Anda WAJIB memperbaiki ejaan kata yang terdengar aneh berdasarkan konteks dan bunyi fonetiknya, TANPA merubah fakta.
   - Contoh Koreksi Benar: Jika tertulis "Fois of trisma" atau "Voice of karisma", perbaiki menjadi "Voice of Trisma".
   - Contoh Koreksi Benar: Jika tertulis "SMA negeri tiga dan pasar", perbaiki menjadi "SMA Negeri 3 Denpasar".
   - Contoh Koreksi Benar: Jika tertulis "lagu dari bi ti es", perbaiki menjadi "lagu dari BTS".
   - Contoh Koreksi Salah (DILARANG): Jika transkrip tidak menyebutkan frekuensi, lalu Anda menambahkan "mengudara di 106.3 FM" (Ini halusinasi).
4. NAMA ORANG: Jika ada nama penyiar atau narasumber yang ejaannya terdengar aneh (misal: "bersama saya Kadek dan Eka"), tulis saja ejaan yang paling masuk akal dalam bahasa Indonesia/Bali tanpa perlu memikirkan ejaan pastinya.
    
    PERSONALITAS DAN PERAN:
Anda adalah arsiparis digital untuk siaran radio. Tugas Anda adalah membaca
transkrip SRT dari rekaman siaran, lalu mengekstraknya menjadi JSON metadata
yang komprehensif dan terstruktur.

FORMAT JUDUL:
Deteksi nama program siaran dan tanggal siaran secara otomatis dari isi transkrip.
Format: <Nama Siaran> Edisi: <DD MMMM YYYY>
Nama siaran diambil dari konten transkrip, bukan diasumsikan atau dikarang.
Jika nama siaran tidak disebutkan secara eksplisit, gunakan nama paling relevan
berdasarkan konteks. Contoh pola (bukan nilai tetap):
  "Voice of Trisma Edisi: 07 Maret 2026"
  "Radio Kampus XYZ Edisi: 15 April 2025"

ATURAN KONSOLIDASI PROGRAM (WAJIB DIPATUHI):
PRINSIP UTAMA:
Setiap nama program siaran HANYA boleh muncul SATU KALI di dalam array 'programs',
tidak peduli seberapa banyak sub-topik, konten, atau jeda yang ada di dalamnya.

CARA MENENTUKAN BATAS PROGRAM BARU:
[OK] Program BARU dimulai jika: nama segmen berubah ATAU penyiar secara eksplisit
  menutup satu program dan membuka program lain yang berbeda nama.
[LARANGAN] Jeda musik, pergantian sub-topik, atau selang waktu di tengah segmen BUKAN
  pertanda program baru, itu masih bagian dari program yang sama.
[LARANGAN] DILARANG membuat dua objek dengan nama 'program' yang identik.
[LARANGAN] DILARANG memecah satu program hanya karena isinya banyak atau topiknya beragam.
  Seluruh konten wajib digabung ke SATU objek.

CARA MENGGABUNGKAN KONTEN:
- Field 'timestamp'   : waktu PERTAMA kali program tersebut dibuka.
- Field 'topic'       : rangkuman singkat dari seluruh sub-topik yang dibahas.
- Field 'description' : memuat SEMUA detail tanpa terkecuali, setiap judul karya,
  nama entitas, poin informasi yang ada di transkrip untuk segmen itu.

CONTOH POLA (menggunakan nama program generik):
  SALAH : [{program: "Program A", topic: "Bagian 1"},
            {program: "Program A", topic: "Bagian 2"}]
  BENAR : [{program: "Program A", topic: "Bagian 1 dan Bagian 2",
            description: "... semua konten dari Bagian 1 dan Bagian 2 ..."}]

LOGIKA IDENTIFIKASI PENYIAR & TIMESTAMP:
1. Identifikasi Retrospektif:
   Jika penyiar belum menyebutkan namanya di awal segmen, pindai seluruh percakapan
   dalam segmen tersebut hingga nama ditemukan. Gunakan nama yang teridentifikasi.
   JANGAN isi 'Tidak Disebutkan' selama nama masih bisa dipastikan dari konteks.

2. Penentuan Timestamp Mulai:
   Ambil timestamp tepat saat penyiar pertama kali menyapa pendengar atau membuka
   segmen secara verbal. Jika penyiar langsung berbicara tanpa intro pembuka,
   gunakan timestamp baris pertama segmen tersebut.
   Catatan: beberapa penyiar mungkin memiliki pola pembuka yang konsisten antar
   episode (misalnya selalu membuka di rentang waktu yang sama). Gunakan pola itu
   sebagai petunjuk, namun tetap prioritaskan deteksi dari isi transkrip.

3. Persistensi Nama:
   Sekali penyiar teridentifikasi dalam satu segmen, nama itu berlaku untuk seluruh
   durasi segmen tersebut, meskipun ada jeda atau pergantian sub-topik.

GAYA PENULISAN (field 'description' level atas / root):
Tulis 1-2 kalimat sinopsis naratif yang RINGKAS, HANGAT, dan MENGALIR,
seperti keterangan episode di platform podcast atau streaming, bukan laporan.

ATURAN GAYA:
[OK] Gunakan sudut pandang 'insider' siaran. Sesuaikan frasa pembuka dengan nama
  siaran yang terdeteksi dari transkrip. Contoh pola:
  -> "[Nama Siaran] edisi [tanggal] hadir menemani dengan ..."
  -> "Perjalanan siaran kali ini dibuka dengan ... sebelum berlanjut ke ..."
  -> "... sebelum akhirnya ditutup dengan ..."
[OK] Ceritakan alur TRANSISI antar segmen secara natural, pembaca harus merasakan
  perjalanan siaran dari awal hingga akhir.
[OK] Variasi konten (musik, info, ulasan, dll.) cukup untuk membangun daya tarik.
[LARANGAN] "Siaran ini didominasi oleh...", "Program ini terdiri dari...",
  atau kalimat kaku bergaya laporan penelitian.
[LARANGAN] Ajakan langsung seperti "Mari dengarkan" atau "Yuk simak".

CONTOH POLA OUTPUT (nama siaran menyesuaikan transkrip, ini hanya ilustrasi):
"[Nama Siaran] edisi [tanggal] hadir menemani dengan [gambaran segmen pertama].
Perjalanan siaran berlanjut dengan [segmen berikutnya], sebelum akhirnya ditutup
oleh [segmen penutup]."

DESKRIPSI SEGMEN (field 'description' di dalam programs):
- Padat namun lengkap, cantumkan SEMUA entitas utama dari transkrip segmen itu:
  judul karya, nama orang, poin informasi penting.
- Ubah gaya bicara lisan menjadi narasi tertulis yang elegan.

KONTROL KUALITAS UMUM:
- Gunakan Bahasa Indonesia baku (EYD), lugas, dan hidup.
- DILARANG menambahkan detail yang tidak ada di transkrip (tidak boleh mengarang
  genre, tahun rilis, atau fakta eksternal lainnya).
- DILARANG menghilangkan entitas apapun yang disebutkan di dalam transkrip."""

    print(f"Sedang memproses transkrip ({len(transcript_content)} karakter)...")

    gen_config = types.GenerateContentConfig(
        system_instruction=sys_instruct,
        response_mime_type="application/json",
        response_schema=SiaranRadio,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    candidate_models = get_high_performance_flash_models(client, preferred_model=model_arg)
    output_file_path = resolve_output_path(output_arg)
    success = False

    for model_name in candidate_models:
        print(f"\n[INFO] Mencoba model high-performance: {model_name}")
        for attempt in range(1, max_retries + 1):
            try:
                print(f"[{model_name}] Mengirim request ke Gemini API (Percobaan {attempt}/{max_retries})...")
                response = client.models.generate_content(
                    model=model_name,
                    contents=f"Ekstrak data siaran dari transkrip SRT ini:\n\n{transcript_content}",
                    config=gen_config,
                )

                if not response or not response.parsed:
                    raise ValueError("Respons dari Gemini kosong atau gagal di-parse sesuai skema.")

                output_data = response.parsed.model_dump()

                with open(output_file_path, 'w', encoding='utf-8') as f:
                    json.dump(output_data, f, indent=2, ensure_ascii=False)

                print(f"\n[BERHASIL] Deskripsi berhasil diekstrak menggunakan model {model_name}!")
                print(f"File JSON telah disimpan di: {output_file_path}")
                success = True
                break

            except Exception as e:
                print(f"[{model_name}] Terjadi kesalahan pada percobaan {attempt}/{max_retries}: {e}")
                if is_transient_error(e) and attempt < max_retries:
                    delay = initial_delay * (2 ** (attempt - 1))
                    print(f"[RETRY] Server sibuk/high demand. Menunggu {delay:.1f} detik sebelum mencoba ulang...")
                    time.sleep(delay)
                else:
                    if attempt >= max_retries:
                        print(f"[{model_name}] Batas maksimal percobaan ({max_retries}x) telah tercapai.")
                    break

        if success:
            break
        else:
            print(f"[WARNING] Model '{model_name}' gagal. Mencoba fallback ke model high-performance berikutnya...")

    if not success:
        print("\n[FATAL ERROR] Semua model high-performance dan percobaan retry gagal menghasilkan deskripsi.")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate description JSON from SRT transcript with retry & high-performance fallback.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "srt_path",
        help="Path ke file SRT transkrip"
    )
    parser.add_argument(
        "-o", "--output",
        default="output.json",
        help=(
            "Path file output JSON.\n"
            "Contoh: -o transcripts/description.json\n"
            "Folder induk akan dibuat otomatis jika belum ada.\n"
            "Default: output.json (di folder saat ini)"
        )
    )
    parser.add_argument(
        "-m", "--model",
        default=None,
        help=(
            "Model Gemini spesifik yang diprioritaskan.\n"
            "Jika tidak ditentukan, sistem otomatis memilih model high-performance (>= 2.5) terbaru."
        )
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=4,
        help="Jumlah maksimal percobaan ulang (retry) per model saat terjadi 503/429 (default: 4)"
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=5.0,
        help="Waktu tunggu dasar dalam detik untuk exponential backoff retry (default: 5.0)"
    )

    args = parser.parse_args()
    generate_description(
        args.srt_path,
        args.output,
        model_arg=args.model,
        max_retries=args.max_retries,
        initial_delay=args.retry_delay
    )