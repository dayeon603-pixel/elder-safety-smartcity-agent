"""KMA ASOS hourly (API허브 kma_sfctm2 cache) -> daily max 체감온도 / daily max 기온 per station.

체감온도 (KMA 여름철 체감온도, the quantity the 폭염특보 uses; 33 C = 주의보, 35 C = 경보):
    Tw = Ta*atan[0.151977*(RH+8.313659)^0.5] + atan(Ta+RH) - atan(RH-1.676331)
         + 0.00391838*RH^1.5*atan(0.023101*RH) - 4.686035                 (Stull 2011 wet-bulb)
    AT = -0.2442 + 0.55399*Tw + 0.45535*Ta - 0.0022*Tw^2 + 0.00278*Tw*Ta + 3.0   (KMA 여름철 체감온도)
Sources: R. Stull (2011), "Wet-Bulb Temperature from Relative Humidity and Air Temperature", J. Appl. Meteor.
Climatol. 50(11), 2267-2269 (Tw); 기상청 여름철 체감온도 산출식 (the AT polynomial in Tw and Ta, Ta in C, RH in %).
Station coordinates: 기상청 ASOS 지점 정보 (기상자료개방포털 meta, redistributed in github Shj-A/Data_campus_project),
cross-checked against neuwcodebox/fire-map-server data/stninfo.csv.

Backtests are hypotheses, not proofs. Nothing here reads data after the day it is attached to except where a
column is explicitly named *_p1 (next-day value used as a forecast proxy; LOOKAHEAD RISK, variant F only).
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("heat.weather")

HUB_CACHE = Path("/Users/chloekang/kcf/data/cache/extra/kma_hub/sfctm2")
STN_SRC = Path("/Users/chloekang/kcf/data/cache/extra/kma_hub/stn_src")
STN_PRIMARY = STN_SRC / "shj_stn.csv"  # 기상청 ASOS 지점 정보 (95 active stations, 5-decimal coordinates)
STN_CROSS = STN_SRC / "neuwcodebox_stninfo.csv"  # independent copy used only to cross-check coordinates
CROSS_TOL_KM = 5.0  # the 2021 copy predates a few relocations (e.g. 259 강진군 moved 2.6 km); primary file is current
MISSING_SENTINELS = (-9.0, -99.0)  # hub fill values (-9 / -99.0); both are outside any valid TA / HM
TA_RANGE = (-40.0, 50.0)
RH_RANGE = (1.0, 100.0)
# TM STN WD WS GST_WD GST_WS GST_TM PA PS PT PR TA TD HM ... : whitespace token indices
I_TM, I_STN, I_TA, I_HM = 0, 1, 11, 13


def wet_bulb_stull(ta: np.ndarray | float, rh: np.ndarray | float) -> np.ndarray:
    """Stull (2011) wet-bulb temperature (C) from air temperature (C) and relative humidity (%)."""
    ta, rh = np.asarray(ta, float), np.asarray(rh, float)
    return (ta * np.arctan(0.151977 * np.sqrt(rh + 8.313659)) + np.arctan(ta + rh) - np.arctan(rh - 1.676331)
            + 0.00391838 * rh**1.5 * np.arctan(0.023101 * rh) - 4.686035)


def summer_apparent_temp(ta: np.ndarray | float, rh: np.ndarray | float) -> np.ndarray:
    """KMA 여름철 체감온도 (C) from air temperature (C) and relative humidity (%). NaN in -> NaN out."""
    ta = np.asarray(ta, float)
    tw = wet_bulb_stull(ta, rh)
    return -0.2442 + 0.55399 * tw + 0.45535 * ta - 0.0022 * tw**2 + 0.00278 * tw * ta + 3.0


def haversine_km(lat1, lon1, lat2, lon2):
    p = np.pi / 180
    a = np.sin((lat2 - lat1) * p / 2) ** 2 + np.cos(lat1 * p) * np.cos(lat2 * p) * np.sin((lon2 - lon1) * p / 2) ** 2
    return 12742 * np.arcsin(np.sqrt(a))


def load_station_coords() -> pd.DataFrame:
    """ASOS station table (stn int, name, lat, lon). Raises if the two copies disagree by > CROSS_TOL_KM."""
    a = pd.read_csv(STN_PRIMARY, encoding="utf-8-sig", dtype=str)
    a = a.apply(lambda s: s.str.replace(" ", "", regex=False).str.strip())
    out = pd.DataFrame({"stn": a["지점코드"].astype(int), "name": a["(현)지점명"],
                        "lat": a["위도"].astype(float), "lon": a["경도"].astype(float)})
    if out.stn.duplicated().any() or not out.lat.between(33, 39).all() or not out.lon.between(124, 132).all():
        raise ValueError("station table failed range/duplicate checks")
    b = pd.read_csv(STN_CROSS)
    b = b.sort_values("시작일").drop_duplicates("지점", keep="last").set_index("지점")
    j = out.set_index("stn").join(b[["위도", "경도"]], how="inner")
    d = haversine_km(j.lat.to_numpy(), j.lon.to_numpy(), j["위도"].to_numpy(), j["경도"].to_numpy())
    if (d > CROSS_TOL_KM).any():
        raise ValueError(f"station coordinates disagree between sources: {j.index[d > CROSS_TOL_KM].tolist()}")
    if (d > 1.0).any():
        logger.warning("stations moved >1 km between sources (primary kept): %s", dict(zip(j.index[d > 1.0].tolist(), d[d > 1.0].round(1).tolist())))
    logger.info("stations: %d with coords, %d cross-checked (max diff %.3f km)", len(out), len(j), d.max())
    return out.sort_values("stn").reset_index(drop=True)


def parse_hub_file(path: Path) -> pd.DataFrame:
    """One cached hub response -> rows (date, hour, stn, ta, hm); sentinels and out-of-range values become NaN."""
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line[0] == "#":
            continue
        t = line.split()
        if len(t) <= I_HM or len(t[I_TM]) != 12:
            continue
        rows.append((t[I_TM], int(t[I_STN]), float(t[I_TA]), float(t[I_HM])))
    d = pd.DataFrame(rows, columns=["tm", "stn", "ta", "hm"])
    d["date"] = pd.to_datetime(d.tm.str[:8], format="%Y%m%d")
    d["hour"] = d.tm.str[8:10].astype(int)
    ta_bad = d.ta.isin(MISSING_SENTINELS) | ~d.ta.between(*TA_RANGE)
    hm_bad = d.hm.isin(MISSING_SENTINELS) | ~d.hm.between(*RH_RANGE)
    d.loc[ta_bad, "ta"] = np.nan
    d.loc[hm_bad, "hm"] = np.nan
    return d.drop(columns="tm")


def load_hourly(hours: list[int], dates: pd.DatetimeIndex, cache: Path = HUB_CACHE) -> pd.DataFrame:
    """Concatenate the cached hourly responses for the requested (date, hour) set. Missing files are an error:
    a silent gap here would be read as 'no hot day'."""
    missing, frames = [], []
    for d in dates:
        for h in hours:
            p = cache / f"{d:%Y%m%d}{h:02d}00.txt"
            if not p.is_file():
                missing.append(p.name)
            else:
                frames.append(parse_hub_file(p))
    if missing:
        raise FileNotFoundError(f"{len(missing)} hub files not cached (e.g. {missing[:3]}); run fetch_hub.py")
    return pd.concat(frames, ignore_index=True)


def daily_max(hourly: pd.DataFrame, min_hours: int) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    """(date x stn) daily max 체감온도 and daily max 기온, over hours where BOTH ta and hm are valid.

    A station-day with fewer than `min_hours` valid hours is NaN (a partial afternoon would understate the max).
    The two families share the same valid hours so they differ only by the humidity term.
    Third return value = diagnostics: share of valid station-days whose max 체감온도 falls on the first / last hour of
    the window (a high share would mean the window cuts the true daily peak).
    """
    h = hourly.dropna(subset=["ta", "hm"]).copy()
    h["at"] = summer_apparent_temp(h.ta.to_numpy(), h.hm.to_numpy())
    g = h.groupby(["date", "stn"])
    agg = g.agg(at=("at", "max"), tx=("ta", "max"), n=("ta", "size"))
    agg.loc[agg.n < min_hours, ["at", "tx"]] = np.nan
    peak_hour = h.loc[g["at"].idxmax(), ["date", "stn", "hour"]].set_index(["date", "stn"])["hour"].reindex(agg.index)
    valid = agg["at"].notna()
    lo, hi = int(h.hour.min()), int(h.hour.max())
    diag = {"hour_window": [lo, hi], "n_station_days": int(valid.sum()),
            "share_peak_at_first_hour": float((peak_hour[valid] == lo).mean()),
            "share_peak_at_last_hour": float((peak_hour[valid] == hi).mean())}
    return agg["at"].unstack("stn"), agg["tx"].unstack("stn"), diag
