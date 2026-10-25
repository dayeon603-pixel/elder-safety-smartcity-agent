"""Small, hand-checkable heat database for tests: 3 regions, 3 days of 2024, B1 vs OURS (variant F)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from app.heat import persist

YEAR = 2024
DATES = ("2024-07-01", "2024-07-02", "2024-07-03")
# (code, sido, name, lat, lon, pop, pop65)
REGIONS = (
    ("11110", "서울특별시", "종로구", 37.57, 126.98, 1000, 250),
    ("11120", "서울특별시", "중구", 37.56, 126.99, 2000, 400),
    ("26110", "부산광역시", "중구", 35.10, 129.03, 3000, 300),
)
UNIT_KEYS = {0: "서울특별시|종로구", 1: "서울특별시|중구", 2: "부산광역시|중구"}
# cases[date][unit_id]
CASES = {DATES[0]: {0: 0, 1: 3, 2: 0}, DATES[1]: {0: 2, 1: 0, 2: 1}, DATES[2]: {0: 0, 1: 0, 2: 0}}
B1_ALERT = {DATES[0]: 0, DATES[1]: 2, DATES[2]: 1}  # one alert per day (K_d = 1)
OURS_ALERT = {DATES[0]: 1, DATES[1]: 0, DATES[2]: 1}
NaN = float("nan")


def _stat(est: float, lo: float, hi: float) -> dict[str, float]:
    return {"est": est, "lo": lo, "hi": hi}


def results_json() -> dict[str, Any]:
    def budget() -> dict[str, Any]:
        return {
            "methods": {
                "B1": {"alerts": 3.0, "caught": 1.0, "capture": _stat(0.25, 0.1, 0.4), "precision": _stat(0.3, 0.2, 0.5)},
                "OURS": {"alerts": 3.0, "caught": 4.0, "capture": _stat(0.6, 0.4, 0.8), "precision": _stat(0.5, 0.3, 0.7)},
            },
            "delta": {
                "OURS-B1": {"capture": _stat(0.35, 0.1, 0.5), "precision": _stat(0.2, -0.1, 0.4)},
            },
        }

    return {
        "seed": 42, "n_boot": 1000, "block_days": 7, "hot_c": 33.0, "heur_beta": 0.3,
        "hgb_params": {"loss": "poisson"}, "git_hash": None,
        "data_sha256": {"heat.csv": "a" * 64}, "code_sha256": {"evaluate.py": "b" * 64},
        "variants_doc": {"F": "forecast proxy", "O": "obs to D-1"},
        "variants": {"F": {
            "folds": {str(YEAR): {
                "budget": {"B1|per_day": budget(), "B1|season_total": budget()},
                "rank": {"B1": {"auc": _stat(0.7, 0.6, 0.8), "pr_auc": _stat(0.2, 0.1, 0.3)},
                         "OURS": {"auc": _stat(0.8, 0.7, 0.9), "pr_auc": _stat(0.4, 0.3, 0.5)}},
                "rank_delta_OURS_minus": {"B1": {"auc": _stat(0.1, 0.02, 0.2), "pr_auc": _stat(0.2, 0.1, 0.3)}},
            }},
            "sweep": {str(YEAR): {"curves": {
                "B1": [{"scale": 0.5, "alert_share": 0.1, "capture": 0.1}, {"scale": 1.0, "alert_share": 0.2, "capture": 0.25}],
                "OURS": [{"scale": 0.5, "alert_share": 0.1, "capture": 0.3}, {"scale": 1.0, "alert_share": 0.2, "capture": 0.6}],
            }}},
        }},
    }


def prediction_frame(nan_b1_cell: bool = False) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for method, alert in (("B1", B1_ALERT), ("OURS", OURS_ALERT)):
        for budget in ("per_day", "season_total"):
            for d in DATES:
                for u in UNIT_KEYS:
                    score = 30.0 + u if method == "B1" else 1.0 + (2 - u) * 0.5 + (u == alert[d])
                    rows.append(dict(method=method, variant="F", test_year=YEAR, budget_kind=budget,
                                     date=d, region_code=u, score=score, alerted=int(u == alert[d])))
    df = pd.DataFrame(rows)
    if nan_b1_cell:
        mask = (df.method == "B1") & (df.date == DATES[2]) & (df.region_code == 0)
        df.loc[mask, "score"] = NaN
    return df


def write_experiment_outputs(out: Path, nan_b1_cell: bool = False) -> tuple[Path, Path, Path]:
    """Write panel / predictions / results files the way the experiment would; return their paths."""
    out.mkdir(parents=True, exist_ok=True)
    panel = out / persist.PANEL_NAME
    pd.DataFrame({"unit_id": list(UNIT_KEYS), "unit_key": list(UNIT_KEYS.values())}).to_csv(panel, index=False)
    pred = out / persist.PRED_NAME
    prediction_frame(nan_b1_cell).to_csv(pred, index=False)
    res = out / persist.RESULTS_NAME
    res.write_text(json.dumps(results_json()), encoding="utf-8")
    return pred, res, panel


def seed_base(conn: sqlite3.Connection) -> None:
    """Regions, one station per region group, dense cases for the 3 days, shelters for 26110 only."""
    conn.executemany(
        "INSERT INTO region (code, sido, name, lat, lon, pop, pop65, pop_asof) VALUES (?,?,?,?,?,?,?,?)",
        [(*r, "2026-09-30") for r in REGIONS],
    )
    conn.execute(
        "INSERT INTO datasets (name, source_url, license, sha256, fetched_at, row_count) "
        "VALUES ('heat_fixture', 'https://example.org/x', 'CC0', ?, '2026-01-01T00:00:00+00:00', 1)",
        ("c" * 64,),
    )
    ds = int(conn.execute("SELECT id FROM datasets WHERE name='heat_fixture'").fetchone()[0])
    conn.execute("INSERT INTO weather_station (id, lat, lon, dataset_id, name, source) "
                 "VALUES ('47108', 37.5, 127.0, ?, '서울', 'kma_asos')", (ds,))
    conn.executemany(
        "INSERT INTO region_station (region_code, station_id, dist_km) VALUES (?, '47108', 5.0)",
        [("11110",), ("11120",)],
    )
    # apparent_max (daily max 체감온도) is known only for the first season day; the others fall back to tmax
    conn.executemany(
        "INSERT INTO weather_daily (station_id, date, tmax, tmin, tavg, apparent_max, n_hours) "
        "VALUES ('47108', ?, ?, 20.0, 25.0, ?, ?)",
        [("2024-06-30", 31.0, None, None), ("2024-07-01", 33.5, 35.1, 10), ("2024-07-02", 34.5, None, None),
         ("2024-07-03", 35.5, None, None)],
    )
    # earlier-year history for hist_rate: 2023-08-01 had 2 cases in 11110
    conn.execute("INSERT INTO heat_case_daily VALUES ('11110', '2023-08-01', 2, 1, 1)")
    codes = [r[0] for r in REGIONS]
    for d, per_unit in CASES.items():
        for u, code in enumerate(codes):
            n = per_unit[u]
            conn.execute("INSERT INTO heat_case_daily VALUES (?, ?, ?, ?, ?)", (code, d, n, min(n, 1), n - min(n, 1)))
    conn.executemany(
        "INSERT INTO shelter (region_code, name, lat, lon, capacity, night_open, weekend_open, dataset_id) "
        "VALUES ('26110', ?, ?, ?, ?, ?, ?, ?)",
        [("far", 35.20, 129.03, 10, None, None, ds), ("near", 35.101, 129.031, 20, 1, 0, ds)],
    )


def build_heat_db(conn: sqlite3.Connection, out_dir: Path, nan_b1_cell: bool = False) -> dict[str, int]:
    """seed_base + persist; returns the persist counters."""
    seed_base(conn)
    pred, res, panel = write_experiment_outputs(out_dir, nan_b1_cell)
    return persist.persist(conn, pred, res, panel)
