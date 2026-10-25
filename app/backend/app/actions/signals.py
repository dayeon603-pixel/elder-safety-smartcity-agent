"""보행신호 연장 권고: crosswalks whose green time is too short for a 0.7 m/s walker.

Rule: needs_green = length / 0.7; a crosswalk is short when green < needs_green and
extend = ceil(needs_green - green) seconds. Only rows with a traffic light, a positive green time and
a plausible length are evaluated. Coverage of the standard dataset is uneven (mostly 경기, 경남,
전북, 대전); regions with no usable rows get a "자료 없음" note and nothing is estimated.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np
import pandas as pd

from . import constants as C
from .dongs import Dong
from .diskcache import cached
from .geometry import points_in_rings
from .models import SignalCoverage, SignalItem, SignalsNote

logger = logging.getLogger("actions.signals")
_CACHE: dict[str, pd.DataFrame] = {}
CROSSWALK_CACHE_VERSION = 1
COLS = [
    "CTPRVN_NM", "SIGNGU_NM", "LNMADR", "RDNMADR", "CRSLK_MANAGE_NO", "LATITUDE", "LONGITUDE", "ET",
    "TFCLGHT_YN", "GREEN_SGNGNR_TIME",
]


def extension_seconds(length_m: float, green_s: float, speed_m_s: float = C.WALK_SPEED_M_S) -> int:
    """Seconds of extra green needed: ceil(length/speed - green), 0 when the green already suffices.

    Raises:
        ValueError: if speed or length is not positive.
    """
    if speed_m_s <= 0 or length_m <= 0:
        raise ValueError("speed and length must be positive")
    return max(0, math.ceil(round(length_m / speed_m_s - green_s, 9)))


def load_crosswalks(data_dir: Path) -> pd.DataFrame:
    """Crosswalk standard data (cached). Raises FileNotFoundError if the CSV is missing."""
    key = str(data_dir.resolve())
    if key not in _CACHE:
        path = data_dir / C.CROSSWALK_CSV
        _CACHE[key] = cached("crosswalks", CROSSWALK_CACHE_VERSION, [path], lambda: _read_crosswalks(path))
    return _CACHE[key]


def _read_crosswalks(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, encoding=C.CROSSWALK_ENCODING, dtype=str, usecols=COLS)
    for c in ("LATITUDE", "LONGITUDE", "ET", "GREEN_SGNGNR_TIME"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def clear_cache() -> None:
    """Drop cached tables (tests)."""
    _CACHE.clear()


def recommend_extensions(
    df: pd.DataFrame, dongs: list[Dong], limit: int = C.SIGNALS_LIMIT_DEFAULT, sido_name: str = ""
) -> tuple[list[SignalItem] | SignalsNote, SignalCoverage]:
    """Crosswalks inside the region's 행정동 that need a longer green, largest extension first."""
    sido = sorted(str(x) for x in df["CTPRVN_NM"].dropna().unique())
    lat, lon = df["LATITUDE"].to_numpy(dtype=float), df["LONGITUDE"].to_numpy(dtype=float)
    valid = np.isfinite(lat) & np.isfinite(lon)
    rings = [r for d in dongs for r in d.rings]
    inside = np.zeros(len(df), dtype=bool)
    if rings and valid.any():
        inside[valid] = points_in_rings(rings, lon[valid], lat[valid])
    region = df[inside]
    ok = (
        (region["TFCLGHT_YN"] == "Y")
        & (region["GREEN_SGNGNR_TIME"] > 0) & (region["GREEN_SGNGNR_TIME"] <= C.CROSSWALK_MAX_GREEN_S)
        & (region["ET"] > 0) & (region["ET"] <= C.CROSSWALK_MAX_LENGTH_M)
    )
    ev = region[ok].copy()
    ev["need"] = ev["ET"] / C.WALK_SPEED_M_S
    ev["ext"] = [extension_seconds(float(e), float(g)) for e, g in zip(ev["ET"], ev["GREEN_SGNGNR_TIME"], strict=True)]
    short = ev[ev["ext"] > 0].sort_values(["ext", "ET", "CRSLK_MANAGE_NO"], ascending=[False, False, True])
    n_sido = int((df["CTPRVN_NM"] == sido_name).sum()) if sido_name else 0
    base = (
        f"표준데이터 {len(df):,}건, 시도 {len(sido)}곳만 담고 있어 전국 전수가 아니다"
        + (" (행 수가 내려받기 상한과 같아 일부만 받았을 수 있다)" if len(df) == C.CROSSWALK_ROW_CAP else "")
        + f". {sido_name or '같은 시도'}는 {n_sido:,}건. "
        f"이 시군구 안 횡단보도 {len(region):,}건 중 신호등·녹색시간·연장이 모두 기재된 {len(ev):,}건만 판정했다."
    )
    cov = SignalCoverage(
        dataset_rows=len(df), dataset_sido=sido, n_crosswalks_in_region=len(region), n_evaluable=len(ev),
        n_short=len(short), note=base,
    )
    if ev.empty:
        why = "이 시군구는 표준데이터에 횡단보도가 없다" if region.empty else "횡단보도는 있으나 녹색신호시간이 기재된 건이 없다"
        return SignalsNote(note=f"자료 없음: {why}. 보행신호 판정을 하지 않았다."), cov
    items = [
        SignalItem(
            crosswalk_id=str(r.CRSLK_MANAGE_NO),
            address=str(r.RDNMADR if isinstance(r.RDNMADR, str) and r.RDNMADR else r.LNMADR),
            lat=float(r.LATITUDE), lon=float(r.LONGITUDE), length_m=float(r.ET), green_s=float(r.GREEN_SGNGNR_TIME),
            required_green_s=round(float(r.need), 1), extend_s=int(r.ext),
        )
        for r in short.head(limit).itertuples()
    ]
    return items, cov
