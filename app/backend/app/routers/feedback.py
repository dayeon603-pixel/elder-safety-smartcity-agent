"""POST /feedback (human decision on a recommendation) and GET /feedback/summary.

Protected by the same demo token as the agent routes, capped to the simulator body limit and rate
limit (see main.py). Rows are append-only. Feedback is logged for the next training cycle only.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .. import db
from ..agent import constants as C
from ..config import Settings
from ..deps import get_conn, get_settings
from ..integrations import webhook
from ..modules import feedback as fb
from ..security import audit
from .agent import MAX_JSON_DEPTH, RUN_RE, json_depth, require_demo_token

router = APIRouter(prefix="/feedback", tags=["feedback"], dependencies=[Depends(require_demo_token)])
NOTE_MAX = 200
ACTION_ID_RE = r"^[a-z0-9][a-z0-9_.:-]{0,63}$"
SGG_RE = r"^[0-9]{5}$"
DATE_RE = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
OUTCOME_MAX_KEYS = 16


class FeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str | None = Field(default=None, pattern=RUN_RE)
    sgg_code: str | None = Field(default=None, pattern=SGG_RE)
    date: str | None = Field(default=None, pattern=DATE_RE)
    action_id: str = Field(pattern=ACTION_ID_RE, description="e.g. cbs, bit, heat.alert, aed.convert")
    decision: Literal["채택", "보류", "부적합"]
    stage: Literal["approve_demo", "hold"] | None = Field(
        default=None, description="approve_demo = 과장 결재 시연 (needs a prior 채택); hold = 보류 with a required reason in `note`")
    note: str = Field(default="", max_length=NOTE_MAX)
    actual_outcome: dict[str, Any] | None = Field(default=None, description="e.g. {\"patients_confirmed\": 2}")

    @model_validator(mode="after")
    def _reference_and_outcome(self) -> FeedbackIn:
        if self.run_id is None and (self.sgg_code is None or self.date is None):
            raise ValueError("give run_id, or both sgg_code and date")
        if self.stage == "hold" and (self.decision != "보류" or not self.note.strip()):
            raise ValueError("stage hold goes with decision 보류 and requires a reason in `note`")
        if self.stage == "approve_demo" and self.decision != "채택":
            raise ValueError("stage approve_demo goes with decision 채택")
        if self.actual_outcome is not None:
            if len(self.actual_outcome) > OUTCOME_MAX_KEYS or json_depth(self.actual_outcome) > MAX_JSON_DEPTH:
                raise ValueError("actual_outcome too large or too deeply nested")
        return self


APPROVED = "채택"
AUDIT_ACTOR = "demo-operator"
AUDIT_ACTION = "feedback.accept"
HOLD_NO_REASON = "(사유 미기재)"
AUDIT_ACTION_STAGE = {"adopt": "feedback.accept", "approve_demo": "feedback.approve_demo", "hold": "feedback.hold"}


def _audit(conn: sqlite3.Connection, settings: Settings, action: str, detail: dict[str, Any]) -> dict[str, Any]:
    h = audit.append(conn, settings.audit_key, AUDIT_ACTOR, action, detail)
    row = conn.execute("SELECT id, prev_hash FROM audit_log WHERE hash = ?", (h,)).fetchone()
    return {"id": int(row["id"]) if row else None, "hash": h, "prev_hash": str(row["prev_hash"]) if row else None,
            "action": action, "verify_url": "/audit/verify"}


def _record_stage(conn: sqlite3.Connection, settings: Settings, rec: dict[str, Any], body: FeedbackIn) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Append the approval-chain stage(s) for a decision: 채택 -> adopt (+ approve_demo when asked), 보류 -> hold; each audited.

    Returns (audit of the first stage, stage record of the last stage); (None, None) for 부적합.
    """
    stage = "approve_demo" if body.stage == "approve_demo" else fb.STAGE_OF_DECISION.get(rec["decision"])
    if stage is None:
        return None, None
    detail = {"feedback_id": rec["id"], "action_id": rec["action_id"], "sgg_code": rec["sgg_code"], "date": rec["date"],
              "run_id": rec["run_id"], "stage": stage, "reason": rec["note"]}
    chain = ["adopt", "approve_demo"] if stage == "approve_demo" and fb.current_status(
        conn, rec["sgg_code"], rec["date"], rec["action_id"], rec["run_id"]) == "draft" else [stage]
    first_audit: dict[str, Any] | None = None
    last: dict[str, Any] | None = None
    for st in chain:
        a = _audit(conn, settings, AUDIT_ACTION_STAGE[st], {**detail, "stage": st})
        first_audit = first_audit or a
        last = fb.insert_stage(conn, stage=st, sgg_code=rec["sgg_code"], date=rec["date"], action_id=rec["action_id"], run_id=rec["run_id"],
                               reason=rec["note"] or (HOLD_NO_REASON if st == "hold" else ""), feedback_id=rec["id"],
                               created_at=rec["created_at"], audit_hash=a["hash"])
    return first_audit, last


def _notify(request: Request, settings: Settings, rec: dict[str, Any]) -> bool:
    """Start the signed webhook for an approved action (if configured); returns whether one was started."""
    if settings.webhook_url is None or rec["decision"] != APPROVED:
        return False
    injected = getattr(request.app.state, "webhook_http", None)
    factory = injected or (lambda: httpx.Client())
    extra = {C.DEMO_TOKEN_HEADER: settings.demo_token} if settings.demo_token else None
    body = {"event": webhook.EVENT_ACTION_APPROVED, "feedback": {k: v for k, v in rec.items() if k not in ("retraining", "stage", "audit")}}
    thread = webhook.notify_in_background(settings, body, int(rec["id"]), http_factory=factory,
                                          conn_factory=lambda: db.connect(settings.db_path), extra_headers=extra,
                                          owns_client=injected is None)
    threads: list[Any] = request.app.state.webhook_threads
    threads[:] = [t for t in threads if t.is_alive()] + [thread]
    return True


@router.post("", status_code=status.HTTP_201_CREATED)
def post_feedback(body: FeedbackIn, request: Request, conn: sqlite3.Connection = Depends(get_conn),
                  settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Record one decision. 404 for an unknown run_id or sgg_code; 422 for an oversized outcome.

    An approved (채택) decision is appended to the hash-chained audit log (`audit: {id, hash}` in the response)
    and triggers the signed outbound webhook when KCF_WEBHOOK_URL is set.
    """
    try:
        rec = fb.insert_feedback(
            conn, run_id=body.run_id, sgg_code=body.sgg_code, date=body.date, action_id=body.action_id,
            decision=body.decision, note=body.note, actual_outcome=body.actual_outcome,
            created_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        )
        rec["audit"], rec["stage"] = _record_stage(conn, settings, rec, body)
        rec["status"] = fb.current_status(conn, rec["sgg_code"], rec["date"], rec["action_id"], rec["run_id"])
        rec["status_semantics"] = "전달 대기 (발송 아님)"
        rec["webhook_started"] = _notify(request, settings, rec)
        return rec
    except fb.StageOrderError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, {"code": "stage_order", "message": str(exc)}) from exc
    except fb.UnknownRunError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"run not found: {exc}") from exc
    except fb.UnknownRegionError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"region not found: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.get("/summary")
def feedback_summary(sgg_code: str | None = Query(default=None, pattern=SGG_RE),
                     conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    """Counts by action type and decision; `retraining` states that feedback is only logged so far."""
    return fb.summary(conn, sgg_code)
