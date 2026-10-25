"""Critical-path tests for the mobility module (greedy exactness, radius, edge cases)."""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import scipy.sparse as sp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib as L  # noqa: E402


def test_radius_is_5min_at_0_8ms() -> None:
    assert abs(L.NET_LIMIT_M - 240.0) < 1e-9
    assert abs(L.radius_m(1.3) - 240 / 1.3) < 1e-9


def test_incidence_boundary_and_empty() -> None:
    inc = L.incidence(np.array([0.0]), np.array([0.0]), np.array([100.0, 200.0]), np.array([0.0, 0.0]), 150.0)
    assert inc.toarray().tolist() == [[1.0, 0.0]]
    assert np.isinf(L.nearest_dist(np.array([1.0]), np.array([1.0]), np.array([]), np.array([]))).all()


def test_lazy_greedy_matches_brute_force_ratio_and_nested() -> None:
    rng = np.random.default_rng(0)
    inc = sp.csr_matrix((rng.random((12, 30)) < 0.2).astype(np.float32))
    w = rng.random(30)
    picks, gains = L.lazy_greedy(inc, w, 4)
    assert abs(gains.sum() - w[L.covered_mask(inc, picks)].sum()) < 1e-9
    best = max(w[L.covered_mask(inc, np.array(c))].sum() for c in itertools.combinations(range(12), 4))
    assert gains.sum() >= (1 - 1 / np.e) * best
    p2, _ = L.lazy_greedy(inc, w, 2)
    assert list(p2) == list(picks[:2])  # nested picks
    assert np.all(np.diff(gains) <= 1e-9)  # submodular: gains non-increasing


def test_greedy_zero_weight_and_overbudget() -> None:
    inc = sp.csr_matrix(np.eye(3, dtype=np.float32))
    picks, _ = L.lazy_greedy(inc, np.zeros(3), 5)
    assert len(picks) == 0
    picks, _ = L.lazy_greedy(inc, np.ones(3), 10)
    assert len(picks) == 3


def test_spearman_ci_degenerate() -> None:
    r = L.spearman_ci(np.ones(10), np.arange(10.0))
    assert np.isnan(r["rho"])
    r = L.spearman_ci(np.arange(25.0), np.arange(25.0))
    assert r["rho"] > 0.99 and r["lo"] > 0.9
