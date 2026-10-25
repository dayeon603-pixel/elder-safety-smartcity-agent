"""GET /documents/{action_id}.docx?sgg_code=&date=[&teams=] : 공문 초안 generated locally (demo token).

Facts come from the same code paths as /actions and /briefing (service.build_actions, modules.briefing),
so the letter and the screen never disagree. 404 unknown action/region, 409 no evaluation run,
503 missing source data.
"""

from __future__ import annotations

import sqlite3
from datetime import date as date_type
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from starlette.responses import Response

from .. import repo_heat
from ..actions import constants as AC
from ..actions import cbs, service
from ..config import Settings
from ..deps import get_conn, get_settings
from ..documents import ACTION_DOCS, build_docx, render
from ..documents.docx import DOCX_MEDIA_TYPE
from ..modules import briefing as brief
from ..modules import repo as mrepo
from ..modules.evidence import heat_evidence
from .agent import require_demo_token
from .heat import NO_RUN_DETAIL, parse_season_date, require_year

router = APIRouter(prefix="/documents", tags=["documents"], dependencies=[Depends(require_demo_token)])
ACTION_RE = r"^[a-z0-9][a-z0-9_.]{0,63}$"
SGG_RE = r"^[0-9]{5}$"
DATE_RE = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
ACTIONS_CHANNELS = ("cbs", "bit", "signals", "route")


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{round(x * 100)}"


def _payload(conn: sqlite3.Connection, settings: Settings, action_id: str, region: dict[str, Any], day: date_type,
             teams: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, str] | None]:
    """(evidence, action payload, sources) for the letter."""
    iso = day.isoformat()
    if action_id in ACTIONS_CHANNELS:
        try:
            resp = service.build_actions(conn, settings.data_dir, region["code"], day, teams)
        except service.NoRunError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, NO_RUN_DETAIL) from exc
        except service.DataUnavailableError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {"code": "data_unavailable", "message": str(exc)}) from exc
        except cbs.MessageTooLongError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, {"code": "cbs_too_long", "message": str(exc)}) from exc
        data = resp.model_dump(mode="json")
        payload: dict[str, Any] = {
            "cbs": data["cbs"],
            "bit": {"stops": data["bit"], "lines": (data["bit"][0]["message"]["lines"] if data["bit"] else [])},
            "signals": {"signals": data["signals"] if isinstance(data["signals"], list) else []},
            "route": data["route"],
        }[action_id]
        return data["evidence"], payload, {action_id: str(data["sources"].get(action_id, AC.SOURCES.get(action_id, "")))}
    require_year(conn, brief.VARIANT, day.year)
    b1 = repo_heat.latest_run_id(conn, repo_heat.BASELINE, brief.VARIANT, day.year)
    ours = repo_heat.latest_run_id(conn, repo_heat.OURS, brief.VARIANT, day.year)
    if b1 is None or ours is None:
        raise HTTPException(status.HTTP_409_CONFLICT, NO_RUN_DETAIL)
    heat = brief.heat_section(conn, region, iso, b1, ours, teams)
    ev = heat["evidence"]
    if action_id == "heat.alert":
        return ev, {}, None
    if action_id == "heat.teams":
        plan = heat["plan_allocation_for_region"]
        return ev, {"teams": plan["teams"], "teams_total": plan["teams_total"]}, None
    if action_id == "silver.check":
        s = brief.silver_section(conn, region)
        return ev, {"hotspots_2024": s["hotspots_2024_in_region"], "covered_current": s["hotspots_covered_current"],
                    "covered_ours": s["hotspots_covered_ours"], "our_picks": s["our_picks_in_region"]}, None
    aed, note = brief.aed_section(conn, region)
    if aed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, note or "no AED data for this region")
    top = aed["candidates_top5"][0] if aed["candidates_top5"] else {}
    return ev, {"night_open_share_pct": _pct(aed["night_open_share"]), "day_open_share_pct": _pct(aed["day_open_share"]),
                "candidate": top}, None


@router.get("/{action_id}.docx")
def document(
    action_id: str = Path(..., pattern=ACTION_RE),
    sgg_code: str = Query(..., pattern=SGG_RE),
    date_: str = Query(..., alias="date", pattern=DATE_RE),
    teams: int = Query(AC.TEAMS_DEFAULT, ge=AC.TEAMS_MIN, le=AC.TEAMS_MAX),
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> Response:
    if action_id not in ACTION_DOCS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no letter template for action {action_id!r}")
    day = parse_season_date(date_)
    region = mrepo.region_by_code(conn, sgg_code)
    if region is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "region not found")
    ev, payload, sources = _payload(conn, settings, action_id, region, day, teams)
    if not ev:
        full = repo_heat.get_region(conn, sgg_code)
        assert full is not None
        ev = heat_evidence(conn, full, date_, score=None, rank=None, n_cells=None, alerted=None)
    name = f"{region['sido']} {region['name']}"
    title, meta, body, rows, footer = render(action_id, name, date_, ev, payload, sources)
    data = build_docx(title, meta, body, rows, footer)
    fname = f"{action_id}_{sgg_code}_{date_}.docx"
    return Response(content=data, media_type=DOCX_MEDIA_TYPE,
                    headers={"Content-Disposition": f'attachment; filename="{fname}"', "X-Document-Status": "draft"})
