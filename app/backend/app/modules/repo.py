"""Read-only queries for the silver-zone and AED modules: parameterised SQL, one function per query."""

from __future__ import annotations

import sqlite3
from typing import Any

from .silver_config import COMPARISON_METHODS, COMPARISON_POOL, HEADLINE_POOL, HEADLINE_VARIANT, OURS

STRATA = ("all", "new")
LIST_TOP5 = "top5_per_gu"
SEOUL = "서울특별시"
WORST_GU_COUNT = 5
SITE_KINDS = ("current", "ours")


def _rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [{k: r[k] for k in r.keys()} for r in cur.fetchall()]


def silver_loaded(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM silver_result LIMIT 1").fetchone() is not None


def aed_loaded(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM aed_gu_gap LIMIT 1").fetchone() is not None


def sources(conn: sqlite3.Connection, module: str) -> list[dict[str, Any]]:
    """Source files of a module with their sha256 (the provenance a slide number traces back to)."""
    return _rows(conn.execute(
        "SELECT source_file AS file, sha256, loaded FROM module_run WHERE module = ? ORDER BY source_file", (module,)
    ))


def max_k(conn: sqlite3.Connection) -> int | None:
    r = conn.execute(
        "SELECT MAX(k) AS k FROM silver_result WHERE variant = ? AND pool = ?", (HEADLINE_VARIANT, HEADLINE_POOL)
    ).fetchone()
    return int(r["k"]) if r and r["k"] is not None else None


def silver_results(conn: sqlite3.Connection, k: int) -> list[dict[str, Any]]:
    """Our headline row (HEADLINE_POOL / OURS) plus the comparison baselines from COMPARISON_POOL, at k."""
    marks = ",".join("?" * len(COMPARISON_METHODS))
    return _rows(conn.execute(
        f"SELECT stratum, method, coverage, ci_lo, ci_hi, n_hot, casualties FROM silver_result "
        f"WHERE variant = ? AND k = ? AND ((pool = ? AND method = ?) OR (pool = ? AND method IN ({marks})))",
        (HEADLINE_VARIANT, k, HEADLINE_POOL, OURS, COMPARISON_POOL, *COMPARISON_METHODS),
    ))


def silver_deltas(conn: sqlite3.Connection, k: int) -> list[dict[str, Any]]:
    """Our paired difference (percentage points) against each other method."""
    return _rows(conn.execute(
        "SELECT stratum, vs, delta_pp, lo_pp, hi_pp FROM silver_delta "
        "WHERE variant = ? AND pool = ? AND k = ? AND method = ?",
        (HEADLINE_VARIANT, HEADLINE_POOL, k, OURS),
    ))


def silver_sites(conn: sqlite3.Connection, kind: str) -> list[dict[str, Any]]:
    return _rows(conn.execute(
        "SELECT lat, lon, name, sgg_code, cur_within_300m AS near_current, ours_within_300m AS near_ours "
        "FROM silver_site WHERE kind = ? ORDER BY id",
        (kind,),
    ))


def silver_hotspots(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return _rows(conn.execute(
        "SELECT lat, lon, casualties, sgg_code, covered_current, covered_ours FROM silver_hotspot_2024 ORDER BY id"
    ))


def silver_region_counts(conn: sqlite3.Connection, code: str) -> dict[str, int]:
    """Current zones, our picks, 2024 hotspots (and how many each side covers) inside one region."""
    kinds = {r["kind"]: int(r["n"]) for r in conn.execute(
        "SELECT kind, COUNT(*) AS n FROM silver_site WHERE sgg_code = ? GROUP BY kind", (code,))}
    h = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(covered_current), 0) AS cur, COALESCE(SUM(covered_ours), 0) AS ours "
        "FROM silver_hotspot_2024 WHERE sgg_code = ?",
        (code,),
    ).fetchone()
    return {
        "current_zones": kinds.get("current", 0), "our_picks": kinds.get("ours", 0),
        "hotspots": int(h["n"]), "hotspots_covered_current": int(h["cur"]), "hotspots_covered_ours": int(h["ours"]),
    }


def aed_stats(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {str(r["name"]): {k: r[k] for k in r.keys()} for r in conn.execute(
        "SELECT name, value, ci_lo, ci_hi, n FROM aed_stat")}


def aed_hourly(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return _rows(conn.execute(
        "SELECT hour, public_arrests, open_share_weekday, open_share_weekend FROM aed_hourly_gap ORDER BY hour"
    ))


_GU_COLS = (
    "gu, sgg_code, night_open_share, day_open_share, gap_score, public_arrests_night, n_aed, n_aed_24h_weekday"
)


def aed_gu_list(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """All 구, worst night gap first (ties by name so the order is stable)."""
    return _rows(conn.execute(f"SELECT {_GU_COLS} FROM aed_gu_gap ORDER BY gap_score DESC, gu"))


def aed_gu_by_name(conn: sqlite3.Connection, gu: str) -> dict[str, Any] | None:
    r = conn.execute(f"SELECT {_GU_COLS} FROM aed_gu_gap WHERE gu = ?", (gu,)).fetchone()
    return {k: r[k] for k in r.keys()} if r else None


def aed_gu_by_code(conn: sqlite3.Connection, code: str) -> dict[str, Any] | None:
    r = conn.execute(f"SELECT {_GU_COLS} FROM aed_gu_gap WHERE sgg_code = ?", (code,)).fetchone()
    return {k: r[k] for k in r.keys()} if r else None


def aed_gu_events(conn: sqlite3.Connection) -> tuple[dict[str, float], dict[str, float]]:
    """({gu: night events/yr}, {gu: 1 − night_open_share}) for the Poisson rank bootstrap (score = events × factor)."""
    rows = conn.execute("SELECT gu, public_arrests_night, night_open_share FROM aed_gu_gap").fetchall()
    return ({str(r["gu"]): float(r["public_arrests_night"]) for r in rows},
            {str(r["gu"]): 1.0 - float(r["night_open_share"]) for r in rows})


def aed_gap_rank(conn: sqlite3.Connection, gap_score: float) -> int:
    """1-based rank of a gap score among the 구 (1 = largest night gap)."""
    r = conn.execute("SELECT 1 + COUNT(*) AS rank FROM aed_gu_gap WHERE gap_score > ?", (gap_score,)).fetchone()
    return int(r["rank"])


def aed_candidates(conn: sqlite3.Connection, gu: str | None) -> list[dict[str, Any]]:
    """Top-5-per-구 list (one 구 or all), best first. Facility, place and hours only."""
    return _rows(conn.execute(
        "SELECT gu, rank_in_gu AS rank, org, address, place, weekday_hours, night_open_weekday_share, gain_score, "
        "lat, lon FROM aed_candidate WHERE list_kind = ? AND (? IS NULL OR gu = ?) ORDER BY gu, rank_in_gu",
        (LIST_TOP5, gu, gu),
    ))


def region_by_code(conn: sqlite3.Connection, code: str) -> dict[str, Any] | None:
    r = conn.execute("SELECT code, sido, name, pop65 FROM region WHERE code = ?", (code,)).fetchone()
    return {k: r[k] for k in r.keys()} if r else None
