-- 0002: keyed audit chain, dataset column roles, incomplete-ingest marker.
--
-- audit_log: the v1 chain was an UNKEYED SHA-256 chain, so anyone with database write
-- access could rewrite history and recompute every hash. It cannot be re-keyed after the
-- fact (that would launder possibly-tampered rows), so the old table is kept read-only as
-- audit_log_legacy_v1 and a fresh HMAC-SHA256 chain starts at the genesis value.
-- HMAC key: derived from KCF_SECRET_KEY (or KCF_AUDIT_KEY); it is never stored in the DB.
DROP TRIGGER audit_log_no_update;
DROP TRIGGER audit_log_no_delete;
ALTER TABLE audit_log RENAME TO audit_log_legacy_v1;
CREATE TRIGGER audit_log_legacy_v1_no_update BEFORE UPDATE ON audit_log_legacy_v1
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER audit_log_legacy_v1_no_delete BEFORE DELETE ON audit_log_legacy_v1
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

CREATE TABLE audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    actor     TEXT NOT NULL,
    action    TEXT NOT NULL,
    detail    TEXT NOT NULL CHECK (json_valid(detail)),
    prev_hash TEXT NOT NULL UNIQUE CHECK (length(prev_hash) = 64),
    hash      TEXT NOT NULL UNIQUE CHECK (length(hash) = 64)
) STRICT;
CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
-- INSERT OR REPLACE would otherwise delete the conflicting row without firing the DELETE
-- trigger (unless recursive_triggers is ON). Reject any insert that collides on id,
-- prev_hash or hash, so a REPLACE can never evict an existing entry.
CREATE TRIGGER audit_log_no_replace BEFORE INSERT ON audit_log
WHEN EXISTS (
    SELECT 1 FROM audit_log
    WHERE id = NEW.id OR prev_hash = NEW.prev_hash OR hash = NEW.hash
)
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

-- datasets: column roles (label/id/time/group/features) are recorded at ingest so that
-- experiments can never silently use the label or an identifier as a feature.
--   column_roles: {"id","time","label","group","label_values","features"}; '{}' = legacy row.
--   status: 'loading' while rows are streamed in batches, 'complete' once finalised.
--   fetched_at (existing column) now means "time of ingest"; file_mtime is the file's mtime.
--   source_url and license are DECLARED by the caller at ingest; the server does not verify
--   them. sha256 covers the file bytes the server actually read.
ALTER TABLE datasets ADD COLUMN column_roles TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(column_roles));
ALTER TABLE datasets ADD COLUMN status TEXT NOT NULL DEFAULT 'complete' CHECK (status IN ('loading', 'complete'));
ALTER TABLE datasets ADD COLUMN file_mtime TEXT;
