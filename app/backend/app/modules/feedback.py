"""Append-only storage for human decisions on recommendations (the 선순환 loop's record).

Feedback is *logged for the next training cycle*; nothing here retrains or re-weights anything yet.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .. import db
from ..agent import store as agent_store

DECISIONS: tuple[str, ...] = ("채택", "보류", "부적합")
MAX_OUTCOME_BYTES = 2048
RETRAIN_NOTE = "피드백은 다음 학습 주기의 입력으로 기록만 됩니다 (아직 재학습에 사용하지 않음)."


class UnknownRunError(LookupError):
    """run_id does not exist."""


class UnknownRegionError(LookupError):
    """sgg_code does not exist."""


def insert_feedback(
    conn: sqlite3.Connection, *, run_id: str | None, sgg_code: str | None, date: str | None, action_id: str,
    decision: str, note: str, actual_outcome: dict[str, Any] | None, created_at: str,
) -> dict[str, Any]:
    """Store one decision; a run_id resolves its region/date so summaries can group by 시군구.

    Raises:
        UnknownRunError, UnknownRegionError: dangling references (the FK would raise anyway; this is clearer).
        ValueError: actual_outcome serialises to more than MAX_OUTCOME_BYTES.
    """
    if run_id is not None:
        run = agent_store.get_run(conn, run_id)
        if run is None:
            raise UnknownRunError(run_id)
        sgg_code = sgg_code or str(run["context"].get("code"))
        date = date or str(run["context"].get("date"))
    if sgg_code is not None and conn.execute("SELECT 1 FROM region WHERE code = ?", (sgg_code,)).fetchone() is None:
        raise UnknownRegionError(sgg_code)
    outcome = None
    if actual_outcome is not None:
        outcome = json.dumps(actual_outcome, ensure_ascii=False, sort_keys=True)
        if len(outcome.encode("utf-8")) > MAX_OUTCOME_BYTES:
            raise ValueError(f"actual_outcome exceeds {MAX_OUTCOME_BYTES} bytes")
    with db.transaction(conn):
        cur = conn.execute(
            "INSERT INTO feedback (created_at, run_id, sgg_code, date, action_id, decision, note, actual_outcome) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (created_at, run_id, sgg_code, date, action_id, decision, note, outcome),
        )
    return {"id": int(cur.lastrowid or 0), "created_at": created_at, "run_id": run_id, "sgg_code": sgg_code,
            "date": date, "action_id": action_id, "decision": decision, "note": note,
            "actual_outcome": actual_outcome, "retraining": RETRAIN_NOTE}


STAGES = ("adopt", "approve_demo", "hold")
STAGE_OF_DECISION = {"채택": "adopt", "보류": "hold"}


class StageOrderError(ValueError):
    """approve_demo without a prior adopt for the same recommendation."""


def insert_stage(conn: sqlite3.Connection, *, stage: str, sgg_code: str | None, date: str | None, action_id: str,
                 run_id: str | None, reason: str, feedback_id: int | None, created_at: str, audit_hash: str | None) -> dict[str, Any]:
    """Append one approval-chain stage (adopt / approve_demo / hold).

    Raises:
        StageOrderError: approve_demo before any adopt of the same (sgg_code, date, action_id) or run_id.
        ValueError: hold without a reason, unknown stage.
    """
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {STAGES}")
    if stage == "hold" and not reason.strip():
        raise ValueError("hold requires a reason")
    if stage == "approve_demo" and current_status(conn, sgg_code, date, action_id, run_id) not in ("adopted", "approved_demo"):
        raise StageOrderError("approve_demo requires a prior adopt (채택) of the same recommendation")
    with db.transaction(conn):
        cur = conn.execute(
            "INSERT INTO feedback_stage (created_at, feedback_id, run_id, sgg_code, date, action_id, stage, reason, audit_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (created_at, feedback_id, run_id, sgg_code, date, action_id, stage, reason.strip(), audit_hash),
        )
    return {"id": int(cur.lastrowid or 0), "stage": stage, "created_at": created_at, "reason": reason.strip(), "audit_hash": audit_hash}


def current_status(conn: sqlite3.Connection, sgg_code: str | None, date: str | None, action_id: str, run_id: str | None = None) -> str:
    """draft | adopted | approved_demo | held from the latest stage row (adopt/hold/approve_demo), 전달 대기 semantics."""
    if run_id is not None:
        row = conn.execute("SELECT stage FROM feedback_stage WHERE run_id = ? AND action_id = ? ORDER BY id DESC LIMIT 1",
                           (run_id, action_id)).fetchone()
    else:
        row = conn.execute("SELECT stage FROM feedback_stage WHERE sgg_code = ? AND date = ? AND action_id = ? ORDER BY id DESC LIMIT 1",
                           (sgg_code, date, action_id)).fetchone()
    if row is None:
        return "draft"
    return {"adopt": "adopted", "approve_demo": "approved_demo", "hold": "held"}[str(row["stage"])]


def stages_by_date(conn: sqlite3.Connection, sgg_code: str | None) -> list[dict[str, Any]]:
    """Per date: adopted / held / approved counts (stage rows), newest date first."""
    sql = "SELECT date, stage, COUNT(*) AS n FROM feedback_stage WHERE date IS NOT NULL"
    args: tuple[Any, ...] = ()
    if sgg_code is not None:
        sql += " AND sgg_code = ?"
        args = (sgg_code,)
    rows = conn.execute(sql + " GROUP BY date, stage ORDER BY date DESC", args).fetchall()
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        out.setdefault(str(r["date"]), {"adopted": 0, "held": 0, "approved": 0})[
            {"adopt": "adopted", "hold": "held", "approve_demo": "approved"}[str(r["stage"])]] = int(r["n"])
    return [{"date": d, **c} for d, c in out.items()]


def summary(conn: sqlite3.Connection, sgg_code: str | None) -> dict[str, Any]:
    """Counts by action_id x decision (optionally for one 시군구) plus totals by decision."""
    sql = "SELECT action_id, decision, COUNT(*) AS n FROM feedback"
    args: tuple[Any, ...] = ()
    if sgg_code is not None:
        sql += " WHERE sgg_code = ?"
        args = (sgg_code,)
    rows = conn.execute(sql + " GROUP BY action_id, decision ORDER BY action_id, decision", args).fetchall()
    by_action: dict[str, dict[str, int]] = {}
    by_decision = dict.fromkeys(DECISIONS, 0)
    for r in rows:
        by_action.setdefault(str(r["action_id"]), dict.fromkeys(DECISIONS, 0))[str(r["decision"])] = int(r["n"])
        by_decision[str(r["decision"])] += int(r["n"])
    return {"sgg_code": sgg_code, "total": sum(by_decision.values()), "by_decision": by_decision,
            "by_action": by_action, "by_date": stages_by_date(conn, sgg_code), "retraining": RETRAIN_NOTE,
            "status_semantics": "모든 단계는 전달 대기 (발송 아님)"}


__all__ = ["DECISIONS", "RETRAIN_NOTE", "STAGES", "STAGE_OF_DECISION", "StageOrderError", "UnknownRegionError", "UnknownRunError",
           "current_status", "insert_feedback", "insert_stage", "stages_by_date", "summary"]
