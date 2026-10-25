"""Tests for the heat evaluation: temporal integrity, hist_rate leakage, selection, fast metrics,
체감온도 formula, new rolling splits, train-only calibration.

Run: uv run --project /Users/chloekang/kcf/app/backend python -m pytest experiments/heat/tests -q
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import average_precision_score, roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_panel as bp  # noqa: E402
import evaluate as ev  # noqa: E402
import weather_hub as wh  # noqa: E402
from common import HOT_C, PANEL_PATH, SEASON_END, SEASON_START, TEST_YEARS, YEARS  # noqa: E402


@pytest.fixture(scope="module")
def panel() -> pd.DataFrame:
    return ev.use_family(pd.read_csv(PANEL_PATH, parse_dates=["date"]), "at")


# ---- 체감온도 formula ---------------------------------------------------------------------------
def _stull_scalar(t: float, rh: float) -> float:
    """Independent scalar re-implementation (math module, no numpy) of Stull (2011) eq. 1."""
    return (t * math.atan(0.151977 * math.sqrt(rh + 8.313659)) + math.atan(t + rh) - math.atan(rh - 1.676331)
            + 0.00391838 * rh**1.5 * math.atan(0.023101 * rh) - 4.686035)


def _kma_scalar(t: float, rh: float) -> float:
    tw = _stull_scalar(t, rh)
    return -0.2442 + 0.55399 * tw + 0.45535 * t - 0.0022 * tw * tw + 0.00278 * tw * t + 3.0


def test_stull_published_example() -> None:
    # Stull (2011), J. Appl. Meteor. Climatol. 50, 2267-2269: T = 20 C, RH = 50 % -> Tw = 13.7 C.
    assert float(wh.wet_bulb_stull(20.0, 50.0)) == pytest.approx(13.7, abs=0.05)


def test_stull_close_to_psychrometric_truth() -> None:
    # Stull states error within about -1.0 .. +0.65 C for RH 5-99 %, T -20..50 C. Check against a
    # psychrometric wet-bulb solved by bisection on e(Tw) = e(T,RH) - 0.00066*(1+0.00115*Tw)*P*(T-Tw) (Magnus).
    def es(t: float) -> float:
        return 6.1094 * math.exp(17.625 * t / (t + 243.04))

    def tw_true(t: float, rh: float, p: float = 1013.25) -> float:
        e = rh / 100 * es(t)
        lo, hi = -40.0, t
        for _ in range(80):
            mid = (lo + hi) / 2
            f = es(mid) - 0.00066 * (1 + 0.00115 * mid) * p * (t - mid) - e
            lo, hi = (mid, hi) if f < 0 else (lo, mid)
        return (lo + hi) / 2

    for t, rh in [(20, 50), (30, 40), (33, 60), (35, 70), (28, 90), (25, 20)]:
        assert abs(float(wh.wet_bulb_stull(t, rh)) - tw_true(t, rh)) < 1.0


@pytest.mark.parametrize("t,rh", [(20, 50), (33, 60), (35, 50), (31, 85), (36, 40), (28, 95)])
def test_apparent_temp_matches_scalar_reference(t: float, rh: float) -> None:
    assert float(wh.summer_apparent_temp(t, rh)) == pytest.approx(_kma_scalar(t, rh), abs=1e-9)


def test_apparent_temp_known_values_and_monotonicity() -> None:
    # hand-evaluated KMA polynomial: Ta=33, RH=60 -> Tw=26.31 -> AT=33.45 (rounded to 2 dp)
    assert float(wh.summer_apparent_temp(33.0, 60.0)) == pytest.approx(33.45, abs=0.01)
    rh = np.linspace(30, 90, 13)
    assert (np.diff(wh.summer_apparent_temp(33.0, rh)) > 0).all()  # more humid -> hotter
    ta = np.linspace(25, 38, 14)
    assert (np.diff(wh.summer_apparent_temp(ta, 60.0)) > 0).all()
    assert np.isnan(wh.summer_apparent_temp(np.nan, 50.0))


def test_parse_hub_file_sentinels(tmp_path: Path) -> None:
    line = lambda stn, ta, hm: f"202408011500 {stn} 25 3.3 -9 -9.0 -9 1004.9 1005.2 7 -0.9 {ta} 25.0 {hm} 31.7 -9.0 tail"  # noqa: E731
    p = tmp_path / "202408011500.txt"
    p.write_text("#START7777\n# header\n" + line(108, "34.5", "58.0") + "\n" + line(112, "-99.0", "-9") + "\n#7777END\n", encoding="utf-8")
    d = wh.parse_hub_file(p)
    assert d.loc[d.stn == 108, ["ta", "hm"]].iloc[0].tolist() == [34.5, 58.0]
    assert d.loc[d.stn == 112, ["ta", "hm"]].isna().all(axis=None)
    assert d.hour.unique().tolist() == [15]


def test_daily_max_needs_min_hours_and_shared_hours() -> None:
    rows = [(pd.Timestamp("2024-08-01"), h, 108, ta, hm) for h, ta, hm in
            [(12, 30.0, 60.0), (13, 33.0, 60.0), (14, 35.0, np.nan), (15, 32.0, 70.0)]]
    h = pd.DataFrame(rows, columns=["date", "hour", "stn", "ta", "hm"])
    at, tx, diag = wh.daily_max(h, min_hours=3)
    assert diag["hour_window"] == [12, 15]
    assert tx.iloc[0, 0] == 33.0  # the 35 C hour has no humidity -> excluded from BOTH families (same station-hours)
    assert at.iloc[0, 0] == pytest.approx(max(float(wh.summer_apparent_temp(33, 60)), float(wh.summer_apparent_temp(32, 70))))
    at4, _, _ = wh.daily_max(h, min_hours=4)
    assert at4.isna().all(axis=None)  # 3 valid hours < 4 required -> NaN, never a partial-afternoon max


def test_station_table_sane() -> None:
    s = wh.load_station_coords()
    assert len(s) >= 90 and s.stn.is_unique
    sy = s.set_index("stn").loc[108]
    assert abs(sy.lat - 37.57) < 0.05 and abs(sy.lon - 126.97) < 0.05  # Seoul


# ---- splits / leakage ---------------------------------------------------------------------------
def test_four_rolling_test_years_and_season_window() -> None:
    assert TEST_YEARS == [2022, 2023, 2024, 2025]
    assert min(TEST_YEARS) - 1 >= YEARS[0] + 2  # 2022 trains on 3 seasons (2019-2021)
    assert SEASON_START == (5, 20) and SEASON_END == (9, 25)


def test_max_train_date_before_min_test_date(panel: pd.DataFrame) -> None:
    for T in ev.TEST_YEARS:
        assert panel[panel.year < T].date.max() < panel[panel.year == T].date.min()


def test_panel_season_is_same_window_every_year(panel: pd.DataFrame) -> None:
    md = panel.date.dt.month * 100 + panel.date.dt.day
    assert md.min() == SEASON_START[0] * 100 + SEASON_START[1] and md.max() == SEASON_END[0] * 100 + SEASON_END[1]
    assert panel.groupby("year").date.nunique().nunique() == 1  # identical number of days in every year


def test_hist_rate_is_strictly_prior_years(panel: pd.DataFrame) -> None:
    assert panel[panel.year == 2019].hist_rate.isna().all()
    for T in (2022, 2025):
        prior = panel[panel.year < T].groupby("unit_id").y.sum()
        got = panel[panel.year == T].groupby("unit_id").hist_cases.first()
        assert (prior.reindex(got.index) == got).all()
    assert panel[panel.year == 2022].groupby("unit_id").hist_days.first().nunique() == 1


def test_lags_are_exact_shifts_no_interpolation_lookahead(panel: pd.DataFrame) -> None:
    p = panel.sort_values(["unit_id", "date"])
    g = p.groupby("unit_id")
    same = (g.t_l0.shift(1) == p.t_l1) | p.t_l1.isna() | g.t_l0.shift(1).isna() | (p.doy == g.doy.transform("min"))
    assert same.mean() > 0.99


def test_population_snapshot_is_previous_december() -> None:
    py = bp.load_pop_by_year({"서울특별시|종로구"})
    assert set(py.year) == set(YEARS)
    # season 2019 must use the 2018-12 snapshot (published before the season), so 2019 != 2020 values
    assert py.set_index("year").loc[2019, "pop_y"] != py.set_index("year").loc[2020, "pop_y"]
    snaps = pd.read_csv(bp.POP_SNAP_CSV, dtype={"code": str})
    r = snaps[(snaps.snapshot == "2018-12") & (snaps.name == "서울특별시 종로구")].iloc[0]
    assert py.set_index("year").loc[2019, "pop_y"] == r["pop"]


def test_panel_population_varies_by_year(panel: pd.DataFrame) -> None:
    one = panel[panel.unit_key == panel.unit_key.iloc[0]].groupby("year")["pop"].first()
    assert one.nunique() > 1
    assert panel.pop_from_registry.mean() > 0.95
    assert (panel.old_share.between(0, 1)).all()


def test_b1_is_apparent_temp_rule(panel: pd.DataFrame) -> None:
    # B1 (F) = AT(D)>=33 and (AT(D-1)>=33 or AT(D+1)>=33); NaN never alerts
    exp = ((panel.t_l0 >= HOT_C) & ((panel.t_l1 >= HOT_C) | (panel.t_p1 >= HOT_C))).astype(int)
    assert (exp == panel.b1_F).all()
    expo = ((panel.t_l1 >= HOT_C) & (panel.t_l2 >= HOT_C)).astype(int)
    assert (expo == panel.b1_O).all()


def test_b1_rule_scores_match_panel_rules(panel: pd.DataFrame) -> None:
    te = panel[panel.year == 2025].reset_index(drop=True)
    for v, col in (("F", "b1_F"), ("O", "b1_O")):
        s = ev.rule_scores(te, v)["B1"]
        assert ((np.nan_to_num(s, nan=-1) >= 33).astype(int) == te[col].to_numpy()).all()


def test_use_family_switches_columns() -> None:
    cols = {f"{f}_{k}": [1.0, 2.0] for f in ("at", "tx") for k in ev.T_KEYS}
    df = pd.DataFrame(cols)
    df["at_l0"], df["tx_l0"] = [10.0, 11.0], [20.0, 21.0]
    for r in ev.RULE_COLS:
        df[r], df[f"tx_{r}"] = [1, 0], [0, 1]
    a, t = ev.use_family(df, "at"), ev.use_family(df, "tx")
    assert a.t_l0.tolist() == [10.0, 11.0] and t.t_l0.tolist() == [20.0, 21.0]
    assert a.b1_F.tolist() == [1, 0] and t.b1_F.tolist() == [0, 1]
    assert not [c for c in a.columns if c.startswith(("at_", "tx_"))] and not [c for c in t.columns if c.startswith(("at_", "tx_"))]


# ---- calibration (train-only) -------------------------------------------------------------------
def _inner(rs: dict[int, float]) -> dict[int, dict[str, float]]:
    return {k: {"ratio_OURS": v, "ratio_B2": v} for k, v in rs.items()}


def test_calib_factor_uses_only_inner_folds_before_test_year() -> None:
    inner = _inner({2021: 0.8, 2022: 1.25, 2023: 2.0, 2024: 3.0})
    f2022 = ev.calib_factor(inner, 2022, "OURS")
    assert f2022["method"] == "raw" and f2022["factor"] == 1.0 and f2022["n_inner"] == 1  # no earlier evidence
    assert f2022["last"] == pytest.approx(0.8)
    f2023 = ev.calib_factor(inner, 2023, "OURS")
    assert f2023["n_inner"] == 2 and f2023["last"] == pytest.approx(1.25)
    # changing folds >= T must not change T's factor or method
    inner2 = {**inner, 2023: {"ratio_OURS": 99.0, "ratio_B2": 99.0}, 2024: {"ratio_OURS": 99.0, "ratio_B2": 99.0}}
    assert ev.calib_factor(inner2, 2023, "OURS") == f2023
    assert ev.calib_factor({}, 2022, "OURS")["factor"] == 1.0


def test_calib_method_picks_persistent_bias_and_rejects_shock() -> None:
    persistent = _inner({2021: 1.5, 2022: 1.5, 2023: 1.5})  # a stable under-prediction: last-ratio beats raw
    c = ev.calib_factor(persistent, 2024, "OURS")
    assert c["method"] in ("last", "trend") and c["factor"] == pytest.approx(1.5)
    shock = _inner({2021: 1.0, 2022: 1.0, 2023: 1.0, 2024: 1.0})  # already calibrated: raw wins (ties -> raw)
    assert ev.calib_factor(shock, 2025, "OURS")["method"] == "raw"


def test_calib_factor_is_clipped() -> None:
    f = ev.calib_factor(_inner({2021: 100.0, 2022: 100.0, 2023: 100.0}), 2024, "OURS")
    assert f["factor"] <= ev.CALIB_MAX_FACTOR and f["last_unclipped"] == pytest.approx(100.0)


def test_uniform_rescale_cannot_change_per_day_selection() -> None:
    rng = np.random.default_rng(3)
    score = rng.random(400)
    day, region = np.repeat(np.arange(20), 20), np.tile(np.arange(20), 20)
    k = rng.integers(0, 6, 20)
    assert (ev.select_per_day(score, region, day, k) == ev.select_per_day(score * 1.7, region, day, k)).all()


def test_inner_fold_never_trains_on_its_own_year(panel: pd.DataFrame) -> None:
    small = panel[panel.unit_id < 20]
    S = ev.sido_frame(small, "F")
    feats, sfe = ev.UNIT_FEATS["F"], ev.sido_feats("F")
    for k in (2021, 2022):
        _, _, te, _ = ev.fit_predict_pair(small, S, "F", feats, sfe, k, k)
        assert te.year.eq(k).all()


# ---- selection / metrics ------------------------------------------------------------------------
def test_select_per_day_budget_and_tiebreak() -> None:
    score = np.array([1.0, 1.0, 1.0, 5.0, np.nan, 2.0])
    day = np.array([0, 0, 0, 1, 1, 1])
    region = np.array([2, 0, 1, 0, 1, 2])
    sel = ev.select_per_day(score, region, day, np.array([2, 1]))
    assert sel.tolist() == [False, True, True, True, False, False]  # ties -> lower region code; NaN never chosen


def test_weighted_auc_ap_match_sklearn() -> None:
    rng = np.random.default_rng(0)
    n, nd = 2000, 20
    day = rng.integers(0, nd, n)
    y = (rng.random(n) < 0.1).astype(int)
    s = np.round(rng.normal(size=n) + y, 1)  # ties on purpose
    a, p = ev.RankStruct.make(s, y, day).auc_ap(np.ones((1, nd)))
    assert a[0] == pytest.approx(roc_auc_score(y, s)) and p[0] == pytest.approx(average_precision_score(y, s))
    W = rng.integers(0, 3, (1, nd)).astype(float)
    a, p = ev.RankStruct.make(s, y, day).auc_ap(W)
    w = W[0, day]
    assert a[0] == pytest.approx(roc_auc_score(y, s, sample_weight=w)) and p[0] == pytest.approx(average_precision_score(y, s, sample_weight=w))


def test_block_weights_conserve_days() -> None:
    W = ev.block_weights(129, np.random.default_rng(1), 50, 7)
    assert (W.sum(1) == 129).all()
