/**
 * stream-metrics.cpp
 *
 * Melakukan HTTP GET ke Cloudflare Worker setiap 60 detik,
 * lalu meng-append hasilnya ke metrics-stats.json dengan format:
 * {
 *   "time": "2026-04-29 15:30:00",
 *   "connection": {
 *     "ping_no": 1,
 *     "status_code": 200,
 *     "stream_on_air": false,
 *     "response_ms": 2147,
 *     "error": null
 *   },
 *   "stats": { ...hasil request... }
 * }
 *
 * Compile:
 *   g++ stream-metrics.cpp -o stream-metrics -lcurl
 */

#include <iostream>
#include <fstream>
#include <sstream>
#include <string>
#include <ctime>
#include <cstring>
#include <thread>
#include <chrono>
#include <curl/curl.h>

// ─── Konfigurasi ────────────────────────────────────────────────────────────
static const char* WORKER_URL   = "https://voiceoftrisma-metrics-worker.anandapradnyana68.workers.dev/";
static const char* OUTPUT_FILE  = "metrics-stats.jsonl";
static const long  TIMEOUT_SEC  = 30;      // Timeout request cURL (detik)

// ─── cURL write callback ────────────────────────────────────────────────────
static size_t writeCallback(char* ptr, size_t size, size_t nmemb, void* userdata) {
    std::string* buf = reinterpret_cast<std::string*>(userdata);
    buf->append(ptr, size * nmemb);
    return size * nmemb;
}

// ─── Timestamp "YYYY-MM-DD HH:MM:SS" (lokal) ───────────────────────────────
static std::string currentTimestamp() {
    std::time_t now = std::time(nullptr);
    char buf[20];
    std::strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S", std::localtime(&now));
    return std::string(buf);
}

// ─── Tunggu sampai tepat detik :00 menit berikutnya ────────────────────────
// Misal sekarang 02:47:13 → sleep 47 detik → ping di 02:48:00.
static long sleepUntilNextMinute() {
    std::time_t now  = std::time(nullptr);
    std::tm*    tm   = std::localtime(&now);

    // Maju ke menit berikutnya, reset detik ke 0
    tm->tm_min  += 1;
    tm->tm_sec   = 0;
    tm->tm_isdst = -1;  // biarkan mktime tentukan DST sendiri

    std::time_t next = std::mktime(tm);
    long        secs = (long)std::difftime(next, now);
    if (secs < 1) secs = 1;  // jaga-jaga jika sudah lewat

    std::this_thread::sleep_for(std::chrono::seconds(secs));
    return secs;
}

// ─── Escape string untuk JSON ────────────────────────────────────────────────
static std::string jsonEscape(const std::string& s) {
    std::ostringstream out;
    for (unsigned char c : s) {
        switch (c) {
            case '"':  out << "\\\""; break;
            case '\\': out << "\\\\"; break;
            case '\n': out << "\\n";  break;
            case '\r': out << "\\r";  break;
            case '\t': out << "\\t";  break;
            default:
                if (c < 0x20) {
                    char hex[8];
                    std::snprintf(hex, sizeof(hex), "\\u%04x", c);
                    out << hex;
                } else {
                    out << c;
                }
        }
    }
    return out.str();
}

// ─── Cek apakah JSON body mengindikasikan stream on-air ─────────────────────
static bool detectStreamOnAir(const std::string& body) {
    // Berdasarkan payload JSON stats dari user:
    // "streamstatus":1 (on-air) atau "streamstatus":0 (off-air)
    if (body.find("\"streamstatus\":1") != std::string::npos) return true;
    if (body.find("\"streamstatus\": 1") != std::string::npos) return true;

    // Fallback heuristik tambahan jika format sewaktu-waktu berbeda:
    if (body.find("\"stream_on_air\":true")  != std::string::npos) return true;
    if (body.find("\"stream_on_air\": true") != std::string::npos) return true;
    
    return false;
}

// ─── Satu siklus ping ────────────────────────────────────────────────────────
static void doPing(int ping_no) {
    std::string responseBody;
    long        statusCode   = 0;
    long        responseMs   = 0;
    std::string errorMsg     = "";   // kosong = tidak ada error
    bool        streamOnAir  = false;

    CURL* curl = curl_easy_init();
    if (!curl) {
        errorMsg = "curl_easy_init() failed";
    } else {
        curl_easy_setopt(curl, CURLOPT_URL,            WORKER_URL);
        curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION,  writeCallback);
        curl_easy_setopt(curl, CURLOPT_WRITEDATA,      &responseBody);
        curl_easy_setopt(curl, CURLOPT_TIMEOUT,        TIMEOUT_SEC);
        curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);
        curl_easy_setopt(curl, CURLOPT_USERAGENT,      "stream-metrics/1.0");

        auto t0 = std::chrono::steady_clock::now();
        CURLcode res = curl_easy_perform(curl);
        auto t1 = std::chrono::steady_clock::now();

        responseMs = (long)std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count();

        if (res != CURLE_OK) {
            errorMsg = curl_easy_strerror(res);
        } else {
            curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &statusCode);
            streamOnAir = detectStreamOnAir(responseBody);
        }

        curl_easy_cleanup(curl);
    }

    // ── Bangun JSON entry ─────────────────────────────────────────────────
    std::string timestamp = currentTimestamp();

    // Nilai "error": null atau "string"
    std::string errorJson = (errorMsg.empty()) ? "null"
                                               : ("\"" + jsonEscape(errorMsg) + "\"");

    // Nilai "stats": objek JSON atau null jika gagal
    std::string statsJson;
    if (!responseBody.empty() && errorMsg.empty()) {
        // Pakai body mentah (sudah JSON dari worker)
        statsJson = responseBody;
        // Hilangkan trailing newline jika ada
        while (!statsJson.empty() && (statsJson.back() == '\n' || statsJson.back() == '\r'))
            statsJson.pop_back();
    } else {
        statsJson = "null";
    }

    std::ostringstream entry;
    entry << "{"
          << "\"time\":\"" << timestamp << "\","
          << "\"connection\":{"
              << "\"ping_no\":"       << ping_no      << ","
              << "\"status_code\":"   << statusCode   << ","
              << "\"stream_on_air\":" << (streamOnAir ? "true" : "false") << ","
              << "\"response_ms\":"   << responseMs   << ","
              << "\"error\":"         << errorJson
          << "},"
          << "\"stats\":"             << statsJson
          << "}";

    // ── Append ke file ────────────────────────────────────────────────────
    std::ofstream ofs(OUTPUT_FILE, std::ios::app);
    if (ofs.is_open()) {
        ofs << entry.str() << "\n";
        ofs.close();
        std::cout << "[" << timestamp << "] ping #" << ping_no
                  << " | " << statusCode << " | " << responseMs << " ms"
                  << " | on-air=" << (streamOnAir ? "yes" : "no")
                  << " | err=" << (errorMsg.empty() ? "none" : errorMsg)
                  << std::endl;
    } else {
        std::cerr << "[" << timestamp << "] Gagal membuka " << OUTPUT_FILE << std::endl;
    }
}

// ─── Main ────────────────────────────────────────────────────────────────────
int main() {
    curl_global_init(CURL_GLOBAL_DEFAULT);
    std::cout << "stream-metrics dimulai. Ping setiap menit pada detik :00"
              << " | Output: " << OUTPUT_FILE << std::endl;

    int ping_no = 1;
    while (true) {
        doPing(ping_no++);
        sleepUntilNextMinute();
    }

    curl_global_cleanup();
    return 0;
}