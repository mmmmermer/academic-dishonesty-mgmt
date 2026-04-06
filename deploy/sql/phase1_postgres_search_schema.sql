-- Phase-1 PostgreSQL schema draft for search optimization
-- NOTE: Review and execute manually in maintenance window.

BEGIN;

ALTER TABLE blacklist
  ADD COLUMN IF NOT EXISTS name_norm VARCHAR(64),
  ADD COLUMN IF NOT EXISTS name_pinyin_full VARCHAR(128),
  ADD COLUMN IF NOT EXISTS name_abbr VARCHAR(64);

COMMIT;

-- Indexes (run after backfill for better planning)
CREATE INDEX IF NOT EXISTS ix_blacklist_status_student_id
  ON blacklist (status, id_card);

CREATE INDEX IF NOT EXISTS ix_blacklist_name_norm
  ON blacklist (name_norm);

CREATE INDEX IF NOT EXISTS ix_blacklist_name_pinyin_full
  ON blacklist (name_pinyin_full);

CREATE INDEX IF NOT EXISTS ix_blacklist_name_abbr
  ON blacklist (name_abbr);

CREATE INDEX IF NOT EXISTS ix_blacklist_active_student_id
  ON blacklist (id_card)
  WHERE status = 1;

CREATE INDEX IF NOT EXISTS ix_blacklist_active_name_norm
  ON blacklist (name_norm)
  WHERE status = 1;

