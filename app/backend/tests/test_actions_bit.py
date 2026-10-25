"""BIT selection: greedy max coverage against brute force, radius semantics, message shape."""

from __future__ import annotations

import itertools

import numpy as np
import pandas as pd
import pytest

from app.actions import bit
from app.actions import constants as C
from app.actions.dongs import Dong

DEG_PER_M = 1 / 111_320.0


def brute_force(cover: np.ndarray, w: np.ndarray, k: int) -> float:
    best = 0.0
    for combo in itertools.combinations(range(cover.shape[0]), min(k, cover.shape[0])):
        covered = cover[list(combo)].any(axis=0)
        best = max(best, float(w[covered].sum()))
    return best


def covered_weight(cover: np.ndarray, w: np.ndarray, chosen: list[int]) -> float:
    return float(w[cover[chosen].any(axis=0)].sum()) if chosen else 0.0


def test_greedy_matches_optimum_on_a_hand_case() -> None:
    # candidate 0 covers {0,1}, 1 covers {1,2}, 2 covers {3}; weights make {0,2}... optimum for k=2
    cover = np.array([[1, 1, 0, 0], [0, 1, 1, 0], [0, 0, 0, 1]], dtype=bool)
    w = np.array([5.0, 1.0, 5.0, 4.0])
    chosen, marginal = bit.greedy_max_coverage(cover, w, 2)
    assert chosen == [0, 1] and marginal == [6.0, 5.0]
    assert covered_weight(cover, w, chosen) == brute_force(cover, w, 2) == 11.0


@pytest.mark.parametrize("seed", range(25))
def test_greedy_within_guarantee_of_brute_force(seed: int) -> None:
    rng = np.random.default_rng(seed)
    n_c, n_d, k = 9, 14, 3
    cover = rng.random((n_c, n_d)) < 0.25
    w = rng.integers(1, 20, n_d).astype(float)
    chosen, marginal = bit.greedy_max_coverage(cover, w, k)
    got, opt = covered_weight(cover, w, chosen), brute_force(cover, w, k)
    assert got <= opt + 1e-9
    assert got >= (1 - 1 / np.e) * opt - 1e-9  # classic guarantee for monotone submodular coverage
    assert sum(marginal) == pytest.approx(got)
    assert all(m > 0 for m in marginal)


def test_greedy_edge_cases() -> None:
    cover = np.zeros((3, 2), dtype=bool)
    assert bit.greedy_max_coverage(cover, np.ones(2), 2) == ([], [])  # nothing to gain
    assert bit.greedy_max_coverage(np.ones((2, 2), dtype=bool), np.ones(2), 0) == ([], [])
    assert bit.greedy_max_coverage(np.ones((2, 2), dtype=bool), np.ones(2), 9)[0] == [0]  # one pick covers all
    with pytest.raises(ValueError):
        bit.greedy_max_coverage(cover, np.ones(2), -1)
    with pytest.raises(ValueError):
        bit.greedy_max_coverage(cover, np.ones(5), 1)
    with pytest.raises(ValueError):
        bit.greedy_max_coverage(cover, np.array([1.0, -1.0]), 1)


def test_coverage_radius_is_inclusive_of_self_and_excludes_far() -> None:
    lat = np.array([37.5, 37.5 + 200 * DEG_PER_M, 37.5 + 400 * DEG_PER_M])
    lon = np.full(3, 127.0)
    cov = bit.coverage_matrix(lat, lon, lat, lon, 300.0)
    assert cov.tolist() == [[True, True, False], [True, True, True], [False, True, True]]


def test_display_message_fits_two_lines_of_15() -> None:
    for t in (None, 33.0, -5.0, 41.9):
        m = bit.display_message(t)
        assert len(m.lines) <= C.BIT_LINES and all(len(x) <= C.BIT_LINE_CHARS for x in m.lines)
    assert bit.display_message(33.4).lines[0] == "오늘 최고 33℃ 폭염"
    assert bit.display_message(33.4, apparent_max_c=35.6).lines[0] == "오늘 체감 36℃ 폭염"
    assert "폭염" not in bit.display_message(30.0, hot=False).lines[0]


def square(lat0: float, lon0: float, size: float = 0.01) -> np.ndarray:
    return np.array([[lon0, lat0], [lon0 + size, lat0], [lon0 + size, lat0 + size], [lon0, lat0 + size]])


def test_select_bit_stops_weights_by_dong_population() -> None:
    a = Dong("A", "가동", 37.505, 127.005, 600, (square(37.5, 127.0),))
    b = Dong("B", "나동", 37.505, 127.025, 300, (square(37.5, 127.02),))
    z = Dong("Z", "다동", 37.545, 127.005, 0, (square(37.54, 127.0),))
    stops = pd.DataFrame({
        "정류장번호": ["S1", "S2", "S3", "S4", "S5", "S6", "OUT"],
        "정류장명": ["a1", "a2", "a3", "b1", "b2", "z1", "x"],
        "위도": [37.5050, 37.5051, 37.5090, 37.5050, 37.5090, 37.545, 37.7],
        "경도": [127.0050, 127.0051, 127.0090, 127.0250, 127.0290, 127.005, 127.0],
    })
    got, info = bit.select_bit_stops(stops, [a, b, z], 34.0, top_n=3)
    # A has 3 stops -> 200 each; S1+S2 are within 300 m of each other -> 400 for the first pick
    assert [s.stop_id for s in got][0] == "S1" and got[0].covered_pop65_est == 400
    assert [s.stop_id for s in got] == ["S1", "S3", "S4"] or [s.stop_id for s in got] == ["S1", "S3", "S5"]
    assert info.n_candidate_stops == 5 and info.region_pop65_with_stops == 900  # Z has 0 residents, OUT is outside
    assert all(len(s.message.lines) == 2 for s in got)
    assert [s.rank for s in got] == [1, 2, 3]


def test_select_with_no_stops_inside_returns_note() -> None:
    a = Dong("A", "가동", 37.505, 127.005, 600, (square(37.5, 127.0),))
    stops = pd.DataFrame({"정류장번호": ["X"], "정류장명": ["x"], "위도": [38.0], "경도": [128.0]})
    got, info = bit.select_bit_stops(stops, [a], 33.0)
    assert got == [] and info.n_candidate_stops == 0 and info.note.startswith("자료 없음")
