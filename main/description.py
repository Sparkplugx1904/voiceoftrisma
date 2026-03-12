import os
import sys
import json
import google.generativeai as genai
from typing_extensions import TypedDict
import typing

# 1. Konfigurasi API Key
# Ganti 'YOUR_API_KEY' dengan API Key Google Gemini Anda
genai.configure(api_key=GEMINI_API_KEY)

def generate_description(srt_file_path):
    # Membaca isi file srt
    try:
        with open(srt_file_path, 'r', encoding='utf-8') as file:
            transcript_content = file.read()
    except Exception as e:
        print(f"Error membaca file: {e}")
        return

    # 2. Definisi Struktur Schema (Agar format tetap/statis)
    class Program(TypedDict):
        program: str
        announcer: str
        timestamp: str
        topic: str
        description: str

    class SiaranRadio(TypedDict):
        title: str
        description: str
        programs: typing.List[Program]

    # 3. Inisialisasi Model dengan Instruksi Sistem
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        system_instruction=(
            "Anda adalah asisten ekstraksi data radio. "
            "Tugas Anda adalah mengekstrak jadwal siaran dari transkrip SRT yang diberikan. "
            "Gunakan format JSON yang sangat ketat sesuai schema. "
            "PENTING: Pastikan timestamp penyiar dimulai tepat saat mereka memperkenalkan diri (verbal), "
            "contohnya Citra di 00:34:29,000. Jangan merubah struktur kunci JSON."
        )
    )

    # 4. Melakukan Request dengan JSON Mode
    prompt = f"Ekstrak data dari transkrip berikut ke dalam format JSON sesuai struktur:\n\n{transcript_content}"
    
    try:
        response = model.generate_content(
            prompt,
            generation_config={
                "response_mime_type": "application/json",
                "response_schema": SiaranRadio
            }
        )

        # 5. Menyimpan Output ke description.json
        output_data = json.loads(response.text)
        
        with open('description.json', 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)
            
        print("Berhasil! Output telah disimpan di description.json")

    except Exception as e:
        print(f"Terjadi kesalahan saat memproses API: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Penggunaan: python description_generator.py nama_file.srt")
    else:
        file_path = sys.argv[1]
        generate_description(file_path)