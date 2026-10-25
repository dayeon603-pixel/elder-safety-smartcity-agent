"""Signed outbound webhook for approved actions (기존 행정 시스템 연동, first step).

Each approved recommendation (POST /feedback with decision 채택) is POSTed as JSON to KCF_WEBHOOK_URL
with `X-Zero-Point-Signature: sha256=HMAC-SHA256(secret, "<timestamp>.<body>")` and
`X-Zero-Point-Timestamp`; the receiver recomputes the MAC and rejects stale or forged calls. Delivery
retries with a short backoff and the outcome (host, status, attempts) is appended to the HMAC audit chain.
The body never carries personal data: feedback fields plus the recommendation's evidence numbers.
스마트도시 통합플랫폼 연계(TTA 연계규격) is the next step; this is not a claim of compliance with it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..config import Settings
from ..security import audit

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Zero-Point-Signature"
TIMESTAMP_HEADER = "X-Zero-Point-Timestamp"
EVENT_HEADER = "X-Zero-Point-Event"
EVENT_ACTION_APPROVED = "action.approved"
SIGNATURE_PREFIX = "sha256="
MAX_SKEW_S = 300
BACKOFF_S = (0.5, 1.0, 2.0)
ACTOR = "webhook"


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    ok: bool
    status: int | None
    attempts: int
    error: str | None


def canonical(body: dict[str, Any]) -> bytes:
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign(secret: bytes, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret, timestamp.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()
    return SIGNATURE_PREFIX + mac


def verify(secret: bytes, timestamp: str, body: bytes, signature: str, now: float | None = None) -> str | None:
    """None when the signature is valid and fresh; otherwise the reason."""
    try:
        ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return "bad timestamp"
    if abs((now if now is not None else time.time()) - ts) > MAX_SKEW_S:
        return "stale timestamp"
    if not hmac.compare_digest(sign(secret, timestamp, body).encode("ascii"), signature.encode("utf-8", "replace")):
        return "bad signature"
    return None


def deliver(http: httpx.Client, url: str, secret: bytes, event: str, body: dict[str, Any], *, attempts: int,
            timeout_s: float, extra_headers: dict[str, str] | None = None,
            sleep: Callable[[float], None] = time.sleep) -> DeliveryResult:
    """POST with retries on transport errors and 5xx; 4xx is final (the receiver rejected it)."""
    raw = canonical(body)
    last_status: int | None = None
    error: str | None = None
    for attempt in range(1, max(1, attempts) + 1):
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        headers = {"Content-Type": "application/json", SIGNATURE_HEADER: sign(secret, ts, raw),
                   TIMESTAMP_HEADER: ts, EVENT_HEADER: event, **(extra_headers or {})}
        try:
            resp = http.post(url, content=raw, headers=headers, timeout=timeout_s)
        except httpx.HTTPError as exc:
            error = type(exc).__name__
        else:
            last_status, error = resp.status_code, None
            if resp.status_code < 300:
                return DeliveryResult(True, resp.status_code, attempt, None)
            if resp.status_code < 500:
                return DeliveryResult(False, resp.status_code, attempt, f"rejected {resp.status_code}")
            error = f"server error {resp.status_code}"
        if attempt < attempts:
            sleep(BACKOFF_S[min(attempt - 1, len(BACKOFF_S) - 1)])
    return DeliveryResult(False, last_status, max(1, attempts), error)


def record(conn: sqlite3.Connection, settings: Settings, result: DeliveryResult, feedback_id: int, url: str) -> None:
    """Audit entry (no secret, no body): where it went and whether it arrived."""
    detail = {"feedback_id": feedback_id, "host": urlsplit(url).hostname, "status": result.status,
              "attempts": result.attempts, "error": result.error}
    audit.append(conn, settings.audit_key, ACTOR, "webhook.delivered" if result.ok else "webhook.failed", detail)


def notify_in_background(
    settings: Settings, body: dict[str, Any], feedback_id: int, *, http_factory: Callable[[], httpx.Client],
    conn_factory: Callable[[], sqlite3.Connection], extra_headers: dict[str, str] | None = None,
    owns_client: bool = True,
) -> threading.Thread:
    """Deliver + audit on a daemon thread so POST /feedback answers immediately. Returns the thread (tests join it).

    `owns_client=False` when the factory hands out a shared client (test double) that must stay open.
    """
    assert settings.webhook_url is not None and settings.webhook_secret is not None

    def work() -> None:
        http = http_factory()
        try:
            result = deliver(http, settings.webhook_url or "", settings.webhook_secret or b"", EVENT_ACTION_APPROVED, body,
                             attempts=settings.webhook_attempts, timeout_s=settings.webhook_timeout_s,
                             extra_headers=extra_headers)
        finally:
            if owns_client:
                http.close()
        conn = conn_factory()
        try:
            record(conn, settings, result, feedback_id, settings.webhook_url or "")
        except sqlite3.Error:
            logger.exception("webhook audit record failed")
        finally:
            conn.close()
        logger.info("webhook feedback=%s ok=%s status=%s attempts=%s", feedback_id, result.ok, result.status, result.attempts)

    t = threading.Thread(target=work, name=f"webhook-{feedback_id}", daemon=True)
    t.start()
    return t


__all__ = ["DeliveryResult", "EVENT_ACTION_APPROVED", "EVENT_HEADER", "SIGNATURE_HEADER", "TIMESTAMP_HEADER",
           "canonical", "deliver", "notify_in_background", "record", "sign", "verify"]
