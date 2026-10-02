-- Migration 0002: tabel record_sessions untuk analytics eksekutabel
-- auto-record. Eksekutabel (experimental/app) panggil:
--   GET  /stream?url=...        -> mulai sesi (status='recording')
--   PUT  /stream/session        -> update ended_at, duration, item_id, status
--
-- Field `status`:
--   'recording' : byte audio sedang di-forward ke eksekutabel
--   'uploaded'  : eksekutabel sudah upload ke Internet Archive sukses
--   'failed'    : upstream tidak reachable / upload gagal
--   'http_NNN'  : upstream merespons HTTP NNN
--
-- Dashboard admin nanti bisa query:
--   SELECT date(started_at, 'unixepoch'), COUNT(*), SUM(bytes_uploaded)
--   FROM record_sessions WHERE status='uploaded' GROUP BY 1;

CREATE TABLE IF NOT EXISTS record_sessions (
  session_id     TEXT PRIMARY KEY,
  started_at     INTEGER NOT NULL,
  ended_at       INTEGER,
  duration_sec   INTEGER,
  bytes_uploaded INTEGER NOT NULL DEFAULT 0,
  item_id        TEXT,
  client_ip      TEXT,
  status         TEXT NOT NULL
) WITHOUT ROWID;

-- Index untuk dashboard (query per-tanggal + filter status).
CREATE INDEX IF NOT EXISTS idx_record_sessions_started
  ON record_sessions (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_record_sessions_status
  ON record_sessions (status, started_at DESC);
