"""Regions: 2026 행정동 population + boundary centroids aggregated to 시군구, plus label aliases.

A region is one analysis unit. With the experiment's unit rules available (merged 구, the 2026
인천 reorganisation) a unit can span several 2026 시군구 codes; its `code` is then the first five
digits of the 행정기관코드 holding most of its population, and a code is never used twice (the
split 제물포구 gives its 5-digit code to one unit only). Without the rules each 2026 시군구 is its
own region.

`region_alias` maps heat-label (시도, 시군구) strings to regions. It is loaded from
experiments/heat/out/crosswalk_heat_to_2026.csv when present; exact-name aliases are always added.
Population and 65+ counts are one 2026-09 snapshot used for every year (static covariate).
"""

from __future__ import annotations

import importlib.util
import json
import logging
import re
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd

from .. import db
from .common import (
    EXPERIMENT_DIR, GEOJSON, POP_CSV, HeatPaths, IngestError, detect_encoding, nospace,
    register_dataset, require_file, resolve_paths,
)

logger = logging.getLogger("heat.regions")

CODE_LEN = 5
CROSSWALK_NAME = "crosswalk_heat_to_2026.csv"
BUILD_PANEL_FILE = "build_panel.py"
POP_URL = "https://www.data.go.kr/data/15097972/fileData.do"
GEOJSON_URL = "https://github.com/vuski/admdongkor"
_AGE_COL = re.compile(r"^(\d+)세")
OLD_AGE = 65
UnitAssigner = Callable[[pd.DataFrame], pd.DataFrame]


def _load_unit_assigner(exp_dir: Path) -> UnitAssigner | None:
    """Import `assign_unit` from the experiment's build_panel.py (the single source of unit rules).

    Returns None when the file is absent or cannot be imported, in which case plain 2026 시군구
    regions are built. build_panel does `from common import ...`, so its directory is put on
    sys.path for the import only.
    """
    path = exp_dir / BUILD_PANEL_FILE
    if not path.is_file():
        logger.warning("unit rules not found at %s: regions = plain 2026 sigungu", path)
        return None
    saved_common = sys.modules.pop("common", None)
    sys.path.insert(0, str(exp_dir))
    try:
        spec = importlib.util.spec_from_file_location("heat_build_panel", path)
        if spec is None or spec.loader is None:
            return None
        module: ModuleType = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fn = getattr(module, "assign_unit", None)
        return fn if callable(fn) else None
    except ImportError:
        logger.exception("could not import %s", path)
        return None
    finally:
        sys.path.remove(str(exp_dir))
        sys.modules.pop("common", None)
        if saved_common is not None:
            sys.modules["common"] = saved_common


def load_dongs(pop_path: Path, geojson_path: Path) -> pd.DataFrame:
    """One row per 2026 행정동: code, names, vertex-mean centroid, population, 65+ and snapshot date.

    Raises:
        IngestError: if a dong has no population row (silently dropping people would bias pop65).
    """
    with geojson_path.open(encoding="utf-8") as fh:
        features = json.load(fh)["features"]
    rows: list[dict[str, object]] = []
    for f in features:
        p, g = f["properties"], f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        pts = np.vstack([np.asarray(poly[0], dtype=float) for poly in polys])
        sgg = str(p["sggnm"]).strip()
        rows.append({
            "adm_cd2": str(p["adm_cd2"]), "new_sido": p["sidonm"], "sgg_display": sgg,
            "new_sgg": nospace(sgg), "dong": str(p["adm_nm"]).split()[-1],
            "lon": float(pts[:, 0].mean()), "lat": float(pts[:, 1].mean()),
        })
    dongs = pd.DataFrame(rows)
    pop = pd.read_csv(pop_path, encoding=detect_encoding(pop_path), thousands=",")
    ages = {c: int(m.group(1)) for c in pop.columns if (m := _AGE_COL.match(str(c)))}
    pop["p65"] = pop[[c for c, a in ages.items() if a >= OLD_AGE]].sum(axis=1)
    pop["adm_cd2"] = pop["행정기관코드"].astype(str)
    pop = pop.rename(columns={"계": "pop", "기준연월": "pop_asof"})
    dongs = dongs.merge(pop[["adm_cd2", "pop", "p65", "pop_asof"]], on="adm_cd2", how="left")
    missing = int(dongs["pop"].isna().sum())
    if missing:
        raise IngestError(f"{missing} boundary dongs have no population row")
    dongs["pop"] = dongs["pop"].astype(int)
    dongs["p65"] = dongs["p65"].astype(int)
    return dongs


def assign_units(dongs: pd.DataFrame, assigner: UnitAssigner | None) -> pd.DataFrame:
    """Add unit_sido / unit_sgg / unit_key (via the experiment's rules, or plain 2026 시군구)."""
    if assigner is not None:
        out = assigner(dongs)
        for col in ("unit_sido", "unit_sgg", "unit_key"):
            if col not in out.columns:
                raise IngestError(f"unit rules did not produce {col}")
        return out
    out = dongs.copy()
    out["unit_sido"] = out["new_sido"]
    out["unit_sgg"] = out["new_sgg"]
    out["rule"] = "exact"
    out["unit_key"] = out["unit_sido"] + "|" + out["unit_sgg"]
    return out


def build_regions(dongs: pd.DataFrame) -> pd.DataFrame:
    """Aggregate dongs to units; assign each a unique 5-digit code. Columns match table `region`.

    Raises:
        IngestError: if a unit has zero population or no free code.
    """
    d = dongs.copy()
    d["c5"] = d["adm_cd2"].str[:CODE_LEN]
    weight = d["pop"].to_numpy(dtype=float)
    d["w"] = np.where(weight > 0, weight, 1.0)
    d["wlat"], d["wlon"] = d["lat"] * d["w"], d["lon"] * d["w"]
    units = d.groupby("unit_key").agg(
        sido=("unit_sido", "first"), sgg=("unit_sgg", "first"), pop=("pop", "sum"),
        pop65=("p65", "sum"), wlat=("wlat", "sum"), wlon=("wlon", "sum"), w=("w", "sum"),
        pop_asof=("pop_asof", "first"), n_parts=("sgg_display", "nunique"),
        display=("sgg_display", "first"),
    ).reset_index()
    if (units["pop"] <= 0).any():
        raise IngestError("a unit has zero population")
    units["lat"], units["lon"] = units["wlat"] / units["w"], units["wlon"] / units["w"]
    exact = (units["n_parts"] == 1) & (units["display"].map(nospace) == units["sgg"])
    units["name"] = np.where(exact, units["display"], units["sgg"])

    pairs = d.groupby(["unit_key", "c5"])["pop"].sum().reset_index()
    pairs = pairs.sort_values(["pop", "c5", "unit_key"], ascending=[False, True, True])
    taken: set[str] = set()
    code_of: dict[str, str] = {}
    for unit_key, c5 in zip(pairs["unit_key"], pairs["c5"], strict=True):
        if unit_key not in code_of and c5 not in taken:
            code_of[unit_key] = c5
            taken.add(c5)
    absent = set(units["unit_key"]) - set(code_of)
    if absent:
        raise IngestError(f"no free 5-digit code for units: {sorted(absent)}")
    units["code"] = units["unit_key"].map(code_of)
    cols = ["code", "sido", "name", "lat", "lon", "pop", "pop65", "pop_asof"]
    out = units[cols].sort_values("code").reset_index(drop=True)
    out["pop"] = out["pop"].astype(int)
    out["pop65"] = out["pop65"].astype(int)
    return out


def _alias_kind(rule: str, heat_sido: str, unit_sido: str) -> str:
    parts = set(rule.split("+")) if rule else set()
    if "incheon_jemulpo_by_dong" in parts:
        return "split"
    if parts & {"merge_gu_to_city", "incheon_reorg"}:
        return "merge"
    if heat_sido != unit_sido or "sejong_rename" in parts:
        return "sido_rename"
    return "exact"


def build_aliases(
    regions: pd.DataFrame, crosswalk_path: Path | None
) -> list[tuple[str, str, str, str]]:
    """(sido_raw, name_raw, region_code, kind) rows. Crosswalk rows win over exact ones.

    name_raw is whitespace-free so lookups normalise the same way.
    """
    by_key = {(r.sido, nospace(r.name)): r.code for r in regions.itertuples()}
    aliases: dict[tuple[str, str], tuple[str, str]] = {
        (sido, name): (code, "exact") for (sido, name), code in by_key.items()
    }
    if crosswalk_path is None or not crosswalk_path.is_file():
        logger.warning("crosswalk absent: exact-name aliases only (%d)", len(aliases))
    else:
        cw = pd.read_csv(crosswalk_path, encoding=detect_encoding(crosswalk_path), dtype=str)
        used = skipped = 0
        for row in cw.itertuples():
            unit_key = row.unit_key if isinstance(row.unit_key, str) else ""
            if row.status != "matched" or "|" not in unit_key:
                continue
            u_sido, u_sgg = unit_key.split("|", 1)
            code = by_key.get((u_sido, u_sgg))
            if code is None:
                skipped += 1
                logger.warning("crosswalk unit %s has no region", unit_key)
                continue
            rule = row.rule if isinstance(row.rule, str) else ""
            raw_name = nospace(row.heat_sgg) if isinstance(row.heat_sgg, str) else ""
            aliases[(row.heat_sido, raw_name)] = (code, _alias_kind(rule, row.heat_sido, u_sido))
            used += 1
        logger.info("crosswalk: %d aliases loaded, %d skipped", used, skipped)
    return [(s, n, c, k) for (s, n), (c, k) in sorted(aliases.items())]


def ingest_regions(conn: sqlite3.Connection, paths: HeatPaths) -> dict[str, int]:
    """Rebuild `region` and `region_alias` from source files; idempotent. Returns row counts."""
    pop_path = require_file(paths.data_dir, POP_CSV)
    geo_path = require_file(paths.data_dir, GEOJSON)
    dongs = load_dongs(pop_path, geo_path)
    regions = build_regions(assign_units(dongs, _load_unit_assigner(EXPERIMENT_DIR)))
    aliases = build_aliases(regions, paths.out_dir / CROSSWALK_NAME)
    with db.transaction(conn):
        register_dataset(
            conn, name="heat_pop_by_dong", path=pop_path, source_url=POP_URL,
            license_="data.go.kr public data (terms not machine-verified)",
            description="Resident population by 행정동, sex and age, 2026-09 snapshot",
            row_count=len(dongs), roles={"kind": "heat", "use": "region.pop, region.pop65"},
        )
        register_dataset(
            conn, name="heat_admin_boundaries", path=geo_path, source_url=GEOJSON_URL,
            license_="see upstream repo (vuski/admdongkor); not verified",
            description="HangJeongDong 2026-10-01 boundaries; only vertex-mean centroids are used",
            row_count=len(dongs), roles={"kind": "heat", "use": "region.lat, region.lon"},
        )
        conn.executemany(
            "INSERT INTO region (code, sido, name, lat, lon, pop, pop65, pop_asof) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(code) DO UPDATE SET sido = excluded.sido, name = excluded.name, "
            "lat = excluded.lat, lon = excluded.lon, pop = excluded.pop, pop65 = excluded.pop65, "
            "pop_asof = excluded.pop_asof",
            [
                (r.code, r.sido, r.name, float(r.lat), float(r.lon), int(r.pop), int(r.pop65),
                 str(r.pop_asof))
                for r in regions.itertuples()
            ],
        )
        conn.execute("DELETE FROM region_alias")
        conn.executemany(
            "INSERT INTO region_alias (sido_raw, name_raw, region_code, kind) VALUES (?, ?, ?, ?)",
            aliases,
        )
    logger.info("regions=%d aliases=%d", len(regions), len(aliases))
    return {"regions": len(regions), "aliases": len(aliases)}


def main() -> int:
    """CLI: python -m app.heat.ingest_regions."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    paths = resolve_paths()
    conn = db.connect(paths.db_path)
    try:
        db.migrate(conn)
        ingest_regions(conn, paths)
    except IngestError as exc:
        logger.error("%s", exc)
        return 2
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
