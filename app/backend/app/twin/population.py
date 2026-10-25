"""서울 생활인구 (real de-facto population) per 행정동 and hour for the story mode (STORY_CONTRACT.md).

Source files land at `<data>/extra/living_pop/<prefix>_<date>.parquet` (columns date, hour, adm_cd, adm_nm, total, age65p) with a
sources JSON next to them. Parquet needs pyarrow (geo group, dev host); the first read converts the day to a JSON cache in
the twin cache dir so the runtime image (no pyarrow) can serve it. Until a file exists the endpoint answers 409
`no_population_data` — counts are never invented. Each 동 also carries the walk-graph node indices inside its polygon so
the frontend can sample moving points on the real network.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, cast

import numpy as np

from ..actions import dongs as dongs_mod
from ..actions.geometry import points_in_rings
from ..config import Settings
from ..heat.common import nospace
from . import scene_build, store

logger = logging.getLogger("twin.population")
LIVING_POP_SUBDIR = "extra/living_pop"
CACHE_PREFIX = "livingpop"
REQUIRED = ("date", "hour", "adm_cd", "adm_nm", "total", "age65p")
SCOPE = "서울 생활인구 (내국인, 행정동 × 시간) 실측; 65세 이상은 생활인구 추정(KT 기반, 주민등록 비율과 비교하지 말 것); 개별 점의 이동 경로는 시각화용"
AGE65_LABEL = "생활인구 추정(KT 기반)"
_LOCK = threading.Lock()
_NODES: dict[str, list[dict[str, Any]]] = {}  # sgg -> per-동 node index lists


class NoPopulationData(RuntimeError):
    """No living-population file (or cache) for the date."""


def _files(settings: Settings, date_: str) -> list[Path]:
    return sorted((settings.data_dir / LIVING_POP_SUBDIR).glob(f"*_{date_}.parquet"))


def _sources_json(directory: Path) -> dict[str, Any] | None:
    for j in sorted(directory.glob("*.json")):
        try:
            doc = json.loads(j.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            return {"file": j.name, **{k: v for k, v in doc.items() if isinstance(v, (str, int, float, list))}}
    return None


def _cache_path(settings: Settings, date_: str, src: Path) -> Path:
    st = src.stat()
    return scene_build.cache_dir(settings) / f"{CACHE_PREFIX}-{date_}-{st.st_size}-{int(st.st_mtime)}.json"


def load_day(settings: Settings, date_: str) -> dict[str, Any]:
    """{rows: [{hour, adm_cd, adm_nm, total, age65p}], file, sha256, sources_json} for one date (JSON-cached).

    Raises:
        NoPopulationData: no parquet for the date and no cache; or pyarrow missing and no cache.
    """
    files = _files(settings, date_)
    if not files:
        cached = sorted(scene_build.cache_dir(settings).glob(f"{CACHE_PREFIX}-{date_}-*.json"))
        if cached:
            return cast(dict[str, Any], json.loads(cached[-1].read_text(encoding="utf-8")))
        raise NoPopulationData(f"no {LIVING_POP_SUBDIR}/*_{date_}.parquet under {settings.data_dir}")
    src = files[-1]
    cpath = _cache_path(settings, date_, src)
    if cpath.is_file():
        return cast(dict[str, Any], json.loads(cpath.read_text(encoding="utf-8")))
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise NoPopulationData(f"{src.name} exists but pyarrow is not installed here and no cache was built") from exc
    table = pq.read_table(src)
    missing = [c for c in REQUIRED if c not in table.column_names]
    if missing:
        raise NoPopulationData(f"{src.name}: missing columns {missing}")
    rows = []
    for r in table.select(list(REQUIRED)).to_pylist():
        if str(r["date"])[:10] != date_:
            continue
        rows.append({"hour": int(r["hour"]), "adm_cd": str(r["adm_cd"]), "adm_nm": str(r["adm_nm"]),
                     "total": float(r["total"]), "age65p": float(r["age65p"])})
    doc = {"rows": rows, "file": src.name, "sha256": scene_build._sha256(src), "sources_json": _sources_json(src.parent)}
    try:
        cpath.parent.mkdir(parents=True, exist_ok=True)
        tmp = cpath.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        tmp.replace(cpath)
    except OSError as exc:
        logger.warning("living pop cache not written: %s", exc)
    return doc


def dong_nodes(conn: sqlite3.Connection, settings: Settings, region: dict[str, Any]) -> list[dict[str, Any]]:
    """Per 행정동: code, name, centroid and the walk-graph node indices inside its rings (cached per 구)."""
    code = region["code"]
    with _LOCK:
        hit = _NODES.get(code)
    if hit is not None:
        return hit
    ds = dongs_mod.dongs_of_region(region["sido"], region["name"], settings.data_dir)
    g = store.graph(conn, settings, code)
    out: list[dict[str, Any]] = []
    for d in ds:
        inside = points_in_rings(d.rings, g.node_lon, g.node_lat)
        out.append({"adm_cd2": d.code, "name": d.name, "centroid": [round(d.lon, 6), round(d.lat, 6)],
                    "walk_nodes": np.flatnonzero(inside).tolist()})
    with _LOCK:
        _NODES[code] = out
    return out


def _match(dong: dict[str, Any], rows_by_cd: dict[str, dict[str, Any]], rows_by_name: dict[str, dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None]:
    cd = dong["adm_cd2"]
    for key, how in ((cd, "adm_cd2"), (cd[:8], "adm_cd2[:8]"), (cd[:7], "adm_cd2[:7]")):
        if key in rows_by_cd:
            return rows_by_cd[key], how
    nm = nospace(dong["name"])
    if nm in rows_by_name:
        return rows_by_name[nm], "adm_nm"
    return None, None


def population(conn: sqlite3.Connection, settings: Settings, region: dict[str, Any], date_: str, hour: int) -> dict[str, Any]:
    """The contract response for one 구 / date / hour.

    Raises:
        NoPopulationData: see `load_day`.
    """
    day = load_day(settings, date_)
    at_hour = [r for r in day["rows"] if r["hour"] == hour]
    if not at_hour:
        raise NoPopulationData(f"{day['file']} has no rows for hour {hour}")
    by_cd = {r["adm_cd"]: r for r in at_hour}
    by_name = {nospace(r["adm_nm"]): r for r in at_hour}
    items: list[dict[str, Any]] = []
    unmatched: list[str] = []
    for d in dong_nodes(conn, settings, region):
        row, how = _match(d, by_cd, by_name)
        if row is None:
            unmatched.append(d["name"])
            continue
        items.append({"adm_cd": row["adm_cd"], "adm_cd2": d["adm_cd2"], "adm_nm": row["adm_nm"], "dong": d["name"], "total": row["total"],
                      "age65p": row["age65p"], "centroid": d["centroid"], "polygon_id": d["adm_cd2"], "walk_nodes": d["walk_nodes"],
                      "matched_by": how})
    if not items:
        raise NoPopulationData(f"{day['file']}: no row matches the 행정동 of {region['name']} (codes/names differ)")
    return {
        "sgg_code": region["code"], "region": f"{region['sido']} {region['name']}", "date": date_, "hour": hour, "items": items,
        "total": round(sum(i["total"] for i in items)), "age65p": round(sum(i["age65p"] for i in items)),
        "unmatched_dongs": unmatched, "scope": SCOPE, "age65p_label": AGE65_LABEL,
        "display_source": "서울시 생활인구 실측 · 65+ 는 생활인구 추정(KT 기반)",
        "walk_nodes_note": "walk_nodes = /twin/{sgg}/scene roads 와 같은 보행망의 노드 인덱스 (node 좌표는 graph npz; 프론트는 roads 끝점으로 복원)",
        "sources": [{"file": day["file"], "sha256": day["sha256"], "what": "서울 생활인구 (행정동 × 시간 × 연령)", "display_source": "서울시 생활인구 실측",
                     "sources_json": day["sources_json"]}],
        "fetched_at": (day["sources_json"] or {}).get("fetched_at"),
    }


def clear_cache() -> None:
    with _LOCK:
        _NODES.clear()


__all__ = ["NoPopulationData", "clear_cache", "dong_nodes", "load_day", "population"]
