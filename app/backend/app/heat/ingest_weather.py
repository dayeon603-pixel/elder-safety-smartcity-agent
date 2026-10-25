"""Meteostat daily Tmax/Tmin/Tavg for Korean stations, and each region's nearest station.

Nearest station = BallTree (haversine) over stations whose gap-filled season coverage is at least
MIN_SEASON_COVERAGE, matching the experiment panel's station choice. Values outside the physical
CHECK range of the table are stored as NULL and counted (never clipped).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

from .. import db
from .common import (
    EARTH_RADIUS_KM, SEASON_END, SEASON_START, STATIONS_JSON, WX_DIR, YEARS, HeatPaths,
    IngestError, register_dataset, require_file, resolve_paths,
)
from ..routers.datasets import sha256_of
from .common import MAX_SOURCE_BYTES

logger = logging.getLogger("heat.weather")

STATIONS_URL = "https://meteostat.net/en/"
COUNTRY = "KR"
TMAX_MIN, TMAX_MAX = -40.0, 50.0  # CHECK range of weather_daily.tmax
MIN_SEASON_COVERAGE = 0.5
FFILL_LIMIT_DAYS = 2  # causal gap fill, same as the panel; used only to pick eligible stations
CALENDAR_PAD = (4, 15)  # calendar starts here to give the panel its lag window
COLS = {0: "date", 1: "tavg", 2: "tmin", 3: "tmax"}


def _read_station(path: Path) -> pd.DataFrame:
    x = pd.read_csv(path, header=None, usecols=list(COLS), names=None)
    x.columns = [COLS[c] for c in x.columns]
    x["date"] = pd.to_datetime(x["date"], errors="coerce")
    x = x.dropna(subset=["date"]).drop_duplicates("date")
    return x[(x["date"].dt.year >= YEARS[0]) & (x["date"].dt.year <= YEARS[-1])]


def _season_mask(cal: pd.DatetimeIndex) -> np.ndarray:
    md = list(zip(cal.month, cal.day, strict=True))
    inside = np.array([SEASON_START <= m <= SEASON_END for m in md])
    mask: np.ndarray = inside & np.asarray(cal.year.isin(YEARS))
    return mask


def eligible_stations(frames: dict[str, pd.DataFrame]) -> set[str]:
    """Stations with >= MIN_SEASON_COVERAGE of in-season days having Tmax after the causal gap fill."""
    cal = pd.date_range(f"{YEARS[0]}-{CALENDAR_PAD[0]:02d}-{CALENDAR_PAD[1]:02d}",
                        f"{YEARS[-1]}-10-01", freq="D")
    mask = _season_mask(cal)
    ok: set[str] = set()
    for sid, f in frames.items():
        s = f.set_index("date")["tmax"].reindex(cal).ffill(limit=FFILL_LIMIT_DAYS)
        if float(s[mask].notna().mean()) >= MIN_SEASON_COVERAGE:
            ok.add(sid)
    return ok


def nearest_station(
    region_ll: np.ndarray, station_ll: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Index and great-circle distance (km) of the nearest station per region; inputs are (n,2) lat/lon deg."""
    tree = BallTree(np.radians(station_ll), metric="haversine")
    dist, idx = tree.query(np.radians(region_ll), k=1)
    nearest: np.ndarray = idx[:, 0]
    km: np.ndarray = dist[:, 0] * EARTH_RADIUS_KM
    return nearest, km


def ingest_weather(conn: sqlite3.Connection, paths: HeatPaths) -> dict[str, int]:
    """Load stations, daily weather and region_station; idempotent. Requires regions first.

    Raises:
        IngestError: if there are no regions, no station files, or no eligible station.
    """
    stations_path = require_file(paths.data_dir, STATIONS_JSON)
    wx_dir = paths.data_dir / WX_DIR
    files = sorted(p for p in wx_dir.glob("*.csv.gz"))
    with gzip.open(stations_path, "rt", encoding="utf-8") as fh:
        meta = {
            s["id"]: (float(s["location"]["latitude"]), float(s["location"]["longitude"]))
            for s in json.load(fh)
            if s.get("country") == COUNTRY
        }
    frames: dict[str, pd.DataFrame] = {}
    manifest: list[str] = []
    for f in files:
        sid = f.name[: -len(".csv.gz")]
        if sid not in meta:
            logger.warning("station file %s has no KR metadata: skipped", f.name)
            continue
        frames[sid] = _read_station(f)
        manifest.append(f"{f.name}:{sha256_of(f, MAX_SOURCE_BYTES)}")
    if not frames:
        raise IngestError("no Korean station files found")
    eligible = eligible_stations(frames)
    if not eligible:
        raise IngestError("no station meets the season coverage threshold")
    regions = conn.execute("SELECT code, lat, lon FROM region ORDER BY code").fetchall()
    if not regions:
        raise IngestError("region table is empty: run ingest_regions first")
    elig = sorted(eligible)
    idx, dist_km = nearest_station(
        np.array([[r["lat"], r["lon"]] for r in regions]),
        np.array([meta[s] for s in elig]),
    )

    out_of_range = 0
    daily: list[tuple[str, str, float | None, float | None, float | None]] = []
    for sid, f in frames.items():
        for row in f.itertuples():
            tmax = None if pd.isna(row.tmax) else float(row.tmax)
            if tmax is not None and not TMAX_MIN <= tmax <= TMAX_MAX:
                out_of_range += 1
                tmax = None
            tmin = None if pd.isna(row.tmin) else float(row.tmin)
            tavg = None if pd.isna(row.tavg) else float(row.tavg)
            if tmax is None and tmin is None and tavg is None:
                continue
            daily.append((sid, row.date.strftime("%Y-%m-%d"), tmax, tmin, tavg))
    manifest_sha = hashlib.sha256("\n".join(manifest).encode("utf-8")).hexdigest()

    with db.transaction(conn):
        register_dataset(
            conn, name="heat_meteostat_stations", path=stations_path, source_url=STATIONS_URL,
            license_="Meteostat CC BY-NC 4.0 (verify before commercial use)",
            description="Meteostat station list (global); Korean stations used",
            row_count=len(meta), roles={"kind": "heat", "use": "weather_station lat/lon"},
        )
        ds_id = register_dataset(
            conn, name="heat_meteostat_daily", path=None, sha256=manifest_sha,
            source_url=STATIONS_URL, license_="Meteostat CC BY-NC 4.0 (verify before commercial use)",
            description=f"Daily tavg/tmin/tmax, {len(frames)} KR station files; sha256 = hash of the "
                        "sorted '<file>:<sha256>' manifest",
            row_count=len(daily), roles={"kind": "heat", "files": len(frames)},
        )
        conn.executemany(
            "INSERT INTO weather_station (id, lat, lon, dataset_id) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET lat = excluded.lat, lon = excluded.lon, "
            "dataset_id = excluded.dataset_id",
            [(sid, meta[sid][0], meta[sid][1], ds_id) for sid in sorted(frames)],
        )
        conn.executemany(
            "INSERT INTO weather_daily (station_id, date, tmax, tmin, tavg) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(station_id, date) DO UPDATE SET tmax = excluded.tmax, "
            "tmin = excluded.tmin, tavg = excluded.tavg",
            daily,
        )
        conn.executemany(
            "INSERT INTO region_station (region_code, station_id, dist_km) VALUES (?, ?, ?) "
            "ON CONFLICT(region_code) DO UPDATE SET station_id = excluded.station_id, "
            "dist_km = excluded.dist_km",
            [(r["code"], elig[int(i)], float(d)) for r, i, d in zip(regions, idx, dist_km, strict=True)],
        )
    logger.info("stations=%d (eligible %d) weather rows=%d tmax out of range->NULL=%d",
                len(frames), len(eligible), len(daily), out_of_range)
    return {
        "stations": len(frames), "eligible_stations": len(eligible), "weather_rows": len(daily),
        "tmax_out_of_range": out_of_range, "region_station": len(regions),
    }


def main() -> int:
    """CLI: python -m app.heat.ingest_weather."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    paths = resolve_paths()
    conn = db.connect(paths.db_path)
    try:
        db.migrate(conn)
        ingest_weather(conn, paths)
    except IngestError as exc:
        logger.error("%s", exc)
        return 2
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
