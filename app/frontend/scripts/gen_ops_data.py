"""
Builds public/ops/index.json and public/ops/<n>.json (one shard per 시도) for the /app 영점 도시 운영 지도.
Everything is real public data already cached under data/cache/extra, or the shelter table of the loaded backend DB
(read-only). Nothing is invented. Rows that cannot be matched to a 시군구 are dropped and counted in the index.
  - 전국횡단보도표준데이터 (data.go.kr 15028201): every row with a traffic light, green time > 0 s, length > 0 m, coordinates in Korea
  - 전국 자동심장충격기(AED) 공공 API 캐시 (data.go.kr 15000652): coordinates + opening hours (mon..sun, 공휴일)
  - 무더위쉼터 (backend DB table `shelter`, loaded from the 충남·부산 files etc.): coordinates + night/weekend flags
Region key = backend region.code (시군구). Matching: same 시도 and the same 시군구 name with spaces removed.
Usage: python3 -I scripts/gen_ops_data.py [data_root] [public_dir] [sqlite_db]
"""
import csv
import gzip
import hashlib
import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(sys.argv[1] if len(sys.argv) > 1 else "../../data/cache/extra")
PUBLIC = Path(sys.argv[2] if len(sys.argv) > 2 else "public") / "ops"
DB = Path(sys.argv[3] if len(sys.argv) > 3 else "../backend/var/kcf.sqlite3")
CROSSWALK_CSV = DATA / "silverzone/kr_15028201_crosswalk_std.csv"
AED_DIR = DATA / "ems/api/aed"
LAT_RANGE = (33.0, 38.7)
LON_RANGE = (124.5, 131.9)
COORD_DECIMALS = 5
DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun", "hol")
# Old/alternate 시도 names in source files -> the name used by the backend region table.
SIDO_ALIAS = {"전라남도": "전남광주통합특별시", "광주광역시": "전남광주통합특별시"}


def norm(s: str) -> str:
    return "".join(s.split())


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def num(s: str) -> float | None:
    try:
        v = float(s.strip())
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def in_korea(lat: object, lon: object) -> bool:
    return isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]


def hhmm(v: object) -> str:
    """'0900' / 900 / 1800 -> '0900'. Missing or malformed -> ''."""
    if v is None:
        return ""
    s = str(v).strip()
    if not s.isdigit() or len(s) > 4:
        return ""
    return s.zfill(4)


def hours_cell(it: dict) -> str:
    """'mon;tue;...;hol' with each day 'HHMM-HHMM' or '-' when the source has no usable pair."""
    out = []
    for d in DAY_KEYS:
        a, b = hhmm(it.get(f"{d}SttTme")), hhmm(it.get(f"{d}EndTme"))
        out.append(f"{a}-{b}" if a and b else "-")
    return ";".join(out)


def main() -> None:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    regions = con.execute("select code, sido, name from region").fetchall()
    by_key = {(sido, norm(name)): code for code, sido, name in regions}
    by_name: dict[str, list[str]] = {}
    for code, _sido, name in regions:
        by_name.setdefault(norm(name), []).append(code)
    sido_of = {code: sido for code, sido, _ in regions}

    def match(sido: str, name: str) -> str | None:
        key = (SIDO_ALIAS.get(sido, sido), norm(name))
        if key in by_key:
            return by_key[key]
        cands = by_name.get(norm(name), [])
        return cands[0] if len(cands) == 1 else None

    cells: dict[str, dict[str, list]] = {c: {"cw": [], "aed": [], "sh": []} for c, _, _ in regions}
    dropped = {"cw": 0, "aed": 0}

    n_cw_rows = 0
    with CROSSWALK_CSV.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            n_cw_rows += 1
            lat, lon = num(r["LATITUDE"]), num(r["LONGITUDE"])
            green, length = num(r["GREEN_SGNGNR_TIME"]), num(r["ET"])
            if r["TFCLGHT_YN"].strip() != "Y" or not in_korea(lat, lon) or green is None or green <= 0 or length is None or length <= 0:
                continue
            code = match(r["CTPRVN_NM"], r["SIGNGU_NM"])
            if code is None:
                dropped["cw"] += 1
                continue
            cells[code]["cw"].append([round(lat, COORD_DECIMALS), round(lon, COORD_DECIMALS), length, green, r["LNMADR"] or r["RDNMADR"]])

    seen: set[tuple] = set()
    files = sorted(AED_DIR.glob("*.json.gz"))
    n_aed = 0
    for p in files:
        body = json.load(gzip.open(p, "rt", encoding="utf-8"))["response"]["body"]
        for it in (body.get("items") or {}).get("item", []):
            lat, lon = it.get("wgs84Lat"), it.get("wgs84Lon")
            if not in_korea(lat, lon):
                continue
            key = (it.get("serialSeq"), round(lat, 5), round(lon, 5))
            if key in seen:
                continue
            seen.add(key)
            n_aed += 1
            code = match(it.get("sido") or "", it.get("gugun") or "")
            if code is None:
                dropped["aed"] += 1
                continue
            cells[code]["aed"].append([round(lat, COORD_DECIMALS), round(lon, COORD_DECIMALS), it.get("org") or "", it.get("buildPlace") or "", it.get("buildAddress") or "", hours_cell(it)])

    for code, name, lat, lon, cap, night, wk in con.execute("select region_code, name, lat, lon, capacity, night_open, weekend_open from shelter where region_code is not null"):
        if code in cells:
            cells[code]["sh"].append([round(lat, COORD_DECIMALS), round(lon, COORD_DECIMALS), name, night, wk, cap])

    PUBLIC.mkdir(parents=True, exist_ok=True)
    sidos = sorted({sido_of[c] for c in cells})
    index_sidos = []
    for i, sido in enumerate(sidos):
        shard = {c: v for c, v in cells.items() if sido_of[c] == sido and (v["cw"] or v["aed"] or v["sh"])}
        fname = f"{i:02d}.json"
        (PUBLIC / fname).write_text(json.dumps({"sido": sido, "regions": shard}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        index_sidos.append({"sido": sido, "file": fname, "regions": {c: [len(v["cw"]), len(v["aed"]), len(v["sh"])] for c, v in shard.items()}})
    index = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "script": "scripts/gen_ops_data.py",
            "region_count_order": ["crosswalks", "aeds", "shelters"],
            "day_order": list(DAY_KEYS),
            "sources": [
                {"name": "전국횡단보도표준데이터", "url": "https://www.data.go.kr/data/15028201/standard.do", "file": CROSSWALK_CSV.name, "sha256": sha256(CROSSWALK_CSV), "rows": n_cw_rows},
                {"name": "전국 자동심장충격기(AED) 공공 API 캐시", "url": "https://www.data.go.kr/data/15000652/openapi.do", "file": f"ems/api/aed/*.json.gz ({len(files)}개)",
                 "sha256": hashlib.sha256(b"".join(sha256(p).encode() for p in files)).hexdigest(), "rows": n_aed},
                {"name": "무더위쉼터 (백엔드 DB shelter 표)", "url": "https://www.data.go.kr/data/15118638/fileData.do", "file": "backend/var/kcf.sqlite3:shelter", "sha256": "n/a"},
            ],
            "dropped_unmatched": dropped,
            "note": "횡단보도 표준데이터는 캐시된 50,000행만 담겨 있어 일부 시군구에만 기록이 있습니다. AED 시간은 평일·주말·공휴일 열 기준입니다.",
        },
        "sidos": index_sidos,
    }
    (PUBLIC / "index.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tot = [sum(len(v[k]) for v in cells.values()) for k in ("cw", "aed", "sh")]
    print(f"shards={len(sidos)} crosswalks={tot[0]} aeds={tot[1]} shelters={tot[2]} dropped={dropped}")


if __name__ == "__main__":
    main()
