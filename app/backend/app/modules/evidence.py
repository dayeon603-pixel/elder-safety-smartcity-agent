"""근거 (evidence) attached to every heat recommendation, shared by /briefing and /actions.

A recommendation is explainable when the reader can see the inputs that produced it: the model's
expected case count λ (the OURS score), the 65+ population, the day's 체감 최고 / 최고기온 and the
station they came from, the rank among that day's cells, and the sha256 of the datasets the numbers
were computed from. The same dict shape is used everywhere so a reviewer can diff screens.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .. import repo_heat

EVIDENCE_DATASETS = ("heat_kma_asos_hourly", "heat_kma_asos_stations", "heat_illness_cases", "heat_pop_by_dong",
                     "heat_admin_boundaries")


def dataset_shas(conn: sqlite3.Connection) -> dict[str, str]:
    """{dataset name: sha256} for the heat datasets the backend serves from (only those present)."""
    rows = {r["name"]: str(r["sha256"]) for r in repo_heat.list_sources(conn)}
    return {name: rows[name] for name in EVIDENCE_DATASETS if name in rows}


def heat_evidence(
    conn: sqlite3.Connection, region: dict[str, Any], day: str, *, score: float | None, rank: int | None,
    n_cells: int | None, alerted: bool | None, wx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Inputs behind a heat recommendation for `region` on `day` (`score` is the OURS λ, expected cases)."""
    weather = wx if wx is not None else repo_heat.station_weather(conn, region["code"], day)
    pop, pop65 = int(region["pop"]), int(region["pop65"])
    return {
        "date": day,
        "expected_cases_lambda": round(float(score), 3) if score is not None else None,
        "rank": rank, "n_cells": n_cells, "alerted": alerted,
        "pop65": pop65, "old_share": round(pop65 / pop, 4) if pop else None,
        "apparent_max_c": weather["apparent_max"], "tmax_c": weather["tmax"], "station": weather["station"],
        "data_sha256": dataset_shas(conn),
        "model": "Poisson GBM (OURS), 시군구 단위; 체감 최고 = 기상청 ASOS 여름철 체감온도 일 최댓값 (KST 10-19시)",
    }


__all__ = ["dataset_shas", "heat_evidence"]
