"""Map layers for the ops frontend, one 시군구 at a time, from the cached source tables.

GET /crosswalks?sgg_code=&speed=   every crosswalk inside the region's 행정동 with length, green time and the
                                   green-time deficit at `speed` (m/s; default 0.7 = 고령 보행속도)
GET /aed?sgg_code=&day=&hour=      every AED (전국 AED API cache) in the 시도/구군 with posted hours and open_now
GET /shelters?sgg_code=&at=        무더위쉼터 with night/weekend flags and open_now (rules in the response)
GET /integrations/status           whether the outbound webhook is configured and how it is triggered
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import repo_heat
from ..actions import aed_points, dongs, signals
from ..actions import constants as C
from ..actions.geometry import points_in_rings
from ..config import Settings
from ..deps import get_conn, get_settings
from ..medical import hours as mhours
from ..medical.service import KST

router = APIRouter(tags=["field"])
SGG_RE = r"^[0-9]{5}$"
AT_RE = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}$"
SPEED_MIN, SPEED_MAX = 0.3, 2.0
SHELTER_DAY_START_H, SHELTER_DAY_END_H = 9, 18
SHELTER_RULES = (
    f"평일 {SHELTER_DAY_START_H:02d}-{SHELTER_DAY_END_H:02d}시: 개방으로 간주 (공개자료에 운영시간이 없어 기본 운영시간 가정)",
    "평일 야간: night_open=1 이면 개방, 0 이면 폐쇄, NULL 이면 미상",
    "주말·공휴일 주간: weekend_open 기준; 주말·공휴일 야간: weekend_open 과 night_open 모두 필요",
)
AED_RULES = ("day=weekday 는 월요일 시간표를 대표값으로 사용", "HHMM 구간, 0000-2400 = 24시간, 시간표 없음 = 미상",
             "매월 n번째 일요일 휴무 플래그(sunFrtYon 등)는 반영하지 않음", "관리자 이름·전화번호는 적재하지 않음")


def _text(v: Any) -> str | None:
    """A CSV string cell, or None for empty/NaN (pandas reads blanks as float NaN even with dtype=str)."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    s = str(v).strip()
    return s or None


def _region(conn: sqlite3.Connection, sgg_code: str) -> dict[str, Any]:
    region = repo_heat.get_region(conn, sgg_code)
    if region is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "region not found")
    return region


def _dongs(region: dict[str, Any], settings: Settings) -> list[dongs.Dong]:
    try:
        ds = dongs.dongs_of_region(region["sido"], region["name"], settings.data_dir)
    except dongs.DongDataError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {"code": "data_unavailable", "message": str(exc)}) from exc
    if not ds:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            {"code": "data_unavailable", "message": f"no 행정동 for {region['sido']} {region['name']}"})
    return ds


def _parse_at(raw: str | None) -> datetime:
    if raw is None:
        return datetime.now(KST)
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=KST)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "at must be YYYY-MM-DDTHH:MM (KST)") from exc


@router.get("/crosswalks")
def crosswalks(sgg_code: str = Query(..., pattern=SGG_RE), speed: float = Query(C.WALK_SPEED_M_S, ge=SPEED_MIN, le=SPEED_MAX),
               conn: sqlite3.Connection = Depends(get_conn), settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Crosswalks inside the region (point-in-polygon over its 행정동), with the green deficit at `speed`."""
    region = _region(conn, sgg_code)
    ds = _dongs(region, settings)
    try:
        df = signals.load_crosswalks(settings.data_dir)
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {"code": "data_unavailable", "message": str(exc.filename)}) from exc
    pts = df.dropna(subset=["LATITUDE", "LONGITUDE"])
    lat, lon = pts["LATITUDE"].to_numpy(dtype=float), pts["LONGITUDE"].to_numpy(dtype=float)
    inside = np.zeros(len(pts), dtype=bool)
    for d in ds:
        inside |= points_in_rings(d.rings, lon, lat)
    sel = pts[inside]
    items: list[dict[str, Any]] = []
    for r in sel.itertuples():
        length = None if np.isnan(r.ET) else float(r.ET)
        green = None if np.isnan(r.GREEN_SGNGNR_TIME) else float(r.GREEN_SGNGNR_TIME)
        deficit: int | None = None
        if (length is not None and 0 < length <= C.CROSSWALK_MAX_LENGTH_M
                and green is not None and 0 < green <= C.CROSSWALK_MAX_GREEN_S):
            deficit = signals.extension_seconds(length, green, speed)
        need = round(length / speed, 1) if length else None
        items.append({
            "id": _text(r.CRSLK_MANAGE_NO), "lat": float(r.LATITUDE), "lon": float(r.LONGITUDE), "length_m": length,
            "green_s": green, "has_signal": r.TFCLGHT_YN == "Y", "address": _text(r.RDNMADR) or _text(r.LNMADR),
            "need_s": need, "deficit_s": deficit,
        })
    items.sort(key=lambda x: (x["deficit_s"] is None, -(x["deficit_s"] or 0)))
    return {
        "sgg_code": sgg_code, "region": f"{region['sido']} {region['name']}", "speed_m_s": speed,
        "total": len(items), "with_signal_and_green": sum(1 for x in items if x["deficit_s"] is not None),
        "deficit_count": sum(1 for x in items if (x["deficit_s"] or 0) > 0), "items": items,
        "source": C.SOURCES["signals"],
    }


@router.get("/aed")
def aed(sgg_code: str = Query(..., pattern=SGG_RE), day: Literal["weekday", "sat", "sun", "holiday"] = "weekday",
        hour: int | None = Query(None, ge=0, le=23), limit: int = Query(C.FIELD_LIST_LIMIT, ge=1, le=C.FIELD_LIST_MAX),
        conn: sqlite3.Connection = Depends(get_conn), settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """All AEDs of the 시도/구군 with hours for `day` and open_now at `hour` (default: now, KST)."""
    region = _region(conn, sgg_code)
    try:
        df = aed_points.load_aeds(settings.data_dir)
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {"code": "data_unavailable", "message": str(exc)}) from exc
    h = datetime.now(KST).hour if hour is None else hour
    sub = aed_points.in_region(df, region["sido"], region["name"])
    out = aed_points.table(sub, aed_points.DAY_PARAM[day], h, limit)
    return {"sgg_code": sgg_code, "region": f"{region['sido']} {region['name']}", "day": day, "hour": h, **out,
            "note": None if out["total"] else "자료 없음", "rules": AED_RULES,
            "source": "전국 자동심장충격기(AED) 공공 API 캐시 (data.go.kr 15000652)"}


def shelter_open(night_open: int | None, weekend_open: int | None, at: datetime) -> bool | None:
    """SHELTER_RULES as code; None = unknown."""
    weekend = at.weekday() >= 5 or mhours.is_holiday(at.date())
    daytime = SHELTER_DAY_START_H <= at.hour < SHELTER_DAY_END_H

    def flag(v: int | None) -> bool | None:
        return None if v is None else bool(v)

    if not weekend:
        return True if daytime else flag(night_open)
    w = flag(weekend_open)
    if daytime or w is False:
        return w
    n = flag(night_open)
    return None if n is None or w is None else (w and n)


@router.get("/shelters")
def shelters(sgg_code: str = Query(..., pattern=SGG_RE), at: str | None = Query(None, pattern=AT_RE),
             conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    """무더위쉼터 of the region with flags and open_now at `at` (KST, default now)."""
    region = _region(conn, sgg_code)
    when = _parse_at(at)
    rows = conn.execute("SELECT id, name, lat, lon, capacity, night_open, weekend_open FROM shelter WHERE region_code = ? "
                        "ORDER BY name, id", (sgg_code,)).fetchall()
    items = [{"id": int(r["id"]), "name": r["name"], "lat": float(r["lat"]), "lon": float(r["lon"]), "capacity": r["capacity"],
              "night_open": None if r["night_open"] is None else bool(r["night_open"]),
              "weekend_open": None if r["weekend_open"] is None else bool(r["weekend_open"]),
              "open_now": shelter_open(r["night_open"], r["weekend_open"], when)} for r in rows]
    return {"sgg_code": sgg_code, "region": f"{region['sido']} {region['name']}", "at": when.isoformat(timespec="minutes"),
            "is_holiday": mhours.is_holiday(when.date()), "holiday_table_covered": mhours.holiday_table_covers(when.date()),
            "total": len(items), "open": sum(1 for x in items if x["open_now"] is True),
            "unknown": sum(1 for x in items if x["open_now"] is None), "items": items, "rules": SHELTER_RULES,
            "note": None if items else "자료 없음 (이 시군구의 무더위쉼터 공개자료가 없다)"}


@router.get("/integrations/status")
def integrations_status(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """The outbound channel: configured or not, and that `POST /feedback` with decision 채택 triggers it."""
    return {"webhook_configured": settings.webhook_url is not None,
            "webhook_host": urlsplit(settings.webhook_url).hostname if settings.webhook_url else None,
            "trigger": "POST /feedback {decision: '채택', action_id, run_id | sgg_code+date} fires the signed webhook",
            "receiver_double": "/sim/webhook", "spec_note": "스마트도시 통합플랫폼 연계(TTA 연계규격)는 다음 단계; 현재는 서명된 JSON 푸시"}
