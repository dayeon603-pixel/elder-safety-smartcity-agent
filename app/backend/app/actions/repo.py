"""Parameterised SQL used only by the action channels (read-only)."""

from __future__ import annotations

import math
import sqlite3
from typing import Any

KM_PER_DEG_LAT = 111.32


def count_cells(conn: sqlite3.Connection, run_id: int, date: str) -> int:
    """Number of stored regions of the run on that day."""
    r = conn.execute("SELECT COUNT(*) AS n FROM alert WHERE run_id = ? AND date = ?", (run_id, date)).fetchone()
    return int(r["n"])


def is_alerted(conn: sqlite3.Connection, run_id: int, code: str, date: str) -> bool | None:
    """Alert flag of the region on that day (None if the run has no row for it)."""
    r = conn.execute(
        "SELECT alerted FROM alert WHERE run_id = ? AND region_code = ? AND date = ?", (run_id, code, date)
    ).fetchone()
    return None if r is None else bool(r["alerted"])


def shelters_near(
    conn: sqlite3.Connection, lat: float, lon: float, radius_km: float, region_code: str
) -> list[dict[str, Any]]:
    """Shelters in the region plus any other region's shelter inside the bounding box of the radius.

    The box is a prefilter; callers apply the exact distance. Unit conversion happens here only.
    """
    dlat = radius_km / KM_PER_DEG_LAT
    dlon = radius_km / (KM_PER_DEG_LAT * max(math.cos(math.radians(lat)), 1e-6))
    cur = conn.execute(
        "SELECT name, region_code, lat, lon, night_open, weekend_open FROM shelter "
        "WHERE region_code = ? OR (lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?)",
        (region_code, lat - dlat, lat + dlat, lon - dlon, lon + dlon),
    )
    return [{k: r[k] for k in r.keys()} for r in cur.fetchall()]
