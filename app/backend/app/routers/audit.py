"""Audit chain verification (rate-limited, briefly cached)."""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Callable

from fastapi import APIRouter, Depends, Request

from ..config import Settings
from ..deps import get_conn, get_settings
from ..schemas import AuditVerifyOut
from ..security import audit as audit_log

router = APIRouter(prefix="/audit", tags=["audit"])


class VerifyCache:
    """Process-local TTL cache: verification is O(n) HMACs, so repeated calls reuse it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value: tuple[float, AuditVerifyOut] | None = None

    def get_or_compute(self, ttl_s: float, compute: Callable[[], AuditVerifyOut]) -> AuditVerifyOut:
        with self._lock:
            now = time.monotonic()
            if self._value is not None and now - self._value[0] < ttl_s:
                return self._value[1]
            result = compute()
            self._value = (now, result)
            return result


@router.get("/verify", response_model=AuditVerifyOut)
def verify(
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> AuditVerifyOut:
    """Recompute the HMAC chain and return the head hash to pin out of band."""

    def compute() -> AuditVerifyOut:
        r = audit_log.verify(conn, settings.audit_key)
        return AuditVerifyOut(
            valid=r.valid, entries=r.entries, head=r.head, broken_at=r.broken_at, reason=r.reason
        )

    cache: VerifyCache = request.app.state.verify_cache
    return cache.get_or_compute(settings.audit_verify_ttl_s, compute)

