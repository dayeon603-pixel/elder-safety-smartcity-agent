"""Rolling-origin evaluation of risk-ranked heat-illness alerts vs the 33 C rule (idea A).

Hypothesis under test (a backtest is a hypothesis, not a proof): at the SAME alert budget, ranking
시군구-days by a Poisson model captures more heat-illness cases than the 2026 특보 rule
(일최고체감온도 >= 33 C on 2+ consecutive days).

Protocol
  * folds: test year T in {2022, 2023, 2024, 2025}; train on every year 2019..T-1 (temporal split only).
  * weather family: `at` (daily max 체감온도, primary) or `tx` (daily max 기온 on the same station-hours; sensitivity,
    `--family tx` writes results_tmax_sensitivity.json only and is never persisted).
  * count calibration: a train-only factor (forward-chained: model fit on years < T-1 predicts T-1; factor = observed /
    predicted of T-1; plus a log-linear year trend) is computed for B2/OURS and reported, but NOT applied
    (APPLY_CALIBRATION = False; it overshoots 2024). A uniform factor could not change any within-day ranking anyway.
  * seeds: python/numpy/sklearn all 42, set before any model fit or resample.
  * methods: B1 (2 consecutive hot days), B1S (single day), B2 (sido-level Poisson HGB split by population),
    HEUR (hist_rate*exp(0.3*Tmax), 0.3 fixed a priori), OURS (HGB Poisson, no tuning).
  * budgets: per-day (k_d = #B1 alerts that day; primary, tie-break by region code), season-total
    (oracle allocation across days, upper bound), and a per-day budget-scale sweep.
  * variants: F (obs to D-1 + Tmax(D) + Tmax(D+1) as forecast proxy) and O (observations to D-1 only).
  * CIs: circular moving-block bootstrap over days (block 7, 1000 reps, seed 42), shared replicates
    across methods so differences are paired.

Run:  uv run --project /Users/chloekang/kcf/app/backend python experiments/heat/evaluate.py
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import average_precision_score, roc_auc_score

from common import (
    COVERAGE_PATH, CROSSWALK_PATH, GEOJSON, HEAT_CSV, HERE, HOT_C, HOURS, OUT_DIR, PANEL_PATH, POOL_LABEL, POP_CSV,
    POP_SNAP_CSV, PRED_PATH, RESULTS_PATH, SEED, SENS_PATH, TEST_YEARS, dump_json, git_hash, set_seed, sha256_file,
)
import weather_hub as wh

logger = logging.getLogger("heat.eval")

N_BOOT = 1000
BLOCK = 7
BLOCK_SENS = (1, 14)
ALPHA = 0.05
SWEEP_SCALES = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
HEUR_BETA = 0.3  # fixed a priori (not fit)
HGB_PARAMS = dict(loss="poisson", max_iter=300, learning_rate=0.05, early_stopping=False, random_state=SEED)  # no tuning

# Variants. F = obs up to D-1 + Tmax(D) + Tmax(D+1) as forecast proxy (LOOKAHEAD RISK: perfect-forecast assumption,
# applies to ALL F-variant methods incl. B1). O = observations up to D-1 only (no lookahead).
VARIANTS = ["F", "O"]
# t_* = the selected weather family (at = 체감온도, tx = 기온); see use_family().
UNIT_FEATS = {
    "F": ["t_l0", "t_l1", "t_l2", "t_p1", "t_3d", "t_streak", "doy", "weekend", "lpop", "old_share",
          "hist_rate", "lat", "lon"],
    "O": ["t_l1", "t_l2", "t_l3", "t_3d_d1", "t_streak_d1", "doy", "weekend", "lpop", "old_share",
          "hist_rate", "lat", "lon"],
}
KEY_T = {"F": "t_l0", "O": "t_l1"}
SIDO_MEAN = {"F": ["t_l0", "t_l1", "t_l2", "t_p1", "t_3d", "t_streak"],
             "O": ["t_l1", "t_l2", "t_l3", "t_3d_d1", "t_streak_d1"]}
RULE_COLS = ("b1_F", "b1s_F", "b1_O", "b1s_O")
T_KEYS = ("l0", "l1", "l2", "l3", "p1", "3d", "3d_d1", "streak", "streak_d1")
CALIB_MIN_FACTOR, CALIB_MAX_FACTOR = 0.25, 4.0  # guard rails; a factor outside this range is reported, not silently used
# Train-only count calibration is MEASURED but NOT APPLIED. Evidence (first full run): the recent-year / trend factor
# fixed no year and broke 2024 (OURS predicted/observed 0.97 -> 1.73 last / 2.10 trend) because the factor of year T-1 is
# measured on a model that never saw T-1, while the year-T model is refit on T-1. See results.json calibration.
APPLY_CALIBRATION = False
FIRST_INNER_YEAR = 2021  # first year that can be predicted by a model trained on >= 2 earlier seasons
RULES = {"F": {"B1": "b1_F", "B1S": "b1s_F"}, "O": {"B1": "b1_O", "B1S": "b1s_O"}}
METHODS = ["B1", "B1S", "B2", "HEUR", "OURS"]
BUDGETS = ["B1", "B1S"]  # B1 = main; B1S = sensitivity
BUDGET_KINDS = ("per_day", "season_total")


# ------------------------------------------------------------------------------------------------
# scores
# ------------------------------------------------------------------------------------------------
def rule_scores(df: pd.DataFrame, variant: str) -> dict[str, np.ndarray]:
    """Continuous extension of each rule (alert iff score >= 33 C, identical to the panel rule columns)."""
    l0, l1, l2, p1 = (df[c].to_numpy(float) for c in ("t_l0", "t_l1", "t_l2", "t_p1"))
    if variant == "F":
        # LOOKAHEAD RISK: l0 and p1 are same-day / next-day observations used as forecast proxies.
        b1 = np.minimum(l0, np.fmax(l1, p1))
        b1[np.isnan(l0) | (np.isnan(l1) & np.isnan(p1))] = np.nan
        return {"B1": b1, "B1S": l0}
    b1 = np.minimum(l1, l2)
    return {"B1": b1, "B1S": l1}


def use_family(P: pd.DataFrame, fam: str) -> pd.DataFrame:
    """Expose weather family `fam` ('at' | 'tx') under generic t_* names and b1_* rule columns."""
    if fam not in ("at", "tx"):
        raise ValueError(fam)
    other = "tx" if fam == "at" else "at"
    out = P.drop(columns=[c for c in P.columns if c.startswith(f"{other}_") and c[len(other) + 1:] in T_KEYS])
    out = out.rename(columns={f"{fam}_{k}": f"t_{k}" for k in T_KEYS})
    if fam == "tx":
        for r in RULE_COLS:
            out[r] = out[f"tx_{r}"]
    return out.drop(columns=[f"tx_{r}" for r in RULE_COLS])


def fit_hgb(X: pd.DataFrame, y: np.ndarray) -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(**HGB_PARAMS).fit(X, y)


def predict_heur(df: pd.DataFrame, tcol: str, tfill: float) -> np.ndarray:
    """HEUR = hist_rate * exp(0.3 * Tmax). Missing Tmax filled with the train mean (no future info)."""
    return df["hist_rate"].to_numpy(float) * np.exp(HEUR_BETA * df[tcol].fillna(tfill).to_numpy(float))


def sido_frame(P: pd.DataFrame, variant: str) -> pd.DataFrame:
    """Aggregate unit-days to 시도-days (pop-weighted means; features see the same info time as the unit model)."""
    cols = SIDO_MEAN[variant]
    w = P["pop"].to_numpy(float)
    G = pd.DataFrame({"sido": P.sido, "date": P.date, "year": P.year, "y": P.y, "pop": P["pop"],
                      "doy": P.doy, "weekend": P.weekend, "hist_rate": P.hist_rate,
                      "w_lat": P.lat * w, "w_lon": P.lon * w, "w_old": P.old_share * w})
    for c in cols:
        ok = P[c].notna().to_numpy()
        G[f"w_{c}"] = np.where(ok, P[c].fillna(0).to_numpy() * w, 0.0)
        G[f"n_{c}"] = np.where(ok, w, 0.0)
    G[f"mx_{cols[0]}"] = P[cols[0]]
    agg = {"y": "sum", "pop": "sum", "doy": "first", "weekend": "first", "year": "first",
           "hist_rate": lambda s: s.sum(min_count=len(s)), "w_lat": "sum", "w_lon": "sum", "w_old": "sum",
           f"mx_{cols[0]}": "max"}
    for c in cols:
        agg[f"w_{c}"] = "sum"
        agg[f"n_{c}"] = "sum"
    S = G.groupby(["sido", "date"], sort=True).agg(agg).reset_index()
    for c in cols:
        S[f"s_{c}"] = S[f"w_{c}"] / S[f"n_{c}"].replace(0, np.nan)
    S["s_lat"], S["s_lon"], S["s_old"] = S.w_lat / S["pop"], S.w_lon / S["pop"], S.w_old / S["pop"]
    S["s_lpop"] = np.log(S["pop"])
    S["s_hist"] = S["hist_rate"]
    S["s_max"] = S[f"mx_{cols[0]}"]
    S["pop_sido"] = S["pop"]
    return S


def sido_feats(variant: str) -> list[str]:
    return [f"s_{c}" for c in SIDO_MEAN[variant]] + ["s_max", "doy", "weekend", "s_lpop", "s_old", "s_hist", "s_lat", "s_lon"]


# ------------------------------------------------------------------------------------------------
# selection and metrics
# ------------------------------------------------------------------------------------------------
def select_per_day(score: np.ndarray, region: np.ndarray, day: np.ndarray, k: np.ndarray) -> np.ndarray:
    """Top-k_d rows per day by score (NaN never selected). Deterministic tie-break: lower region code first."""
    s = np.where(np.isfinite(score), score, -np.inf)
    order = np.lexsort((region, -s, day))
    d_sorted = day[order]
    first = np.r_[0, np.flatnonzero(np.diff(d_sorted)) + 1]
    start = np.repeat(first, np.diff(np.r_[first, len(d_sorted)]))
    rank = np.arange(len(order)) - start
    sel_sorted = (rank < k[d_sorted]) & np.isfinite(s[order])
    sel = np.zeros(len(score), bool)
    sel[order] = sel_sorted
    return sel


def select_global(score: np.ndarray, region: np.ndarray, K: int) -> np.ndarray:
    """Season-total budget: top-K across all days (oracle allocation across days; secondary analysis only)."""
    s = np.where(np.isfinite(score), score, -np.inf)
    order = np.lexsort((region, -s))[:K]
    sel = np.zeros(len(score), bool)
    sel[order] = np.isfinite(s[order])
    return sel


def day_sums(sel: np.ndarray, y: np.ndarray, day: np.ndarray, nd: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-day (cases caught, alerts, alerts with >=1 case)."""
    return (np.bincount(day, weights=y * sel, minlength=nd), np.bincount(day, weights=sel.astype(float), minlength=nd),
            np.bincount(day, weights=((y > 0) & sel).astype(float), minlength=nd))


def block_weights(nd: int, rng: np.random.Generator, reps: int, block: int) -> np.ndarray:
    """Circular moving-block bootstrap over days -> multiplicity matrix (reps x nd)."""
    nb = int(np.ceil(nd / block))
    starts = rng.integers(0, nd, size=(reps, nb))
    idx = ((starts[:, :, None] + np.arange(block)[None, None, :]) % nd).reshape(reps, -1)[:, :nd]
    W = np.zeros((reps, nd))
    np.add.at(W, (np.repeat(np.arange(reps), nd), idx.ravel()), 1.0)
    return W


def ci(x: np.ndarray) -> tuple[float, float]:
    lo, hi = np.nanpercentile(x, [100 * ALPHA / 2, 100 * (1 - ALPHA / 2)])
    return float(lo), float(hi)


@dataclass
class RankStruct:
    """Tie-grouped descending order for weighted AUC / average precision under bootstrap weights."""
    day_sorted: np.ndarray
    ysorted: np.ndarray
    starts: np.ndarray

    @staticmethod
    def make(score: np.ndarray, yb: np.ndarray, day: np.ndarray) -> "RankStruct":
        ok = np.isfinite(score)
        s = np.where(ok, score, np.nanmin(score[ok]) - 1.0)  # NaN-score rows rank last (finite so diff() has no NaN)
        order = np.argsort(-s, kind="stable")
        ss = s[order]
        starts = np.r_[0, np.flatnonzero(np.diff(ss)) + 1]
        return RankStruct(day[order], yb[order].astype(float), starts)

    def auc_ap(self, W: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Weighted ROC-AUC (ties = 0.5) and average precision (sklearn step definition) per bootstrap row."""
        out_auc, out_ap = [], []
        for i in range(0, W.shape[0], 100):
            Wr = W[i : i + 100][:, self.day_sorted]
            P = np.add.reduceat(Wr * self.ysorted, self.starts, axis=1)
            N = np.add.reduceat(Wr * (1 - self.ysorted), self.starts, axis=1)
            Pt, Nt = P.sum(1, keepdims=True), N.sum(1, keepdims=True)
            cP, cN = np.cumsum(P, 1), np.cumsum(N, 1)
            auc = (P * (Nt - cN + 0.5 * N)).sum(1) / (Pt[:, 0] * Nt[:, 0])
            ap = ((P / Pt) * (cP / np.maximum(cP + cN, 1e-12))).sum(1)
            out_auc.append(auc)
            out_ap.append(ap)
        return np.concatenate(out_auc), np.concatenate(out_ap)


def stat_block(est: float, boot: np.ndarray) -> dict[str, float]:
    lo, hi = ci(boot)
    return {"est": float(est), "lo": lo, "hi": hi}




# ------------------------------------------------------------------------------------------------
# main evaluation
# ------------------------------------------------------------------------------------------------
def fit_predict_pair(P: pd.DataFrame, S_all: pd.DataFrame, variant: str, feats: list[str], sfe: list[str], train_lt: int,
                     pred_year: int) -> tuple[np.ndarray, np.ndarray, pd.DataFrame, HistGradientBoostingRegressor]:
    """Fit OURS (unit HGB) and B2 (시도 HGB) on years < train_lt; predict year `pred_year` unit rows.

    Returns (ours, b2, te, unit model). te = the pred_year rows with lam_sido merged. Temporal split only.
    """
    set_seed(SEED)  # seed before every fit
    tr = P[(P.year < train_lt) & P[KEY_T[variant]].notna()]
    te = P[P.year == pred_year].reset_index(drop=True)
    assert tr.date.max() < te.date.min(), "temporal leakage: train overlaps test"
    m = fit_hgb(tr[feats], tr.y.to_numpy())
    ours = m.predict(te[feats])
    Str = S_all[(S_all.year < train_lt) & S_all[sfe[0]].notna()]
    assert Str.date.max() < S_all[S_all.year == pred_year].date.min()
    m2 = fit_hgb(Str[sfe], Str.y.to_numpy())
    Ste = S_all[S_all.year == pred_year][["sido", "date"] + sfe + ["pop_sido"]].copy()
    Ste["lam_sido"] = m2.predict(Ste[sfe])
    te = te.merge(Ste[["sido", "date", "lam_sido", "pop_sido"]], on=["sido", "date"], how="left")
    # B2: sido-level Poisson HGB, expected count split to 시군구 by population share
    b2 = (te.lam_sido * te["pop"] / te.pop_sido).to_numpy()
    return ours, b2, te, m


def inner_ratios(P: pd.DataFrame, S_all: pd.DataFrame, variant: str, feats: list[str], sfe: list[str]) -> dict[int, dict[str, float]]:
    """Forward-chained train-only calibration ratios: for k >= FIRST_INNER_YEAR, fit on years < k, predict k,
    ratio_k = observed cases / predicted cases (rows with a valid key weather value, as in training)."""
    out: dict[int, dict[str, float]] = {}
    for k in range(FIRST_INNER_YEAR, max(TEST_YEARS)):
        ours, b2, te, _ = fit_predict_pair(P, S_all, variant, feats, sfe, k, k)
        ok = te[KEY_T[variant]].notna().to_numpy() & np.isfinite(b2)
        y = te.y.to_numpy(float)
        out[k] = {"observed": float(y[ok].sum()), "OURS": float(ours[ok].sum()), "B2": float(b2[ok].sum()),
                  "ratio_OURS": float(y[ok].sum() / ours[ok].sum()), "ratio_B2": float(y[ok].sum() / b2[ok].sum())}
    return out


def _trend_factor(ks: list[int], logr: np.ndarray, target: int) -> float:
    """Log-linear (year-trend) extrapolation of the ratio series to year `target` (>= 2 points, else last value)."""
    if len(ks) < 2:
        return float(logr[-1])
    b, a = np.polyfit(np.array(ks, float) - target, logr, 1)
    return float(a)  # value at x = target (b*0)


def calib_factor(inner: dict[int, dict[str, float]], T: int, model: str) -> dict:
    """Train-only count calibration for test year T.

    Candidates (all use inner folds k <= T-1 only): `raw` (factor 1), `last` (ratio of year T-1 = recent-year
    rescale), `trend` (log-linear year-trend extrapolation of the ratios). The method is picked by its error on the
    earlier inner folds k in [FIRST_INNER_YEAR+1, T-1] (what it would have predicted for k from folds < k); with no
    earlier evidence (T = 2022) it stays `raw`. Factor is clipped to [CALIB_MIN_FACTOR, CALIB_MAX_FACTOR].
    """
    ks_all = [k for k in sorted(inner) if k <= T - 1]
    clip = lambda x: float(np.clip(x, CALIB_MIN_FACTOR, CALIB_MAX_FACTOR))  # noqa: E731
    if not ks_all:
        return {"method": "raw", "factor": 1.0, "last": 1.0, "trend": 1.0, "n_inner": 0, "inner_errors": {}}
    logr = {k: float(np.log(inner[k][f"ratio_{model}"])) for k in ks_all}
    last_u = float(np.exp(logr[ks_all[-1]]))
    trend_u = float(np.exp(_trend_factor(ks_all, np.array([logr[k] for k in ks_all]), T - 1 + 1)))
    errs: dict[str, list[float]] = {"raw": [], "last": [], "trend": []}
    for k in ks_all:
        prev = [j for j in ks_all if j < k]
        if not prev:
            continue  # nothing earlier to predict k from
        errs["raw"].append(abs(logr[k]))
        errs["last"].append(abs(logr[k] - logr[prev[-1]]))
        errs["trend"].append(abs(logr[k] - _trend_factor(prev, np.array([logr[j] for j in prev]), k)))
    mean_err = {m: (float(np.mean(v)) if v else None) for m, v in errs.items()}
    method = "raw"
    if all(v is not None for v in mean_err.values()):
        method = min(mean_err, key=lambda m: (mean_err[m], ("raw", "last", "trend").index(m)))
    factor = {"raw": 1.0, "last": clip(last_u), "trend": clip(trend_u)}[method]
    return {"method": method, "factor": factor, "last": clip(last_u), "trend": clip(trend_u), "last_unclipped": last_u,
            "trend_unclipped": trend_u, "n_inner": len(ks_all), "inner_errors": mean_err}


def run_variant(P: pd.DataFrame, variant: str, hist_col: str = "hist_rate") -> dict:
    """Fit all models per fold. Training rows: years < T; test rows: year T (temporal split only)."""
    feats = [hist_col if f == "hist_rate" else f for f in UNIT_FEATS[variant]]
    S_all = sido_frame(P.assign(hist_rate=P[hist_col]), variant)
    sfe = sido_feats(variant)
    inner = inner_ratios(P, S_all, variant, feats, sfe)
    folds = {}
    for T in TEST_YEARS:
        ours_raw, b2_raw, te, m = fit_predict_pair(P, S_all, variant, feats, sfe, T, T)
        tr = P[(P.year < T) & P[KEY_T[variant]].notna()]
        heur = predict_heur(te.assign(hist_rate=te[hist_col]), KEY_T[variant], float(tr[KEY_T[variant]].mean()))
        f_o, f_b = calib_factor(inner, T, "OURS"), calib_factor(inner, T, "B2")
        sc = rule_scores(te, variant)
        # adopted counts = raw (APPLY_CALIBRATION False); a train-only factor, if applied, is uniform within a test year -> rankings unchanged
        k_b, k_o = (f_b["factor"], f_o["factor"]) if APPLY_CALIBRATION else (1.0, 1.0)
        sc.update({"B2": b2_raw * k_b, "HEUR": heur, "OURS": ours_raw * k_o})
        raw = {"B2": b2_raw, "OURS": ours_raw}
        folds[T] = dict(te=te, scores=sc, raw=raw, n_train=len(tr), hgb_n_iter=int(m.n_iter_),
                        calib={"OURS": f_o, "B2": f_b}, train_max_date=str(tr.date.max().date()),
                        test_min_date=str(te.date.min().date()))
    return {"folds": folds, "inner": inner}


def summarize(parts: list[dict]) -> tuple[dict, dict, dict]:
    """Metrics from per-day sums. parts: [{W, cases_d, sums{m:(c,a,h)}}] one per year (fold) or several (pooled).
    Returns (per-method stats, bootstrap arrays, paired deltas a-b for all ordered pairs)."""
    n_cases = sum(p["cases_d"].sum() for p in parts)
    nb = sum(p["W"] @ p["cases_d"] for p in parts)
    res, boot = {}, {}
    for m in METHODS:
        C = sum(p["sums"][m][0].sum() for p in parts)
        A = sum(p["sums"][m][1].sum() for p in parts)
        H = sum(p["sums"][m][2].sum() for p in parts)
        cb = sum(p["W"] @ p["sums"][m][0] for p in parts)
        ab = sum(p["W"] @ p["sums"][m][1] for p in parts)
        hb = sum(p["W"] @ p["sums"][m][2] for p in parts)
        capb, prb, cpab = cb / nb, hb / np.maximum(ab, 1e-12), cb / np.maximum(ab, 1e-12)
        res[m] = {"alerts": float(A), "caught": float(C), "alerts_with_case": float(H),
                  "capture": stat_block(C / n_cases, capb),
                  "precision": stat_block(H / max(A, 1), prb),  # share of alerted cells with >=1 case
                  "cases_per_alert": stat_block(C / max(A, 1), cpab)}
        boot[m] = (capb, prb)
    delta = {}
    for a in METHODS:
        for b in METHODS:
            if a == b:
                continue
            dc = boot[a][0] - boot[b][0]
            dp = boot[a][1] - boot[b][1]
            delta[f"{a}-{b}"] = {
                "capture": stat_block(res[a]["capture"]["est"] - res[b]["capture"]["est"], dc),
                "precision": stat_block(res[a]["precision"]["est"] - res[b]["precision"]["est"], dp),
                "boot_share_capture_delta_le_0": float(np.mean(dc <= 0)),
            }
    return res, boot, delta


def evaluate_fold(variant: str, te: pd.DataFrame, scores: dict[str, np.ndarray], Wd: np.ndarray) -> dict:
    dates = np.sort(te.date.unique())
    day = np.searchsorted(dates, te.date.to_numpy())
    nd = len(dates)
    y = te.y.to_numpy(float)
    region = te.unit_id.to_numpy()
    yb = (y > 0).astype(int)
    cases_d = np.bincount(day, weights=y, minlength=nd)
    alerts_rule = {k: (te[c] == 1).to_numpy() for k, c in RULES[variant].items()}
    out: dict = {"n_rows": int(len(te)), "n_days": int(nd), "n_cases": float(y.sum()), "n_pos_rows": int(yb.sum()),
                 "base_rate_cell_has_case": float(yb.mean()), "budget": {}, "rank": {}}
    # --- rank metrics (budget-free; label = >=1 case) ---------------------------------------------------
    rb = {}
    for m in METHODS:
        s = scores[m]
        ok = np.isfinite(s)
        sf = np.where(ok, s, np.nanmin(s[ok]) - 1.0)
        e_auc, e_ap = roc_auc_score(yb, sf), average_precision_score(yb, sf)
        rs = RankStruct.make(s, yb, day)
        a1, p1 = rs.auc_ap(np.ones((1, nd)))
        assert abs(a1[0] - e_auc) < 1e-9 and abs(p1[0] - e_ap) < 1e-9, "fast weighted AUC/AP != sklearn"
        ab, pb = rs.auc_ap(Wd)
        out["rank"][m] = {"auc": stat_block(e_auc, ab), "pr_auc": stat_block(e_ap, pb)}
        rb[m] = (ab, pb)
    out["rank_delta_OURS_minus"] = {
        m0: {"auc": stat_block(out["rank"]["OURS"]["auc"]["est"] - out["rank"][m0]["auc"]["est"], rb["OURS"][0] - rb[m0][0]),
             "pr_auc": stat_block(out["rank"]["OURS"]["pr_auc"]["est"] - out["rank"][m0]["pr_auc"]["est"], rb["OURS"][1] - rb[m0][1])}
        for m0 in METHODS if m0 != "OURS"}
    # --- budgets -----------------------------------------------------------------------------------------
    sel_store, raw = {}, {}
    for bsrc in BUDGETS:
        k_d = np.bincount(day, weights=alerts_rule[bsrc].astype(float), minlength=nd).astype(int)
        K = int(k_d.sum())
        for kind in BUDGET_KINDS:
            sums = {}
            for m in METHODS:
                sel = select_per_day(scores[m], region, day, k_d) if kind == "per_day" else select_global(scores[m], region, K)
                sums[m] = day_sums(sel, y, day, nd)
                sel_store[(bsrc, kind, m)] = sel
            res, _, delta = summarize([{"W": Wd, "cases_d": cases_d, "sums": sums}])
            out["budget"][f"{bsrc}|{kind}"] = {"methods": res, "delta": delta, "budget_alerts_total": K,
                                               "alert_share_of_cells": K / len(te)}
            raw[f"{bsrc}|{kind}"] = sums
    return {"out": out, "raw": raw, "cases_d": cases_d, "sel": sel_store, "day": day}


def sweep_fold(variant: str, te: pd.DataFrame, scores: dict[str, np.ndarray]) -> dict:
    """Per-day budget-scale sweep (0.25x..2x of B1) + literal B1 threshold sweep."""
    dates = np.sort(te.date.unique())
    day = np.searchsorted(dates, te.date.to_numpy())
    nd = len(dates)
    y = te.y.to_numpy(float)
    region = te.unit_id.to_numpy()
    k_d = np.bincount(day, weights=(te[RULES[variant]["B1"]] == 1).to_numpy().astype(float), minlength=nd)
    out = {"scales": SWEEP_SCALES, "curves": {}, "b1_threshold_curve": []}
    for m in METHODS:
        out["curves"][m] = []
        for sc in SWEEP_SCALES:
            sel = select_per_day(scores[m], region, day, np.rint(sc * k_d).astype(int))
            out["curves"][m].append({"scale": sc, "alert_share": float(sel.mean()), "capture": float((y * sel).sum() / y.sum())})
    s2 = scores["B1"]
    for thr in np.arange(28.0, 36.01, 0.5):
        sel = np.isfinite(s2) & (s2 >= thr)
        out["b1_threshold_curve"].append({"thr": float(thr), "alert_share": float(sel.mean()), "capture": float((y * sel).sum() / y.sum())})
    return out


def calibration(folds: dict, inner: dict) -> dict:
    """Predicted vs observed counts for the two count models (B2, OURS), raw and train-only recalibrated.
    HEUR is a ranking score, not a count. ratio = predicted total / observed total (1.0 = calibrated in total)."""
    out: dict = {"per_year_total": {}, "inner_folds": inner, "bins": {}, "apply_calibration": APPLY_CALIBRATION}
    allp = {m: [] for m in ("B2", "OURS")}
    ally = []
    for T, f in folds.items():
        y = f["te"].y.to_numpy(float)
        ok = np.isfinite(f["raw"]["OURS"]) & np.isfinite(f["raw"]["B2"])
        row = {"observed": float(y.sum()), "factors": f["calib"], "adopted": "calibrated" if APPLY_CALIBRATION else "raw"}
        for m in allp:
            row[f"{m}_raw"] = float(np.nansum(f["raw"][m]))
            row[m] = float(np.nansum(f["scores"][m]))
            row[f"{m}_raw_over_obs"] = row[f"{m}_raw"] / row["observed"]
            row[f"{m}_over_obs"] = row[m] / row["observed"]
            row[f"{m}_last_over_obs"] = row[f"{m}_raw"] * f["calib"][m]["last"] / row["observed"]  # diagnostic
            row[f"{m}_trend_over_obs"] = row[f"{m}_raw"] * f["calib"][m]["trend"] / row["observed"]  # diagnostic
        out["per_year_total"][str(T)] = row
        ally.append(y)
        for m in allp:
            allp[m].append(f["scores"][m])
    y = np.concatenate(ally)

    def bins_for(p: np.ndarray) -> dict:
        ok = np.isfinite(p)
        p, yy = p[ok], y[ok]
        edges = np.unique(np.quantile(p, [0, .5, .7, .8, .9, .95, .98, .99, .995, 1.0]))
        b = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, len(edges) - 2)
        rows = []
        for i in range(len(edges) - 1):
            mk = b == i
            if mk.sum():
                rows.append({"n": int(mk.sum()), "mean_pred": float(p[mk].mean()), "mean_obs": float(yy[mk].mean()),
                             "se_obs": float(np.sqrt(max(yy[mk].mean(), 1e-12) / mk.sum()))})
        pc = np.clip(p, 1e-9, None)
        dev = float(np.mean(2 * (np.where(yy > 0, yy * np.log(np.where(yy > 0, yy, 1) / pc), 0) - (yy - pc))))
        return {"rows": rows, "mean_poisson_deviance_pooled": dev}

    for m in allp:
        out["bins"][m] = bins_for(np.concatenate(allp[m]))
    return out


def sha_manifest(paths: list[Path]) -> str:
    """SHA-256 over sorted 'name:sha256' lines (one hash for a directory of many cached responses)."""
    import hashlib
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(f"{p.name}:{sha256_file(p)}\n".encode())
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", choices=("at", "tx"), default="at", help="at = 체감온도 (primary), tx = 기온 sensitivity")
    args = ap.parse_args()
    primary = args.family == "at"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    set_seed(SEED)
    t0 = time.time()
    P = use_family(pd.read_csv(PANEL_PATH, parse_dates=["date"]), args.family).sort_values(["date", "unit_id"]).reset_index(drop=True)
    rng = np.random.default_rng(SEED)
    results: dict = {"seed": SEED, "git_hash": git_hash(), "n_boot": N_BOOT, "block_days": BLOCK, "hot_c": HOT_C,
                     "weather_family": args.family, "weather_hours_kst": HOURS,
                     "heur_beta": HEUR_BETA, "test_years": TEST_YEARS, "hgb_params": HGB_PARAMS,
                     "variants_doc": {"F": "obs to D-1 + value(D) + value(D+1) as perfect-forecast proxy (LOOKAHEAD RISK)",
                                      "O": "observations up to D-1 only"},
                     "rule_doc": ("B1 = 일최고체감온도 >= 33 C on 2+ consecutive days (2026 특보 criterion)" if primary
                                  else "B1 = daily max 기온 >= 33 C on 2+ consecutive days (old Tmax proxy; sensitivity)"),
                     "ci": "95% percentile; circular moving-block bootstrap over days; draws shared across methods (paired)",
                     "variants": {}}
    pred_frames = []
    for variant in VARIANTS:
        rv = run_variant(P, variant)
        folds = rv["folds"]
        fold_out, parts_by_key, sweeps = {}, {}, {}
        for T in TEST_YEARS:
            f = folds[T]
            nd = f["te"].date.nunique()
            Wd = block_weights(nd, rng, N_BOOT, BLOCK)
            fe = evaluate_fold(variant, f["te"], f["scores"], Wd)
            o = fe["out"]
            o["model"] = {"n_train_rows": f["n_train"], "hgb_n_iter": f["hgb_n_iter"], "train_max_date": f["train_max_date"],
                          "test_min_date": f["test_min_date"], "calibration_factors": f["calib"]}
            sens = {}
            sums = fe["raw"]["B1|per_day"]
            for L in BLOCK_SENS:
                WL = block_weights(nd, np.random.default_rng(SEED + L), N_BOOT, L)
                dd = (WL @ sums["OURS"][0] - WL @ sums["B1"][0]) / (WL @ fe["cases_d"])
                sens[f"block_{L}"] = stat_block(o["budget"]["B1|per_day"]["delta"]["OURS-B1"]["capture"]["est"], dd)
            o["block_sensitivity_OURS_minus_B1_capture"] = sens
            fold_out[T] = o
            for key, sm in fe["raw"].items():
                parts_by_key.setdefault(key, []).append({"W": None, "cases_d": fe["cases_d"], "sums": sm, "nd": nd})
            sweeps[T] = sweep_fold(variant, f["te"], f["scores"])
            te = f["te"]
            if primary:
                for kind in BUDGET_KINDS:
                    for m in METHODS:
                        pred_frames.append(pd.DataFrame({
                            "method": m, "variant": variant, "test_year": T, "budget_kind": kind, "date": te.date.dt.strftime("%Y-%m-%d"),
                            "region_code": te.unit_id.to_numpy(), "score": f["scores"][m],
                            "alerted": fe["sel"][("B1", kind, m)].astype(np.int8)}))
            logger.info("%s %s %d done (%.0fs)", args.family, variant, T, time.time() - t0)
        # pooled test years: independent block draws per year, shared across methods
        Ws = {T: block_weights(fold_out[T]["n_days"], rng, N_BOOT, BLOCK) for T in TEST_YEARS}
        pooled = {}
        for key, parts in parts_by_key.items():
            for T, p in zip(TEST_YEARS, parts):
                p["W"] = Ws[T]
            res, _, delta = summarize(parts)
            pooled[key] = {"methods": res, "delta": delta}
        results["variants"][variant] = {"folds": {str(T): fold_out[T] for T in TEST_YEARS}, f"pooled_{POOL_LABEL}": pooled,
                                        "sweep": {str(T): sweeps[T] for T in TEST_YEARS}, "calibration": calibration(folds, rv["inner"])}
    if not primary:
        results["runtime_s"] = time.time() - t0
        dump_json(results, SENS_PATH)
        logger.info("sensitivity written %s", SENS_PATH)
        return
    results["controls"] = controls(P)
    hub_files = sorted(wh.HUB_CACHE.glob("*.txt"))
    results["data_sha256"] = {
        "heat_illness_csv": sha256_file(HEAT_CSV), "population_csv_2026": sha256_file(POP_CSV),
        "population_snapshots_by_year_csv": sha256_file(POP_SNAP_CSV),
        "boundaries_geojson": sha256_file(GEOJSON),
        "asos_station_table": sha256_file(wh.STN_PRIMARY),
        "kma_hub_hourly_manifest": {"n_files": len(hub_files), "sha256": sha_manifest(hub_files)},
        "panel_csv_gz": sha256_file(PANEL_PATH), "crosswalk_csv": sha256_file(CROSSWALK_PATH)}
    results["coverage_report"] = json.loads(COVERAGE_PATH.read_text())
    results["code_sha256"] = {n: sha256_file(HERE / n) for n in ("build_panel.py", "evaluate.py", "common.py", "weather_hub.py")}
    results["runtime_s"] = time.time() - t0
    dump_json(results, RESULTS_PATH)
    pd.concat(pred_frames).to_csv(PRED_PATH, index=False, float_format="%.5g")
    write_table(results)
    logger.info("done %.0fs", time.time() - t0)


def controls(P: pd.DataFrame) -> dict:
    """Negative control (shuffled train labels) and a deliberate-leakage demo. Neither feeds any reported result."""
    set_seed(SEED)
    T, v = max(TEST_YEARS), "F"
    feats = UNIT_FEATS[v]
    tr = P[(P.year < T) & P.t_l0.notna()]
    te = P[P.year == T].reset_index(drop=True)
    dates = np.sort(te.date.unique())
    day = np.searchsorted(dates, te.date.to_numpy())
    k_d = np.bincount(day, weights=te.b1_F.to_numpy(float), minlength=len(dates)).astype(int)
    y, region = te.y.to_numpy(float), te.unit_id.to_numpy()
    out = {"year": T, "variant": v, "alert_share_b1": float(te.b1_F.mean())}
    m = fit_hgb(tr[feats], np.random.default_rng(SEED).permutation(tr.y.to_numpy()))
    sel = select_per_day(m.predict(te[feats]), region, day, k_d)
    out["negative_control_shuffled_train_labels_capture"] = float((y * sel).sum() / y.sum())
    leak = P.groupby("unit_id").y.mean().rename("hist_leak")  # LEAKAGE RISK: uses test-year labels. Demo only.
    tr2, te2 = tr.join(leak, on="unit_id"), te.join(leak, on="unit_id")
    f2 = ["hist_leak" if f == "hist_rate" else f for f in feats]
    m2 = fit_hgb(tr2[f2], tr2.y.to_numpy())
    sel2 = select_per_day(m2.predict(te2[f2]), region, day, k_d)
    out["LEAKY_hist_rate_over_all_years_capture__DO_NOT_REPORT"] = float((y * sel2).sum() / y.sum())
    # chance reference for the per-day budget: random picks within each day (expected capture, analytic).
    chance = {}
    for vv in VARIANTS:
        col = RULES[vv]["B1"]
        for TT in TEST_YEARS:
            d = P[P.year == TT]
            g = d.groupby("date").agg(n=("y", "size"), k=(col, "sum"), c=("y", "sum"))
            chance[f"{vv}_{TT}"] = float((g.c * g.k / g.n).sum() / g.c.sum())
    out["random_within_day_expected_capture_at_B1_budget"] = chance
    return out


def write_table(results: dict) -> None:
    rows = []
    for variant, vr in results["variants"].items():
        items = [(T, fr["budget"], fr["rank"]) for T, fr in vr["folds"].items()] + [(POOL_LABEL.replace("_", "-"), vr[f"pooled_{POOL_LABEL}"], None)]
        for T, budget, rank in items:
            for key, b in budget.items():
                for m, r in b["methods"].items():
                    d = b["delta"].get(f"{m}-B1", {}).get("capture", {}) if m != "B1" else {}
                    rk = (rank or {}).get(m, {})
                    rows.append(dict(variant=variant, year=T, budget=key, method=m, alerts=r["alerts"], caught=r["caught"],
                                     capture=r["capture"]["est"], cap_lo=r["capture"]["lo"], cap_hi=r["capture"]["hi"],
                                     precision=r["precision"]["est"], prec_lo=r["precision"]["lo"], prec_hi=r["precision"]["hi"],
                                     cases_per_alert=r["cases_per_alert"]["est"],
                                     auc=rk.get("auc", {}).get("est"), pr_auc=rk.get("pr_auc", {}).get("est"),
                                     delta_vs_B1_capture=d.get("est"), delta_lo=d.get("lo"), delta_hi=d.get("hi")))
    pd.DataFrame(rows).to_csv(OUT_DIR / "results_table.csv", index=False)


if __name__ == "__main__":
    main()
