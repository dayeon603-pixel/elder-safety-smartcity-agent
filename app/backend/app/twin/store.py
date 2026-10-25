"""Locate and load the precomputed scene / graph files of a 구 (numpy only; no geo dependencies).

The exact cache key is preferred; when only another key exists (sources mounted elsewhere, or rebuilt with a
different mtime) the newest file is served and `cache` says "stale-key". Loaded graphs stay in memory.
"""

from __future__ import annotations

import gzip
import heapq
import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from ..config import Settings
from . import scene_build
from .proj import point_to_local

logger = logging.getLogger("twin.store")
FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]
_LOCK = threading.Lock()
_GRAPHS: dict[str, WalkIndex] = {}
_SCENES: dict[str, tuple[bytes, str]] = {}  # path -> (gzip body, sha256 etag)
BUILD_LOCK = threading.Lock()


class SceneNotBuilt(RuntimeError):
    """No scene file exists for the 구 (and it could not be built in this process)."""


@dataclass(frozen=True, slots=True)
class SceneFile:
    path: Path
    key: str
    cache: str  # "exact" | "stale-key"


@dataclass(slots=True)
class WalkIndex:
    """Routing view of one 구's walk network."""

    sgg_code: str
    node_lon: FloatArray
    node_lat: FloatArray
    node_x: FloatArray
    node_y: FloatArray
    indptr: IntArray
    nbr: IntArray
    w: FloatArray
    eid: IntArray
    rev: IntArray
    road_flat: FloatArray
    road_start: IntArray
    center: tuple[float, float]
    bbox: tuple[float, float, float, float]
    built_at: str

    def nearest(self, lon: float, lat: float) -> tuple[int, float]:
        """(node index, metres) of the closest node to a point."""
        x, y = point_to_local(lon, lat, *self.center)
        d2 = (self.node_x - x) ** 2 + (self.node_y - y) ** 2
        i = int(np.argmin(d2))
        return i, float(np.sqrt(d2[i]))

    def nearest_many(self, lon: FloatArray, lat: FloatArray) -> tuple[IntArray, FloatArray]:
        """Vectorised `nearest` for many points (chunked so memory stays bounded)."""
        from .proj import to_local

        xs, ys = to_local(lon, lat, *self.center)
        idx = np.empty(len(xs), dtype=np.int64)
        dist = np.empty(len(xs), dtype=np.float64)
        chunk = 256
        for s in range(0, len(xs), chunk):
            dx = self.node_x[None, :] - xs[s:s + chunk, None]
            dy = self.node_y[None, :] - ys[s:s + chunk, None]
            d2 = dx * dx + dy * dy
            j = np.argmin(d2, axis=1)
            idx[s:s + chunk] = j
            dist[s:s + chunk] = np.sqrt(d2[np.arange(len(j)), j])
        return idx, dist

    def shortest_to_any(self, start: int, targets: dict[int, Any]) -> tuple[int, float, list[int]] | None:
        """Dijkstra from `start` until the first node of `targets` is settled.

        Returns:
            (target node, metres, adjacency slots walked) or None when no target is reachable.
        """
        n = len(self.node_x)
        dist = np.full(n, np.inf)
        prev_node = np.full(n, -1, dtype=np.int64)
        prev_slot = np.full(n, -1, dtype=np.int64)
        dist[start] = 0.0
        heap: list[tuple[float, int]] = [(0.0, start)]
        indptr, nbr, w = self.indptr, self.nbr, self.w
        while heap:
            d, u = heapq.heappop(heap)
            if d > dist[u]:
                continue
            if u in targets:
                slots: list[int] = []
                cur = u
                while cur != start:
                    slots.append(int(prev_slot[cur]))
                    cur = int(prev_node[cur])
                slots.reverse()
                return u, float(d), slots
            for s in range(int(indptr[u]), int(indptr[u + 1])):
                v = int(nbr[s])
                nd = d + float(w[s])
                if nd < dist[v]:
                    dist[v] = nd
                    prev_node[v] = u
                    prev_slot[v] = s
                    heapq.heappush(heap, (nd, v))
        return None

    def path_lonlat(self, slots: list[int]) -> list[list[float]]:
        """Edge geometries of the walked adjacency slots, joined, as [[lon, lat], ...]."""
        from .proj import to_lonlat

        parts: list[FloatArray] = []
        for s in slots:
            e = int(self.eid[s])
            a, b = int(self.road_start[e]), int(self.road_start[e + 1])
            seg = self.road_flat[2 * a:2 * b].reshape(-1, 2)
            if self.rev[s]:
                seg = seg[::-1]
            if parts:
                seg = seg[1:]
            parts.append(seg)
        if not parts:
            return []
        xy = np.concatenate(parts)
        lon, lat = to_lonlat(xy[:, 0], xy[:, 1], *self.center)
        return [[round(float(a), 6), round(float(b), 6)] for a, b in zip(lon, lat, strict=True)]


def _find(d: Path, prefix: str, suffix: str, sgg_code: str, key: str) -> SceneFile | None:
    exact = d / f"{prefix}-{sgg_code}-{key}{suffix}"
    if exact.is_file():
        return SceneFile(exact, key, "exact")
    candidates = sorted(d.glob(f"{prefix}-{sgg_code}-*{suffix}"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return None
    k = candidates[0].name[len(prefix) + 1 + len(sgg_code) + 1:-len(suffix)]
    return SceneFile(candidates[0], k, "stale-key")


def _key_or_none(settings: Settings, sgg_code: str) -> str | None:
    sources = scene_build.scene_sources(settings)
    if all(p.is_file() for p in sources):
        return scene_build.source_key(sgg_code, sources, settings.data_dir)
    return None


def locate(settings: Settings, sgg_code: str, prefix: str = "scene", suffix: str = ".json.gz") -> SceneFile | None:
    d = scene_build.cache_dir(settings)
    if not d.is_dir():
        return None
    key = _key_or_none(settings, sgg_code) or ""
    return _find(d, prefix, suffix, sgg_code, key)


def ensure_scene(conn: sqlite3.Connection, settings: Settings, sgg_code: str) -> SceneFile:
    """The scene file, building it on the spot when sources and build dependencies are present.

    Raises:
        SceneNotBuilt: nothing cached and the build is impossible here (message says why / how to build).
    """
    found = locate(settings, sgg_code)
    if found is not None and found.cache == "exact":
        return found
    with BUILD_LOCK:
        again = locate(settings, sgg_code)
        if again is not None and again.cache == "exact":
            return again
        try:
            info = scene_build.build_scene(conn, settings, sgg_code)  # sources changed (or first call): rebuild
        except scene_build.TwinBuildError as exc:
            if again is not None:
                logger.warning("scene %s: serving %s (cannot rebuild here: %s)", sgg_code, again.path.name, exc)
                return again
            raise SceneNotBuilt(f"{exc}. Build it where the geo group is installed: "
                                f"`uv run python -m app.twin.precompute {sgg_code}`") from exc
    with _LOCK:
        _SCENES.clear()
        _GRAPHS.pop(sgg_code, None)
    return SceneFile(Path(info["json_gz"]), info["key"], "exact")


def scene_bytes(file: SceneFile) -> tuple[bytes, str]:
    """Gzipped JSON body and its ETag, from memory after the first read."""
    k = str(file.path)
    with _LOCK:
        hit = _SCENES.get(k)
    if hit is not None:
        return hit
    t0 = time.perf_counter()
    body = file.path.read_bytes()
    import hashlib

    etag = '"%s"' % hashlib.sha256(body).hexdigest()[:32]
    with _LOCK:
        _SCENES.clear()  # one scene body per process is enough; the files are large
        _SCENES[k] = (body, etag)
    logger.info("scene %s loaded (%d bytes, %.0f ms)", file.path.name, len(body), (time.perf_counter() - t0) * 1000)
    return body, etag


def scene_summary(file: SceneFile) -> dict[str, Any]:
    """Counts and bounds of a scene (decompresses once; for /twin/<sgg>/scene?meta=1 and tests)."""
    body, _ = scene_bytes(file)
    doc = json.loads(gzip.decompress(body))
    return {"sgg_code": doc["sgg_code"], "region": doc["region"], "center": doc["center"], "bbox": doc["bbox"],
            "buildings": doc["buildings"]["count"], "roads": doc["roads"]["count"], "built_at": doc["built_at"],
            "height_source_counts": doc["buildings"].get("height_source_counts"), "measured_pct": doc["buildings"].get("measured_pct"),
            "imputation": doc["buildings"].get("imputation"), "use_counts": doc["buildings"].get("use_counts"),
            "sources": doc["sources"], "fetched_at": doc["fetched_at"], "cache_key": doc["cache_key"]}


def graph(conn: sqlite3.Connection, settings: Settings, sgg_code: str) -> WalkIndex:
    """The routing index of a 구 (built with the scene when missing).

    Raises:
        SceneNotBuilt: see `ensure_scene`.
    """
    with _LOCK:
        g = _GRAPHS.get(sgg_code)
    if g is not None:
        return g
    ensure_scene(conn, settings, sgg_code)  # rebuilds when the sources changed and a build is possible here
    found = locate(settings, sgg_code, "graph", ".npz")
    if found is None:
        raise SceneNotBuilt(f"graph file for {sgg_code} missing after build")
    with np.load(found.path) as z:
        g = WalkIndex(
            sgg_code=sgg_code, node_lon=z["node_lon"], node_lat=z["node_lat"], node_x=z["node_x"], node_y=z["node_y"],
            indptr=z["indptr"], nbr=z["nbr"], w=z["w"], eid=z["eid"], rev=z["rev"], road_flat=z["road_flat"],
            road_start=z["road_start"], center=(float(z["center"][0]), float(z["center"][1])),
            bbox=tuple(float(v) for v in z["bbox"]),  # type: ignore[arg-type]
            built_at=str(z["built_at"]),
        )
    with _LOCK:
        _GRAPHS[sgg_code] = g
    return g


def clear_cache() -> None:
    """Drop in-memory scenes and graphs (tests)."""
    with _LOCK:
        _GRAPHS.clear()
        _SCENES.clear()


__all__ = ["SceneFile", "SceneNotBuilt", "WalkIndex", "clear_cache", "ensure_scene", "graph", "locate", "scene_bytes",
           "scene_summary"]
