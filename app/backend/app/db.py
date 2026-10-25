"""sqlite3 connection factory and a tiny checksummed migration runner (no ORM)."""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

DB_DIR = Path(__file__).resolve().parents[1] / "db"
BUSY_TIMEOUT_MS = 5000
_PRAGMA_START = re.compile(r"\A(?:\s*--[^\n]*\n|\s*/\*.*?\*/)*\s*PRAGMA\b", re.IGNORECASE | re.DOTALL)
_MIGRATION_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")
_SCHEMA_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY CHECK (version >= 1),
    name       TEXT NOT NULL,
    checksum   TEXT NOT NULL CHECK (length(checksum) = 64),
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT
"""


class MigrationError(RuntimeError):
    """Raised when migrations are malformed or an applied one was edited."""


def connect(path: Path | str) -> sqlite3.Connection:
    """Open a connection with FK enforcement, WAL and a busy timeout.

    Autocommit mode (isolation_level=None): transactions are explicit via BEGIN/COMMIT.
    """
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    # FULL: an acknowledged commit (including every audit write) survives power loss.
    conn.execute("PRAGMA synchronous = FULL")
    # Makes INSERT OR REPLACE fire DELETE triggers (the append-only audit_log relies on it).
    conn.execute("PRAGMA recursive_triggers = ON")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """BEGIN IMMEDIATE ... COMMIT, ROLLBACK on any exception.

    Re-entrant: if `conn` is already inside a transaction the block joins it and the
    outermost caller decides commit/rollback. This lets register + audit share one commit.
    """
    if conn.in_transaction:
        yield
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _discover(db_dir: Path) -> list[tuple[int, str, Path]]:
    found: list[tuple[int, str, Path]] = [(1, "schema", db_dir / "schema.sql")]
    mig_dir = db_dir / "migrations"
    if mig_dir.is_dir():
        for p in sorted(mig_dir.glob("*.sql")):
            m = _MIGRATION_NAME.match(p.name)
            if not m:
                raise MigrationError(f"bad migration filename: {p.name}")
            version = int(m.group(1))
            if version < 2:
                raise MigrationError(f"{p.name}: migrations start at 0002")
            found.append((version, m.group(2), p))
    versions = [v for v, _, _ in found]
    if len(set(versions)) != len(versions):
        raise MigrationError("duplicate migration version")
    return sorted(found)


def split_statements(sql: str) -> list[str]:
    """Split a SQL script into statements using SQLite's own tokenizer.

    `sqlite3.complete_statement` understands string literals, comments and
    CREATE TRIGGER ... BEGIN ... END bodies, so a `;` or the word PRAGMA inside a string
    never splits or reclassifies a statement.
    """
    statements: list[str] = []
    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            statements.append(buf.strip())
            buf = ""
    if buf.strip():
        statements.append(buf.strip())
    return statements


def _read_applied(conn: sqlite3.Connection) -> dict[int, str]:
    return {
        int(r["version"]): str(r["checksum"])
        for r in conn.execute("SELECT version, checksum FROM schema_version")
    }


def migrate(conn: sqlite3.Connection, db_dir: Path = DB_DIR) -> int:
    """Apply pending migrations atomically; return the current schema version.

    Each file runs in one BEGIN IMMEDIATE transaction together with its schema_version
    row. The applied set is re-read after the write lock is taken, so two processes
    starting together cannot apply the same file twice: the loser sees it applied and
    skips. PRAGMA statements (detected per statement, not per line) run outside the
    transaction, where they take effect.

    Raises:
        MigrationError: on malformed files or a checksum mismatch for an applied file.
    """
    conn.execute(_SCHEMA_VERSION_DDL)
    for version, name, path in _discover(db_dir):
        sql = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        statements = split_statements(sql)
        pragmas = [s for s in statements if _PRAGMA_START.match(s)]
        body = [s for s in statements if not _PRAGMA_START.match(s)]
        if version in _read_applied(conn):
            pragmas = []  # cheap pre-check; authoritative re-check happens under the lock
        for pragma in pragmas:
            conn.execute(pragma)
        conn.execute("BEGIN IMMEDIATE")
        try:
            applied = _read_applied(conn)
            if version in applied:
                if applied[version] != checksum:
                    raise MigrationError(f"{path.name} was modified after being applied")
                conn.execute("COMMIT")
                continue
            if version < max(applied, default=0):
                raise MigrationError(f"{path.name}: version is older than the applied head")
            for statement in body:
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_version (version, name, checksum) VALUES (?, ?, ?)",
                (version, name, checksum),
            )
            conn.execute("COMMIT")
        except (sqlite3.Error, MigrationError):
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            logger.exception("migration %s failed", path.name)
            raise
        logger.info("applied migration %04d_%s", version, name)
    return max(_read_applied(conn), default=0)
