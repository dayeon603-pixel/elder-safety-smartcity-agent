"""Heat-illness alert evaluation (read-only). See ideas/G2_A_heat.md for the response contract.

Errors: 404 unknown region; 422 malformed/out-of-season date or a year without a test run;
409 `no_completed_run` when the evaluation has not been persisted (run `make heat-eval`).
Shelter data covers only some regions: those without any are reported as coverage "none".
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from pydantic import TypeAdapter

from .. import repo_heat
from ..deps import get_conn
from ..heat import common as heat_common
from ..heat import plan as heat_plan
from ..heat import pooled as heat_pooled
from ..heat import verify as heat_verify
from ..heat.plan import haversine_km
from ..schemas import (
    HeatAlerts, HeatCurve, HeatDay, HeatEvalRow, HeatMeta, HeatPlan, HeatPooled, HeatPlanEval, HeatRegionBrief,
    HeatRegionDetail,
)

router = APIRouter(prefix="/heat", tags=["heat"])

HTTP_422 = 422  # starlette renamed the constant across versions; the number is stable
Variant = Literal["F", "O"]
SEASON_START = heat_common.SEASON_START
SEASON_END = heat_common.SEASON_END
NEARBY_LIMIT = 10
TEAMS_DEFAULT, TEAMS_MIN, TEAMS_MAX = 10, 1, 100
PlanMode = Literal["spread", "coverage"]
SHELTERS_DEFAULT, SHELTERS_MIN, SHELTERS_MAX = 5, 0, 50
DATE_RE = r"^\d{4}-\d{2}-\d{2}$"
REGION_CODE_RE = r"^\d{5}$"
NO_RUN_DETAIL = {
    "code": "no_completed_run",
    "message": "No completed evaluation run in the database. Run `make heat-eval`.",
}
_REGIONS_ADAPTER = TypeAdapter(list[HeatRegionBrief])


def _no_run() -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, NO_RUN_DETAIL)


def _unprocessable(msg: str) -> HTTPException:
    return HTTPException(HTTP_422, msg)


def parse_season_date(raw: str) -> date:
    """Parse YYYY-MM-DD and require it inside the 05-20..09-25 season.

    Raises:
        HTTPException: 422 if malformed or outside the season.
    """
    try:
        d = date.fromisoformat(raw)
    except ValueError as exc:
        raise _unprocessable("date must be YYYY-MM-DD") from exc
    if d.isoformat() != raw or not SEASON_START <= (d.month, d.day) <= SEASON_END:
        raise _unprocessable("date is outside the heat season (%02d-%02d..%02d-%02d)" % (*SEASON_START, *SEASON_END))
    return d


def require_year(conn: sqlite3.Connection, variant: str, year: int) -> None:
    """409 when the variant has no completed run at all; 422 when `year` is not a test year."""
    years = repo_heat.completed_years(conn, variant)
    if not years:
        raise _no_run()
    if year not in years:
        raise _unprocessable(f"year {year} is not a test year (available: {years})")


def _run_pair(conn: sqlite3.Connection, variant: str, year: int) -> tuple[int, int]:
    b1 = repo_heat.latest_run_id(conn, repo_heat.BASELINE, variant, year)
    ours = repo_heat.latest_run_id(conn, repo_heat.OURS, variant, year)
    if b1 is None or ours is None:
        raise _no_run()
    return b1, ours


def _flag(v: int | None) -> bool | None:
    return None if v is None else bool(v)


@router.get("/meta", response_model=HeatMeta)
def meta(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    """Available test years/variants/methods (empty lists before the first evaluation) and data sources."""
    return {
        "years": repo_heat.completed_years(conn),
        "variants": repo_heat.completed_variants(conn),
        "methods": repo_heat.run_methods(conn),
        "season": {"start": "%02d-%02d" % SEASON_START, "end": "%02d-%02d" % SEASON_END},
        "sources": repo_heat.list_sources(conn),
    }


@router.get("/eval", response_model=list[HeatEvalRow])
def evaluation(
    variant: Variant = "F",
    budget: Literal["per_day_b1", "season_total"] = "per_day_b1",
    conn: sqlite3.Connection = Depends(get_conn),
) -> list[dict[str, Any]]:
    """Per year x method x metric with 95% block-bootstrap CI and the paired difference vs B1."""
    rows = repo_heat.eval_rows(conn, variant, budget)
    if not rows:
        raise _no_run()
    return [
        {
            "year": r["year"], "method": r["method"], "metric": r["metric"], "value": r["value"],
            "ci_lo": r["ci_lo"], "ci_hi": r["ci_hi"],
            "delta_vs_b1": (
                None if r["d_value"] is None
                else {"value": r["d_value"], "ci_lo": r["d_lo"], "ci_hi": r["d_hi"]}
            ),
        }
        for r in rows
    ]


@router.get("/eval/pooled", response_model=HeatPooled)
def evaluation_pooled(variant: Variant = "F") -> dict[str, Any]:
    """Pooled (all test years) metrics from experiments/heat/out/results.json, with its sha256.

    409 no_completed_run when the file is missing/unreadable or has no pooled block.
    """
    try:
        return heat_pooled.pooled_results(variant)
    except heat_pooled.PooledUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, {**NO_RUN_DETAIL, "reason": str(exc)}) from exc


VERIFY_BOOT_MIN, VERIFY_BOOT_MAX = 100, 5000


@router.get("/verify")
def verify(n_boot: int = Query(heat_verify.N_BOOT_DEFAULT, ge=VERIFY_BOOT_MIN, le=VERIFY_BOOT_MAX),
           conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    """재현: recompute pooled capture + paired block-bootstrap CI from stored predictions and cases; compare with results.json.

    409 no_completed_run when no runs are stored. `lines` are projector-ready Korean lines (same as `make verify-heat`).
    """
    try:
        return heat_verify.verify(conn, n_boot)
    except heat_verify.VerifyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, {**NO_RUN_DETAIL, "reason": str(exc)}) from exc


@router.get("/curve", response_model=HeatCurve)
def curve(
    year: int = Query(..., ge=2000, le=2100),
    variant: Variant = "F",
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Capture rate against a budget scale (x = multiple of B1's daily alert count)."""
    require_year(conn, variant, year)
    b1, _ = _run_pair(conn, variant, year)
    b1_capture = repo_heat.run_capture(conn, b1)
    if b1_capture is None:
        raise _no_run()
    series: dict[str, list[dict[str, float]]] = {}
    for p in repo_heat.curve_points(conn, variant, year):
        series.setdefault(p["method"], []).append({"x": p["x"], "capture": p["capture"]})
    return {"b1_point": {"x": 1.0, "capture": b1_capture}, "series": series}


@router.get("/days", response_model=list[HeatDay])
def days(
    year: int = Query(..., ge=2000, le=2100),
    sort: Literal["gap", "date"] = "gap",
    variant: Variant = "F",
    conn: sqlite3.Connection = Depends(get_conn),
) -> list[dict[str, Any]]:
    """Season days; `gap` puts the days where we caught the most extra cases over B1 first."""
    require_year(conn, variant, year)
    b1, ours = _run_pair(conn, variant, year)
    rows = repo_heat.days(conn, b1, ours, year)
    if sort == "gap":
        rows.sort(key=lambda r: (-(r["ours_captured"] - r["b1_captured"]), r["date"]))
    return rows


@router.get("/regions", response_model=list[HeatRegionBrief])
def region_list(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Response:
    """All regions (static between ingests). Strong ETag over the body; honours If-None-Match."""
    body = _REGIONS_ADAPTER.dump_json(_REGIONS_ADAPTER.validate_python(repo_heat.regions(conn)))
    etag = '"%s"' % hashlib.sha256(body).hexdigest()[:32]
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    wanted = {t.strip().removeprefix("W/") for t in request.headers.get("if-none-match", "").split(",")}
    if etag in wanted or "*" in wanted:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


@router.get("/alerts", response_model=HeatAlerts)
def alerts(
    date_: str = Query(..., alias="date", pattern=DATE_RE),
    variant: Variant = "F",
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """B1 alerts vs ours on one day (equal count K), with the day's actual cases per region."""
    d = parse_season_date(date_)
    require_year(conn, variant, d.year)
    b1, ours = _run_pair(conn, variant, d.year)
    b1_cells = repo_heat.alerted_cells(conn, b1, date_)
    return {
        "date": date_,
        "k": len(b1_cells),
        "total_cases": repo_heat.total_cases_on(conn, date_),
        "b1": b1_cells,
        "ours": repo_heat.alerted_cells(conn, ours, date_),
        "cases": repo_heat.cases_on(conn, date_),
    }


@router.get("/region/{code}", response_model=HeatRegionDetail)
def region_detail(
    code: str = Path(..., pattern=REGION_CODE_RE),
    date_: str = Query(..., alias="date", pattern=DATE_RE),
    variant: Variant = "F",
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Why a region was (not) alerted on a day, and the shelters on record for it.

    drivers.tmax / drivers.apparent_max are the nearest ASOS station's daily max 기온 / 체감온도
    (KST 10-19h) on the date (variant F) or the day before (variant O, which sees nothing later than
    D-1); drivers.station names that station. drivers.hist_rate is the unshrunk mean cases per season
    day over all earlier years in the database. Shelter distances are from the region centroid.
    """
    d = parse_season_date(date_)
    region = repo_heat.get_region(conn, code)
    if region is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "region not found")
    require_year(conn, variant, d.year)
    b1, ours = _run_pair(conn, variant, d.year)
    ours_score = repo_heat.cell_score(conn, ours, code, date_)
    tmax_day = d if variant == "F" else d - timedelta(days=1)
    wx = repo_heat.station_weather(conn, code, tmax_day.isoformat())
    rows = repo_heat.region_shelters(conn, code)
    near = sorted(
        (
            {
                "name": s["name"], "dist_km": round(haversine_km(region["lat"], region["lon"], s["lat"], s["lon"]), 3),
                "night_open": _flag(s["night_open"]), "weekend_open": _flag(s["weekend_open"]),
            }
            for s in rows
        ),
        key=lambda s: (s["dist_km"], s["name"]),
    )[:NEARBY_LIMIT]
    return {
        "region": region,
        "cases_today": repo_heat.cases_in_region(conn, code, date_),
        "scores": {
            "b1": repo_heat.cell_score(conn, b1, code, date_),
            "ours": ours_score,
            "rank": None if ours_score is None else repo_heat.cell_rank(conn, ours, date_, ours_score),
        },
        "drivers": {
            "tmax": wx["tmax"], "apparent_max": wx["apparent_max"], "station": wx["station"],
            "hist_rate": repo_heat.prior_case_rate(conn, code, f"{d.year}-01-01"),
            "old_share": region["pop65"] / region["pop"],
        },
        "shelters": {"coverage": "data" if rows else "none", "nearby": near},
    }


@router.get("/plan", response_model=HeatPlan)
def action_plan(
    date_: str = Query(..., alias="date", pattern=DATE_RE),
    variant: Variant = "F",
    teams: int = Query(TEAMS_DEFAULT, ge=TEAMS_MIN, le=TEAMS_MAX),
    shelters: int = Query(SHELTERS_DEFAULT, ge=SHELTERS_MIN, le=SHELTERS_MAX),
    mode: PlanMode = "spread",
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Where to send `teams` visiting teams and which `shelters` to keep open nights/weekends.

    mode=spread (default): one team per region, ranked by lambda. mode=coverage: stacked teams under
    an assumed per-team reach (assumption-based, not the headline).

    Uses only the stored OURS scores of the day (no case data); `actual_cases` and `compare` are
    attached for evaluation. `compare` is the same teams spread by the B1 rule. Shelters with
    unknown hours are never proposed and are counted in `unknown_hours_count`.
    """
    d = parse_season_date(date_)
    require_year(conn, variant, d.year)
    b1, ours = _run_pair(conn, variant, d.year)
    return heat_plan.build_day_plan(conn, b1, ours, date_, variant, teams, shelters, mode)


@router.get("/plan/eval", response_model=HeatPlanEval)
def action_plan_eval(
    year: int = Query(..., ge=2000, le=2100),
    teams: int = Query(TEAMS_DEFAULT, ge=TEAMS_MIN, le=TEAMS_MAX),
    variant: Variant = "F",
    mode: PlanMode = "spread",
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Season total of actual cases in regions that got a team: our plan vs the B1 plan, same teams."""
    require_year(conn, variant, year)
    b1, ours = _run_pair(conn, variant, year)
    return heat_plan.evaluate_year(conn, b1, ours, year, teams, mode)
