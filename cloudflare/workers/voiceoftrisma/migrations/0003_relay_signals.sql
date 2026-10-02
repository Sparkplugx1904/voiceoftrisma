-- Migration 0003: relay_signals — komunikasi antar VM GitHub Actions
-- Runner A memicu B lewat Cloudflare, B mengumumkan dirinya siap lewat
-- tabel ini, A poll endpoint relay/status sampai mendapat konfirmasi B
-- siap merekam → A boleh cut dan upload.
--
-- Lifecycle entry:
--   1. Saat B dipicu: baris dimasukkan dengan siap_pada = NULL
--   2. Saat B mulai merekam: siap_pada diisi timestamp (ms)
--   3. Runner lama (> 24 jam) dibersihkan secara periodik

CREATE TABLE IF NOT EXISTS relay_signals (
  rantai_id  TEXT    NOT NULL,   -- ID rantai harian (YYYY-MM-DD)
  nomor      INTEGER NOT NULL,   -- nomor runner B (2, 3, ...)
  run_id     TEXT,               -- GITHUB_RUN_ID milik B
  siap_pada  INTEGER,            -- epoch ms saat B mulai rekam; NULL = belum siap
  dibuat     INTEGER NOT NULL DEFAULT (unixepoch() * 1000),
  PRIMARY KEY (rantai_id, nomor)
);

CREATE INDEX IF NOT EXISTS idx_relay_rantai
  ON relay_signals (rantai_id, dibuat DESC);
