"""Heat endpoints' data access: parameterised SQL only, one function per query.

"Latest generation": persist never rewrites a run, so a re-evaluation adds new runs. Every query
here reads, per (method, variant, test_year, budget_kind), only the run with the highest id.
"""

from __future__ import annotations

import sqlite3
from typing import Any

BASELINE = "B1"
OURS = "OURS"
MAIN_BUDGET = "per_day_b1"
METHOD_ORDER = ("B1", "B1S", "B2", "HEUR", "OURS")
METRIC_ORDER = ("capture", "precision", "auc", "prauc", "n_alerts")
HEAT_DATASET_LIKE = "heat\\_%"

_LATEST = "(SELECT MAX(id) FROM alert_run GROUP BY method, variant, test_year, budget_kind)"
_METHOD_RANK = "CASE r.method " + " ".join(f"WHEN '{m}' THEN {i}" for i, m in enumerate(METHOD_ORDER)) + " END"
_METRIC_RANK = "CASE m.metric " + " ".join(f"WHEN '{m}' THEN {i}" for i, m in enumerate(METRIC_ORDER)) + " END"


def _rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [{k: r[k] for k in r.keys()} for r in cur.fetchall()]


def list_sources(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Complete datasets registered by the heat ingest."""
    return _rows(conn.execute(
        "SELECT name, source_url, license, sha256, fetched_at, row_count FROM datasets "
        "WHERE name LIKE ? ESCAPE '\\' AND status = 'complete' ORDER BY name",
        (HEAT_DATASET_LIKE,),
    ))


def completed_years(conn: sqlite3.Connection, variant: str | None = None) -> list[int]:
    """Test years that have a metric-bearing OURS run and its B1 reference (optionally per variant)."""
    cur = conn.execute(
        f"SELECT DISTINCT r.test_year AS y FROM alert_run r "
        f"JOIN alert_run b ON b.id = r.budget_ref_run_id AND b.method = ? "
        f"WHERE r.id IN {_LATEST} AND r.method = ? AND r.budget_kind = ? "
        f"AND (? IS NULL OR r.variant = ?) "
        f"AND EXISTS (SELECT 1 FROM eval_metric m WHERE m.run_id = r.id AND m.metric = 'capture' "
        f"AND m.baseline_run_id IS NULL) ORDER BY y",
        (BASELINE, OURS, MAIN_BUDGET, variant, variant),
    )
    return [int(r["y"]) for r in cur.fetchall()]


def completed_variants(conn: sqlite3.Connection) -> list[str]:
    cur = conn.execute(
        f"SELECT DISTINCT r.variant AS v FROM alert_run r WHERE r.id IN {_LATEST} "
        f"AND r.method = ? AND r.budget_kind = ? ORDER BY v",
        (OURS, MAIN_BUDGET),
    )
    return [str(r["v"]) for r in cur.fetchall()]


def run_methods(conn: sqlite3.Connection) -> list[str]:
    cur = conn.execute(
        f"SELECT DISTINCT r.method AS m FROM alert_run r WHERE r.id IN {_LATEST} ORDER BY {_METHOD_RANK}"
    )
    return [str(r["m"]) for r in cur.fetchall()]


def latest_run_id(
    conn: sqlite3.Connection, method: str, variant: str, year: int, budget: str = MAIN_BUDGET
) -> int | None:
    r = conn.execute(
        "SELECT MAX(id) AS id FROM alert_run WHERE method = ? AND variant = ? AND test_year = ? "
        "AND budget_kind = ?",
        (method, variant, year, budget),
    ).fetchone()
    return int(r["id"]) if r and r["id"] is not None else None


def eval_rows(conn: sqlite3.Connection, variant: str, budget: str) -> list[dict[str, Any]]:
    """Absolute metrics with the paired delta against the run's B1 reference (NULL when none)."""
    return _rows(conn.execute(
        f"SELECT r.test_year AS year, r.method AS method, m.metric AS metric, m.value AS value, "
        f"m.ci_lo AS ci_lo, m.ci_hi AS ci_hi, d.value AS d_value, d.ci_lo AS d_lo, d.ci_hi AS d_hi "
        f"FROM alert_run r JOIN eval_metric m ON m.run_id = r.id AND m.baseline_run_id IS NULL "
        f"AND m.metric <> 'capture_curve' "
        f"LEFT JOIN eval_metric d ON d.run_id = r.id AND d.metric = m.metric AND d.x IS m.x "
        f"AND d.baseline_run_id = r.budget_ref_run_id "
        f"WHERE r.id IN {_LATEST} AND r.variant = ? AND r.budget_kind = ? "
        f"ORDER BY r.test_year, {_METHOD_RANK}, {_METRIC_RANK}",
        (variant, budget),
    ))


def curve_points(conn: sqlite3.Connection, variant: str, year: int) -> list[dict[str, Any]]:
    """Budget-scale capture curve of every method (per_day_b1 runs)."""
    return _rows(conn.execute(
        f"SELECT r.method AS method, m.x AS x, m.value AS capture FROM alert_run r "
        f"JOIN eval_metric m ON m.run_id = r.id AND m.metric = 'capture_curve' "
        f"WHERE r.id IN {_LATEST} AND r.variant = ? AND r.test_year = ? AND r.budget_kind = ? "
        f"ORDER BY {_METHOD_RANK}, m.x",
        (variant, year, MAIN_BUDGET),
    ))


def run_capture(conn: sqlite3.Connection, run_id: int) -> float | None:
    r = conn.execute(
        "SELECT value FROM eval_metric WHERE run_id = ? AND metric = 'capture' AND baseline_run_id IS NULL",
        (run_id,),
    ).fetchone()
    return float(r["value"]) if r else None


def days(conn: sqlite3.Connection, b1_run: int, ours_run: int, year: int) -> list[dict[str, Any]]:
    """Per season day: cases, B1 budget K_d, cases inside B1 alerts, cases inside our alerts."""
    return _rows(conn.execute(
        "WITH cases AS (SELECT date, SUM(n_cases) AS n FROM heat_case_daily "
        "WHERE date BETWEEN ? AND ? GROUP BY date), "
        "b1 AS (SELECT a.date, COUNT(*) AS k, SUM(c.n_cases) AS cap FROM alert a "
        "JOIN heat_case_daily c ON c.region_code = a.region_code AND c.date = a.date "
        "WHERE a.run_id = ? AND a.alerted = 1 GROUP BY a.date), "
        "ours AS (SELECT a.date, SUM(c.n_cases) AS cap FROM alert a "
        "JOIN heat_case_daily c ON c.region_code = a.region_code AND c.date = a.date "
        "WHERE a.run_id = ? AND a.alerted = 1 GROUP BY a.date) "
        "SELECT cases.date AS date, cases.n AS n_cases, COALESCE(b1.k, 0) AS k_budget, "
        "COALESCE(b1.cap, 0) AS b1_captured, COALESCE(ours.cap, 0) AS ours_captured "
        "FROM cases LEFT JOIN b1 ON b1.date = cases.date LEFT JOIN ours ON ours.date = cases.date "
        "ORDER BY cases.date",
        (f"{year}-01-01", f"{year}-12-31", b1_run, ours_run),
    ))


def regions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return _rows(conn.execute("SELECT code, sido, name, lat, lon, pop65 FROM region ORDER BY code"))


def get_region(conn: sqlite3.Connection, code: str) -> dict[str, Any] | None:
    r = conn.execute(
        "SELECT code, sido, name, lat, lon, pop, pop65 FROM region WHERE code = ?", (code,)
    ).fetchone()
    return {k: r[k] for k in r.keys()} if r else None


def alerted_cells(conn: sqlite3.Connection, run_id: int, date: str) -> list[dict[str, Any]]:
    """Alerted cells of a run on a day with that day's case count, highest score first."""
    return _rows(conn.execute(
        "SELECT a.region_code AS code, a.score AS score, COALESCE(c.n_cases, 0) AS cases "
        "FROM alert a LEFT JOIN heat_case_daily c ON c.region_code = a.region_code AND c.date = a.date "
        "WHERE a.run_id = ? AND a.date = ? AND a.alerted = 1 ORDER BY a.score DESC, a.region_code",
        (run_id, date),
    ))


def cases_on(conn: sqlite3.Connection, date: str) -> list[dict[str, Any]]:
    return _rows(conn.execute(
        "SELECT region_code AS code, n_cases AS n FROM heat_case_daily "
        "WHERE date = ? AND n_cases > 0 ORDER BY n_cases DESC, region_code",
        (date,),
    ))


def total_cases_on(conn: sqlite3.Connection, date: str) -> int:
    r = conn.execute("SELECT COALESCE(SUM(n_cases), 0) AS n FROM heat_case_daily WHERE date = ?", (date,)).fetchone()
    return int(r["n"])


def cases_in_region(conn: sqlite3.Connection, code: str, date: str) -> int:
    r = conn.execute(
        "SELECT n_cases FROM heat_case_daily WHERE region_code = ? AND date = ?", (code, date)
    ).fetchone()
    return int(r["n_cases"]) if r else 0


def cell_score(conn: sqlite3.Connection, run_id: int, code: str, date: str) -> float | None:
    r = conn.execute(
        "SELECT score FROM alert WHERE run_id = ? AND region_code = ? AND date = ?", (run_id, code, date)
    ).fetchone()
    return float(r["score"]) if r else None


def cell_rank(conn: sqlite3.Connection, run_id: int, date: str, score: float) -> int:
    """1-based rank by score among the stored cells of that day (1 = highest)."""
    r = conn.execute(
        "SELECT 1 + COUNT(*) AS rank FROM alert WHERE run_id = ? AND date = ? AND score > ?",
        (run_id, date, score),
    ).fetchone()
    return int(r["rank"])


def station_weather(conn: sqlite3.Connection, code: str, date: str) -> dict[str, Any]:
    """{tmax, apparent_max, station} of the region's nearest station on `date` (None where there is a gap).

    `station` = {id, name, source, dist_km} or None when the region has no station assigned.
    """
    r = conn.execute(
        "SELECT rs.station_id AS sid, rs.dist_km AS dist_km, s.name AS name, s.source AS source, "
        "w.tmax AS tmax, w.apparent_max AS apparent_max FROM region_station rs "
        "JOIN weather_station s ON s.id = rs.station_id "
        "LEFT JOIN weather_daily w ON w.station_id = rs.station_id AND w.date = ? WHERE rs.region_code = ?",
        (date, code),
    ).fetchone()
    if r is None:
        return {"tmax": None, "apparent_max": None, "station": None}
    return {
        "tmax": round(float(r["tmax"]), 1) if r["tmax"] is not None else None,
        "apparent_max": round(float(r["apparent_max"]), 1) if r["apparent_max"] is not None else None,
        "station": {"id": r["sid"], "name": r["name"], "source": r["source"], "dist_km": round(float(r["dist_km"]), 1)},
    }


def station_tmax(conn: sqlite3.Connection, code: str, date: str) -> float | None:
    """Observed Tmax of the region's nearest station on `date` (NULL if the station has a gap)."""
    tmax: float | None = station_weather(conn, code, date)["tmax"]
    return tmax


def station_apparent_max(conn: sqlite3.Connection, code: str, date: str) -> float | None:
    """Daily max 체감온도 of the region's nearest station on `date` (NULL when not computed)."""
    at: float | None = station_weather(conn, code, date)["apparent_max"]
    return at


def region_case_count(conn: sqlite3.Connection, code: str, before: str) -> int:
    """Total stored cases of the region strictly before `before` (the sample size behind its rank)."""
    r = conn.execute("SELECT COALESCE(SUM(n_cases), 0) AS n FROM heat_case_daily WHERE region_code = ? AND date < ?",
                     (code, before)).fetchone()
    return int(r["n"]) if r else 0


def prior_case_rate(conn: sqlite3.Connection, code: str, before: str) -> float | None:
    """Mean cases per season day over all stored days strictly before `before` (no later data)."""
    r = conn.execute(
        "SELECT SUM(n_cases) * 1.0 / COUNT(*) AS rate FROM heat_case_daily "
        "WHERE region_code = ? AND date < ?",
        (code, before),
    ).fetchone()
    return float(r["rate"]) if r and r["rate"] is not None else None


def shelter_count(conn: sqlite3.Connection, code: str) -> int:
    r = conn.execute("SELECT COUNT(*) AS n FROM shelter WHERE region_code = ?", (code,)).fetchone()
    return int(r["n"])


def region_shelters(conn: sqlite3.Connection, code: str) -> list[dict[str, Any]]:
    return _rows(conn.execute(
        "SELECT name, lat, lon, night_open, weekend_open FROM shelter WHERE region_code = ?", (code,)
    ))


def plan_cells(
    conn: sqlite3.Connection, run_id: int, first: str, last: str, alerted_only: bool = True
) -> list[dict[str, Any]]:
    """Cells (alerted only, or every stored cell) of a run in [first, last] with region size and that day's actual cases.

    Ordered by date, then score descending, then code, so callers can group without re-sorting.
    """
    return _rows(conn.execute(
        "SELECT a.date AS date, a.region_code AS code, r.sido AS sido, r.name AS name, a.score AS score, "
        "r.pop65 AS pop65, COALESCE(c.n_cases, 0) AS cases "
        "FROM alert a JOIN region r ON r.code = a.region_code "
        "LEFT JOIN heat_case_daily c ON c.region_code = a.region_code AND c.date = a.date "
        "WHERE a.run_id = ? AND a.date BETWEEN ? AND ? AND (a.alerted = 1 OR ? = 0) "
        "ORDER BY a.date, a.score DESC, a.region_code",
        (run_id, first, last, int(alerted_only)),
    ))


def shelters_in_regions(conn: sqlite3.Connection, codes: list[str]) -> list[dict[str, Any]]:
    """Shelters of the given regions (placeholders are generated from the list length only)."""
    if not codes:
        return []
    marks = ",".join("?" * len(codes))
    return _rows(conn.execute(
        f"SELECT id, region_code, name, lat, lon, night_open, weekend_open FROM shelter "
        f"WHERE region_code IN ({marks}) ORDER BY region_code, id",
        tuple(codes),
    ))
