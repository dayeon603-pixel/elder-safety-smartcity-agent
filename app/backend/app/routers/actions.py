"""GET /actions/{sgg_code}: recommendation drafts for four action channels (never sends anything).

Errors: 404 unknown region; 422 malformed/out-of-season date, bad limit; 409 `no_completed_run`
when the heat evaluation is not stored; 503 `data_unavailable` when a source file is missing.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status

from ..actions import constants as C
from ..actions import cbs, service
from ..actions.models import ActionsResponse
from ..config import Settings
from ..deps import get_conn, get_settings
from ..medical import region_of
from ..modules import briefing as brief
from .heat import NO_RUN_DETAIL, parse_season_date, require_year

router = APIRouter(prefix="/actions", tags=["actions"])


@router.get("/{sgg_code}", response_model=ActionsResponse)
def actions(
    request: Request,
    sgg_code: str = Path(..., pattern=C.SGG_CODE_RE),
    date_: str = Query(..., alias="date", pattern=C.DATE_RE),
    teams: int = Query(C.TEAMS_DEFAULT, ge=C.TEAMS_MIN, le=C.TEAMS_MAX),
    cbs_limit: int = Query(C.CBS_LIMIT_CURRENT, description="CBS character limit: 157 (157자형 폭염 경보 표준문안) or 90 (90자형 표준)"),
    bit_top_n: int = Query(C.BIT_TOP_N_DEFAULT, ge=1, le=C.BIT_TOP_N_MAX),
    signals_limit: int = Query(C.SIGNALS_LIMIT_DEFAULT, ge=1, le=C.SIGNALS_LIMIT_MAX),
    route_stops: int = Query(C.ROUTE_STOPS_DEFAULT, ge=C.ROUTE_STOPS_MIN, le=C.ROUTE_STOPS_MAX),
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> Any:
    """CBS draft (+ 쉬운 말), BIT stops, crosswalk extensions and a visiting route for one 시군구."""
    if cbs_limit not in C.CBS_LIMITS:
        raise HTTPException(422, f"cbs_limit must be one of {list(C.CBS_LIMITS)}")
    day = parse_season_date(date_)
    require_year(conn, service.VARIANT, day.year)
    try:
        resp = service.build_actions(
            conn, settings.data_dir, sgg_code, day, teams, cbs_limit, bit_top_n, signals_limit, route_stops
        )
    except service.RegionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown region {sgg_code}") from exc
    except cbs.MessageTooLongError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            {"code": "cbs_too_long", "message": f"재난문자 필수 문구가 {cbs_limit}자 한도를 넘습니다: {exc}"}) from exc
    except service.NoRunError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, NO_RUN_DETAIL) from exc
    except service.DataUnavailableError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, {"code": "data_unavailable", "message": str(exc)}
        ) from exc
    resp.evidence["medical_night"] = brief.medical_section(request.app.state.medical, region_of(conn, sgg_code), date_)
    return resp
