"""Silver-zone, AED night-gap and briefing endpoints (read-only; data from `make modules-load`).

Errors: 404 unknown region / 구; 409 `no_module_data` when the module tables are empty;
409 `no_completed_run` (heat) for the briefing without a persisted heat evaluation; 422 bad input.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status

from .. import repo_heat
from ..deps import get_conn
from ..medical import region_of
from ..modules import briefing as brief
from ..modules import repo, silver_config
from ..modules.rank import poisson_rank
from ..modules.schemas import (
    AedCandidate, AedGu, AedHour, AedSummary, Briefing, SilverMap, SilverSummary,
)
from .heat import DATE_RE, NO_RUN_DETAIL, REGION_CODE_RE, TEAMS_DEFAULT, TEAMS_MAX, TEAMS_MIN
from .heat import parse_season_date, require_year

silver_router = APIRouter(prefix="/silver", tags=["silver"])
aed_router = APIRouter(prefix="/aed", tags=["aed"])
briefing_router = APIRouter(prefix="/briefing", tags=["briefing"])

NO_DATA_DETAIL = {"code": "no_module_data", "message": "Module tables are empty. Run `make modules-load`."}
GU_NAME_MAX = 16
METHOD_LABEL = {
    "current": "현행", "random_k": "무작위", "pop65_k": "고령인구 순", "past_k": "과거 사고 순", "ours_full": "에이전트",
    "ours_opt": "에이전트",
}
DEFINITIONS_SILVER = {
    "coverage": "포착률 (300m 안에 구역이 있는 사고다발지 ÷ 전체 사고다발지)",
    "ci": "95% 신뢰구간 (같은 실험을 반복해도 결과가 이 범위 안에 들어올 가능성이 높은 구간)",
    "pp": "%p (퍼센트끼리의 차이: 42% → 56% = 14%p)",
    "new": "new = 과거 사고다발지 300m 밖에 새로 생긴 사고다발지",
}
DEFINITIONS_AED = {
    "open_share": "열림 비율 (개방시간표상 그 시각에 열려 있는 AED ÷ 전체 AED)",
    "night": "밤 = 22시~06시",
    "public_arrest": "공공장소 심정지 (집 밖에서 발생한 심정지)",
    "ci": "95% 신뢰구간 (같은 실험을 반복해도 결과가 이 범위 안에 들어올 가능성이 높은 구간)",
}
AED_SCOPE = "서울 25개 구, 소방청 구급활동정보 2024-01~11, 개방시간표 기준 (실제 보행 4분 거리 분석 아님)"


def _no_data() -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, NO_DATA_DETAIL)


def _need_silver(conn: sqlite3.Connection) -> None:
    if not repo.silver_loaded(conn):
        raise _no_data()


def _need_aed(conn: sqlite3.Connection) -> None:
    if not repo.aed_loaded(conn):
        raise _no_data()


def _sources(conn: sqlite3.Connection, module: str) -> list[dict[str, Any]]:
    return [{**s, "loaded": bool(s["loaded"])} for s in repo.sources(conn, module)]


@silver_router.get("/summary", response_model=SilverSummary)
def silver_summary(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    """Headline at the largest k: ours (promoted ours_opt, pool OPT by default) vs the pool-C baselines, 95% CIs."""
    _need_silver(conn)
    k = repo.max_k(conn)
    assert k is not None
    results = repo.silver_results(conn, k)
    deltas = repo.silver_deltas(conn, k)
    strata: list[dict[str, Any]] = []
    for stratum in repo.STRATA:
        by_method = {r["method"]: r for r in results if r["stratum"] == stratum}
        if not by_method:
            continue
        any_row = next(iter(by_method.values()))
        strata.append({
            "stratum": stratum, "n_hot": any_row["n_hot"], "casualties": any_row["casualties"],
            "methods": [
                {"method": m, "label": METHOD_LABEL[m], "coverage": by_method[m]["coverage"],
                 "ci_lo": by_method[m]["ci_lo"], "ci_hi": by_method[m]["ci_hi"]}
                for m in silver_config.HEADLINE_METHODS if m in by_method
            ],
            "ours_minus": [
                {"vs": d["vs"], "label": METHOD_LABEL[d["vs"].split(":", 1)[1]], "delta_pp": d["delta_pp"],
                 "lo_pp": d["lo_pp"], "hi_pp": d["hi_pp"]}
                for m in silver_config.HEADLINE_METHODS for d in deltas
                if d["stratum"] == stratum and d["vs"] == f"{silver_config.COMPARISON_POOL}:{m}"
            ],
        })
    return {
        "k": k, "variant": silver_config.HEADLINE_VARIANT, "pool": silver_config.HEADLINE_POOL,
        "ours_method": silver_config.OURS, "comparison_pool": silver_config.COMPARISON_POOL,
        "source": silver_config.SILVER_SOURCE,
        "definitions": DEFINITIONS_SILVER, "strata": strata, "sources": _sources(conn, "silver"),
    }


@silver_router.get("/map", response_model=SilverMap)
def silver_map(city: Literal["seoul"] = "seoul", conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    """Current zones, our picks and 2024 hotspots with coverage flags (the k=3918 map of the experiment)."""
    _need_silver(conn)
    k = repo.max_k(conn)
    assert k is not None

    def sites(kind: str) -> list[dict[str, Any]]:
        return [{**s, "near_current": bool(s["near_current"]), "near_ours": bool(s["near_ours"])}
                for s in repo.silver_sites(conn, kind)]

    return {
        "city": city, "k": k, "current": sites("current"), "ours": sites("ours"),
        "hotspots_2024": [
            {**h, "covered_current": bool(h["covered_current"]), "covered_ours": bool(h["covered_ours"])}
            for h in repo.silver_hotspots(conn)
        ],
        "sources": _sources(conn, "silver"),
    }


def _interval(stats: dict[str, dict[str, Any]], name: str) -> dict[str, Any]:
    s = stats[name]
    return {"value": s["value"], "ci_lo": s["ci_lo"], "ci_hi": s["ci_hi"], "n": s["n"]}


@aed_router.get("/summary", response_model=AedSummary)
def aed_summary(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    """Day vs night open share, night share of public arrests (95% CI) and the five worst 구."""
    _need_aed(conn)
    stats = repo.aed_stats(conn)

    def shares(sched: str) -> dict[str, float]:
        return {
            "day": stats[f"{sched}.open_share_day"]["value"], "night": stats[f"{sched}.open_share_night"]["value"],
            "day_arrest_weighted": stats[f"{sched}.open_share_day_arrest_weighted"]["value"],
            "night_arrest_weighted": stats[f"{sched}.open_share_night_arrest_weighted"]["value"],
        }

    return {
        "scope": AED_SCOPE, "definitions": DEFINITIONS_AED, "weekday": shares("weekday"), "weekend": shares("weekend"),
        "public_arrests_total": int(stats["public_arrests_total"]["value"]),
        "night_share_of_public_arrests": _interval(stats, "public_arrests_night_share"),
        "share_arrests_open_lt_50pct": _interval(stats, "weekday.share_arrests_open_lt_50pct"),
        "worst_gu": repo.aed_gu_list(conn)[: repo.WORST_GU_COUNT],
        "sources": _sources(conn, "aed"),
    }


@aed_router.get("/hourly", response_model=list[AedHour])
def aed_hourly(conn: sqlite3.Connection = Depends(get_conn)) -> list[dict[str, Any]]:
    """24 hours: public arrests and the share of AEDs open on weekdays / weekends."""
    _need_aed(conn)
    return repo.aed_hourly(conn)


@aed_router.get("/gu", response_model=list[AedGu])
def aed_gu(conn: sqlite3.Connection = Depends(get_conn)) -> list[dict[str, Any]]:
    """Per-구 night gap, worst first, each with its Poisson-bootstrap rank interval and top-5 probability."""
    _need_aed(conn)
    rows = repo.aed_gu_list(conn)
    events, factors = repo.aed_gu_events(conn)
    for r in rows:
        unc = poisson_rank(events, factors, str(r["gu"]))
        r["rank_interval"], r["top5_probability"], r["rank_text"] = [unc.rank_lo, unc.rank_hi], round(unc.top_k_probability, 3), unc.text()
    return rows


@aed_router.get("/candidates", response_model=list[AedCandidate])
def aed_candidates(
    gu: str | None = Query(None, min_length=1, max_length=GU_NAME_MAX), conn: sqlite3.Connection = Depends(get_conn)
) -> list[dict[str, Any]]:
    """Top 5 24-hour conversion candidates per 구 (one 구 with ?gu=, else all). 구 not on the list: empty."""
    _need_aed(conn)
    if gu is not None and repo.aed_gu_by_name(conn, gu) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "gu not found")
    return repo.aed_candidates(conn, gu)


@briefing_router.get("/{sgg_code}", response_model=Briefing)
def briefing(
    request: Request,
    sgg_code: str = Path(..., pattern=REGION_CODE_RE),
    date_: str = Query(..., alias="date", pattern=DATE_RE),
    teams: int = Query(TEAMS_DEFAULT, ge=TEAMS_MIN, le=TEAMS_MAX),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """오늘의 에이전트 브리핑: heat standing, silver-zone counts, AED night gap and 밤 의료 공백 for one 시군구 on one day.

    The AED block is null (with `aed_note`) outside Seoul. Silver counts are null outside Seoul. `medical` is
    null when the NMC data is unavailable (no key, no snapshot); otherwise it is labelled live/snapshot with age.
    """
    d = parse_season_date(date_)
    region = repo.region_by_code(conn, sgg_code)
    if region is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "region not found")
    require_year(conn, brief.VARIANT, d.year)
    b1 = repo_heat.latest_run_id(conn, repo_heat.BASELINE, brief.VARIANT, d.year)
    ours = repo_heat.latest_run_id(conn, repo_heat.OURS, brief.VARIANT, d.year)
    if b1 is None or ours is None:
        raise HTTPException(status.HTTP_409_CONFLICT, NO_RUN_DETAIL)
    heat = brief.heat_section(conn, region, date_, b1, ours, teams)
    silver = brief.silver_section(conn, region)
    aed, aed_note = brief.aed_section(conn, region)
    medical = brief.medical_section(request.app.state.medical, region_of(conn, sgg_code), date_)
    heat["evidence"]["medical_night"] = medical
    return {
        "date": date_, "teams": teams, "region": region, "heat": heat, "silver": silver, "aed": aed,
        "aed_note": aed_note, "medical": medical, "actions": brief.build_actions(region, heat, silver, aed, medical),
    }


ROUTERS = (silver_router, aed_router, briefing_router)
