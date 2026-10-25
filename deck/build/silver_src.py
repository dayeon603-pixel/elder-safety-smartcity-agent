"""Seoul-only view of the silver-zone map file for the deck.

The experiment's map file (map_seoul_current_vs_ours_k3918_fold2024.csv) is cut by a bounding box, so it also holds
2024 hotspots in 경기도 next to Seoul (its `sido` column is empty). The deck and the /twin API must agree, and the API
counts hotspots by 행정구역, so here every point is tagged by point-in-polygon against the official 행정동 boundary.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import shapely

ROOT = Path(__file__).resolve().parents[2]
MAP_CSV = ROOT / "app/backend/experiments/silver/out/opt/promoted/map_seoul_current_vs_ours_k3918_fold2024.csv"
BOUNDARY = ROOT / "data/cache/extra/boundaries/HangJeongDong_ver20261001.geojson"
SEOUL = "서울특별시"
SRC = "experiments/silver/out/opt/promoted/map_seoul_current_vs_ours_k3918_fold2024.csv (서울 행정동 경계 안만)"


def _seoul_polygon() -> shapely.Geometry:
    doc = json.loads(BOUNDARY.read_text(encoding="utf-8"))
    parts = [shapely.geometry.shape(f["geometry"]) for f in doc["features"] if f["properties"].get("sidonm") == SEOUL]
    if not parts:
        raise ValueError(f"no {SEOUL} features in {BOUNDARY.name}")
    return shapely.union_all(parts)


def load_map() -> pd.DataFrame:
    """The map rows with an `in_seoul` column (True when the point lies inside Seoul's 행정동 boundary)."""
    d = pd.read_csv(MAP_CSV)
    poly = _seoul_polygon()
    shapely.prepare(poly)
    d["in_seoul"] = shapely.contains_xy(poly, d.lon.to_numpy(), d.lat.to_numpy())
    return d


def seoul_hotspots() -> pd.DataFrame:
    d = load_map()
    return d[(d.kind == "hotspot_2024") & d.in_seoul]
