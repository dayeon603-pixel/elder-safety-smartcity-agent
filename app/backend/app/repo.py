"""Data access: plain parameterised SQL, one function per query."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from typing import Any

from . import db


def _row(r: sqlite3.Row) -> dict[str, Any]:
    return {k: r[k] for k in r.keys()}


def create_user(
    conn: sqlite3.Connection, email: str, password_hash: str, role: str = "member"
) -> dict[str, Any]:
    """Insert a user with an explicit role (never inferred from table state).

    Joins the caller's transaction if open. Raises sqlite3.IntegrityError on a duplicate
    email. Admins are created only by `app.cli seed-admin` or a KCF_ADMIN_EMAIL match.
    """
    with db.transaction(conn):
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, role) VALUES (?, ?, ?)",
            (email, password_hash, role),
        )
    user = get_user_by_id(conn, int(cur.lastrowid or 0))
    assert user is not None
    return user


def update_password_hash(conn: sqlite3.Connection, user_id: int, password_hash: str) -> None:
    """Replace a user's password hash (used for transparent rehash on login)."""
    with db.transaction(conn):
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))


def set_role(conn: sqlite3.Connection, user_id: int, role: str) -> None:
    """Set a user's role; joins the caller's transaction if open."""
    with db.transaction(conn):
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))


def get_user_by_email(conn: sqlite3.Connection, email: str) -> dict[str, Any] | None:
    r = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    return _row(r) if r else None


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> dict[str, Any] | None:
    r = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _row(r) if r else None


DATASET_COLUMNS = (
    "id, name, source_url, license, sha256, fetched_at, file_name, row_count, description, "
    "column_roles, status, file_mtime"
)
RecordRow = tuple[str, str | None, int | None, str | None, str]


def _dataset_row(r: sqlite3.Row) -> dict[str, Any]:
    d = _row(r)
    d["column_roles"] = json.loads(d["column_roles"])
    return d


def list_datasets(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Complete datasets only; half-ingested ones (status 'loading') are invisible."""
    rows = conn.execute(
        f"SELECT {DATASET_COLUMNS} FROM datasets WHERE status = 'complete' ORDER BY id"
    ).fetchall()
    return [_dataset_row(r) for r in rows]


def get_dataset_by_name(conn: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    r = conn.execute(f"SELECT {DATASET_COLUMNS} FROM datasets WHERE name = ?", (name,)).fetchone()
    return _dataset_row(r) if r else None


def begin_dataset(
    conn: sqlite3.Connection,
    *,
    name: str,
    source_url: str,
    license_: str,
    sha256: str,
    fetched_at: str,
    file_name: str | None,
    description: str,
    created_by: int | None,
    column_roles: dict[str, Any],
    file_mtime: str | None,
) -> int:
    """Insert a dataset row with status 'loading' (committed immediately); return its id.

    Raises sqlite3.IntegrityError if the name is taken (including by an ingest in flight).
    """
    with db.transaction(conn):
        cur = conn.execute(
            "INSERT INTO datasets (name, source_url, license, sha256, fetched_at, file_name, "
            "row_count, description, created_by, column_roles, status, file_mtime) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, 'loading', ?)",
            (name, source_url, license_, sha256, fetched_at, file_name, description, created_by,
             json.dumps(column_roles, sort_keys=True), file_mtime),
        )
    return int(cur.lastrowid or 0)


def insert_records(conn: sqlite3.Connection, dataset_id: int, records: Sequence[RecordRow]) -> None:
    """Insert one batch of records in its own short transaction."""
    with db.transaction(conn):
        conn.executemany(
            "INSERT INTO records (dataset_id, ext_id, event_time, label, group_key, payload) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(dataset_id, *r) for r in records],
        )


def finalize_dataset(
    conn: sqlite3.Connection, dataset_id: int, row_count: int, column_roles: dict[str, Any]
) -> None:
    """Mark a streamed dataset complete (joins the caller's transaction if open)."""
    with db.transaction(conn):
        conn.execute(
            "UPDATE datasets SET status = 'complete', row_count = ?, column_roles = ? WHERE id = ?",
            (row_count, json.dumps(column_roles, sort_keys=True), dataset_id),
        )


def delete_dataset(conn: sqlite3.Connection, dataset_id: int) -> None:
    """Delete a dataset and (via ON DELETE CASCADE) its records."""
    with db.transaction(conn):
        conn.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))


def delete_incomplete_datasets(conn: sqlite3.Connection) -> int:
    """Remove datasets left in 'loading' by a crash; return how many."""
    with db.transaction(conn):
        cur = conn.execute("DELETE FROM datasets WHERE status = 'loading'")
    return int(cur.rowcount)


def insert_dataset(
    conn: sqlite3.Connection,
    *,
    name: str,
    source_url: str,
    license_: str,
    sha256: str,
    fetched_at: str,
    file_name: str | None,
    description: str,
    created_by: int | None,
    records: Sequence[RecordRow],
    column_roles: dict[str, Any],
    file_mtime: str | None = None,
) -> dict[str, Any]:
    """Insert a complete dataset and its records in one transaction (in-memory callers).

    `records` rows are (ext_id, event_time, label, group_key, payload_json).
    """
    with db.transaction(conn):
        dataset_id = begin_dataset(
            conn, name=name, source_url=source_url, license_=license_, sha256=sha256,
            fetched_at=fetched_at, file_name=file_name, description=description,
            created_by=created_by, column_roles=column_roles, file_mtime=file_mtime,
        )
        insert_records(conn, dataset_id, records)
        finalize_dataset(conn, dataset_id, len(records), column_roles)
    found = get_dataset_by_name(conn, name)
    assert found is not None
    return found


def _run_row(r: sqlite3.Row) -> dict[str, Any]:
    d = _row(r)
    d["params"] = json.loads(d["params"])
    d["metrics"] = json.loads(d["metrics"])
    return d


def insert_run(conn: sqlite3.Connection, run: dict[str, Any]) -> int:
    """Insert one run row (params/metrics are dicts, stored as JSON)."""
    cur = conn.execute(
        "INSERT INTO runs (experiment, dataset_id, role, method, params, metrics, seed, split_kind, "
        "n_train, n_test, git_hash, duration_s, started_at, created_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run["experiment"], run.get("dataset_id"), run["role"], run["method"],
            json.dumps(run["params"], sort_keys=True), json.dumps(run["metrics"], sort_keys=True),
            run["seed"], run["split_kind"], run["n_train"], run["n_test"], run["git_hash"],
            run["duration_s"], run["started_at"], run.get("created_by"),
        ),
    )
    return int(cur.lastrowid or 0)


def list_runs(conn: sqlite3.Connection, limit: int, offset: int) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return [_run_row(r) for r in rows]


def last_run(conn: sqlite3.Connection) -> dict[str, Any] | None:
    r = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return _run_row(r) if r else None


def latest_comparison(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Both runs (baseline, ours) of the most recent experiment."""
    rows = conn.execute(
        "SELECT * FROM runs WHERE experiment = (SELECT experiment FROM runs ORDER BY id DESC LIMIT 1) "
        "ORDER BY role"
    ).fetchall()
    return [_run_row(r) for r in rows]
