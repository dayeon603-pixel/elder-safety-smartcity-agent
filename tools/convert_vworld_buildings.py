"""Convert V-World 「GIS건물통합정보」 (AL_D010, 국토교통부) shapefile → one-구 building parquet for the /twin scene.

Field meaning (AL_D010 layout): A1 GIS건물통합식별번호 · A9 건축물용도명 · A13 사용승인일자 · A16 높이(m) ·
A23 원천시군구코드 · A24 건물명 · A26 지상층수 · A27 지하층수 · A22 데이터기준일자.
0 in A16/A26 means "not recorded" and is written as null (the scene builder then estimates from footprint area).

Output (read by app/twin/scene_build.read_buildings): WKB `geometry` (EPSG:4326), `num_floors`, `height_m`, `building`,
plus provenance columns, and a sources JSON next to it.

Usage:
    app/backend/.venv/bin/python -I tools/convert_vworld_buildings.py <AL_D010_xx.shp> <out.parquet> [--sgg 11620]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pyogrio
import shapely

logger = logging.getLogger("convert_vworld")

LAYER_CRS = "EPSG:5186"
OUT_CRS = "EPSG:4326"
ENCODING = "cp949"
FIELDS = ["A1", "A9", "A13", "A16", "A22", "A23", "A24", "A26", "A27"]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _positive(v: object) -> float | None:
    try:
        x = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return x if x > 0 else None


def convert(shp: Path, out: Path, sgg: str) -> dict[str, object]:
    """Filter one 시군구, reproject to WGS84, write parquet + sources JSON; returns the stats dict.

    Raises:
        ValueError: the 시군구 has no rows or the layer CRS is not the expected EPSG:5186.
    """
    info = pyogrio.read_info(shp, encoding=ENCODING)
    if info["crs"] != LAYER_CRS:
        raise ValueError(f"unexpected CRS {info['crs']} (expected {LAYER_CRS})")
    df = pyogrio.read_dataframe(shp, columns=FIELDS, where=f"A23 = '{sgg}'", encoding=ENCODING)
    if df.empty:
        raise ValueError(f"no rows with A23 = {sgg} in {shp.name}")
    geoms = shapely.transform(df.geometry.values, _to_wgs84(), interleaved=False)
    floors = [_positive(v) for v in df["A26"]]
    heights = [_positive(v) for v in df["A16"]]
    table = pa.table({
        "geometry": pa.array(shapely.to_wkb(geoms), type=pa.binary()),
        "num_floors": pa.array([None if f is None else str(int(f)) for f in floors], type=pa.string()),
        "height_m": pa.array(heights, type=pa.float64()),
        "building": pa.array([str(v or "yes") for v in df["A9"]], type=pa.string()),
        "underground_floors": pa.array([_positive(v) for v in df["A27"]], type=pa.float64()),
        "approval_date": pa.array([None if v is None else str(v) for v in df["A13"]], type=pa.string()),
        "name": pa.array([None if v is None else str(v) for v in df["A24"]], type=pa.string()),
        "source_record_id": pa.array([str(v) for v in df["A1"]], type=pa.string()),
    })
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out, compression="zstd")
    n = len(df)
    n_floors = sum(f is not None for f in floors)
    n_height_only = sum(f is None and h is not None for f, h in zip(floors, heights))
    stats: dict[str, object] = {
        "dataset": "국토교통부 GIS건물통합정보 (AL_D010), V-World 오픈마켓 dsId=18",
        "license": "공공누리 제1유형 (출처표시)",
        "url": "https://www.vworld.kr/dtmk/dtmk_ntads_s002.do?svcCde=NA&dsId=18",
        "source_file": shp.name,
        "source_zip_sha256": None,
        "data_reference_date": str(df["A22"].iloc[0]),
        "sgg_code": sgg,
        "buildings": n,
        "with_floors": n_floors,
        "with_floors_pct": round(100.0 * n_floors / n, 2),
        "height_only": n_height_only,
        "height_rule": "지상층수(A26) 있으면 층수 × 3.2 m (scene builder), 없으면 높이(A16), 둘 다 0이면 면적 기반 추정",
        "converted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "output_sha256": _sha256(out),
        "converter": "tools/convert_vworld_buildings.py",
    }
    return stats


def _to_wgs84():  # type: ignore[no-untyped-def]
    from pyproj import Transformer

    t = Transformer.from_crs(LAYER_CRS, OUT_CRS, always_xy=True)
    return lambda x, y: t.transform(x, y)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("shp", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--sgg", default="11620")
    ap.add_argument("--zip", type=Path, default=None, help="original zip, for its sha256 in the sources JSON")
    a = ap.parse_args()
    stats = convert(a.shp, a.out, a.sgg)
    if a.zip is not None:
        stats["source_zip_sha256"] = _sha256(a.zip)
    side = a.out.with_suffix(".sources.json")
    side.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
