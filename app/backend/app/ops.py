"""Operational checks shared by `/health/deep`, the startup self-test and `make demo`.

Every external probe has a timeout; nothing here can hang. Each check reports `ok` plus the
evidence it looked at, so a failing demo box tells you *what* is missing.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timezone
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from . import db, repo_heat
from .agent import constants as C
from .agent import store
from .agent.ollama import OllamaClient, OllamaUnavailable
from .config import Settings
from .medical import hours
from .modules import repo as modules_repo

logger = logging.getLogger(__name__)

STATUS_OK, STATUS_DEGRADED, STATUS_FAIL = "ok", "degraded", "fail"
HARD_CHECKS = ("db", "migrations", "datasets")  # any of these failing = the demo cannot run
MIN_REGIONS = 1


@dataclass(frozen=True, slots=True)
class Check:
    ok: bool
    detail: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, **self.detail}


def check_db(settings: Settings) -> tuple[Check, Check, Check, Check]:
    """(db, migrations, datasets, last_agent_run) from one short-lived connection."""
    try:
        conn = db.connect(settings.db_path)
    except sqlite3.Error as exc:
        bad = Check(False, {"error": type(exc).__name__})
        return bad, bad, bad, Check(True, {"run": None})
    try:
        mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
        busy = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
        db_check = Check(mode.lower() == "wal" and busy >= db.BUSY_TIMEOUT_MS,
                         {"path": str(settings.db_path), "journal_mode": mode, "busy_timeout_ms": busy})
        head = max((v for v, _, _ in db._discover(db.DB_DIR)), default=0)
        try:
            applied = max(db._read_applied(conn), default=0)
        except sqlite3.Error:
            applied = 0
        migrations = Check(applied == head and head > 0, {"applied": applied, "head": head})
        datasets = _datasets(conn) if migrations.ok else Check(False, {"error": "schema not at head"})
        try:
            last = store.last_run(conn) if migrations.ok else None
        except sqlite3.Error:
            last = None
        last_run = Check(last is None or bool(last["ok"]), {"run": last})
    finally:
        conn.close()
    return db_check, migrations, datasets, last_run


def _datasets(conn: sqlite3.Connection) -> Check:
    regions = int(conn.execute("SELECT COUNT(*) FROM region").fetchone()[0])
    years = repo_heat.completed_years(conn)
    weather_days = int(conn.execute("SELECT COUNT(*) FROM weather_daily").fetchone()[0])
    silver, aed = modules_repo.silver_loaded(conn), modules_repo.aed_loaded(conn)
    detail = {"regions": regions, "heat_test_years": years, "weather_days": weather_days,
              "silver_loaded": silver, "aed_loaded": aed}
    return Check(regions >= MIN_REGIONS and bool(years) and silver and aed, detail)


def check_ollama(base_url: str = C.OLLAMA_URL, client_factory: Callable[[], httpx.Client] | None = None) -> Check:
    """Reachable + planner model installed + resident in memory (`/api/ps`)."""
    http = client_factory() if client_factory else httpx.Client(base_url=base_url)
    try:
        client = OllamaClient(http)
        try:
            models = client.models()
        except OllamaUnavailable as exc:
            return Check(False, {"url": base_url, "reachable": False, "error": str(exc)})
        try:
            resident = client.loaded()
        except OllamaUnavailable:
            resident = []
        installed = C.PLANNER_MODEL in models
        return Check(installed, {
            "url": base_url, "reachable": True, "planner_model": C.PLANNER_MODEL, "installed": installed,
            "loaded": C.PLANNER_MODEL in resident, "vlm_installed": C.VLM_MODEL in models,
            "vlm_loaded": C.VLM_MODEL in resident, "keep_alive": C.KEEP_ALIVE,
        })
    finally:
        http.close()


def warm_up(models: tuple[str, ...], base_url: str = C.OLLAMA_URL,
            client_factory: Callable[[], httpx.Client] | None = None) -> dict[str, float | None]:
    """Preload `models` with KEEP_ALIVE; returns {model: load_ms or None on failure}. Never raises."""
    http = client_factory() if client_factory else httpx.Client(base_url=base_url)
    out: dict[str, float | None] = {}
    try:
        client = OllamaClient(http)
        for model in models:
            t0 = time.perf_counter()
            try:
                client.warm_up(model)
                out[model] = round((time.perf_counter() - t0) * 1000, 1)
                logger.info("warm-up %s: %.0f ms (keep_alive %s)", model, out[model], C.KEEP_ALIVE)
            except OllamaUnavailable as exc:
                out[model] = None
                logger.warning("warm-up %s failed: %s", model, exc)
    finally:
        http.close()
    return out


def deep_health(settings: Settings, ollama_factory: Callable[[], httpx.Client] | None = None) -> dict[str, Any]:
    """Full report. `status` is fail when a hard check fails, degraded when only Ollama is off."""
    db_check, migrations, datasets, last_run = check_db(settings)
    ollama = check_ollama(client_factory=ollama_factory)
    today = datetime.now(timezone.utc).date()
    holidays = Check(hours.holiday_table_covers(today), {"covered_through": hours.HOLIDAY_TABLE_END_YEAR, "today": today.isoformat(),
                                                         "note": "medical/hours.py 설·추석·대체공휴일 표; 범위 밖 연도는 고정 공휴일만 인식"})
    checks = {"db": db_check, "migrations": migrations, "datasets": datasets, "ollama": ollama,
              "last_agent_run": last_run, "holiday_table": holidays}
    if not all(checks[k].ok for k in HARD_CHECKS) or (settings.require_ollama and not ollama.ok):
        status = STATUS_FAIL
    elif not ollama.ok or not ollama.detail.get("loaded") or not last_run.ok or not holidays.ok:
        status = STATUS_DEGRADED
    else:
        status = STATUS_OK
    return {"status": status, "checks": {k: v.as_dict() for k, v in checks.items()}}


__all__ = ["STATUS_DEGRADED", "STATUS_FAIL", "STATUS_OK", "check_db", "check_ollama", "deep_health", "warm_up"]
