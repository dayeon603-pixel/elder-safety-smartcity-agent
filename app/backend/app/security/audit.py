"""Append-only HMAC-SHA256 chained audit log (key held outside the database)."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .. import db

GENESIS_HASH = "0" * 64


@dataclass(frozen=True, slots=True)
class VerifyResult:
    """Outcome of walking the chain. `broken_at` is the first bad row id."""

    valid: bool
    entries: int
    head: str
    broken_at: int | None
    reason: str | None


def canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, UTF-8 preserved."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def entry_hash(key: bytes, prev_hash: str, ts: str, actor: str, action: str, detail_json: str) -> str:
    """hash = HMAC-SHA256(key, prev_hash || 0x0A || canonical({ts, actor, action, detail}))."""
    body = canonical_json({"ts": ts, "actor": actor, "action": action, "detail": json.loads(detail_json)})
    return hmac.new(key, f"{prev_hash}\n{body}".encode("utf-8"), hashlib.sha256).hexdigest()


def append(
    conn: sqlite3.Connection,
    key: bytes,
    actor: str,
    action: str,
    detail: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> str:
    """Append one entry; return its hash.

    Joins the caller's transaction if one is open (so a business write and its audit
    entry commit together); otherwise opens its own BEGIN IMMEDIATE. The timestamp is
    taken after the write lock is held, so ts order equals chain order.
    """
    detail_json = canonical_json(detail or {})
    with db.transaction(conn):
        ts = (now or datetime.now(timezone.utc)).isoformat(timespec="milliseconds")
        row = conn.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        prev = str(row["hash"]) if row else GENESIS_HASH
        h = entry_hash(key, prev, ts, actor, action, detail_json)
        conn.execute(
            "INSERT INTO audit_log (ts, actor, action, detail, prev_hash, hash) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ts, actor, action, detail_json, prev, h),
        )
    return h


def verify(conn: sqlite3.Connection, key: bytes) -> VerifyResult:
    """Recompute every hash in order; report the first inconsistency.

    Without the key an attacker who can write the database cannot produce a valid chain.
    Detects edits and mid-chain deletions/insertions. Tail truncation (or replacing the
    whole table with an older copy) is only detectable by comparing `head` with a value
    pinned outside the database. Anyone holding the key can forge entries.
    """
    prev = GENESIS_HASH
    count = 0
    for row in conn.execute(
        "SELECT id, ts, actor, action, detail, prev_hash, hash FROM audit_log ORDER BY id"
    ):
        if row["prev_hash"] != prev:
            return VerifyResult(False, count, prev, int(row["id"]), "prev_hash mismatch")
        expected = entry_hash(key, prev, row["ts"], row["actor"], row["action"], row["detail"])
        if not hmac.compare_digest(str(row["hash"]), expected):
            return VerifyResult(False, count, prev, int(row["id"]), "hash mismatch")
        prev = str(row["hash"])
        count += 1
    return VerifyResult(True, count, prev, None, None)
