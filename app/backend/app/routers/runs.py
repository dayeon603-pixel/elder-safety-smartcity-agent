"""Experiment runs."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Query

from .. import repo
from ..deps import get_conn
from ..schemas import RunOut

router = APIRouter(prefix="/runs", tags=["runs"])
MAX_PAGE = 200


@router.get("", response_model=list[RunOut])
def list_runs(
    limit: int = Query(50, ge=1, le=MAX_PAGE),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(get_conn),
) -> list[dict[str, Any]]:
    return repo.list_runs(conn, limit, offset)


@router.get("/comparison", response_model=list[RunOut])
def comparison(conn: sqlite3.Connection = Depends(get_conn)) -> list[dict[str, Any]]:
    """Baseline and ours of the most recent experiment."""
    return repo.latest_comparison(conn)
