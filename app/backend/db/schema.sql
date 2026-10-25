-- Baseline schema (schema_version 1). Later changes go in db/migrations/NNNN_name.sql.
-- Conventions: timestamps are ISO-8601 UTC text; JSON columns are guarded by json_valid();
-- every FK is declared and indexed; the audit table is append-only (enforced by triggers).
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE users (
    id            INTEGER PRIMARY KEY,
    email         TEXT    NOT NULL UNIQUE COLLATE NOCASE
                          CHECK (length(email) BETWEEN 3 AND 254 AND instr(email, '@') > 1),
    password_hash TEXT    NOT NULL CHECK (password_hash LIKE '$argon2id$%'),
    role          TEXT    NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member')),
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

-- Provenance: every dataset is traceable to a URL, a license, and a content hash.
CREATE TABLE datasets (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL UNIQUE CHECK (length(name) BETWEEN 1 AND 64),
    source_url  TEXT    NOT NULL CHECK (source_url LIKE 'http://%' OR source_url LIKE 'https://%'),
    license     TEXT    NOT NULL CHECK (length(license) BETWEEN 1 AND 64),
    sha256      TEXT    NOT NULL CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    fetched_at  TEXT    NOT NULL,
    file_name   TEXT,
    row_count   INTEGER NOT NULL CHECK (row_count >= 0),
    description TEXT    NOT NULL DEFAULT '',
    created_by  INTEGER REFERENCES users (id) ON DELETE SET NULL,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;
CREATE INDEX idx_datasets_created_by ON datasets (created_by);

-- Generic domain rows. Typed key columns are the placeholders to specialise per topic;
-- the full original row stays in payload so ingest is lossless.
CREATE TABLE records (
    id         INTEGER PRIMARY KEY,
    dataset_id INTEGER NOT NULL REFERENCES datasets (id) ON DELETE CASCADE,
    ext_id     TEXT    NOT NULL,
    event_time TEXT,
    label      INTEGER,
    group_key  TEXT,
    payload    TEXT    NOT NULL CHECK (json_valid(payload) AND json_type(payload) = 'object'),
    UNIQUE (dataset_id, ext_id)
) STRICT;
CREATE INDEX idx_records_dataset_time ON records (dataset_id, event_time);
CREATE INDEX idx_records_dataset_group ON records (dataset_id, group_key);
CREATE INDEX idx_records_dataset_label ON records (dataset_id, label);

-- One row per method per experiment: baseline vs ours share an `experiment` id.
CREATE TABLE runs (
    id          INTEGER PRIMARY KEY,
    experiment  TEXT    NOT NULL CHECK (length(experiment) BETWEEN 1 AND 128),
    dataset_id  INTEGER REFERENCES datasets (id) ON DELETE SET NULL,
    role        TEXT    NOT NULL CHECK (role IN ('baseline', 'ours')),
    method      TEXT    NOT NULL CHECK (length(method) BETWEEN 1 AND 128),
    params      TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(params)),
    metrics     TEXT    NOT NULL CHECK (json_valid(metrics)),
    seed        INTEGER NOT NULL,
    split_kind  TEXT    NOT NULL CHECK (split_kind IN ('random', 'temporal')),
    n_train     INTEGER NOT NULL CHECK (n_train > 0),
    n_test      INTEGER NOT NULL CHECK (n_test > 0),
    git_hash    TEXT    NOT NULL,
    duration_s  REAL    NOT NULL CHECK (duration_s >= 0),
    started_at  TEXT    NOT NULL,
    created_by  INTEGER REFERENCES users (id) ON DELETE SET NULL,
    UNIQUE (experiment, role)
) STRICT;
CREATE INDEX idx_runs_dataset ON runs (dataset_id);
CREATE INDEX idx_runs_started ON runs (started_at);
CREATE INDEX idx_runs_created_by ON runs (created_by);

-- SHA-256 hash chain: hash = SHA256(prev_hash || canonical(ts, actor, action, detail)).
CREATE TABLE audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    actor     TEXT NOT NULL,
    action    TEXT NOT NULL,
    detail    TEXT NOT NULL CHECK (json_valid(detail)),
    prev_hash TEXT NOT NULL CHECK (length(prev_hash) = 64),
    hash      TEXT NOT NULL UNIQUE CHECK (length(hash) = 64)
) STRICT;
CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
