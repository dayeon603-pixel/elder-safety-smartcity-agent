"""Compose the four channels for one 시군구 and day. Importable by /briefing (no HTTP types here)."""

from __future__ import annotations

import logging
import sqlite3
from datetime import date as date_type
from pathlib import Path

from .. import repo_heat
from ..heat import plan as heat_plan
from ..heat.plan import haversine_km
from ..heat.ranktext import expected_cases_text, rank_text
from ..modules.evidence import heat_evidence
from . import bit, cbs, dongs, repo, route, signals
from . import constants as C
from .models import ActionContext, ActionsResponse, RouteResult, ShelterRef

logger = logging.getLogger("actions.service")
VARIANT = "F"  # same-day Tmax is the forecast proxy; the D-1 variant would need '어제' wording


class ActionsError(Exception):
    """Base class for service-level failures."""


class RegionNotFoundError(ActionsError):
    """Unknown 시군구 code."""


class NoRunError(ActionsError):
    """No stored OURS/B1 run pair for the requested year and variant."""


class DataUnavailableError(ActionsError):
    """A source file the channel needs is missing or unreadable."""


def nearby_shelters(
    conn: sqlite3.Connection, region_code: str, lat: float, lon: float, limit: int = C.SHELTER_CANDIDATES
) -> list[ShelterRef]:
    """Nearest shelters to (lat, lon) **inside the region only**.

    A shelter of a neighbouring 구 is never named in a 구's text (a 관악구 draft once pointed to a 동작구 경로당); when the
    region has no shelter data the caller omits the sentence and says the 구청 list must be linked.
    """
    refs: list[ShelterRef] = []
    for s in repo.shelters_near(conn, lat, lon, C.SHELTER_SEARCH_KM, region_code):
        if s["region_code"] != region_code:
            continue
        km = haversine_km(lat, lon, s["lat"], s["lon"])
        refs.append(ShelterRef(
            name=str(s["name"]), region_code=s["region_code"], distance_km=round(km, 2),
            night_open=None if s["night_open"] is None else bool(s["night_open"]),
            weekend_open=None if s["weekend_open"] is None else bool(s["weekend_open"]),
        ))
    refs.sort(key=lambda r: (r.distance_km, r.name))
    return refs[:limit]


def build_actions(
    conn: sqlite3.Connection, data_dir: Path, sgg_code: str, day: date_type, teams: int = C.TEAMS_DEFAULT,
    cbs_limit: int = C.CBS_LIMIT_CURRENT, bit_top_n: int = C.BIT_TOP_N_DEFAULT,
    signals_limit: int = C.SIGNALS_LIMIT_DEFAULT, route_stops: int = C.ROUTE_STOPS_DEFAULT,
) -> ActionsResponse:
    """All four action channels for `sgg_code` on `day`.

    Raises:
        RegionNotFoundError: unknown region code.
        NoRunError: no stored B1/OURS runs for that year and variant.
        DataUnavailableError: a required source file is missing or unreadable.
        ValueError: out-of-range cbs_limit.
    """
    region = repo_heat.get_region(conn, sgg_code)
    if region is None:
        raise RegionNotFoundError(sgg_code)
    variant = VARIANT
    iso = day.isoformat()
    b1 = repo_heat.latest_run_id(conn, repo_heat.BASELINE, variant, day.year)
    ours = repo_heat.latest_run_id(conn, repo_heat.OURS, variant, day.year)
    if b1 is None or ours is None:
        raise NoRunError(f"{variant}/{day.year}")

    score = repo_heat.cell_score(conn, ours, sgg_code, iso)
    rank = repo_heat.cell_rank(conn, ours, iso, score) if score is not None else None
    n_cells = repo.count_cells(conn, ours, iso)
    tmax_day = day
    wx = repo_heat.station_weather(conn, sgg_code, tmax_day.isoformat())
    tmax, apparent = wx["tmax"], wx["apparent_max"]
    felt = apparent if apparent is not None else tmax  # the evaluation's HOT criterion is on 체감온도

    plan = heat_plan.build_day_plan(conn, b1, ours, iso, variant, teams, 0, heat_plan.MODE_SPREAD)
    alloc = {a["code"]: int(a["teams"]) for a in plan["allocations"]}
    team_n = alloc.get(sgg_code, 0)

    try:
        region_dongs = dongs.dongs_of_region(region["sido"], region["name"], data_dir)
    except dongs.DongDataError as exc:
        raise DataUnavailableError(str(exc)) from exc
    if not region_dongs:
        raise DataUnavailableError(f"no 행정동 found for {region['sido']} {region['name']}")
    top_dong = max(region_dongs, key=lambda d: (d.pop65, d.code))

    shelters = nearby_shelters(conn, sgg_code, top_dong.lat, top_dong.lon)
    note = None
    if not shelters:
        note = "자료 없음: 이 시군구의 무더위쉼터 공개자료가 없어 쉼터를 문자에 넣지 않았다 (구청 보유 목록 연계 필요)."
    alerted = repo.is_alerted(conn, ours, sgg_code, iso)
    hot = bool(alerted) or (felt is not None and felt >= C.HEAT_THRESHOLD_C)
    n_cases_hist = repo_heat.region_case_count(conn, sgg_code, f"{day.year}-01-01")
    rtext = rank_text(rank, n_cells, n_cases_hist)
    show_rank = rank is not None and rtext.mode != "insufficient" and (bool(alerted) or rank <= C.RANK_TEXT_TOP_SHARE * n_cells)
    draft = cbs.build_cbs(
        cbs.CbsInput(
            region_name=region["name"], month=day.month, day=day.day, tmax_c=tmax,
            rank=rank, n_cells=n_cells, shelters=tuple(shelters), hot=hot, show_rank=show_rank,
            apparent_max_c=apparent, rank_text=rtext.text,
        ),
        limit=cbs_limit, shelter_note=note,
    )

    try:
        stops_df = bit.load_stops(data_dir)
        bit_stops, bit_info = bit.select_bit_stops(stops_df, region_dongs, tmax, bit_top_n, hot=hot,
                                                   apparent_max_c=apparent)
    except FileNotFoundError as exc:
        raise DataUnavailableError(f"bus stop file missing: {exc.filename}") from exc
    try:
        cw = signals.load_crosswalks(data_dir)
    except FileNotFoundError as exc:
        raise DataUnavailableError(f"crosswalk file missing: {exc.filename}") from exc
    sig, sig_cov = signals.recommend_extensions(cw, region_dongs, signals_limit, region["sido"])

    r_stops, km, km_plain = route.plan_route(region_dongs, route_stops)
    rt = RouteResult(
        stops=r_stops, km=km, km_pop65_order=km_plain, distance_basis=route.DISTANCE_BASIS, method=route.METHOD,
        allocated=team_n > 0, teams_in_region=team_n,
        note=(
            f"방문팀 {team_n}개 배정(spread)." if team_n > 0
            else f"이 날 방문팀 {teams}개 배정(spread) 대상 밖이라 동선은 참고용이다."
        ),
    )
    ctx = ActionContext(
        date=iso, variant=variant, alerted=alerted, score=score, rank=rank,
        n_cells=n_cells, tmax_c=tmax, apparent_max_c=apparent, tmax_day=tmax_day.isoformat(), pop65=int(region["pop65"]),
        team_allocated=team_n > 0, teams_total=teams,
    )
    return ActionsResponse(
        sgg_code=sgg_code, region_name=f"{region['sido']} {region['name']}", context=ctx, cbs=draft,
        bit=bit_stops, bit_info=bit_info, signals=sig, signals_coverage=sig_cov, route=rt,
        sources={**C.SOURCES, "cbs": C.SOURCES["cbs"] + (f"; {C.SOURCE_SHELTER}" if shelters else "")}, disclaimer=C.DISCLAIMER,
        evidence={**heat_evidence(conn, region, iso, score=score, rank=rank, n_cells=n_cells, alerted=alerted, wx=wx),
                  "rank_text": rtext.as_dict(), "rank_basis_cases": n_cases_hist,
                  **({"expected_cases_text": expected_cases_text(float(score), apparent)[0],
                      "expected_cases_is_lower_bound": expected_cases_text(float(score), apparent)[1]}
                     if score is not None else {})},
    )
