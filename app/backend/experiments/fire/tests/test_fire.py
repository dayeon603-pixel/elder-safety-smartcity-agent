"""Edge-case tests for the fire experiment (units, selection, EB shrinkage)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data import build_panel, unit_key  # noqa: E402
from run import capture, nb_k, select  # noqa: E402


def test_unit_key_merges() -> None:
    assert unit_key("경상북도", "군위군") == unit_key("대구광역시", "군위군")
    assert unit_key("경기도", "부천시 원미구") == unit_key("경기도", "부천시")
    assert unit_key("강원도", "원주시") == unit_key("강원특별자치도", "원주시")


def test_panel_totals_and_no_nan() -> None:
    p = build_panel()
    assert p.cas.sum() == 5689 and p.deaths.sum() == 967
    assert not p[["pop", "pop65", "fires", "cas"]].isna().any().any()
    assert (p.pop65 <= p["pop"]).all()


def test_select_ties_deterministic_and_size() -> None:
    s = np.zeros(50)
    a = select(s, 5, np.random.default_rng(1)); b = select(s, 5, np.random.default_rng(1))
    assert len(set(a)) == 5 and (a == b).all()


def test_capture_bounds() -> None:
    y = np.array([0.0, 3, 1, 6]); assert capture(y, np.array([3, 1])) == 0.9


def test_nb_k_poisson_limit_large() -> None:
    rng = np.random.default_rng(0); m = np.full(2000, 5.0); y = rng.poisson(m).astype(float)
    assert nb_k(y, m) > 50  # no overdispersion -> very large shape (strong shrinkage to prior)
