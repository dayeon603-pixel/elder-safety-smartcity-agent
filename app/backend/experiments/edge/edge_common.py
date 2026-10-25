"""Shared paths and interval helpers for the edge-case audit.

Everything here is read-only over the other experiments' outputs. Numbers written by these scripts are
audit evidence, not product claims. Backtests are hypotheses, not proofs.
"""

from __future__ import annotations

import json
import logging
import math
import random
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger("edge")

SEED = 42
HERE = Path(__file__).resolve().parent
EXP = HERE.parent
OUT = HERE / "out"
FIGS = HERE / "figs"
STYLE = EXP.parent / "figures" / "icassp.mplstyle"

HEAT_OUT = EXP / "heat" / "out"
PANEL = HEAT_OUT / "panel.csv.gz"
PRED = HEAT_OUT / "test_predictions.csv.gz"
HEAT_RESULTS = HEAT_OUT / "results.json"
SILVER_OUT = EXP / "silver" / "out"
AED_OUT = EXP / "aed" / "out"
V119_OUT = EXP / "validate119" / "out"
FALL_RESULTS = EXP / "fall" / "results"

TEST_YEARS = (2022, 2023, 2024, 2025)
Z95 = 1.959963984540054


def set_seed(seed: int = SEED) -> None:
    random.seed(seed)
    np.random.seed(seed)


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float, float]:
    """(point, lo, hi) Wilson score interval for a binomial share. n == 0 -> (nan, nan, nan)."""
    if n <= 0:
        return math.nan, math.nan, math.nan
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return p, max(0.0, centre - half), min(1.0, centre + half)


def wilson_half_width(p: float, n: int, z: float = Z95) -> float:
    """Half-width of the Wilson interval at share p and sample size n."""
    if n <= 0:
        return math.nan
    denom = 1 + z * z / n
    return z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom


def n_for_half_width(p: float, half: float, z: float = Z95) -> int:
    """Smallest n whose Wald half-width at share p is <= half (the usual sample-size formula)."""
    return int(math.ceil(z * z * p * (1 - p) / (half * half)))


def poisson_ci(k: int, z: float = Z95) -> tuple[float, float]:
    """Exact (Garwood) 95% interval for a Poisson count via the chi-square relation."""
    from scipy.stats import chi2

    lo = 0.0 if k == 0 else chi2.ppf(0.025, 2 * k) / 2
    hi = chi2.ppf(0.975, 2 * (k + 1)) / 2
    return float(lo), float(hi)


def dump(obj: Any, path: Path) -> None:
    def default(o: object) -> object:
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            return None if np.isnan(o) else float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        if isinstance(o, Path):
            return str(o)
        raise TypeError(type(o))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=default), encoding="utf-8")
    logger.info("wrote %s", path)


def use_style() -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if STYLE.is_file():
        plt.style.use(str(STYLE))


__all__ = [
    "AED_OUT", "FALL_RESULTS", "FIGS", "HEAT_OUT", "HEAT_RESULTS", "OUT", "PANEL", "PRED", "SEED", "SILVER_OUT",
    "TEST_YEARS", "V119_OUT", "dump", "n_for_half_width", "poisson_ci", "set_seed", "use_style", "wilson",
    "wilson_half_width",
]
