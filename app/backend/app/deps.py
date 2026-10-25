"""FastAPI dependencies: settings, per-request DB connection, authenticated user."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Any

from fastapi import Depends, HTTPException, Request, status

from . import db, repo
from .config import Settings
from .security import tokens


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_conn(settings: Settings = Depends(get_settings)) -> Iterator[sqlite3.Connection]:
    conn = db.connect(settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


def current_user(
    request: Request,
    settings: Settings = Depends(get_settings),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Resolve the Bearer token to an active user or raise 401."""
    header = request.headers.get("authorization", "")
    scheme, _, raw = header.partition(" ")
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "invalid or missing token", headers={"WWW-Authenticate": "Bearer"}
    )
    if scheme.lower() != "bearer" or not raw:
        raise unauthorized
    try:
        claims = tokens.verify_token(settings.secret_key, raw.strip())
    except tokens.TokenError as exc:
        raise unauthorized from exc
    user = repo.get_user_by_id(conn, int(claims["sub"]))
    if user is None or not user["is_active"]:
        raise unauthorized
    return user
