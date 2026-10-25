"""Tiny silver / AED output directories in the experiments' exact file formats (for loader and API tests)."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

SILVER_RESULT_HEADER = ["variant", "stratum", "pool", "method", "k", "coverage", "lo", "hi", "n_hot", "cas"]
# (stratum, method, coverage, lo, hi)
SILVER_RESULTS = (
    ("all", "current", 0.15, 0.12, 0.18), ("all", "random_k", 0.74, 0.72, 0.76), ("all", "pop65_k", 0.75, 0.72, 0.78),
    ("all", "past_k", 0.93, 0.91, 0.94), ("all", "ours_full", 0.89, 0.87, 0.91),
    ("new", "current", 0.12, 0.05, 0.20), ("new", "random_k", 0.10, 0.07, 0.15), ("new", "pop65_k", 0.24, 0.15, 0.33),
    ("new", "past_k", 0.24, 0.15, 0.33), ("new", "ours_full", 0.21, 0.13, 0.31),
)
DELTA_HEADER = ["variant", "stratum", "pool", "method", "k", "vs", "delta_pp", "lo_pp", "hi_pp"]
MAP_HEADER = ["lat", "lon", "kind", "sido", "zone_nm", "cas", "cur_within_300m", "ours_within_300m"]
# (lat, lon, kind, sido, zone_nm, cas, cur, ours); 종로구 ~ (37.58, 126.98), 중구 ~ (37.56, 126.99)
MAP_ROWS = (
    (37.580, 126.980, "current", "서울특별시", "사직경로당", "", 1, 0),
    (37.581, 126.981, "current", "서울특별시", "종로노인정", "", 1, 1),
    (37.560, 126.990, "ours", "서울특별시", "", "", 0, 1),
    (37.579, 126.979, "ours", "서울특별시", "", "", 0, 1),
    (37.582, 126.982, "ours", "서울특별시", "", "", 1, 1),
    (37.580, 126.981, "hotspot_2024", "", "", 7, 0, 1),
    (37.561, 126.991, "hotspot_2024", "", "", 4, 1, 1),
    (35.101, 129.031, "ours", "부산광역시", "", "", 0, 1),
)
HOURLY_HEADER = ["hour", "public_ca", "pct_open_weekday", "pct_open_weekend_W1_as_reported",
                 "pct_open_weekend_W2_placeholder_as_weekday", "pct_open_weekend_W3_placeholder_unknown_dropped"]
GU_HEADER = ["code", "gu", "lat", "lon", "area_km2", "n_aed", "n_aed_24h_weekday", "night_pct_open_weekday",
             "day_pct_open_weekday", "public_ca_per_yr_night_est", "night_gap_score_weekday"]
# (source code, gu, n_aed, n_aed_24h, night %, day %, night arrests/yr, gap)
GU_ROWS = (("11010", "종로구", 100, 40, 40.0, 72.0, 17.2, 9.7), ("11020", "중구", 90, 45, 50.0, 75.0, 18.8, 10.56))
CAND_HEADER = ["gu", "org", "address", "place_in_building", "lat", "lon", "weekday_hours", "weekday_open_hours_of_24",
               "night_open_weekday_share", "gain_score", "serialSeq", "weekend_24h_placeholder_pattern",
               "also_in_density_set"]
# (gu, org, place, hours, night share, gain, serial)
CAND_ROWS = (
    ("종로구", "A경로당", "1층 현관", "0900-1600", 0.0, 7.5, "010-1"),
    ("종로구", "B초등학교", "본관 입구", "nan-nan", 0.1, 8.5, "010-2"),
    ("중구", "C주민센터", "로비", "0000-0100", 0.0, 6.0, "020-1"),
)


def _write(path: Path, header: list[str], rows: list[list[Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


# promoted (optimised) run: ours_opt in pool OPT; the baselines are the same pool-C rows. (stratum, coverage, lo, hi)
SILVER_OPT = (("all", 0.93, 0.92, 0.95), ("new", 0.39, 0.28, 0.51))
N_HOT = {"all": 70, "new": 10}


def _write_silver_files(d: Path, ours_rows: list[list[Any]], ours_deltas: list[list[Any]]) -> None:
    d.mkdir(parents=True, exist_ok=True)
    _write(d / "coverage_results.csv", SILVER_RESULT_HEADER,
           [["harm", s, "C", m, 3918, c, lo, hi, N_HOT[s], 400.0] for s, m, c, lo, hi in SILVER_RESULTS]
           + [["harm", "all", "C", "current", 250, 0.02, 0.01, 0.03, 70, 400.0]] + ours_rows)
    _write(d / "delta_results.csv", DELTA_HEADER,
           [["harm", s, "C", "ours_full", 3918, f"C:{m}", round(0.89 - c, 2) * 100, -1.0, 1.0]
            for s, m, c, _, _ in SILVER_RESULTS if m != "ours_full" and s == "all"]
           + [["harm", "new", "C", "ours_full", 3918, "C:current", 9.0, 1.3, 18.1]] + ours_deltas)
    _write(d / "map_seoul_current_vs_ours_k3918_fold2024.csv", MAP_HEADER, [list(r) for r in MAP_ROWS])


def write_silver(d: Path) -> Path:
    """v1 files in `d` (ours_full) and the promoted run in `d/opt/promoted` (ours_opt) plus both protocols."""
    _write_silver_files(d, [], [])
    opt_rows = [["harm", s, "OPT", "ours_opt", 3918, c, lo, hi, N_HOT[s], 400.0] for s, c, lo, hi in SILVER_OPT]
    opt_deltas = [["harm", s, "OPT", "ours_opt", 3918, f"C:{m}", round(oc - c, 2) * 100, -1.0, 1.0]
                  for s, oc, _, _ in SILVER_OPT for ss, m, c, _, _ in SILVER_RESULTS if ss == s and m != "ours_full"]
    _write_silver_files(d / "opt" / "promoted", opt_rows, opt_deltas)
    _write(d / "coverage_by_fold.csv", ["variant", "fold"], [["harm", 2022]])
    _write(d / "test_hotspots_meta.csv", ["variant", "fold"], [["harm", 2022]])
    (d / "protocol.json").write_text(json.dumps({"config": {"k_main": 3918}}), encoding="utf-8")
    (d / "opt" / "protocol_opt.json").write_text(json.dumps({"config": {"k_main": 3918, "pool": "OPT"}}), encoding="utf-8")
    return d


def write_aed(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    _write(d / "hourly_gap.csv", HOURLY_HEADER,
           [[h, 10 + h, 49.0 if h < 6 else 78.0, 50.0, 48.0, 49.0] for h in range(24)])
    _write(d / "seoul_gu_night_gap_map.csv", GU_HEADER,
           [[c, g, 37.5, 127.0, 20.0, n, n24, night, day, arr, gap] for c, g, n, n24, night, day, arr, gap in GU_ROWS])
    for fname, rows in (("recommended_candidates_top5_per_gu.csv", CAND_ROWS), ("recommended_candidates_5pct.csv", CAND_ROWS[:2])):
        _write(d / fname, CAND_HEADER,
               [[g, o, f"{g} 주소 1", p, 37.5, 127.0, h, 7.0, ns, gain, s, False, False] for g, o, p, h, ns, gain, s in rows])
    _write(d / "policy_eval.csv", ["budget"], [["1%"]])
    (d / "aed_diagnostics.json").write_text(json.dumps({"n_aed_seoul": 1}), encoding="utf-8")
    (d / "protocol.json").write_text(json.dumps({"seed": 42}), encoding="utf-8")
    weekday = {
        "share_arrests_when_open_lt_50pct": [0.14, 0.12, 0.15, 344], "night_share_of_arrests_22_06": [0.18, 0.16, 0.19, 447],
        "mean_pct_open_night": 49.3, "mean_pct_open_day": 78.3, "arrest_weighted_pct_open_night": 48.9,
        "arrest_weighted_pct_open_day": 80.8,
    }
    weekend = {**weekday, "mean_pct_open_night": 51.5, "mean_pct_open_day": 62.5}
    (d / "summary.json").write_text(
        json.dumps({"audit": {"public_ca": 2534}, "desc": {"weekday": weekday, "weekend_W1_as_reported": weekend}}),
        encoding="utf-8",
    )
    return d
