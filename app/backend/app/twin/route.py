"""Walk-network route to the nearest AED that is open at the clock reading (Dijkstra on the precomputed CSR).

Targets are every Seoul AED inside the 구's graph bounds (routes may cross the border) whose posted hours say open at
(daytype, hour) and that snaps to the network within AED_SNAP_MAX_M. Distance = network path + the two snap legs
(straight lines), time at WALK_SPEED_M_S (0.8 m/s, 고령 보행).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from typing import Any

import numpy as np

from ..actions import aed_points
from ..actions.geometry import haversine_m
from ..config import Settings
from . import constants as C
from .state import AED_SOURCE, DataUnavailable, DayType
from .store import WalkIndex

logger = logging.getLogger("twin.route")
_LOCK = threading.Lock()
_SNAPPED: dict[str, dict[str, Any]] = {}  # sgg -> {"records": [...], "node": IntArray, "snap_m": FloatArray}


class TooFarFromNetwork(ValueError):
    """The start point does not lie near the walk network of the 구."""


class NoOpenAed(RuntimeError):
    """No open AED is reachable on the network at that time."""


def _snapped_aeds(settings: Settings, g: WalkIndex) -> dict[str, Any]:
    """Seoul AEDs inside the graph bounds with their nearest node (cached per 구)."""
    with _LOCK:
        hit = _SNAPPED.get(g.sgg_code)
    if hit is not None:
        return hit
    try:
        df = aed_points.load_aeds(settings.data_dir)
    except FileNotFoundError as exc:
        raise DataUnavailable(f"AED cache missing: {exc}") from exc
    lon_min, lat_min, lon_max, lat_max = g.bbox
    pad = C.ROUTE_BOUNDS_PAD_DEG
    lat = df["lat"].to_numpy(dtype=float)
    lon = df["lon"].to_numpy(dtype=float)
    mask = ((df["sido"] == C.SEOUL).to_numpy() & np.isfinite(lat) & np.isfinite(lon)
            & (lon >= lon_min - pad) & (lon <= lon_max + pad) & (lat >= lat_min - pad) & (lat <= lat_max + pad))
    sub = df[mask]
    t0 = time.perf_counter()
    node, snap = g.nearest_many(sub["lon"].to_numpy(dtype=float), sub["lat"].to_numpy(dtype=float))
    out = {"records": sub.to_dict("records"), "node": node, "snap_m": snap}
    logger.info("snapped %d AEDs to the %s network (%.0f ms)", len(sub), g.sgg_code, (time.perf_counter() - t0) * 1000)
    with _LOCK:
        _SNAPPED[g.sgg_code] = out
    return out


def route_to_open_aed(conn: sqlite3.Connection, settings: Settings, g: WalkIndex, lat: float, lon: float, date_: str,
                      hour: int, daytype: DayType) -> dict[str, Any]:
    """Path to the nearest open AED.

    Raises:
        TooFarFromNetwork: start farther than ROUTE_MAX_SNAP_M from any walk node.
        NoOpenAed: no AED is open (or none reachable from the start component).
        DataUnavailable: AED cache missing.
    """
    t0 = time.perf_counter()
    start, start_snap = g.nearest(lon, lat)
    if start_snap > C.ROUTE_MAX_SNAP_M:
        raise TooFarFromNetwork(f"start point is {start_snap:.0f} m from the {g.sgg_code} walk network (max {C.ROUTE_MAX_SNAP_M:.0f} m)")
    snapped = _snapped_aeds(settings, g)
    day = aed_points.DAY_PARAM[daytype]
    targets: dict[int, list[int]] = {}
    n_open = 0
    for i, r in enumerate(snapped["records"]):
        if aed_points.open_state(r, day, hour) is not True or snapped["snap_m"][i] > C.AED_SNAP_MAX_M:
            continue
        n_open += 1
        targets.setdefault(int(snapped["node"][i]), []).append(i)
    if not targets:
        raise NoOpenAed(f"no AED inside the {g.sgg_code} network bounds is open at {daytype} {hour:02d}h")
    hit = g.shortest_to_any(start, targets)
    if hit is None:
        raise NoOpenAed("no open AED is reachable from the start point on the walk network")
    node, metres, slots = hit
    # several AEDs may share the node: take the one closest to it
    best = min(targets[node], key=lambda i: float(snapped["snap_m"][i]))
    rec = snapped["records"][best]
    path = g.path_lonlat(slots)
    start_pt = [round(lon, 6), round(lat, 6)]
    end_pt = [round(float(rec["lon"]), 6), round(float(rec["lat"]), 6)]
    if not path:
        path = [[round(float(g.node_lon[start]), 6), round(float(g.node_lat[start]), 6)]]
    full = [start_pt, *path, end_pt]
    total = metres + start_snap + float(snapped["snap_m"][best])
    return {
        "aed": {"id": str(rec["serialSeq"]), "name": rec["org"] or "", "place": rec["buildPlace"] or None,
                "address": rec["buildAddress"] or None, "lat": float(rec["lat"]), "lon": float(rec["lon"]),
                "hours": aed_points.hours_text(rec, day), "open": True, "gugun": rec["gugun"]},
        "path": full, "meters": round(total, 1), "network_meters": round(metres, 1),
        "snap_start_m": round(start_snap, 1), "snap_aed_m": round(float(snapped["snap_m"][best]), 1),
        "seconds": round(total / C.WALK_SPEED_M_S), "speed_m_s": C.WALK_SPEED_M_S,
        "straight_line_m": round(haversine_m(lat, lon, float(rec["lat"]), float(rec["lon"])), 1),
        "date": date_, "hour": hour, "daytype": daytype, "open_aeds_considered": n_open, "nodes_in_network": int(len(g.node_x)),
        "sgg_code": g.sgg_code, "compute_ms": round((time.perf_counter() - t0) * 1000, 1),
        "sources": [{"what": "OSM walk network", "license": C.OSM_LICENSE, "url": C.OSM_URL, "built_at": g.built_at,
                     "display_source": "오픈스트리트맵 보행망"},
                    {"what": AED_SOURCE, "display_source": "국립중앙의료원 AED 운영시간표"}],
        "fetched_at": g.built_at,
        "note": "경로 = OSM 보행망 최단경로 + 출발·도착 직선 접속 구간; 신호 대기·엘리베이터는 포함하지 않는다",
    }


def clear_cache() -> None:
    with _LOCK:
        _SNAPPED.clear()


__all__ = ["NoOpenAed", "TooFarFromNetwork", "clear_cache", "route_to_open_aed"]
