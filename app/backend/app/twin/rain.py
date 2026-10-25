"""Real rain nights near a 구: ASOS hourly rain >= 10 mm/h on summer nights (20h-05h), 2023-2025.

Hourly KMA files are cached for 2025-06-01..2025-09-30 (all hours). For 2023 and 2024 only KST 10-19 h files exist
(heat season cache), so nights cannot be checked hourly; those years are listed from Meteostat daily totals as
*unverified* candidates (>= RAIN_DAILY_FALLBACK_MM per day) and labelled so. No value is invented.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

from ..config import Settings
from .. import repo_heat
from . import constants as C
from . import weather

logger = logging.getLogger("twin.rain")


class NoRainData(RuntimeError):
    """Neither hourly nor daily rain data for the station is on disk."""


def _summer_days(year: int) -> Iterator[date]:
    d = date(year, C.RAIN_SUMMER_MONTHS[0], 1)
    end = date(year, C.RAIN_SUMMER_MONTHS[-1] + 1, 1) if C.RAIN_SUMMER_MONTHS[-1] < 12 else date(year + 1, 1, 1)
    while d < end:
        yield d
        d += timedelta(days=1)


def night_hours_of(d: date) -> list[tuple[date, int]]:
    """The night that starts on `d`: 20..23 h of d and 00..05 h of d+1."""
    return [(d, h) for h in C.RAIN_NIGHT_HOURS if h >= 12] + [(d + timedelta(days=1), h) for h in C.RAIN_NIGHT_HOURS if h < 12]


def hourly_nights(settings: Settings, station_id: str, year: int) -> tuple[list[dict[str, Any]], int, int]:
    """(nights with max hourly rain >= threshold, hours checked, hours missing) for one summer."""
    out: list[dict[str, Any]] = []
    checked = missing = 0
    for d in _summer_days(year):
        best: tuple[float, date, int] | None = None
        hours: list[dict[str, Any]] = []
        for dd, h in night_hours_of(d):
            obs = weather.station_hour(settings.data_dir, station_id, dd, h)
            if obs is None or obs.rain_mm is None:
                missing += 1
                continue
            checked += 1
            if obs.rain_mm >= C.RAIN_THRESHOLD_MM_H:
                hours.append({"date": dd.isoformat(), "hour": h, "rain_mm": obs.rain_mm, "temp": obs.ta})
            if best is None or obs.rain_mm > best[0]:
                best = (obs.rain_mm, dd, h)
        if hours and best is not None:
            out.append({"date": d.isoformat(), "max_mm_h": best[0], "max_at": {"date": best[1].isoformat(), "hour": best[2]},
                        "hours_ge_threshold": hours, "verified": True, "resolution": "hourly"})
    return out, checked, missing


def daily_candidates(settings: Settings, station_id: str, year: int) -> tuple[list[dict[str, Any]], int]:
    """Unverified candidates from daily totals (no night/hourly information)."""
    prcp = weather.meteostat_daily_prcp(settings.data_dir, station_id)
    out: list[dict[str, Any]] = []
    n = 0
    for d in _summer_days(year):
        v = prcp.get(d.isoformat())
        if v is None:
            continue
        n += 1
        if v >= C.RAIN_DAILY_FALLBACK_MM:
            out.append({"date": d.isoformat(), "daily_mm": v, "verified": False, "resolution": "daily",
                        "note": f"일강수 {v:.1f} mm; 밤 시간대·시간당 {C.RAIN_THRESHOLD_MM_H:.0f} mm 여부는 확인 불가 (시간자료 미캐시)"})
    return out, n


def rain_days(conn: sqlite3.Connection, settings: Settings, region: dict[str, Any]) -> dict[str, Any]:
    """The contract's rain-day list with explicit coverage per year.

    Raises:
        NoRainData: the station has neither hourly nor daily files.
    """
    wx = repo_heat.station_weather(conn, region["code"], f"{C.RAIN_YEARS[-1]}-07-01")
    station = wx["station"]
    if station is None:
        raise NoRainData(f"no ASOS station is assigned to {region['name']}")
    sid = str(station["id"])
    years: list[dict[str, Any]] = []
    verified: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    any_data = False
    for y in C.RAIN_YEARS:
        hourly, checked, missing = hourly_nights(settings, sid, y)
        if checked:
            any_data = True
            verified.extend(hourly)
            years.append({"year": y, "resolution": "hourly", "night_hours_checked": checked, "night_hours_missing": missing,
                          "n_days": len(hourly)})
            continue
        daily, n_days = daily_candidates(settings, sid, y)
        if n_days:
            any_data = True
        candidates.extend(daily)
        years.append({"year": y, "resolution": "daily" if n_days else "none", "days_with_daily_total": n_days,
                      "n_candidates": len(daily),
                      "note": "밤 시간자료가 캐시에 없음 (kma_hub/sfctm2 는 10-19시만); 일강수량 기준 후보만 제시" if n_days
                      else "이 연도의 강수 자료가 캐시에 없다"})
    if not any_data:
        raise NoRainData(f"no hourly or daily rain files for station {sid} under {settings.data_dir}")
    return {
        "sgg_code": region["code"], "region": f"{region['sido']} {region['name']}",
        "station": {**station, "note": f"구 중심에서 가장 가까운 ASOS 지점 ({station['dist_km']} km); 관악구 안에는 ASOS 지점이 없다"},
        "criteria": {"threshold_mm_h": C.RAIN_THRESHOLD_MM_H, "night_hours": list(C.RAIN_NIGHT_HOURS),
                     "months": list(C.RAIN_SUMMER_MONTHS), "years": list(C.RAIN_YEARS),
                     "daily_fallback_mm": C.RAIN_DAILY_FALLBACK_MM},
        "days": verified, "unverified_candidates": candidates, "coverage": years,
        "sources": [{"what": "기상청 API허브 kma_sfctm2 시간자료 (RN = 시간 강수, mm)", "dir": C.HOURLY_RAIN_DIR,
                     "display_source": "기상청 시간 강수 관측"},
                    {"what": "Meteostat 일자료 prcp (KMA ASOS 재배포)", "dir": C.METEOSTAT_DIR, "display_source": "기상청 일 강수 관측 (재배포본)"}],
        "fetched_at": _fetched_at(conn),
    }


def _fetched_at(conn: sqlite3.Connection) -> str | None:
    r = conn.execute("SELECT fetched_at FROM datasets WHERE name = 'heat_kma_asos_hourly'").fetchone()
    return None if r is None else r["fetched_at"]


__all__ = ["NoRainData", "daily_candidates", "hourly_nights", "night_hours_of", "rain_days"]
