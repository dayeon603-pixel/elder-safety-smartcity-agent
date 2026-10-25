"""버스정류장 안내단말(BIT) targets: which stops in a region should show the heat message.

Demand model (no sub-동 population grid exists): each 행정동's 65+ residents are split evenly over
the bus stops inside that 행정동's boundary, so every stop carries a share of nearby 65+ residents.
A stop "covers" the shares of all stops within BIT_RADIUS_M of it (itself included). Picking
top-N stops is greedy maximum coverage of that weighted demand: each round takes the stop with the
largest not-yet-covered weight within radius (ratio 1 - 1/e of the optimum is guaranteed for
monotone submodular coverage; tests compare against brute force on small cases).
The per-stop numbers are estimates and are labelled so.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pandas as pd

from . import constants as C
from .diskcache import cached
from .dongs import Dong
from .geometry import BoolArray, FloatArray, haversine_matrix_m, points_in_rings
from .models import BitInfo, BitMessage, BitStop

STOPS_CACHE_VERSION = 1
logger = logging.getLogger("actions.bit")
_STOP_CACHE: dict[str, pd.DataFrame] = {}
IntArray = npt.NDArray[np.int64]


def coverage_matrix(
    cand_lat: FloatArray, cand_lon: FloatArray, dem_lat: FloatArray, dem_lon: FloatArray, radius_m: float
) -> BoolArray:
    """Boolean (n_cand, n_demand): demand j lies within radius_m of candidate i (haversine, chunked rows)."""
    out = np.zeros((len(cand_lat), len(dem_lat)), dtype=bool)
    for s in range(0, len(cand_lat), C.BIT_DISTANCE_CHUNK):
        sl = slice(s, s + C.BIT_DISTANCE_CHUNK)
        out[sl] = haversine_matrix_m(cand_lat[sl], cand_lon[sl], dem_lat, dem_lon) <= radius_m
    return out


def greedy_max_coverage(cover: BoolArray, weights: FloatArray, k: int) -> tuple[list[int], list[float]]:
    """Greedy weighted maximum coverage.

    Args:
        cover: (n_cand, n_demand) bool, cover[i, j] = candidate i covers demand j.
        weights: non-negative weight per demand point.
        k: how many candidates to pick (fewer are returned when no positive gain is left).

    Returns:
        (chosen candidate indices in pick order, marginal covered weight of each pick).
        Ties go to the lowest index.

    Raises:
        ValueError: on negative k/weights or shape mismatch.
    """
    if k < 0:
        raise ValueError("k must be >= 0")
    if cover.shape[1] != len(weights):
        raise ValueError("weights length must equal number of demand points")
    if (weights < 0).any():
        raise ValueError("weights must be non-negative")
    w = weights.astype(float)
    gains = cover.astype(float) @ w
    uncovered = np.ones(len(w), dtype=bool)
    chosen: list[int] = []
    marginal: list[float] = []
    for _ in range(min(k, cover.shape[0])):
        i = int(np.argmax(gains))
        if gains[i] <= 0:
            break
        chosen.append(i)
        marginal.append(float(gains[i]))
        newly = cover[i] & uncovered
        if newly.any():
            gains -= cover[:, newly].astype(float) @ w[newly]
            uncovered[newly] = False
    return chosen, marginal


def load_stops(data_dir: Path) -> pd.DataFrame:
    """Nationwide stops with valid Korean coordinates (cached per data dir).

    Raises:
        FileNotFoundError: if the CSV is missing.
    """
    key = str(data_dir.resolve())
    if key not in _STOP_CACHE:
        path = data_dir / C.BUS_STOPS_CSV
        _STOP_CACHE[key] = cached("bus_stops", STOPS_CACHE_VERSION, [path], lambda: _read_stops(path))
    return _STOP_CACHE[key]


def _read_stops(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, encoding=C.BUS_STOPS_ENCODING, dtype={"정류장번호": str})
    df = raw[["정류장번호", "정류장명", "위도", "경도"]].copy()
    df["위도"] = pd.to_numeric(df["위도"], errors="coerce")
    df["경도"] = pd.to_numeric(df["경도"], errors="coerce")
    lo_lat, hi_lat = C.BIT_LAT_RANGE
    lo_lon, hi_lon = C.BIT_LON_RANGE
    ok = df["위도"].between(lo_lat, hi_lat) & df["경도"].between(lo_lon, hi_lon)
    dropped = int((~ok).sum())
    if dropped:
        logger.warning("dropped %d stops with missing/out-of-range coordinates", dropped)
    return df[ok].reset_index(drop=True)


def clear_cache() -> None:
    """Drop cached stop tables (tests)."""
    _STOP_CACHE.clear()


def display_message(tmax_c: float | None, hot: bool = True, apparent_max_c: float | None = None) -> BitMessage:
    """Two lines of at most BIT_LINE_CHARS characters each; 체감 최고 when known, else 최고 기온."""
    word = "폭염" if hot else "더위"
    if apparent_max_c is not None:
        line1 = f"오늘 체감 {apparent_max_c:.0f}℃ {word}"
    else:
        line1 = f"오늘 최고 {tmax_c:.0f}℃ {word}" if tmax_c is not None else f"오늘 {word} 주의"
    msg = BitMessage(lines=[line1, "한낮 외출 자제"])
    if len(msg.lines) > C.BIT_LINES or any(len(x) > C.BIT_LINE_CHARS for x in msg.lines):
        raise ValueError(f"BIT message exceeds {C.BIT_LINES}x{C.BIT_LINE_CHARS}: {msg.lines}")
    return msg


def select_bit_stops(
    stops: pd.DataFrame, dongs: list[Dong], tmax_c: float | None, top_n: int = C.BIT_TOP_N_DEFAULT,
    radius_m: float = C.BIT_RADIUS_M, hot: bool = True, apparent_max_c: float | None = None,
) -> tuple[list[BitStop], BitInfo]:
    """Pick up to top_n stops inside the region's 행정동 maximising covered 65+ (see module doc)."""
    msg_note = (
        "정류장별 65+ 인구는 행정동 65+를 동 내 정류장에 균등 배분한 추정값. "
        "정류장이 없는 행정동의 인구는 후보에서 제외된다."
    )
    method = f"greedy maximum coverage, 반경 {radius_m:.0f} m (haversine)"
    lats_all = stops["위도"].to_numpy(dtype=float)
    lons_all = stops["경도"].to_numpy(dtype=float)
    parts: list[pd.DataFrame] = []
    with_stops = 0
    for d in dongs:
        if d.pop65 <= 0:
            continue
        inside = points_in_rings(d.rings, lons_all, lats_all)
        if inside.any():
            part = stops[inside].copy()
            part["dong"] = d.name
            part["w"] = d.pop65 / len(part)
            parts.append(part)
            with_stops += d.pop65
    if not parts:
        return [], BitInfo(
            radius_m=radius_m, covered_pop65_est=0, region_pop65_with_stops=0, n_candidate_stops=0,
            method=method, note="자료 없음: 이 시군구 안에서 좌표가 확인되는 정류장이 없다. " + msg_note,
        )
    cand = pd.concat(parts, ignore_index=True)
    cand = cand.sort_values(["정류장번호"], kind="stable").reset_index(drop=True)
    lat, lon = cand["위도"].to_numpy(dtype=float), cand["경도"].to_numpy(dtype=float)
    w = cand["w"].to_numpy(dtype=float)
    cover = coverage_matrix(lat, lon, lat, lon, radius_m)
    chosen, marginal = greedy_max_coverage(cover, w, top_n)
    message = display_message(tmax_c, hot, apparent_max_c)
    out = [
        BitStop(
            rank=n + 1, stop_id=str(cand.at[i, "정류장번호"]), name=str(cand.at[i, "정류장명"]),
            lat=float(lat[i]), lon=float(lon[i]), dong=str(cand.at[i, "dong"]),
            covered_pop65_est=int(round(g)), message=message,
        )
        for n, (i, g) in enumerate(zip(chosen, marginal, strict=True))
    ]
    info = BitInfo(
        radius_m=radius_m, covered_pop65_est=int(round(sum(marginal))),
        region_pop65_with_stops=with_stops, n_candidate_stops=len(cand),
        method=method, note=msg_note,
    )
    return out, info
