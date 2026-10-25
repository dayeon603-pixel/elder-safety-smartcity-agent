"""Shared constants and helpers for the heat-illness evaluation (idea A, 특보 사각지대).

Everything here is read-only with respect to the data cache. Backtests are hypotheses,
not proofs: no number produced by this package is a claim about deployed performance.
"""

from __future__ import annotations

import hashlib
import json
import os
import logging
import random
import subprocess
from pathlib import Path

import numpy as np

logger = logging.getLogger("heat")

SEED = 42
HERE = Path(__file__).resolve().parent
OUT_DIR = Path(os.environ.get("HEAT_OUT_DIR", str(HERE / "out")))  # override only for dry runs
FIG_DIR = HERE / "figs"
CACHE = Path("/Users/chloekang/kcf/data/cache")

HEAT_CSV = CACHE / "b_climate_env_energy/kr_15149889_heat_illness_reports_2011_2025.csv"
POP_CSV = CACHE / "j_population_regional/kr_15097972_resident_pop_by_dong_sex_age_202609.csv"
GEOJSON = CACHE / "extra/boundaries/HangJeongDong_ver20261001.geojson"
WX_DIR = CACHE / "extra/meteostat_daily"
STATIONS_JSON = WX_DIR / "stations_lite.json.gz"

YEARS = list(range(2019, 2026))
TEST_YEARS = [2022, 2023, 2024, 2025]  # rolling-origin: train 2019..T-1 (T=2022 trains on 3 seasons)
POOL_LABEL = "2022_2025"
SEASON_START = (5, 20)  # inclusive
SEASON_END = (9, 25)  # inclusive; the heat-case file ends 2025-09-25, so every year uses the same window
HOT_C = 33.0  # 2026 폭염주의보 criterion: 일최고체감온도 >= 33 C (applied to daily max 체감온도; Tmax only in the sensitivity run)
FETCH_START = (5, 17)  # 3 lag days before the season start (lag features need D-1..D-3)
FETCH_END = (9, 26)  # 1 lead day after the season end (forecast proxy for D+1 on the last day)
HOURS = [int(x) for x in os.environ.get("HEAT_HOURS", "10,11,12,13,14,15,16,17,18,19").split(",")]  # KST hours entering the daily max (hub cache must hold all of them)
MIN_HOURS = int(os.environ.get("HEAT_MIN_HOURS", "7"))  # valid hours (of len(HOURS)) required for a station-day; fewer -> NaN
FAMILIES = ("at", "tx")  # at = 체감온도 (primary); tx = hourly-max 기온 on the same station-hours (sensitivity)
POP_SNAP_CSV = CACHE / "extra/mois_pop/sgg_pop_dec_snapshots.csv"

PANEL_PATH = OUT_DIR / "panel.csv.gz"  # parquet engine (pyarrow) is not installed in the backend env
CROSSWALK_PATH = OUT_DIR / "crosswalk_heat_to_2026.csv"
COVERAGE_PATH = OUT_DIR / "coverage_report.json"
PRED_PATH = OUT_DIR / "test_predictions.csv.gz"
RESULTS_PATH = OUT_DIR / "results.json"
SENS_PATH = OUT_DIR / "results_tmax_sensitivity.json"


def set_seed(seed: int = SEED) -> None:
    """Seed python and numpy RNGs (sklearn models take random_state explicitly)."""
    random.seed(seed)
    np.random.seed(seed)


def sha256_file(path: Path) -> str:
    """Streaming SHA-256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_hash() -> str | None:
    """HEAD commit of the enclosing repo, or None when the tree is not a git repo."""
    for cwd in (HERE, Path("/Users/chloekang/kcf")):
        try:
            r = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, check=True
            )
            return r.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    return None


def dump_json(obj: object, path: Path) -> None:
    """Write JSON with numpy scalars handled."""

    def default(o: object) -> object:
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return None if np.isnan(o) else float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        if isinstance(o, Path):
            return str(o)
        raise TypeError(type(o))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=default))
