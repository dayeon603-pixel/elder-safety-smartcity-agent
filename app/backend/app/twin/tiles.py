"""Satellite tile proxy (V-World WMTS) so the API key never reaches the browser.

GET /twin/tiles/satellite/{z}/{x}/{y}.jpg -> https://api.vworld.kr/req/wmts/1.0.0/<key>/Satellite/{z}/{y}/{x}.jpeg (TileMatrix /
TileRow / TileCol). Only zoom 6-19 and tiles intersecting the demo 구 bbox (+2 km) are served; others 404. Tiles are kept in a
process-lifetime LRU (<= 3,000) only: no disk cache (terms unverified). The key is read from VWORLD_KEY; without it the route
answers 503 `no_vworld_key` so the frontend falls back to its offline basemap. Logs and error bodies carry z/x/y only.
"""

from __future__ import annotations

import logging
import math
import threading
from collections import OrderedDict
from typing import Final

import httpx

logger = logging.getLogger("twin.tiles")
UPSTREAM: Final[str] = "https://api.vworld.kr/req/wmts/1.0.0/{key}/Satellite/{z}/{y}/{x}.jpeg"
ZOOM_MIN, ZOOM_MAX = 6, 19
TIMEOUT_S: Final[float] = 5.0
MAX_TILES: Final[int] = 3000
CACHE_CONTROL: Final[str] = "private, max-age=3600"
MEDIA_TYPE: Final[str] = "image/jpeg"
# 관악구 bbox (scene bbox) + 2 km
PAD_KM: Final[float] = 2.0
GWANAK_BBOX: Final[tuple[float, float, float, float]] = (126.899469, 37.435724, 126.98862, 37.494989)  # lon_min, lat_min, lon_max, lat_max
_LOCK = threading.Lock()
_CACHE: OrderedDict[tuple[int, int, int], bytes] = OrderedDict()


class TileError(RuntimeError):
    """Upstream failure (message never contains the key or the URL)."""


def allowed_bbox(bbox: tuple[float, float, float, float] = GWANAK_BBOX, pad_km: float = PAD_KM) -> tuple[float, float, float, float]:
    lon_min, lat_min, lon_max, lat_max = bbox
    dlat = pad_km / 111.32
    dlon = pad_km / (111.32 * math.cos(math.radians((lat_min + lat_max) / 2)))
    return lon_min - dlon, lat_min - dlat, lon_max + dlon, lat_max + dlat


def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Web-Mercator tile -> (lon_min, lat_min, lon_max, lat_max)."""
    n = 2**z

    def lat(yy: int) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))

    return x / n * 360.0 - 180.0, lat(y + 1), (x + 1) / n * 360.0 - 180.0, lat(y)


def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    """Web-Mercator tile (x, y) containing a point at zoom z."""
    n = 2**z
    x = int((lon + 180.0) / 360.0 * n)
    lat_r = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def tile_allowed(z: int, x: int, y: int, bbox: tuple[float, float, float, float] | None = None) -> bool:
    """Zoom inside 6-19, x/y inside the grid, and the tile intersects the padded demo bbox."""
    if not ZOOM_MIN <= z <= ZOOM_MAX or not 0 <= x < 2**z or not 0 <= y < 2**z:
        return False
    a_lon0, a_lat0, a_lon1, a_lat1 = bbox or allowed_bbox()
    t_lon0, t_lat0, t_lon1, t_lat1 = tile_bounds(z, x, y)
    return t_lon0 <= a_lon1 and t_lon1 >= a_lon0 and t_lat0 <= a_lat1 and t_lat1 >= a_lat0


def fetch_tile(http: httpx.Client, key: str, z: int, x: int, y: int) -> bytes:
    """Tile bytes from the process cache or upstream (5 s timeout).

    Raises:
        TileError: non-200, non-image, transport error or timeout (redacted).
    """
    k = (z, x, y)
    with _LOCK:
        hit = _CACHE.get(k)
        if hit is not None:
            _CACHE.move_to_end(k)
            return hit
    url = UPSTREAM.format(key=key, z=z, y=y, x=x)
    try:
        r = http.get(url, timeout=TIMEOUT_S)
    except httpx.HTTPError as exc:
        logger.warning("tile %d/%d/%d upstream error: %s", z, x, y, type(exc).__name__)
        raise TileError(f"upstream {type(exc).__name__}") from exc
    if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
        logger.warning("tile %d/%d/%d upstream status %d (%s)", z, x, y, r.status_code, r.headers.get("content-type", "?"))
        raise TileError(f"upstream status {r.status_code}")
    body = r.content
    with _LOCK:
        _CACHE[k] = body
        _CACHE.move_to_end(k)
        while len(_CACHE) > MAX_TILES:
            _CACHE.popitem(last=False)
    return body


def cache_size() -> int:
    with _LOCK:
        return len(_CACHE)


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


__all__ = ["CACHE_CONTROL", "MAX_TILES", "MEDIA_TYPE", "TileError", "allowed_bbox", "cache_size", "clear_cache", "fetch_tile",
           "lonlat_to_tile", "tile_allowed", "tile_bounds"]
