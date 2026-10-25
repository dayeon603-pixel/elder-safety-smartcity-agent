"""Observed weather for the twin: KMA API허브 hourly text (TA, HM, RN) and Meteostat daily totals, from the data cache.

Hourly files: `<data>/extra/rain_kma/hub_sfctm2/YYYYMMDDHH.txt` (all 24 h, 2025-06..09) and
`<data>/extra/kma_hub/sfctm2/YYYYMMDDHH00.txt` (KST 10-19 h, 2019-2025 heat seasons). One line per station;
whitespace tokens; -9 = missing (RN -9 with a valid TA means "no precipitation", as the rain experiment validated
against RN_DAY). Nothing here is modelled: a missing hour is reported as missing.
"""

from __future__ import annotations

import gzip
import logging
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..heat.apparent import summer_apparent_temp
from . import constants as C

logger = logging.getLogger("twin.weather")
_LOCK = threading.Lock()
_HOURLY: dict[tuple[str, str], HourObs | None] = {}  # (file path, station) -> parsed
_DAILY: dict[str, dict[str, float | None]] = {}  # meteostat path -> {date: prcp}
METEOSTAT_DATE, METEOSTAT_PRCP = 0, 4


@dataclass(frozen=True, slots=True)
class HourObs:
    """One station-hour: air temperature, relative humidity, hourly rain (mm) and the derived 체감온도."""

    ta: float | None
    rh: float | None
    rain_mm: float | None
    feels: float | None
    source_file: str


def _valid(v: float, lo_hi: tuple[float, float]) -> bool:
    return v not in C.KMA_MISSING_SENTINELS and lo_hi[0] <= v <= lo_hi[1]


def parse_hour_line(tokens: list[str], source_file: str) -> HourObs:
    """Observation from the whitespace tokens of one station line."""
    ta_raw, hm_raw, rn_raw = float(tokens[C.KMA_TOKEN_TA]), float(tokens[C.KMA_TOKEN_HM]), float(tokens[C.KMA_TOKEN_RN])
    ta = ta_raw if _valid(ta_raw, C.KMA_TA_RANGE) else None
    rh = hm_raw if _valid(hm_raw, C.KMA_RH_RANGE) else None
    if rn_raw >= 0:
        rain: float | None = rn_raw
    else:
        rain = 0.0 if ta is not None else None
    feels = round(float(summer_apparent_temp(ta, rh)), 1) if ta is not None and rh is not None else None
    return HourObs(ta=ta, rh=rh, rain_mm=rain, feels=feels, source_file=source_file)


def read_station_hour(path: Path, station_id: str) -> HourObs | None:
    """The station's line of one hourly file, or None when the file or the station line is absent."""
    key = (str(path), station_id)
    with _LOCK:
        if key in _HOURLY:
            return _HOURLY[key]
    obs: HourObs | None = None
    if path.is_file():
        try:
            text = path.read_text(encoding="euc-kr", errors="replace")
        except OSError as exc:
            logger.warning("cannot read %s: %s", path.name, exc)
            text = ""
        for line in text.splitlines():
            if not line or line[0] == "#":
                continue
            tokens = line.split()
            if len(tokens) <= C.KMA_TOKEN_RN or len(tokens[C.KMA_TOKEN_TM]) != C.KMA_TM_LEN:
                continue
            if tokens[C.KMA_TOKEN_STN] == station_id:
                try:
                    obs = parse_hour_line(tokens, path.name)
                except ValueError:
                    obs = None
                break
    with _LOCK:
        _HOURLY[key] = obs
    return obs


def hourly_paths(data_dir: Path, d: date, hour: int) -> list[Path]:
    """Candidate files for a KST hour, best (all-hours rain cache) first."""
    stamp = f"{d:%Y%m%d}{hour:02d}"
    return [data_dir / C.HOURLY_RAIN_DIR / f"{stamp}.txt", data_dir / C.HOURLY_HEAT_DIR / f"{stamp}00.txt"]


def station_hour(data_dir: Path, station_id: str, d: date, hour: int) -> HourObs | None:
    """Observation at (date, hour) from whichever hourly cache has it."""
    for p in hourly_paths(data_dir, d, hour):
        obs = read_station_hour(p, station_id)
        if obs is not None:
            return obs
    return None


def meteostat_daily_prcp(data_dir: Path, station_id: str) -> dict[str, float | None]:
    """{date: prcp mm} of the Meteostat daily file of a KMA station (47 + id); empty when the file is absent."""
    path = data_dir / C.METEOSTAT_DIR / f"{C.METEOSTAT_WMO_PREFIX}{station_id}.csv.gz"
    k = str(path)
    with _LOCK:
        if k in _DAILY:
            return _DAILY[k]
    out: dict[str, float | None] = {}
    if path.is_file():
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                cells = line.rstrip("\n").split(",")
                if len(cells) <= METEOSTAT_PRCP or len(cells[METEOSTAT_DATE]) != 10:
                    continue
                raw = cells[METEOSTAT_PRCP]
                try:
                    out[cells[METEOSTAT_DATE]] = float(raw) if raw else None
                except ValueError:
                    continue
    with _LOCK:
        _DAILY[k] = out
    return out


def clear_cache() -> None:
    """Drop parsed files (tests)."""
    with _LOCK:
        _HOURLY.clear()
        _DAILY.clear()


__all__ = ["HourObs", "clear_cache", "hourly_paths", "meteostat_daily_prcp", "parse_hour_line", "read_station_hour",
           "station_hour"]
