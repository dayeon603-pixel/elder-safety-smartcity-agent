"""Health and info."""

from __future__ import annotations

import sqlite3

from typing import Any

from fastapi import APIRouter, Depends, Request
from starlette.responses import JSONResponse

from .. import __version__, ops, repo
from ..config import Settings
from ..deps import get_conn, get_settings
from ..schemas import InfoOut

router = APIRouter()
HTTP_503 = 503


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/deep")
def health_deep(request: Request, settings: Settings = Depends(get_settings)) -> Any:
    """DB (WAL, busy_timeout), migrations at head, datasets, Ollama + model, last agent run.

    200 with status ok|degraded; 503 with status fail when the demo cannot run.
    """
    factory = getattr(request.app.state, "ollama_client_factory", None)
    report = ops.deep_health(settings, factory)
    report["version"] = __version__
    code = HTTP_503 if report["status"] == ops.STATUS_FAIL else 200
    return JSONResponse(report, status_code=code)


@router.get("/info", response_model=InfoOut)
def info(conn: sqlite3.Connection = Depends(get_conn)) -> InfoOut:
    version = conn.execute("SELECT COALESCE(MAX(version), 0) AS v FROM schema_version").fetchone()["v"]
    last = repo.last_run(conn)
    return InfoOut(
        version=__version__,
        schema_version=int(version),
        datasets=[{"name": d["name"], "sha256": d["sha256"]} for d in repo.list_datasets(conn)],
        last_run=(
            {k: last[k] for k in ("id", "experiment", "role", "method", "metrics", "started_at")}
            if last
            else None
        ),
    )
