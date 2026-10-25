"""Route (NN + 2-opt) and crosswalk-extension rules."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.actions import route, signals
from app.actions import constants as C
from app.actions.dongs import Dong
from app.actions.models import SignalsNote


def random_dist(seed: int, n: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    pts = [(37.4 + float(rng.random()) * 0.2, 126.8 + float(rng.random()) * 0.3) for _ in range(n)]
    return route.distance_matrix_km(pts)


@pytest.mark.parametrize("seed", range(30))
def test_two_opt_improves_or_equals_nn(seed: int) -> None:
    dist = random_dist(seed, 12)
    nn = route.nearest_neighbor(dist, 0)
    opt = route.two_opt(nn, dist)
    assert sorted(opt) == list(range(12)) and opt[0] == 0
    assert route.path_length(opt, dist) <= route.path_length(nn, dist) + 1e-9


def test_two_opt_fixes_a_crossing() -> None:
    # indices 1 and 2 sit in swapped positions on a line
    pts = [(37.0, 127.0), (37.0, 127.02), (37.0, 127.01), (37.0, 127.03)]
    dist = route.distance_matrix_km(pts)
    assert route.two_opt([0, 1, 2, 3], dist) == [0, 2, 1, 3]


def test_nn_validation_and_trivial_sizes() -> None:
    dist = random_dist(1, 3)
    with pytest.raises(ValueError):
        route.nearest_neighbor(dist, 5)
    assert route.two_opt([0], dist[:1, :1]) == [0]
    assert route.two_opt([0, 1], dist[:2, :2]) == [0, 1]


def dong(code: str, lat: float, lon: float, pop: int) -> Dong:
    return Dong(code, f"d{code}", lat, lon, pop, ())


def test_plan_route_starts_at_largest_and_reports_savings() -> None:
    ds = [dong("1", 37.50, 127.00, 900), dong("2", 37.50, 127.10, 800), dong("3", 37.50, 127.01, 700),
          dong("4", 37.50, 127.11, 600), dong("5", 37.60, 127.0, 0)]
    stops, km, km_plain = route.plan_route(ds, 4)
    assert [s.dong_code for s in stops] == ["1", "3", "2", "4"]  # zero-population dong never used
    assert stops[0].leg_km == 0.0 and [s.order for s in stops] == [1, 2, 3, 4]
    assert km == pytest.approx(sum(s.leg_km for s in stops), abs=0.01)
    assert km < km_plain  # visiting in pure 65+ order zigzags
    assert route.DISTANCE_BASIS == "직선거리 기준"


def test_plan_route_empty_and_single() -> None:
    assert route.plan_route([], 5) == ([], 0.0, 0.0)
    s, km, _ = route.plan_route([dong("1", 37.5, 127.0, 10)], 5)
    assert len(s) == 1 and km == 0.0


def test_extension_seconds_formula() -> None:
    assert signals.extension_seconds(21, 20) == 10  # 21/0.7 = 30
    assert signals.extension_seconds(14, 20) == 0  # exactly 20 s needed
    assert signals.extension_seconds(14.1, 20) == 1  # 20.14 -> ceil(0.14)
    assert signals.extension_seconds(7, 40) == 0
    with pytest.raises(ValueError):
        signals.extension_seconds(0, 10)
    with pytest.raises(ValueError):
        signals.extension_seconds(10, 10, 0)


SQUARE = np.array([[127.0, 37.5], [127.1, 37.5], [127.1, 37.6], [127.0, 37.6]])
REGION = [Dong("A", "가동", 37.55, 127.05, 100, (SQUARE,))]


def xw(no: str, lat: float, lon: float, length: float | None, green: float | None, light: str = "Y") -> dict[str, object]:
    return dict(
        CTPRVN_NM="경기도", SIGNGU_NM="x", LNMADR="지번", RDNMADR=None, CRSLK_MANAGE_NO=no, LATITUDE=lat,
        LONGITUDE=lon, ET=length, TFCLGHT_YN=light, GREEN_SGNGNR_TIME=green,
    )


def test_recommend_filters_and_orders() -> None:
    df = pd.DataFrame([
        xw("c1", 37.55, 127.05, 21, 20),  # short by 10 s
        xw("c2", 37.55, 127.06, 28, 20),  # short by 20 s
        xw("c3", 37.55, 127.07, 7, 20),  # fine
        xw("c4", 37.55, 127.08, 30, 20, light="N"),  # no signal: not evaluated
        xw("c5", 37.55, 127.09, 30, 0),  # green missing
        xw("c6", 37.99, 127.05, 40, 10),  # outside region
        xw("c7", 37.55, 127.01, 500, 10),  # implausible length
        xw("c8", float("nan"), 127.05, 21, 20),  # no coordinate
    ])
    items, cov = signals.recommend_extensions(df, REGION, sido_name="경기도")
    assert isinstance(items, list)
    assert [(i.crosswalk_id, i.extend_s) for i in items] == [("c2", 20), ("c1", 10)]
    assert items[0].required_green_s == 40.0 and items[0].address == "지번"
    assert (cov.n_crosswalks_in_region, cov.n_evaluable, cov.n_short) == (6, 3, 2)
    assert "전국 전수가 아니다" in cov.note


def test_recommend_limit_and_none_short() -> None:
    df = pd.DataFrame([xw(f"c{i}", 37.55, 127.01 + i / 100, 21 + i, 20) for i in range(5)])
    items, _ = signals.recommend_extensions(df, REGION, limit=2)
    assert isinstance(items, list) and len(items) == 2 and items[0].extend_s >= items[1].extend_s
    ok = pd.DataFrame([xw("c", 37.55, 127.05, 7, 30)])
    items2, cov2 = signals.recommend_extensions(ok, REGION)
    assert items2 == [] and cov2.n_evaluable == 1 and cov2.n_short == 0


def test_recommend_no_data_notes() -> None:
    none_in = pd.DataFrame([xw("c", 38.5, 127.05, 21, 20)])
    n1, c1 = signals.recommend_extensions(none_in, REGION)
    assert isinstance(n1, SignalsNote) and "자료 없음" in n1.note and c1.n_crosswalks_in_region == 0
    no_green = pd.DataFrame([xw("c", 37.55, 127.05, 21, None)])
    n2, _ = signals.recommend_extensions(no_green, REGION)
    assert isinstance(n2, SignalsNote) and "녹색신호시간" in n2.note


def test_cap_warning_only_at_cap() -> None:
    df = pd.DataFrame([xw("c", 37.55, 127.05, 21, 20)])
    assert "상한" not in signals.recommend_extensions(df, REGION)[1].note
    assert C.CROSSWALK_ROW_CAP == 50_000 and math.isfinite(C.WALK_SPEED_M_S)
