"""Distances and point-in-polygon tests shared by the action channels (numpy, no row loops)."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
import numpy.typing as npt

EARTH_RADIUS_M = 6371008.8
PIP_POINT_CHUNK = 2000

FloatArray = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]
Ring = npt.NDArray[np.float64]  # columns lon, lat


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def haversine_matrix_m(lat_a: FloatArray, lon_a: FloatArray, lat_b: FloatArray, lon_b: FloatArray) -> FloatArray:
    """Pairwise distances in metres, shape (len(a), len(b))."""
    pa, pb = np.radians(lat_a)[:, None], np.radians(lat_b)[None, :]
    dlon = np.radians(lon_b)[None, :] - np.radians(lon_a)[:, None]
    h = np.sin((pb - pa) / 2) ** 2 + np.cos(pa) * np.cos(pb) * np.sin(dlon / 2) ** 2
    out: FloatArray = 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(np.minimum(1.0, h)))
    return out


def points_in_ring(ring: Ring, lons: FloatArray, lats: FloatArray) -> BoolArray:
    """Even-odd ray casting of many points against one ring (holes are ignored)."""
    inside = np.zeros(len(lons), dtype=bool)
    if len(lons) == 0 or len(ring) < 3:
        return inside
    x1, y1 = ring[:, 0], ring[:, 1]
    x2, y2 = np.roll(x1, -1), np.roll(y1, -1)
    box = (lons >= x1.min()) & (lons <= x1.max()) & (lats >= y1.min()) & (lats <= y1.max())
    idx = np.flatnonzero(box)
    dy = y2 - y1
    safe_dy = np.where(dy == 0, 1.0, dy)
    for start in range(0, len(idx), PIP_POINT_CHUNK):
        sel = idx[start:start + PIP_POINT_CHUNK]
        px, py = lons[sel][:, None], lats[sel][:, None]
        crosses = (y1[None, :] > py) != (y2[None, :] > py)
        x_at = (x2 - x1)[None, :] * (py - y1[None, :]) / safe_dy[None, :] + x1[None, :]
        inside[sel] = (np.count_nonzero(crosses & (px < x_at), axis=1) % 2) == 1
    return inside


def points_in_rings(rings: Sequence[Ring], lons: FloatArray, lats: FloatArray) -> BoolArray:
    """True where the point lies in at least one ring."""
    out = np.zeros(len(lons), dtype=bool)
    for ring in rings:
        out |= points_in_ring(ring, lons, lats)
    return out
