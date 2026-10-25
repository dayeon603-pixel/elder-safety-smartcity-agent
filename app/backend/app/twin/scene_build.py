"""Precompute one 구's static twin scene: every OSM building footprint (no simplification), the walk network,
the 구 boundary, into compact files the API serves with numpy only.

Inputs (KCF_DATA_DIR): extra/osm/seoul_buildings.parquet (pyarrow needed, geo group), extra/osm/seoul_walk.graphml
(parsed with lxml, a runtime dependency: osmnx/networkx are NOT used), the 행정동 boundaries already loaded by
app.actions.dongs. shapely (geo group) is used only to union the 행정동 into the 구 outline; without it the
행정동 rings themselves are the boundary.

Outputs (<cache dir>/): scene-<sgg>-<key>.json.gz (served as-is, Content-Encoding gzip) and graph-<sgg>-<key>.npz
(routing). `key` = sha256 of (layout version, sgg, source file sizes/mtimes); the API falls back to the newest
file of the 구 when the key differs (data mounted read-only elsewhere) and says so in `cache`.

Run: python -m app.twin.precompute 11620 [more codes]   (also lazily on the first /twin/<sgg>/scene call in dev)
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import struct
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
from lxml import etree

from .. import repo_heat
from ..actions import dongs as dongs_mod
from ..actions.geometry import points_in_rings
from ..config import Settings
from . import constants as C
from .proj import to_local

logger = logging.getLogger("twin.build")
FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]
GRAPHML_NS = "{http://graphml.graphdrawing.org/xmlns}"
WKB_POINT, WKB_POLYGON, WKB_MULTIPOLYGON = 1, 3, 6
WKB_SRID_FLAG = 0x20000000
WKB_TYPE_MASK = 0xFF
LEVELS_RE = re.compile(r"-?\d+(?:\.\d+)?")
WKT_LINESTRING_RE = re.compile(r"LINESTRING\s*\((.*)\)", re.IGNORECASE)


class TwinBuildError(RuntimeError):
    """The scene cannot be built: a source file or an optional build dependency is missing."""


@dataclass(frozen=True, slots=True)
class Footprint:
    """One simple polygon of a building (multipolygons are split into their parts)."""

    outer: FloatArray  # (n, 2) lon, lat; closed ring (first == last)
    holes: tuple[FloatArray, ...]
    building: str
    levels: float | None
    row: int
    levels_source: str | None = None  # "floors" | "source_height" | None (to be imputed)


# --- WKB ------------------------------------------------------------------------------------------------


def _rings(buf: memoryview, pos: int, little: bool) -> tuple[list[FloatArray], int]:
    bo = "<" if little else ">"
    (n_rings,) = struct.unpack_from(f"{bo}I", buf, pos)
    pos += 4
    rings: list[FloatArray] = []
    for _ in range(n_rings):
        (n_pts,) = struct.unpack_from(f"{bo}I", buf, pos)
        pos += 4
        arr = np.frombuffer(buf, dtype=f"{bo}f8", count=n_pts * 2, offset=pos).reshape(n_pts, 2).astype(np.float64)
        pos += n_pts * 16
        rings.append(arr)
    return rings, pos


def parse_wkb_polygons(wkb: bytes) -> list[tuple[FloatArray, tuple[FloatArray, ...]]]:
    """(outer, holes) of every polygon in a WKB Polygon/MultiPolygon (little- or big-endian, optional SRID).

    Raises:
        ValueError: for any other geometry type or a truncated buffer.
    """
    buf = memoryview(wkb)
    out: list[tuple[FloatArray, tuple[FloatArray, ...]]] = []

    def one(pos: int) -> int:
        little = buf[pos] == 1
        bo = "<" if little else ">"
        (gtype,) = struct.unpack_from(f"{bo}I", buf, pos + 1)
        pos += 5
        if gtype & WKB_SRID_FLAG:
            pos += 4
        kind = gtype & WKB_TYPE_MASK
        if kind == WKB_POLYGON:
            rings, pos = _rings(buf, pos, little)
            if rings:
                out.append((rings[0], tuple(rings[1:])))
            return pos
        if kind == WKB_MULTIPOLYGON:
            (n,) = struct.unpack_from(f"{bo}I", buf, pos)
            pos += 4
            for _ in range(n):
                pos = one(pos)
            return pos
        raise ValueError(f"unsupported WKB geometry type {kind}")

    try:
        one(0)
    except struct.error as exc:
        raise ValueError("truncated WKB") from exc
    return out


def use_category(tag: str | None) -> int:
    """Small-int building use from the Korean use name (V-World A9) or the OSM building tag, by substring; 5 = 미상."""
    if not tag:
        return C.USE_UNKNOWN
    t = str(tag).strip().lower()
    if not t or t in ("yes", "none", "nan", "unknown", "building"):
        return C.USE_UNKNOWN
    for cat, pats in C.USE_PATTERNS:
        if any(p in t for p in pats):
            return cat
    return C.USE_UNKNOWN


def parse_levels(raw: str | None) -> float | None:
    """First number of a building:levels tag ("3", "3.5", "2;3", "3 " ...); None if absent or implausible."""
    if raw is None:
        return None
    m = LEVELS_RE.search(str(raw))
    if m is None:
        return None
    v = float(m.group(0))
    if not math.isfinite(v) or v < C.MIN_LEVELS or v > C.MAX_LEVELS:
        return None
    return v


# --- buildings --------------------------------------------------------------------------------------------


def _pick(names: Sequence[str], candidates: Sequence[str]) -> str | None:
    return next((c for c in candidates if c in names), None)


def read_buildings(path: Path) -> list[Footprint]:
    """All footprints of a parquet (pyarrow): WKB geometry column + a levels or height column + an optional use tag.

    Works for the OSM extract (building, building:levels, geometry) and for official 국토부 footprints with any of the
    column names in constants.BUILDING_*_COLUMNS; a height column (metres) wins over levels (height / 3.2 -> levels).
    Multipolygons become several Footprints with one row id.

    Raises:
        TwinBuildError: missing file, pyarrow not installed, or no geometry column.
    """
    if not path.is_file():
        raise TwinBuildError(f"missing source file: {path}")
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise TwinBuildError("pyarrow is not installed (uv sync --group geo); cannot read the buildings parquet") from exc
    names = pq.read_schema(path).names
    geom_col = _pick(names, C.BUILDING_GEOMETRY_COLUMNS)
    if geom_col is None:
        raise TwinBuildError(f"{path.name}: no geometry column among {C.BUILDING_GEOMETRY_COLUMNS} (columns: {names})")
    levels_col, height_col, tag_col = (_pick(names, C.BUILDING_LEVELS_COLUMNS), _pick(names, C.BUILDING_HEIGHT_COLUMNS),
                                       _pick(names, C.BUILDING_TAG_COLUMNS))
    if levels_col is None and height_col is None:
        raise TwinBuildError(f"{path.name}: no levels/height column among {C.BUILDING_LEVELS_COLUMNS + C.BUILDING_HEIGHT_COLUMNS}")
    cols = [c for c in (geom_col, levels_col, height_col, tag_col) if c is not None]
    table = pq.read_table(path, columns=cols)
    n = table.num_rows
    geoms = table.column(geom_col).to_pylist()
    levels = table.column(levels_col).to_pylist() if levels_col else [None] * n
    heights = table.column(height_col).to_pylist() if height_col else [None] * n
    tags = table.column(tag_col).to_pylist() if tag_col else [None] * n
    out: list[Footprint] = []
    bad = 0
    for i, (tag, lv, ht, g) in enumerate(zip(tags, levels, heights, geoms, strict=True)):
        if g is None:
            bad += 1
            continue
        try:
            polys = parse_wkb_polygons(g)
        except ValueError:
            bad += 1
            continue
        lv_val = parse_levels(lv)
        src: str | None = "floors" if lv_val is not None else None
        if lv_val is None and ht is not None and _finite(ht):
            lv_val = parse_levels(str(float(ht) / C.LEVEL_HEIGHT_M))
            src = "source_height" if lv_val is not None else None
        for outer, holes in polys:
            if len(outer) < 4:
                bad += 1
                continue
            out.append(Footprint(outer=outer, holes=holes, building=str(tag or "yes"), levels=lv_val, row=i, levels_source=src))
    logger.info("buildings %s: %d rows -> %d polygons (%d skipped: non-polygon/invalid); columns geom=%s levels=%s height=%s tag=%s",
                path.name, n, len(out), bad, geom_col, levels_col, height_col, tag_col)
    return out


def _finite(v: Any) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def read_buildings_shp(path: Path) -> list[Footprint]:
    """국토부 GIS건물통합정보 (zipped shapefile, any projected CRS) -> Footprints in WGS84 via geopandas (geo group).

    Raises:
        TwinBuildError: geopandas missing or the file cannot be read / has no floors column.
    """
    try:
        import geopandas as gpd
    except ImportError as exc:
        raise TwinBuildError("geopandas is not installed (uv sync --group geo); cannot read the shapefile") from exc
    try:
        gdf = gpd.read_file(path)
    except Exception as exc:  # pyogrio/fiona raise driver-specific errors; the file is untrusted input
        raise TwinBuildError(f"cannot read shapefile {path.name}: {type(exc).__name__}: {exc}") from exc
    if gdf.crs is None:
        raise TwinBuildError(f"{path.name}: shapefile has no CRS (.prj missing)")
    gdf = gdf.to_crs("EPSG:4326")
    names = list(gdf.columns)
    levels_col, height_col, tag_col = (_pick(names, C.SHP_LEVELS_COLUMNS), _pick(names, C.SHP_HEIGHT_COLUMNS), _pick(names, C.SHP_TAG_COLUMNS))
    if levels_col is None and height_col is None:
        raise TwinBuildError(f"{path.name}: no floors column among {C.SHP_LEVELS_COLUMNS} (columns: {names})")
    import shapely

    out: list[Footprint] = []
    for i, (geom, lv, ht, tag) in enumerate(zip(gdf.geometry, gdf[levels_col] if levels_col else [None] * len(gdf),
                                                 gdf[height_col] if height_col else [None] * len(gdf),
                                                 gdf[tag_col] if tag_col else [None] * len(gdf), strict=True)):
        if geom is None or geom.is_empty:
            continue
        lv_val = parse_levels(None if lv is None or (isinstance(lv, float) and math.isnan(lv)) else str(lv))
        src: str | None = "floors" if lv_val is not None else None
        if lv_val is None and ht is not None and _finite(ht) and float(ht) > 0:
            lv_val = parse_levels(str(float(ht) / C.LEVEL_HEIGHT_M))
            src = "source_height" if lv_val is not None else None
        try:
            polys = parse_wkb_polygons(shapely.to_wkb(geom))
        except ValueError:
            continue
        for outer, holes in polys:
            if len(outer) >= 4:
                out.append(Footprint(outer=outer, holes=holes, building=str(tag or "yes"), levels=lv_val, row=i, levels_source=src))
    logger.info("shapefile %s: %d rows -> %d polygons; floors=%s height=%s", path.name, len(gdf), len(out), levels_col, height_col)
    return out


def footprint_area_m2(outer: FloatArray, lat0: float) -> float:
    """Shoelace area of a lon/lat ring in square metres (local equirectangular about lat0)."""
    x, y = to_local(outer[:, 0], outer[:, 1], float(outer[0, 0]), lat0)
    return float(abs(np.dot(x[:-1], y[1:]) - np.dot(x[1:], y[:-1])) / 2.0)


@dataclass(frozen=True, slots=True)
class AreaBands:
    """Median recorded floors per footprint-area band (deciles of the known rows of the 구)."""

    edges: tuple[float, ...]  # ascending interior cut points (len = bands - 1)
    medians: tuple[float, ...]  # one per band
    n_known: int
    global_median: float

    @classmethod
    def fit(cls, areas: Sequence[float], levels: Sequence[float], bands: int = C.AREA_BANDS) -> AreaBands:
        a, lv = np.asarray(areas, dtype=float), np.asarray(levels, dtype=float)
        gm = float(np.median(lv)) if len(lv) else C.MIN_LEVELS
        if len(a) < C.MIN_KNOWN_FOR_BANDS:
            return cls(edges=(), medians=(gm,), n_known=len(a), global_median=gm)
        edges = np.quantile(a, np.linspace(0, 1, bands + 1)[1:-1])
        band = np.searchsorted(edges, a, side="right")
        meds = tuple(float(np.median(lv[band == b])) if np.any(band == b) else gm for b in range(bands))
        return cls(edges=tuple(float(e) for e in edges), medians=meds, n_known=len(a), global_median=gm)

    def band_of(self, area: float) -> int:
        return int(np.searchsorted(np.asarray(self.edges), area, side="right")) if self.edges else 0

    def impute(self, area: float) -> tuple[float, int]:
        """(levels, height_source code): 2 = area band, 3 = global median (too few known rows)."""
        if not self.edges:
            return self.global_median, 3
        return self.medians[self.band_of(area)], 2

    def table(self) -> list[dict[str, float | int | None]]:
        lows = (0.0, *self.edges)
        highs = (*self.edges, None)
        return [{"band": i, "area_m2_from": round(lo, 1), "area_m2_to": None if hi is None else round(hi, 1), "median_floors": m}
                for i, (lo, hi, m) in enumerate(zip(lows, highs, self.medians, strict=True))]


def representative_points(fps: Sequence[Footprint]) -> tuple[FloatArray, FloatArray]:
    """Vertex mean of the outer ring (the 대표점 used for 구 membership)."""
    lon = np.array([f.outer[:-1, 0].mean() for f in fps], dtype=np.float64)
    lat = np.array([f.outer[:-1, 1].mean() for f in fps], dtype=np.float64)
    return lon, lat


# --- boundary ---------------------------------------------------------------------------------------------


def region_rings(dongs: Sequence[dongs_mod.Dong]) -> list[FloatArray]:
    return [np.asarray(r, dtype=np.float64) for d in dongs for r in d.rings]


def boundary_rings(dongs: Sequence[dongs_mod.Dong]) -> tuple[list[FloatArray], str]:
    """Outline of the 구 as rings (lon, lat): shapely union of its 행정동 when available, else the 행정동 rings."""
    rings = region_rings(dongs)
    try:
        import shapely
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
    except ImportError:
        logger.warning("shapely not installed: boundary = 행정동 rings (not unioned)")
        return rings, "행정동 경계 그대로 (shapely 없음)"
    merged = unary_union([Polygon(r).buffer(0) for r in rings])
    polys = list(merged.geoms) if merged.geom_type == "MultiPolygon" else [merged]
    out = [np.asarray(shapely.get_coordinates(p.exterior), dtype=np.float64) for p in polys]
    return out, "행정동 경계 합집합 (HangJeongDong 2026-10)"


# --- walk network -----------------------------------------------------------------------------------------


@dataclass(slots=True)
class WalkGraph:
    node_id: list[str]
    node_lon: FloatArray
    node_lat: FloatArray
    edge_u: IntArray
    edge_v: IntArray
    edge_len: FloatArray
    edge_geom: list[FloatArray]  # (n, 2) lon, lat from u to v


def _parse_wkt_linestring(wkt: str) -> FloatArray | None:
    m = WKT_LINESTRING_RE.search(wkt)
    if m is None:
        return None
    pts = [p.split() for p in m.group(1).split(",")]
    try:
        return np.array([[float(a), float(b)] for a, b, *_ in pts], dtype=np.float64)
    except ValueError:
        return None


def _iter_graphml(path: Path) -> Iterator[tuple[str, dict[str, str], etree._Element]]:
    keys: dict[str, str] = {}
    for _, el in etree.iterparse(str(path), events=("end",), tag=(GRAPHML_NS + "key", GRAPHML_NS + "node", GRAPHML_NS + "edge")):
        if el.tag == GRAPHML_NS + "key":
            keys[el.get("id", "")] = el.get("attr.name", "")
        else:
            data = {keys.get(d.get("key", ""), ""): (d.text or "") for d in el}
            yield el.tag, data, el
        el.clear()
        parent = el.getparent()
        if parent is not None:
            while el.getprevious() is not None:
                del parent[0]


def read_walk_graph(path: Path, bbox: tuple[float, float, float, float]) -> WalkGraph:
    """Undirected walk edges with at least one endpoint inside `bbox` (lon_min, lat_min, lon_max, lat_max).

    osmnx stores two-way streets as two directed edges; the pair is collapsed by (min(u,v), max(u,v), length).
    Edges without geometry are straight lines between their nodes.

    Raises:
        TwinBuildError: missing graphml.
    """
    if not path.is_file():
        raise TwinBuildError(f"missing source file: {path}")
    lon_min, lat_min, lon_max, lat_max = bbox
    node_pos: dict[str, tuple[float, float]] = {}
    inside: set[str] = set()
    edges: dict[tuple[str, str, float], FloatArray | None] = {}
    t0 = time.perf_counter()
    for tag, data, el in _iter_graphml(path):
        if tag == GRAPHML_NS + "node":
            nid = el.get("id", "")
            try:
                x, y = float(data.get("x", "")), float(data.get("y", ""))
            except ValueError:
                continue
            node_pos[nid] = (x, y)
            if lon_min <= x <= lon_max and lat_min <= y <= lat_max:
                inside.add(nid)
            continue
        u, v = el.get("source", ""), el.get("target", "")
        if u == v or (u not in inside and v not in inside):
            continue
        try:
            length = float(data.get("length", ""))
        except ValueError:
            continue
        length = max(length, C.MIN_EDGE_M)
        geom = _parse_wkt_linestring(data["geometry"]) if data.get("geometry") else None
        key = (u, v, round(length, 1)) if u < v else (v, u, round(length, 1))
        if key in edges:
            continue
        if geom is not None and u > v:
            geom = geom[::-1].copy()
        edges[key] = geom
    used = sorted({k[0] for k in edges} | {k[1] for k in edges})
    index = {n: i for i, n in enumerate(used)}
    eu = np.array([index[k[0]] for k in edges], dtype=np.int64)
    ev = np.array([index[k[1]] for k in edges], dtype=np.int64)
    el_ = np.array([k[2] for k in edges], dtype=np.float64)
    geoms: list[FloatArray] = []
    for k, g in edges.items():
        if g is None:
            g = np.array([node_pos[k[0]], node_pos[k[1]]], dtype=np.float64)
        geoms.append(g)
    logger.info("walk graph: %d nodes, %d undirected edges in bbox (%.1fs)", len(used), len(edges), time.perf_counter() - t0)
    return WalkGraph(
        node_id=used,
        node_lon=np.array([node_pos[n][0] for n in used], dtype=np.float64),
        node_lat=np.array([node_pos[n][1] for n in used], dtype=np.float64),
        edge_u=eu, edge_v=ev, edge_len=el_, edge_geom=geoms,
    )


def csr(n_nodes: int, eu: IntArray, ev: IntArray, w: FloatArray) -> tuple[IntArray, IntArray, FloatArray, IntArray, IntArray]:
    """Undirected adjacency: indptr, neighbour, weight, edge id, reversed flag (1 when the edge geometry runs v->u)."""
    src = np.concatenate([eu, ev])
    dst = np.concatenate([ev, eu])
    ww = np.concatenate([w, w])
    eid = np.concatenate([np.arange(len(eu)), np.arange(len(eu))])
    rev = np.concatenate([np.zeros(len(eu), dtype=np.int64), np.ones(len(eu), dtype=np.int64)])
    order = np.argsort(src, kind="stable")
    counts = np.bincount(src, minlength=n_nodes)
    indptr = np.zeros(n_nodes + 1, dtype=np.int64)
    indptr[1:] = np.cumsum(counts)
    return indptr, dst[order], ww[order], eid[order], rev[order]


# --- assembly ---------------------------------------------------------------------------------------------


def _flatten(rings: Sequence[FloatArray], lon0: float, lat0: float) -> tuple[FloatArray, IntArray]:
    """Rings -> flat [x0,y0,x1,y1,...] metres and the start index (in vertices) of each ring."""
    starts = np.zeros(len(rings) + 1, dtype=np.int64)
    starts[1:] = np.cumsum([len(r) for r in rings])
    if not rings:
        return np.zeros(0, dtype=np.float64), starts
    all_pts = np.concatenate(rings)
    x, y = to_local(all_pts[:, 0], all_pts[:, 1], lon0, lat0)
    flat = np.empty(len(all_pts) * 2, dtype=np.float64)
    flat[0::2], flat[1::2] = x, y
    return flat, starts


def _round_list(a: FloatArray, nd: int = C.ROUNDING_M) -> list[float]:
    return [round(float(v), nd) for v in a]


def source_key(sgg_code: str, sources: Sequence[Path], data_dir: Path) -> str:
    h = hashlib.sha256(f"twin|v{C.SCENE_VERSION}|{sgg_code}".encode())
    for p in sources:
        st = p.stat()
        rel = p.relative_to(data_dir) if p.is_relative_to(data_dir) else p.name
        h.update(f"|{rel}|{st.st_size}|{int(st.st_mtime)}".encode())
    return h.hexdigest()[:16]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sources_json(parquet: Path) -> dict[str, Any] | None:
    """The fetcher's provenance JSON next to an official parquet (any *.json in its directory), if present."""
    if parquet.parent.name != Path(C.BUILDINGS_OFFICIAL_GLOB).parent.name:
        return None
    for j in sorted(parquet.parent.glob("*.json")):
        try:
            doc = json.loads(j.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            flat = {k: v for k, v in doc.items() if isinstance(v, (str, int, float))}
            nested = {f"{k}.{kk}": vv for k, v in doc.items() if isinstance(v, dict) for kk, vv in v.items() if isinstance(vv, (str, int, float))}
            return {"file": j.name, **flat, **nested}
    return None


def cache_dir(settings: Settings) -> Path:
    """Where scene files live: KCF_TWIN_CACHE_DIR, else <data dir>/extra/twin."""
    return Path(os.environ.get(C.CACHE_DIR_ENV, str(settings.data_dir / C.TWIN_CACHE_SUBDIR)))


def building_candidates(settings: Settings) -> list[tuple[Path, str]]:
    """Building files in preference order: KCF_TWIN_BUILDINGS, newest official extra/buildings/*.parquet, OSM.

    The first file that has footprints inside the 구 is used (an official extract may cover one 구 only).
    """
    out: list[tuple[Path, str]] = []
    explicit = os.environ.get(C.BUILDINGS_SOURCE_ENV, "").strip()
    if explicit:
        p = Path(explicit)
        out.append(((p if p.is_absolute() else settings.data_dir / p), f"건물 외곽선 ({C.BUILDINGS_SOURCE_ENV})"))
    shp = sorted({q for g in C.BUILDINGS_SHP_GLOBS for q in settings.data_dir.glob(g)}, key=lambda q: q.stat().st_mtime, reverse=True)
    for q in shp:
        out.append((q, "국토부 GIS건물통합정보 (V-World, 지상층수)"))
    for q in sorted(settings.data_dir.glob(C.BUILDINGS_OFFICIAL_GLOB), key=lambda q: q.stat().st_mtime, reverse=True):
        out.append((q, "건물 외곽선 통합본 (extra/buildings; 출처 JSON 참조)"))
    out.append((settings.data_dir / C.BUILDINGS_PARQUET, "OSM buildings (building:levels)"))
    return out


def scene_sources(settings: Settings) -> list[Path]:
    """Every existing building candidate + the walk graphml; the cache key follows all of them."""
    return [p for p, _ in building_candidates(settings) if p.is_file()] + [settings.data_dir / C.WALK_GRAPHML]


def build_scene(conn: sqlite3.Connection, settings: Settings, sgg_code: str, out_dir: Path | None = None) -> dict[str, Any]:
    """Build and write the scene + graph files of one 구; returns {json_gz, npz, key, n_buildings, bytes, seconds}.

    Raises:
        TwinBuildError: unknown region, no 행정동, missing source or build dependency.
    """
    t0 = time.perf_counter()
    region = repo_heat.get_region(conn, sgg_code)
    if region is None:
        raise TwinBuildError(f"region {sgg_code} not found")
    try:
        dongs = dongs_mod.dongs_of_region(region["sido"], region["name"], settings.data_dir)
    except dongs_mod.DongDataError as exc:
        raise TwinBuildError(str(exc)) from exc
    if not dongs:
        raise TwinBuildError(f"no 행정동 for {region['sido']} {region['name']}")
    sources = scene_sources(settings)
    graphml = settings.data_dir / C.WALK_GRAPHML
    if not graphml.is_file():
        raise TwinBuildError(f"missing source file: {graphml}")
    if len(sources) < 2:
        raise TwinBuildError(f"no buildings parquet: looked for {C.BUILDINGS_OFFICIAL_GLOB} and {C.BUILDINGS_PARQUET} under {settings.data_dir}")
    key = source_key(sgg_code, sources, settings.data_dir)
    d = out_dir or cache_dir(settings)
    d.mkdir(parents=True, exist_ok=True)

    rings = region_rings(dongs)
    allr = np.concatenate(rings)
    lon_min, lat_min = float(allr[:, 0].min()), float(allr[:, 1].min())
    lon_max, lat_max = float(allr[:, 0].max()), float(allr[:, 1].max())
    lon0, lat0 = (lon_min + lon_max) / 2, (lat_min + lat_max) / 2

    mine: list[Footprint] = []
    used: tuple[Path, str] | None = None
    for cand, label in building_candidates(settings):
        if not cand.is_file():
            continue
        fps = read_buildings_shp(cand) if cand.suffix.lower() in (".zip", ".shp") else read_buildings(cand)
        rlon, rlat = representative_points(fps)
        inside = points_in_rings(rings, rlon, rlat)
        mine = [f for f, ok in zip(fps, inside, strict=True) if ok]
        if mine:
            used = (cand, label)
            break
        logger.info("%s has no footprint inside %s; trying the next building source", cand.name, region["name"])
    if used is None or not mine:
        raise TwinBuildError(f"no building file has a footprint with its representative point inside {region['name']}")
    areas = [footprint_area_m2(f.outer, lat0) for f in mine]
    known = [(a, f.levels) for a, f in zip(areas, mine, strict=True) if f.levels is not None]
    if not known:
        raise TwinBuildError(f"no building inside {region['name']} has recorded floors or height; cannot impute")
    bands = AreaBands.fit([a for a, _ in known], [lv for _, lv in known])
    heights = np.empty(len(mine), dtype=np.float64)
    src = np.empty(len(mine), dtype=np.int64)
    for i, (f, area) in enumerate(zip(mine, areas, strict=True)):
        if f.levels is not None:
            lv, s = f.levels, (1 if f.levels_source == "source_height" else 0)
        else:
            lv, s = bands.impute(area)
        heights[i], src[i] = lv * C.LEVEL_HEIGHT_M, s
    n_src = {code: int((src == code).sum()) for code in C.HEIGHT_SOURCE_NAMES}
    uses = np.array([use_category(f.building) for f in mine], dtype=np.int64)
    use_counts = {C.USE_NAMES[k]: int((uses == k).sum()) for k in C.USE_NAMES}
    b_flat, b_start = _flatten([f.outer for f in mine], lon0, lat0)
    hole_rings = [h for f in mine for h in f.holes]
    hole_parent = np.array([i for i, f in enumerate(mine) for _ in f.holes], dtype=np.int64)
    h_flat, h_start = _flatten(hole_rings, lon0, lat0)

    bnd, bnd_method = boundary_rings(dongs)
    bnd_flat, bnd_start = _flatten(bnd, lon0, lat0)

    pad_lat = C.GRAPH_PAD_M / (C.EARTH_RADIUS_M * math.pi / 180)
    pad_lon = pad_lat / math.cos(math.radians(lat0))
    graph = read_walk_graph(graphml, (lon_min - pad_lon, lat_min - pad_lat, lon_max + pad_lon, lat_max + pad_lat))
    r_flat, r_start = _flatten(graph.edge_geom, lon0, lat0)
    indptr, nbr, w, eid, rev = csr(len(graph.node_id), graph.edge_u, graph.edge_v, graph.edge_len)
    nx_, ny_ = to_local(graph.node_lon, graph.node_lat, lon0, lat0)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    shas = {p.name: _sha256(p) for p in (used[0], graphml)}
    b_lon_all = np.concatenate([f.outer[:, 0] for f in mine])
    b_lat_all = np.concatenate([f.outer[:, 1] for f in mine])
    bbox = [round(min(lon_min, float(b_lon_all.min())), 6), round(min(lat_min, float(b_lat_all.min())), 6),
            round(max(lon_max, float(b_lon_all.max())), 6), round(max(lat_max, float(b_lat_all.max())), 6)]
    scene: dict[str, Any] = {
        "sgg_code": sgg_code, "region": f"{region['sido']} {region['name']}", "name": region["name"],
        "center": [round(lon0, 7), round(lat0, 7)], "bbox": bbox,
        "coordinate_system": "METER_OFFSETS",
        "format": {
            "positions": "flat [x0,y0,x1,y1,...] metres east/north of `center` (deck.gl COORDINATE_SYSTEM.METER_OFFSETS, "
                         "coordinateOrigin = center); load with new Float32Array(positions)",
            "start_indices": "vertex index where polygon i starts (length = count + 1); rings are closed (first == last)",
            "heights": "metres per polygon (count)", "height_source": "per polygon: " + json.dumps(C.HEIGHT_SOURCE_LABELS, ensure_ascii=False),
            "holes": "inner rings (rare); holes.parent[i] = polygon index. Not needed for the extruded look.",
            "use": "per polygon small int (Uint8Array), see use_names",
            "roads": "walk-network edges (paths): same flat layout, start_indices per edge",
            "simplification": "none: every footprint vertex of every building whose representative point lies in the 구",
        },
        "buildings": {
            "count": len(mine), "osm_rows": len({f.row for f in mine}),
            "positions": _round_list(b_flat), "start_indices": b_start.tolist(),
            "heights": [round(float(h), 2) for h in heights], "height_source": src.tolist(),
            "height_source_names": C.HEIGHT_SOURCE_NAMES,
            "use": uses.tolist(), "use_names": {str(k): v for k, v in C.USE_NAMES.items()}, "use_counts": use_counts,
            "use_note": "용도 = 원본 building/용도명 컬럼의 부분 문자열 매칭 (국토부 A9 건축물용도명 또는 OSM building 태그); 5 = 미상",
            "height_source_counts": {C.HEIGHT_SOURCE_NAMES[k]: v for k, v in n_src.items()},
            "measured_pct": round(100.0 * (n_src[0] + n_src[1]) / len(mine), 1),
            "imputation": {"method": "footprint-area deciles of buildings with recorded floors in this 구 -> median floors per band",
                           "n_known": bands.n_known, "bands": bands.table(), "global_median_floors": bands.global_median},
            "level_height_m": C.LEVEL_HEIGHT_M,
            "holes": {"count": len(hole_rings), "positions": _round_list(h_flat), "start_indices": h_start.tolist(),
                      "parent": hole_parent.tolist()},
        },
        "roads": {"count": len(graph.edge_u), "positions": _round_list(r_flat), "start_indices": r_start.tolist(),
                  "lengths_m": [round(float(v), 1) for v in graph.edge_len], "pad_m": C.GRAPH_PAD_M,
                  "nodes": len(graph.node_id)},
        "boundary": {"rings": len(bnd), "positions": _round_list(bnd_flat), "start_indices": bnd_start.tolist(),
                     "method": bnd_method, "dongs": [{"code": d.code, "name": d.name} for d in dongs]},
        "sources": [
            {"file": used[0].name, "sha256": shas[used[0].name], "what": used[1],
             "license": C.OSM_LICENSE if used[0] == settings.data_dir / C.BUILDINGS_PARQUET else "출처 JSON 참조 (extra/buildings)",
             "url": C.OSM_URL if used[0] == settings.data_dir / C.BUILDINGS_PARQUET else None,
             "display_source": "오픈스트리트맵 건물" if used[0] == settings.data_dir / C.BUILDINGS_PARQUET else "건물 외곽선 (공식 자료 통합본)",
             "sources_json": _sources_json(used[0]), "candidates": [str(p.relative_to(settings.data_dir)) if p.is_relative_to(settings.data_dir) else str(p)
                                                                      for p in sources[:-1]]},
            {"file": graphml.name, "sha256": shas[graphml.name], "what": "OSM walk network (osmnx graphml)",
             "license": C.OSM_LICENSE, "url": C.OSM_URL, "display_source": "오픈스트리트맵 보행망"},
            {"file": "HangJeongDong_ver20261001.geojson", "what": "행정동 경계 (구 소속 판정·외곽선)", "display_source": "행정동 경계 (2026-10)"},
        ],
        "fetched_at": now, "built_at": now, "cache_key": key, "scene_version": C.SCENE_VERSION,
    }
    body = gzip.compress(json.dumps(scene, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), compresslevel=6)
    json_path = d / f"scene-{sgg_code}-{key}.json.gz"
    npz_path = d / f"graph-{sgg_code}-{key}.npz"
    pid = os.getpid()  # another process (the reloading dev server) may build the same scene at once
    tmp = json_path.with_name(f"{json_path.name}.{pid}.tmp")
    tmp.write_bytes(body)
    tmp.replace(json_path)
    tmp_npz = npz_path.with_name(f"{npz_path.stem}.{pid}.tmp.npz")
    np.savez_compressed(
        tmp_npz, node_lon=graph.node_lon, node_lat=graph.node_lat, node_x=nx_, node_y=ny_,
        indptr=indptr, nbr=nbr, w=w, eid=eid, rev=rev, road_flat=r_flat, road_start=r_start,
        center=np.array([lon0, lat0]), bbox=np.array(bbox, dtype=np.float64),
        built_at=np.array(now), sgg_code=np.array(sgg_code),
    )
    tmp_npz.replace(npz_path)
    for old in list(d.glob(f"scene-{sgg_code}-*.json.gz")) + list(d.glob(f"graph-{sgg_code}-*.npz")):
        if old not in (json_path, npz_path):
            old.unlink(missing_ok=True)
    secs = time.perf_counter() - t0
    logger.info("scene %s: %d buildings, %d edges, %d bytes gz, %.1fs", sgg_code, len(mine), len(graph.edge_u), len(body), secs)
    return {"json_gz": str(json_path), "npz": str(npz_path), "key": key, "n_buildings": len(mine), "building_file": used[0].name,
            "height_source_counts": scene["buildings"]["height_source_counts"], "measured_pct": scene["buildings"]["measured_pct"],
            "use_counts": use_counts,
            "n_edges": int(len(graph.edge_u)), "bytes_gz": len(body), "seconds": round(secs, 1)}


__all__ = ["AreaBands", "Footprint", "TwinBuildError", "WalkGraph", "build_scene", "building_candidates", "cache_dir", "csr", "parse_levels",
           "footprint_area_m2", "parse_wkb_polygons", "use_category", "read_buildings", "read_buildings_shp", "read_walk_graph", "scene_sources", "source_key"]
