"""
Builds public/field_crosswalks.json and public/field_aeds.json for the /field simulation page.
Inputs are real public data already cached under data/cache/extra (nothing is invented or interpolated):
  - 전국횡단보도표준데이터 (data.go.kr 15028201): green time, crosswalk length (ET), lanes (CARTRK_CO), coordinates
  - 보행노인 사고다발지 2024 (data.go.kr 15029185, 한국도로교통공단): hotspot centre + casualties
  - 전국 자동심장충격기(AED) 공공 API 캐시 (ems/api/aed): coordinates, installer, place
Selection (deterministic, no random draw):
  crosswalk rows with a traffic light (TFCLGHT_YN=Y), green time > 0 s, length > 0 m, lanes > 0, coordinates in Korea,
  within HOTSPOT_RADIUS_M of a 2024 보행노인 hotspot centre; up to PER_HOTSPOT nearest crosswalks per hotspot, hotspots ordered by
  casualties (ties: occurrences, then code), first TOP_N crosswalks kept. The cached crosswalk file is a 50,000-row subset
  of the national standard data, so only some hotspots have a crosswalk record.
AEDs: every AED with coordinates within AED_KEEP_M of any chosen crosswalk.
Usage: python3 -I scripts/gen_field_data.py [data_root] [public_dir]
"""
import csv
import gzip
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(sys.argv[1] if len(sys.argv) > 1 else "../../data/cache/extra")
PUBLIC = Path(sys.argv[2] if len(sys.argv) > 2 else "public")
CROSSWALK_CSV = DATA / "silverzone/kr_15028201_crosswalk_std.csv"
HOTSPOT_CSV = DATA / "silverzone/kr_15029185_acc_hotspot_std.csv"
AED_DIR = DATA / "ems/api/aed"
HOTSPOT_YEAR = "2024"
HOTSPOT_TYPE = "보행노인"
HOTSPOT_RADIUS_M = 300.0
TOP_N = 40
PER_HOTSPOT = 3
AED_KEEP_M = 3000.0
LAT_RANGE = (33.0, 38.7)
LON_RANGE = (124.5, 131.9)
EARTH_R = 6_371_000.0
LAT_M = 111_320.0


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def num(s: str) -> float | None:
    try:
        v = float(s.strip())
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def in_korea(lat: float | None, lon: float | None) -> bool:
    return lat is not None and lon is not None and LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]


def main() -> None:
    hotspots = []
    with HOTSPOT_CSV.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r["ACDNT_YEAR"] != HOTSPOT_YEAR or r["ACDNT_TYPE_SE"] != HOTSPOT_TYPE:
                continue
            lat, lon = num(r["LATITUDE"]), num(r["LONGITUDE"])
            if in_korea(lat, lon):
                hotspots.append({
                    "id": r["LC_CODE"], "name": r["ACDNT_AREA_LC_NM"], "lat": lat, "lon": lon,
                    "occurrences": int(num(r["OCCRRNC_CO"]) or 0), "casualties": int(num(r["CASLT_CO"]) or 0),
                    "deaths": int(num(r["DEATH_CO"]) or 0),
                })
    best: dict[str, list[dict]] = {}
    n_rows = n_valid = 0
    with CROSSWALK_CSV.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            n_rows += 1
            lat, lon = num(r["LATITUDE"]), num(r["LONGITUDE"])
            green, length, lanes = num(r["GREEN_SGNGNR_TIME"]), num(r["ET"]), num(r["CARTRK_CO"])
            if r["TFCLGHT_YN"].strip() != "Y" or not in_korea(lat, lon):
                continue
            if green is None or green <= 0 or length is None or length <= 0 or lanes is None or lanes <= 0:
                continue
            n_valid += 1
            for h in hotspots:
                if abs(h["lat"] - lat) * LAT_M > HOTSPOT_RADIUS_M * 1.5:
                    continue
                d = haversine(lat, lon, h["lat"], h["lon"])
                if d <= HOTSPOT_RADIUS_M:
                    best.setdefault(h["id"], []).append({"d": d, "h": h, "r": r, "lat": lat, "lon": lon, "green": green, "length": length, "lanes": lanes})
    ranked: list[dict] = []
    for hid in sorted(best, key=lambda k: (-best[k][0]["h"]["casualties"], -best[k][0]["h"]["occurrences"], k)):
        ranked.extend(sorted(best[hid], key=lambda b: b["d"])[:PER_HOTSPOT])
    ranked = ranked[:TOP_N]
    crosswalks = [{
        "id": f"cw{i + 1:02d}",
        "sido": b["r"]["CTPRVN_NM"], "sigungu": b["r"]["SIGNGU_NM"],
        "address": b["r"]["LNMADR"] or b["r"]["RDNMADR"],
        "lat": b["lat"], "lon": b["lon"],
        "greenSec": b["green"], "redSec": num(b["r"]["RED_SGNGNR_TIME"]),
        "lengthM": b["length"], "widthM": num(b["r"]["BT"]), "lanes": int(b["lanes"]),
        "hasIsland": b["r"]["TFCILND_YN"].strip() == "Y",
        "hotspot": {**b["h"], "distanceM": round(b["d"])},
    } for i, b in enumerate(ranked)]

    aeds = []
    seen = set()
    files = sorted(AED_DIR.glob("*.json.gz"))
    for p in files:
        body = json.load(gzip.open(p, "rt", encoding="utf-8"))["response"]["body"]
        for it in (body.get("items") or {}).get("item", []):
            lat, lon = it.get("wgs84Lat"), it.get("wgs84Lon")
            if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)) or not in_korea(lat, lon):
                continue
            if not any(haversine(lat, lon, c["lat"], c["lon"]) <= AED_KEEP_M for c in crosswalks):
                continue
            key = (round(lat, 6), round(lon, 6), it.get("org"), it.get("buildPlace"))
            if key in seen:
                continue
            seen.add(key)
            aeds.append({"org": it.get("org") or "", "place": it.get("buildPlace") or "", "sido": it.get("sido") or "",
                         "gugun": it.get("gugun") or "", "address": it.get("buildAddress") or "", "lat": lat, "lon": lon})

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cw_meta = {
        "generated_at": now, "script": "scripts/gen_field_data.py",
        "sources": [
            {"name": "전국횡단보도표준데이터", "url": "https://www.data.go.kr/data/15028201/standard.do", "file": CROSSWALK_CSV.name, "sha256": sha256(CROSSWALK_CSV), "rows": n_rows},
            {"name": "보행노인 사고다발지 표준데이터(한국도로교통공단)", "url": "https://www.data.go.kr/data/15029185/standard.do", "file": HOTSPOT_CSV.name, "sha256": sha256(HOTSPOT_CSV)},
        ],
        "selection": f"신호등 있음, 녹색시간>0, 연장>0, 차로수>0, 좌표 유효인 횡단보도({n_valid}건) 중 {HOTSPOT_YEAR} {HOTSPOT_TYPE} 사고다발지 중심 {int(HOTSPOT_RADIUS_M)} m 이내. 다발지마다 가까운 순 최대 {PER_HOTSPOT}곳, 사상자 수 많은 다발지부터 {TOP_N}곳. 입력 CSV는 전국 표준데이터 중 {n_rows}행만 담겨 있어 일부 다발지에만 횡단보도 기록이 있습니다.",
        "hotspots_considered": len(hotspots), "hotspots_with_crosswalk": len(best), "crosswalks_in_radius": sum(len(v) for v in best.values()),
    }
    aed_meta = {
        "generated_at": now, "script": "scripts/gen_field_data.py",
        "sources": [{"name": "전국 자동심장충격기(AED) 공공 API 캐시", "url": "https://www.data.go.kr/data/15000652/openapi.do",
                     "file": f"ems/api/aed/*.json.gz ({len(files)}개)", "sha256": hashlib.sha256(b"".join(sha256(p).encode() for p in files)).hexdigest(),
                     "sha256_note": "파일별 sha256을 이어 붙인 문자열의 sha256"}],
        "selection": f"선택한 횡단보도 {len(crosswalks)}곳 중 하나라도 {int(AED_KEEP_M)} m 이내인 AED.",
    }
    PUBLIC.mkdir(parents=True, exist_ok=True)
    (PUBLIC / "field_crosswalks.json").write_text(json.dumps({"meta": cw_meta, "crosswalks": crosswalks}, ensure_ascii=False, indent=1), encoding="utf-8")
    (PUBLIC / "field_aeds.json").write_text(json.dumps({"meta": aed_meta, "aeds": aeds}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"crosswalks={len(crosswalks)} (valid rows {n_valid}, hotspots {len(hotspots)}, matched {len(best)}, in radius {sum(len(v) for v in best.values())}) aeds={len(aeds)}")


if __name__ == "__main__":
    main()
