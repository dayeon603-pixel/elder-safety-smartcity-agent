"""Extract the 21 관악구 행정동 polygons from the 행정동 boundary file into public/story/gwanak_dong.json.

Input : data/cache/extra/boundaries/HangJeongDong_ver20261001.geojson (행정동 경계)
Output: app/frontend/public/story/gwanak_dong.json  [{adm_cd (10-digit = adm_cd2), adm_nm, rings: [[lon,lat],...][]}]
Run   : python3 -I app/frontend/scripts/make_dong_json.py
"""
import json
from pathlib import Path

SGG = "11620"
ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "data/cache/extra/boundaries/HangJeongDong_ver20261001.geojson"
DST = ROOT / "app/frontend/public/story/gwanak_dong.json"
DECIMALS = 6


def rings_of(geom: dict) -> list[list[list[float]]]:
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    return [[[round(x, DECIMALS), round(y, DECIMALS)] for x, y in poly[0]] for poly in polys]


def main() -> None:
    feats = json.loads(SRC.read_text(encoding="utf-8"))["features"]
    out = [
        {"adm_cd": f["properties"]["adm_cd2"], "adm_nm": f["properties"]["adm_nm"].split()[-1], "rings": rings_of(f["geometry"])}
        for f in feats
        if f["properties"].get("sgg") == SGG
    ]
    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(json.dumps({"source": "행정동 경계 HangJeongDong_ver20261001 (관악구 21개 행정동)", "dongs": out}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(len(out), DST.stat().st_size)


main()
