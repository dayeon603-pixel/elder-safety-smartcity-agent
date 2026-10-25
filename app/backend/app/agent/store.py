"""SQLite persistence for simulator receipts and agent runs (rows are immutable by trigger)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .. import db


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def insert_inbox(conn: sqlite3.Connection, receipt_id: str, channel: str, payload: dict[str, Any]) -> str:
    """Store a received simulator message; returns its created_at."""
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    ts = now_iso()
    with db.transaction(conn):
        conn.execute(
            "INSERT INTO sim_inbox (receipt_id, channel, payload, payload_sha256, received_at)"
            " VALUES (?,?,?,?,?)",
            (receipt_id, channel, text, hashlib.sha256(text.encode("utf-8")).hexdigest(), ts),
        )
    return ts


def list_inbox(conn: sqlite3.Connection, channel: str | None, limit: int) -> list[dict[str, Any]]:
    sql = "SELECT receipt_id, channel, payload_sha256, received_at FROM sim_inbox"
    args: tuple[Any, ...] = ()
    if channel:
        sql += " WHERE channel = ?"
        args = (channel,)
    rows = conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
    return [dict(r) for r in rows]


def get_inbox(conn: sqlite3.Connection, receipt_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM sim_inbox WHERE receipt_id = ?", (receipt_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["payload"] = json.loads(out["payload"])
    return out


def insert_run(conn: sqlite3.Connection, rec: dict[str, Any]) -> None:
    """Persist one finished run. JSON columns are serialized here."""
    with db.transaction(conn):
        conn.execute(
            "INSERT INTO agent_run (run_id, trigger, context, planner, plan, steps, briefing,"
            " briefing_source, grounding, model_name, model_digest, vlm_name, vlm_digest,"
            " image_sha256, total_ms, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rec["run_id"], rec["trigger"], json.dumps(rec["context"], ensure_ascii=False),
                rec["planner"], json.dumps(rec["plan"], ensure_ascii=False),
                json.dumps(rec["steps"], ensure_ascii=False), rec["briefing"],
                rec["briefing_source"], json.dumps(rec["grounding"], ensure_ascii=False),
                rec["model_name"], rec["model_digest"], rec["vlm_name"], rec["vlm_digest"],
                rec["image_sha256"], rec["total_ms"], rec["created_at"],
            ),
        )


def get_run(conn: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM agent_run WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    for key in ("context", "plan", "steps", "grounding"):
        out[key] = json.loads(out[key])
    out.pop("id", None)
    return out


def last_run(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Newest run's health-relevant fields (no briefing text), or None."""
    row = conn.execute(
        "SELECT run_id, trigger, planner, briefing_source, total_ms, created_at, steps FROM agent_run"
        " ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return None
    steps = json.loads(row["steps"])
    out = {k: row[k] for k in ("run_id", "trigger", "planner", "briefing_source", "total_ms", "created_at")}
    out["ok"] = all(bool(s.get("ok")) for s in steps)
    return out


def list_runs(conn: sqlite3.Connection, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT run_id, trigger, planner, briefing_source, total_ms, created_at FROM agent_run"
        " ORDER BY id DESC LIMIT ?", (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
