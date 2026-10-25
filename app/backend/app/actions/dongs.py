"""행정동 table of a region: centroid, 65+ population and boundary rings.

Reuses the heat ingest (`ingest_regions.load_dongs` + the experiment's unit rules) so a region's
행정동 are exactly the ones its `region.pop65` was summed from. Centroids are vertex means, the
same as experiments/heat. Loading parses a 35 MB GeoJSON, so the result is cached per data dir.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..heat import ingest_regions
from ..heat.common import EXPERIMENT_DIR, GEOJSON, POP_CSV, IngestError, nospace
from .diskcache import cached
from .geometry import FloatArray, Ring

CACHE_VERSION = 1

logger = logging.getLogger("actions.dongs")
_LOCK = threading.Lock()
_CACHE: dict[str, dict[str, list[Dong]]] = {}


class DongDataError(RuntimeError):
    """The 행정동 source files are missing or inconsistent."""


@dataclass(frozen=True, slots=True)
class Dong:
    """One 2026 행정동."""

    code: str
    name: str
    lat: float
    lon: float
    pop65: int
    rings: tuple[Ring, ...]


def _rings_by_code(geojson_path: Path) -> dict[str, tuple[Ring, ...]]:
    with geojson_path.open(encoding="utf-8") as fh:
        features = json.load(fh)["features"]
    out: dict[str, tuple[Ring, ...]] = {}
    for f in features:
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        out[str(f["properties"]["adm_cd2"])] = tuple(np.asarray(p[0], dtype=float) for p in polys)
    return out


def _build(data_dir: Path, experiment_dir: Path) -> dict[str, list[Dong]]:
    pop_path, geo_path = data_dir / POP_CSV, data_dir / GEOJSON
    for p in (pop_path, geo_path):
        if not p.is_file():
            raise DongDataError(f"missing source file: {p}")
    try:
        table = ingest_regions.load_dongs(pop_path, geo_path)
        table = ingest_regions.assign_units(table, ingest_regions._load_unit_assigner(experiment_dir))
        rings = _rings_by_code(geo_path)
    except (IngestError, KeyError, ValueError, OSError) as exc:
        raise DongDataError(f"cannot read 행정동 sources: {exc}") from exc
    by_unit: dict[str, list[Dong]] = {}
    for r in table.itertuples():
        key = f"{r.unit_sido}|{nospace(str(r.unit_sgg))}"
        by_unit.setdefault(key, []).append(Dong(
            code=str(r.adm_cd2), name=str(r.dong), lat=float(r.lat), lon=float(r.lon),
            pop65=int(r.p65), rings=rings[str(r.adm_cd2)],
        ))
    logger.info("loaded %d units of 행정동", len(by_unit))
    return by_unit


def dongs_of_region(sido: str, name: str, data_dir: Path, experiment_dir: Path | None = None) -> list[Dong]:
    """행정동 of the region (시도, 시군구 name). Empty list if the unit has none.

    Raises:
        DongDataError: if the source files cannot be read.
    """
    exp = EXPERIMENT_DIR if experiment_dir is None else experiment_dir
    key = f"{data_dir.resolve()}|{exp.resolve()}"
    with _LOCK:
        if key not in _CACHE:
            sources = [data_dir / POP_CSV, data_dir / GEOJSON]
            if not all(p.is_file() for p in sources):
                raise DongDataError(f"missing source file: {[str(p) for p in sources if not p.is_file()]}")
            _CACHE[key] = cached("dongs", CACHE_VERSION, sources, lambda: _build(data_dir, exp))
        table = _CACHE[key]
    return list(table.get(f"{sido}|{nospace(name)}", []))


def clear_cache() -> None:
    """Drop cached tables (tests)."""
    with _LOCK:
        _CACHE.clear()


def centroid_arrays(dongs: list[Dong]) -> tuple[FloatArray, FloatArray]:
    """(lat, lon) arrays of the dong centroids."""
    return (
        np.array([d.lat for d in dongs], dtype=float),
        np.array([d.lon for d in dongs], dtype=float),
    )
