"""기상청 ASOS 95 stations (API허브 kma_sfctm2 hourly cache) -> daily max 체감온도 / 기온, nearest station per region.

Same construction as the evaluation panel (experiments/heat/build_panel.py): for every cached
station-day, the daily maximum over KST hours HOURS of the KMA 여름철 체감온도 computed from TA and HM,
and of TA itself, over the hours where BOTH are valid; fewer than MIN_HOURS valid hours -> NULL
(a partial afternoon would understate the peak). Stations: 기상청 ASOS 지점 정보 (stn_src/shj_stn.csv).
`region_station` is recomputed to the nearest ASOS station (great-circle), replacing the Meteostat
choice, so /heat/region, /actions and the agent read the same temperature family the model was
evaluated on. Meteostat rows stay in the tables (different station ids) but are no longer referenced.
"""

from __future__ import annotations

import csv
import hashlib
import logging
import math
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .. import db
from .apparent import summer_apparent_temp
from .common import EARTH_RADIUS_KM, HeatPaths, IngestError, register_dataset, resolve_paths

logger = logging.getLogger("heat.asos")

HUB_DIR = "extra/kma_hub/sfctm2"
STN_CSV = "extra/kma_hub/stn_src/shj_stn.csv"
HUB_URL = "https://apihub.kma.go.kr/ (kma_sfctm2, 지상관측 시간자료)"
STN_URL = "https://data.kma.go.kr/ (ASOS 지점 정보; copy redistributed in github Shj-A/Data_campus_project)"
LICENSE_KMA = "기상청 공공누리 제1유형 (출처표시)"
SOURCE = "kma_asos"
HOURS: tuple[int, ...] = (10, 11, 12, 13, 14, 15, 16, 17, 18, 19)  # KST hours entering the daily max
MIN_HOURS = 7  # valid hours required for a station-day; fewer -> NULL (same as the evaluation panel)
MISSING_SENTINELS = (-9.0, -99.0)  # hub fill values; both outside any valid TA / HM
TA_RANGE = (-40.0, 50.0)
RH_RANGE = (1.0, 100.0)
AT_RANGE = (-40.0, 60.0)  # CHECK range of weather_daily.apparent_max
I_TM, I_STN, I_TA, I_HM = 0, 1, 11, 13  # whitespace token indices of a hub line
TM_LEN = 12
LAT_RANGE, LON_RANGE = (33.0, 39.0), (124.0, 132.0)
STN_COLS = {"id": "지점코드", "name": "(현)지점명", "lat": "위도", "lon": "경도"}


@dataclass(frozen=True, slots=True)
class Station:
    id: str
    name: str
    lat: float
    lon: float


def read_stations(path: Path) -> list[Station]:
    """ASOS station table; duplicate ids or coordinates outside Korea are an error.

    Raises:
        IngestError: missing columns, duplicate id, out-of-range coordinates.
    """
    out: list[Station] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                cells = {k: (row[col] or "").replace(" ", "").strip() for k, col in STN_COLS.items()}
                lat, lon = float(cells["lat"]), float(cells["lon"])
            except (KeyError, ValueError) as exc:
                raise IngestError(f"{path.name}: bad station row {row!r} ({exc})") from exc
            sid = str(int(cells["id"]))
            if sid in seen:
                raise IngestError(f"{path.name}: duplicate station {sid}")
            if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]):
                raise IngestError(f"{path.name}: station {sid} outside Korea ({lat}, {lon})")
            seen.add(sid)
            out.append(Station(sid, cells["name"], lat, lon))
    if not out:
        raise IngestError(f"{path.name}: no stations")
    return sorted(out, key=lambda s: int(s.id))


def _valid(v: float, lo: float, hi: float) -> float:
    return math.nan if v in MISSING_SENTINELS or not lo <= v <= hi else v


def parse_hub_file(path: Path) -> list[tuple[str, int, str, float, float]]:
    """(date, hour, station_id, ta, hm) per data line; sentinels / out-of-range values become NaN."""
    rows: list[tuple[str, int, str, float, float]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line[0] == "#":
            continue
        t = line.split()
        if len(t) <= I_HM or len(t[I_TM]) != TM_LEN:
            continue
        try:
            tm, sid, ta, hm = t[I_TM], str(int(t[I_STN])), float(t[I_TA]), float(t[I_HM])
        except ValueError:
            continue
        rows.append((f"{tm[:4]}-{tm[4:6]}-{tm[6:8]}", int(tm[8:10]), sid, _valid(ta, *TA_RANGE), _valid(hm, *RH_RANGE)))
    return rows


def daily_max(rows: list[tuple[str, int, str, float, float]],
              min_hours: int = MIN_HOURS) -> dict[tuple[str, str], tuple[float | None, float | None, int]]:
    """{(station, date): (apparent_max, tmax, n_valid_hours)}; both maxima over the same valid hours."""
    by_key: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    for date, hour, sid, ta, hm in rows:
        if hour in HOURS and not (math.isnan(ta) or math.isnan(hm)):
            by_key[(sid, date)].append((ta, hm))
    out: dict[tuple[str, str], tuple[float | None, float | None, int]] = {}
    for key, vals in by_key.items():
        n = len(vals)
        if n < min_hours:
            out[key] = (None, None, n)
            continue
        ta_arr = np.array([v[0] for v in vals], dtype=np.float64)
        hm_arr = np.array([v[1] for v in vals], dtype=np.float64)
        at = float(np.max(summer_apparent_temp(ta_arr, hm_arr)))
        out[key] = (at if AT_RANGE[0] <= at <= AT_RANGE[1] else None, float(np.max(ta_arr)), n)
    return out


def nearest(region_ll: np.ndarray, station_ll: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Index and great-circle km of the nearest station per region (brute force; inputs (n,2) deg)."""
    r = np.radians(region_ll)[:, None, :]
    s = np.radians(station_ll)[None, :, :]
    a = np.sin((s[..., 0] - r[..., 0]) / 2) ** 2 + np.cos(r[..., 0]) * np.cos(s[..., 0]) * np.sin((s[..., 1] - r[..., 1]) / 2) ** 2
    km = 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))
    idx = km.argmin(axis=1)
    return idx, km[np.arange(len(idx)), idx]


def _manifest_sha(files: list[Path]) -> str:
    h = hashlib.sha256()
    for f in files:
        h.update(f.name.encode("utf-8"))
        h.update(hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()


def ingest_asos(conn: sqlite3.Connection, paths: HeatPaths) -> dict[str, int]:
    """Stations, daily maxima and region_station from the hub cache; idempotent. Requires regions.

    Raises:
        IngestError: missing station file / cache, no regions, or no station-day computed.
    """
    stn_path = paths.data_dir / STN_CSV
    hub = paths.data_dir / HUB_DIR
    if not stn_path.is_file():
        raise IngestError(f"missing {stn_path}")
    files = sorted(hub.glob("*.txt")) if hub.is_dir() else []
    if not files:
        raise IngestError(f"no hub cache files in {hub}")
    stations = read_stations(stn_path)
    known = {s.id for s in stations}
    rows: list[tuple[str, int, str, float, float]] = []
    for f in files:
        rows.extend(r for r in parse_hub_file(f) if r[2] in known)
    daily = daily_max(rows)
    if not daily:
        raise IngestError("no station-day could be computed from the hub cache")
    regions = conn.execute("SELECT code, lat, lon FROM region ORDER BY code").fetchall()
    if not regions:
        raise IngestError("region table is empty: run ingest_regions first")
    idx, km = nearest(np.array([[r["lat"], r["lon"]] for r in regions]),
                      np.array([[s.lat, s.lon] for s in stations]))
    manifest = _manifest_sha(files)
    dates = sorted({d for _, d in daily})
    with db.transaction(conn):
        stn_ds = register_dataset(
            conn, name="heat_kma_asos_stations", path=stn_path, source_url=STN_URL, license_=LICENSE_KMA,
            description=f"기상청 ASOS 지점 정보 ({len(stations)} stations, lat/lon)", row_count=len(stations),
            roles={"kind": "heat", "use": "weather_station (kma_asos)"},
        )
        register_dataset(
            conn, name="heat_kma_asos_hourly", path=None, sha256=manifest, source_url=HUB_URL, license_=LICENSE_KMA,
            description=f"API허브 kma_sfctm2 hourly TA/HM, {len(files)} cached hours {dates[0]}..{dates[-1]}; "
                        f"daily max 체감온도/기온 over KST {HOURS[0]}-{HOURS[-1]}h, >= {MIN_HOURS} valid hours; "
                        "sha256 = hash of '<file name><sha256(file)>' in name order",
            row_count=len(daily), roles={"kind": "heat", "hours": list(HOURS), "min_hours": MIN_HOURS},
        )
        conn.executemany(
            "INSERT INTO weather_station (id, lat, lon, dataset_id, name, source) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET lat = excluded.lat, lon = excluded.lon, dataset_id = excluded.dataset_id, "
            "name = excluded.name, source = excluded.source",
            [(s.id, s.lat, s.lon, stn_ds, s.name, SOURCE) for s in stations],
        )
        conn.executemany(
            "INSERT INTO weather_daily (station_id, date, tmax, tmin, tavg, apparent_max, n_hours) "
            "VALUES (?, ?, ?, NULL, NULL, ?, ?) ON CONFLICT(station_id, date) DO UPDATE SET "
            "tmax = excluded.tmax, apparent_max = excluded.apparent_max, n_hours = excluded.n_hours",
            [(sid, date, tx, at, n) for (sid, date), (at, tx, n) in sorted(daily.items())],
        )
        conn.executemany(
            "INSERT INTO region_station (region_code, station_id, dist_km) VALUES (?, ?, ?) "
            "ON CONFLICT(region_code) DO UPDATE SET station_id = excluded.station_id, dist_km = excluded.dist_km",
            [(r["code"], stations[int(i)].id, float(d)) for r, i, d in zip(regions, idx, km, strict=True)],
        )
    n_null = sum(1 for at, _, _ in daily.values() if at is None)
    logger.info("asos stations=%d files=%d station-days=%d (apparent NULL=%d) region_station=%d",
                len(stations), len(files), len(daily), n_null, len(regions))
    return {"stations": len(stations), "files": len(files), "station_days": len(daily),
            "apparent_null": n_null, "region_station": len(regions)}


def main() -> int:
    """CLI: python -m app.heat.ingest_asos."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    paths = resolve_paths()
    conn = db.connect(paths.db_path)
    try:
        db.migrate(conn)
        ingest_asos(conn, paths)
    except IngestError as exc:
        logger.error("%s", exc)
        return 2
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
