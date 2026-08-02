-- Migration 0001: inisialisasi skema D1 Voice of Trisma
-- 2026-08-02 — pengganti 4 KV namespace (VOT_ADMIN_STORE,
-- VOT_STREAM_STATS, ARCHIVE_KV, VOT_METRICS_STORE).

-- Tabel generik key-value untuk dokumen JSON (jadwal, jadwal_history,
-- admin_logs, last, last_stats, UPLOADS_DATA_FULL).
CREATE TABLE IF NOT EXISTS kv_store (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at INTEGER NOT NULL
) WITHOUT ROWID;

-- Time-series sample pendengar (bucket 5 menit). PRIMARY KEY = awal bucket
-- (detik unix, kelipatan 300) sehingga upsert per bucket dedupe otomatis
-- via ON CONFLICT — tidak perlu read-modify-write seperti di KV.
CREATE TABLE IF NOT EXISTS samples (
  t INTEGER PRIMARY KEY,
  listeners INTEGER NOT NULL,
  status INTEGER NOT NULL
) WITHOUT ROWID;
