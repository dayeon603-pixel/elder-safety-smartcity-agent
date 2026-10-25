"""Registration and login."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
import time
from collections import OrderedDict

from fastapi import APIRouter, Depends, HTTPException, Request, status

from .. import db, repo
from ..config import Settings
from ..deps import get_conn, get_settings
from ..schemas import Credentials, LoginIn, RegisterOut, TokenOut
from ..security import audit, passwords, tokens
from ..security.ratelimit import client_key

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

FAILED_LOGIN_WINDOW_S = 60
MAX_TRACKED_CLIENTS = 10_000
CLIENT_DIGEST_CHARS = 16


class FailureAggregator:
    """At most one `auth.login_failed` audit row per client key per window.

    Failures inside the window are only counted; the count is reported on the first
    failure of the next window. An attacker therefore adds O(1) audit rows per key per
    minute instead of one per attempt. State is per process and bounded (LRU).
    """

    def __init__(self, window_s: int = FAILED_LOGIN_WINDOW_S, max_keys: int = MAX_TRACKED_CLIENTS) -> None:
        self._window = window_s
        self._max = max_keys
        self._state: OrderedDict[str, tuple[int, int]] = OrderedDict()
        self._lock = threading.Lock()

    def record(self, key: str, now: float | None = None) -> tuple[bool, int, int]:
        """Return (should_write, window_index, suppressed_in_previous_window)."""
        window = int((time.time() if now is None else now) // self._window)
        with self._lock:
            prev_window, count = self._state.get(key, (-1, 0))
            if prev_window == window:
                self._state[key] = (window, count + 1)
                self._state.move_to_end(key)
                return False, window, 0
            self._state[key] = (window, 1)
            self._state.move_to_end(key)
            while len(self._state) > self._max:
                self._state.popitem(last=False)
            return True, window, max(0, count - 1) if prev_window >= 0 else 0


def _token_for(settings: Settings, user: dict[str, object]) -> TokenOut:
    tok = tokens.issue_token(settings.secret_key, int(str(user["id"])), str(user["role"]), settings.token_ttl_s)
    return TokenOut(access_token=tok, expires_in=settings.token_ttl_s)


@router.post("/register", response_model=RegisterOut, status_code=status.HTTP_202_ACCEPTED)
def register(
    body: Credentials,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> RegisterOut:
    """Create an account if the email is free; the reply is identical either way.

    No token is returned (that would let anyone who knows an email log in as it);
    the client signs in with POST /auth/login. The Argon2 hash is computed before the
    lookup so both paths cost about the same.
    """
    pw_hash = passwords.hash_password(body.password)
    email = body.email
    role = "admin" if settings.admin_email and email.lower() == settings.admin_email else "member"
    try:
        with db.transaction(conn):
            user = repo.create_user(conn, email, pw_hash, role)
            audit.append(conn, settings.audit_key, f"user:{user['id']}", "auth.register", {"role": role})
    except sqlite3.IntegrityError:
        logger.info("registration for an existing email ignored")
    return RegisterOut()


@router.post("/login", response_model=TokenOut)
def login(
    body: LoginIn,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> TokenOut:
    user = repo.get_user_by_email(conn, body.email)
    ok = passwords.verify_password(user["password_hash"] if user else None, body.password)
    if not ok or user is None or not user["is_active"]:
        key = hashlib.sha256(client_key(request, settings.trust_proxy).encode()).hexdigest()[:CLIENT_DIGEST_CHARS]
        failures: FailureAggregator = request.app.state.login_failures
        write, window, suppressed = failures.record(key)
        if write:
            audit.append(
                conn, settings.audit_key, "anonymous", "auth.login_failed",
                {"client": key, "window": window, "suppressed_in_previous_window": suppressed},
            )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    new_hash = (
        passwords.hash_password(body.password) if passwords.needs_rehash(user["password_hash"]) else None
    )
    with db.transaction(conn):
        if new_hash is not None:
            repo.update_password_hash(conn, int(user["id"]), new_hash)
        audit.append(conn, settings.audit_key, f"user:{user['id']}", "auth.login", {})
    return _token_for(settings, user)
