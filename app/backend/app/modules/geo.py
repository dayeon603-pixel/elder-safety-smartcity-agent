"""Assign points to a 시군구 `region.code`: boundary polygon first, nearest centroid as fallback.

Polygons come from the same HangJeongDong GeoJSON the heat ingest uses (property `sgg` = 5-digit
code). A polygon hit is used only if that code exists in `region` (merged units may not match);
otherwise the point falls back to the nearest region centroid within MAX_CENTROID_KM. Holes in
polygons are ignored (administrative boundaries have none that matter at this scale).
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

logger = logging.getLogger("modules.geo")

METHOD_POLYGON = "polygon"
METHOD_CENTROID = "centroid"
MAX_CENTROID_KM = 15.0
EARTH_RADIUS_KM = 6371.0088

Assignment = tuple[str | None, str | None]  # (sgg_code, method)
Ring = npt.NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class _Shape:
    code: str
    rings: tuple[Ring, ...]  # outer rings, columns lon, lat


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def _in_ring(ring: Ring, lon: float, lat: float) -> bool:
    """Even-odd ray casting."""
    x, y = ring[:, 0], ring[:, 1]
    x2, y2 = np.roll(x, -1), np.roll(y, -1)
    crosses = (y > lat) != (y2 > lat)
    with np.errstate(divide="ignore", invalid="ignore"):
        x_at = (x2 - x) * (lat - y) / (y2 - y) + x
    return bool(np.count_nonzero(crosses & (lon < x_at)) % 2)


def _bbox(shape: _Shape) -> list[float]:
    """[lon_min, lon_max, lat_min, lat_max] over all rings of the shape."""
    return [
        min(float(r[:, 0].min()) for r in shape.rings), max(float(r[:, 0].max()) for r in shape.rings),
        min(float(r[:, 1].min()) for r in shape.rings), max(float(r[:, 1].max()) for r in shape.rings),
    ]


def load_shapes(geojson_path: Path, wanted: set[str]) -> list[_Shape]:
    """Outer rings per feature whose `sgg` is in `wanted`."""
    with geojson_path.open(encoding="utf-8") as fh:
        features = json.load(fh)["features"]
    shapes: list[_Shape] = []
    for f in features:
        code = str(f["properties"]["sgg"])
        if code not in wanted:
            continue
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        shapes.append(_Shape(code, tuple(np.asarray(p[0], dtype=float) for p in polys)))
    return shapes


def assign_points(
    conn: sqlite3.Connection, points: Sequence[tuple[float, float]], geojson_path: Path | None
) -> list[Assignment]:
    """(sgg_code, method) per (lat, lon) point; (None, None) when no region is close enough."""
    regions = [(str(r["code"]), float(r["lat"]), float(r["lon"])) for r in conn.execute(
        "SELECT code, lat, lon FROM region")]
    if not regions:
        logger.warning("region table is empty: sgg_code stays NULL")
        return [(None, None)] * len(points)
    shapes: list[_Shape] = []
    if geojson_path is not None and geojson_path.is_file():
        shapes = load_shapes(geojson_path, {c for c, _, _ in regions})
    boxes = np.array([_bbox(s) for s in shapes]) if shapes else np.empty((0, 4))
    out: list[Assignment] = []
    for lat, lon in points:
        hit: str | None = None
        if shapes:
            for i in np.flatnonzero((boxes[:, 0] <= lon) & (lon <= boxes[:, 1])
                                    & (boxes[:, 2] <= lat) & (lat <= boxes[:, 3])):
                if any(_in_ring(r, lon, lat) for r in shapes[int(i)].rings):
                    hit = shapes[int(i)].code
                    break
        if hit is not None:
            out.append((hit, METHOD_POLYGON))
            continue
        code, dist = min(((c, _haversine_km(lat, lon, la, lo)) for c, la, lo in regions), key=lambda t: t[1])
        out.append((code, METHOD_CENTROID) if dist <= MAX_CENTROID_KM else (None, None))
    return out
