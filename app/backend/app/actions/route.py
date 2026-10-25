"""방문팀 동선: order the top 65+ 행정동 of a region into a short visiting route.

Nearest-neighbour from the largest-65+ 행정동, then 2-opt on the open path (start fixed). Distances
are straight lines (haversine), not road distances; the response says so.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from . import constants as C
from .dongs import Dong
from .geometry import FloatArray, haversine_matrix_m
from .models import RouteStop

DISTANCE_BASIS = "직선거리 기준"
METHOD = "상위 65+ 행정동 선택 -> nearest-neighbor -> 2-opt (출발은 65+ 최다 행정동 고정)"
EPS = 1e-9


def distance_matrix_km(points: Sequence[tuple[float, float]]) -> FloatArray:
    """Pairwise haversine distances (km) of (lat, lon) points."""
    lat = np.array([p[0] for p in points], dtype=float)
    lon = np.array([p[1] for p in points], dtype=float)
    return haversine_matrix_m(lat, lon, lat, lon) / 1000.0


def path_length(order: Sequence[int], dist: FloatArray) -> float:
    """Length of the open path visiting `order` in sequence."""
    return float(sum(dist[a, b] for a, b in zip(order, order[1:], strict=False)))


def nearest_neighbor(dist: FloatArray, start: int = 0) -> list[int]:
    """Greedy path from `start`; ties go to the lowest index."""
    n = len(dist)
    if not 0 <= start < n:
        raise ValueError("start out of range")
    order = [start]
    left = set(range(n)) - {start}
    while left:
        nxt = min(left, key=lambda j: (dist[order[-1], j], j))
        order.append(nxt)
        left.remove(nxt)
    return order


def two_opt(order: list[int], dist: FloatArray, max_passes: int = C.TWO_OPT_MAX_PASSES) -> list[int]:
    """2-opt on an open path with the first node fixed; never returns a longer path than the input."""
    best = list(order)
    n = len(best)
    for _ in range(max_passes):
        improved = False
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                before = dist[best[i - 1], best[i]] + (dist[best[j], best[j + 1]] if j + 1 < n else 0.0)
                after = dist[best[i - 1], best[j]] + (dist[best[i], best[j + 1]] if j + 1 < n else 0.0)
                if after < before - EPS:
                    best[i:j + 1] = reversed(best[i:j + 1])
                    improved = True
        if not improved:
            break
    return best


def plan_route(dongs: list[Dong], n_stops: int = C.ROUTE_STOPS_DEFAULT) -> tuple[list[RouteStop], float, float]:
    """Top-n 행정동 by 65+ (ties by code) as an ordered route.

    Returns:
        (stops with leg lengths, route km, km of visiting in plain descending-65+ order)
    """
    top = sorted((d for d in dongs if d.pop65 > 0), key=lambda d: (-d.pop65, d.code))[:n_stops]
    if not top:
        return [], 0.0, 0.0
    dist = distance_matrix_km([(d.lat, d.lon) for d in top])
    order = two_opt(nearest_neighbor(dist, 0), dist)
    stops = [
        RouteStop(
            order=n + 1, dong_code=top[i].code, dong=top[i].name, lat=top[i].lat, lon=top[i].lon,
            pop65=top[i].pop65, leg_km=0.0 if n == 0 else round(float(dist[order[n - 1], i]), 3),
        )
        for n, i in enumerate(order)
    ]
    return stops, round(path_length(order, dist), 3), round(path_length(range(len(top)), dist), 3)
