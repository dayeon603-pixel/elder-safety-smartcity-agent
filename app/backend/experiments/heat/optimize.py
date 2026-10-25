"""Nested-validation search for a better heat-alert targeting score (idea A). Research code; does NOT touch
out/results.json or evaluate.py defaults. All outputs go to out/opt/.

Backtests are hypotheses, not proofs: nothing here is a claim about deployed performance.

Protocol (per test year Y in 2022..2025, per variant F / O)
  * ALL selection (target encoding, feature groups, hyperparameters, model family, ensemble weight) uses inner rolling
    validation on years < Y only: inner fold k in [max(2021, Y-3) .. Y-1] trains on years < k and is scored on year k.
    Inner score = pooled capture at the per-day B1 budget (k_d = number of B1 alerts that day; B1 is a weather rule, so
    its budget never touches labels).
  * The selected configuration is refit on years < Y and scored ONCE on year Y.
  * Inner folds are identical for every Y (they only depend on k), so they are memoised; this cannot leak because the
    fold-k fit sees years < k only and every target-encoding column of a year-r row uses years < r only.
  * Search is staged (greedy), not exhaustive: A target encoding -> B feature groups (2 greedy rounds) -> C
    hyperparameters (coordinate, then combined) -> D other families (binary HGB, spline Poisson GLM, HEUR grid) ->
    E rank-average ensembles -> F family winner = argmax inner capture (ties: simpler family first).
  * Not available (skipped): LambdaMART/pairwise ranking (no lightgbm/xgboost; sklearn has none), outdoor-worker proxy
    (no data in cache).  A binary-target HGB (>=1 case) is included as the only rank-oriented variant.
  * CIs: circular moving-block bootstrap over days, block 7, 1000 reps, paired across methods.

Run:   optimize.py search --variant F|O     (writes out/opt/sel_<v>.pkl, search_log_<v>.jsonl)
       optimize.py report                   (bootstrap tables -> out/opt/opt_results.json, opt_tables.md)
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import pickle
import time
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.linear_model import PoissonRegressor
from sklearn.preprocessing import SplineTransformer

import evaluate as ev
import weather_hub as wh
from common import GEOJSON, HERE, HOURS, PANEL_PATH, SEED, TEST_YEARS, dump_json, set_seed

logger = logging.getLogger("heat.opt")
OPT_DIR = HERE / "out" / "opt"
METEO_DIR = Path("/Users/chloekang/kcf/data/cache/extra/meteostat_daily")
HOT_C = 33.0
HOT_DAY_C = 30.0  # hot-day target encoding threshold on same-day HISTORICAL observation (past years only)
TROPICAL_NIGHT_C = 25.0
INNER_FIRST = 2021
INNER_MAX_FOLDS = 3
N_BOOT, BLOCK = 1000, 7

# ----------------------------------------------------------------------------------------------------------------
# auxiliary features
# ----------------------------------------------------------------------------------------------------------------
HGB_TE = ("a_d1.0_k130_global", "a_d1.0_k30_global", "a_d1.0_k400_global", "a_d0.7_k30_global", "a_d0.7_k130_global",
          "a_d0.7_k400_global", "a_d0.5_k30_global", "a_d0.5_k130_global", "a_d0.5_k400_global", "a_d1.0_k130_sido",
          "a_d0.7_k130_sido", "h_d1.0_k10", "h_d0.7_k40")  # TE grid for the (expensive) HGB stage; HEUR scans all 22
GROUPS = ("lags", "night", "rh", "dow", "dens")


def _polygon_area_km2(rings: list) -> float:
    """Shoelace area of outer ring minus holes on a local equirectangular projection (km^2)."""
    tot = 0.0
    for i, ring in enumerate(rings):
        a = np.asarray(ring, float)
        x = a[:, 0] * 111.32 * np.cos(np.deg2rad(a[:, 1].mean()))
        y = a[:, 1] * 110.57
        s = 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
        tot += s if i == 0 else -s
    return tot


def unit_area_km2() -> pd.DataFrame:
    """unit_key -> area_km2 from the 2026 행정동 polygons aggregated with build_panel's crosswalk (static geometry)."""
    import build_panel as bp
    d = bp.assign_unit(bp.load_dongs())[["adm_cd2", "unit_key"]]
    gj = json.loads(Path(GEOJSON).read_text(encoding="utf-8"))
    rows = []
    for f in gj["features"]:
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        rows.append((str(f["properties"]["adm_cd2"]), sum(_polygon_area_km2(p) for p in polys)))
    a = pd.DataFrame(rows, columns=["adm_cd2", "area"]).merge(d, on="adm_cd2")
    return a.groupby("unit_key").area.sum().rename("area_km2").reset_index()


def rh_daily() -> pd.DataFrame:
    """date x stn mean RH over the hub hours (HOURS), cached. Same hours as the 체감온도 daily max."""
    path = OPT_DIR / "rh_daily.csv.gz"
    if path.is_file():
        return pd.read_csv(path, index_col=0, parse_dates=True).rename(columns=int)
    cal = pd.date_range("2019-05-17", "2025-09-26", freq="D")
    cal = cal[[(d.month, d.day) >= (5, 17) and (d.month, d.day) <= (9, 26) for d in cal]]
    h = wh.load_hourly(HOURS, cal)
    m = h.dropna(subset=["hm"]).groupby(["date", "stn"]).hm.agg(["mean", "size"])
    m.loc[m["size"] < 7, "mean"] = np.nan
    out = m["mean"].unstack("stn")
    out.columns = out.columns.astype(int)
    out.to_csv(path)
    return out


def tmin_daily() -> tuple[pd.DataFrame, pd.DataFrame]:
    """(date x KMA stn daily Tmin, station table) from meteostat (WMO 47xxx -> KMA stn xxx); stations with <80% coverage dropped."""
    cols = ["date", "tavg", "tmin", "tmax", "prcp", "snow", "wdir", "wspd", "wpgt", "pres", "tsun"]
    series = {}
    for f in sorted(METEO_DIR.glob("47*.csv.gz")):
        d = pd.read_csv(f, header=None, names=cols, parse_dates=["date"]).set_index("date")
        d = d.loc["2019-05-01":"2025-09-30", "tmin"]
        m = d.index.month.isin([6, 7, 8, 9])
        if d[m].notna().mean() >= 0.8:
            series[int(f.name[:5]) - 47000] = d
    T = pd.DataFrame(series)
    coords = wh.load_station_coords().set_index("stn")
    T = T[[c for c in T.columns if c in coords.index]]
    return T, coords.loc[T.columns].rename_axis("stn").reset_index()


def _lagged(M: pd.DataFrame, k: int, dates: np.ndarray, cols: np.ndarray) -> np.ndarray:
    """Value of matrix M (daily index, any columns) at date-k for the given row dates and column labels (NaN if absent)."""
    full = M.reindex(pd.date_range(M.index.min() - pd.Timedelta(days=10), M.index.max(), freq="D"))
    Mk = full.shift(k)
    ri = Mk.index.get_indexer(pd.DatetimeIndex(dates))
    ci = Mk.columns.get_indexer(cols)
    out = np.full(len(dates), np.nan)
    ok = (ri >= 0) & (ci >= 0)
    out[ok] = Mk.to_numpy()[ri[ok], ci[ok]]
    return out


def build_aux(P: pd.DataFrame) -> pd.DataFrame:
    """Causal auxiliary columns aligned to P rows. LOOKAHEAD RISK flags are per column in the comments."""
    A = P.pivot(index="date", columns="unit_id", values="t_l0")  # observed same-day T; lagged use only (k >= 1)
    dates, uid = P.date.to_numpy(), P.unit_id.to_numpy()
    lag = np.vstack([_lagged(A, k, dates, uid) for k in range(1, 8)])  # (7, n)  T(D-1)..T(D-7)
    aux = pd.DataFrame(index=P.index)
    valid = np.isfinite(lag).sum(0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        aux["mean7"] = np.where(valid >= 4, np.nanmean(lag, 0), np.nan)  # D-1..D-7 only, no lookahead
        aux["max7"] = np.where(valid >= 4, np.nanmax(lag, 0), np.nan)
    aux["hot7"] = np.nansum(lag >= HOT_C, 0)
    H = (A >= HOT_C).astype(float).reindex(pd.date_range(A.index.min(), A.index.max(), freq="D")).fillna(0.0)
    ssh = H.groupby(H.index.year).cumsum().shift(1)  # season-to-date hot days through D-1 (no D0)
    aux["ssh"] = ssh.to_numpy()[ssh.index.get_indexer(pd.DatetimeIndex(dates)), ssh.columns.get_indexer(uid)]
    # relative humidity (afternoon mean over HOURS)
    R = rh_daily()
    stn = P.station_id.to_numpy()
    aux["rh_l1"] = _lagged(R, 1, dates, stn)
    aux["rh_l0"] = _lagged(R, 0, dates, stn)  # LOOKAHEAD RISK: same-day obs; F variant only (same assumption as t_l0)
    # night minimum (열대야) from meteostat tmin of nearest covered station
    T, st = tmin_daily()
    u = P.drop_duplicates("unit_id")[["unit_id", "lat", "lon"]].sort_values("unit_id")
    D = wh.haversine_km(u.lat.to_numpy()[:, None], u.lon.to_numpy()[:, None], st.lat.to_numpy()[None], st.lon.to_numpy()[None])
    near = pd.Series(st.stn.to_numpy()[D.argmin(1)], index=u.unit_id.to_numpy())
    nstn = near.reindex(uid).to_numpy()
    aux["tmin_l1"] = _lagged(T, 1, dates, nstn)
    tn = np.vstack([_lagged(T, k, dates, nstn) for k in (1, 2, 3)])
    aux["tn3"] = np.nansum(tn >= TROPICAL_NIGHT_C, 0)
    aux["tmin_l0"] = _lagged(T, 0, dates, nstn)  # LOOKAHEAD RISK: Tmin of day D (dawn of D); F variant only
    aux["night_km"] = pd.Series(D.min(1), index=u.unit_id.to_numpy()).reindex(uid).to_numpy()
    # density / vulnerability
    ar = unit_area_km2().set_index("unit_key").area_km2
    area = P.unit_key.map(ar).to_numpy(float)
    aux["ldens"] = np.log(P["pop"].to_numpy(float) / np.maximum(area, 0.1))
    aux["lold"] = np.log(np.maximum(P["pop"].to_numpy(float) * P.old_share.to_numpy(float), 1.0))
    return aux


def target_encodings(P: pd.DataFrame) -> tuple[dict[str, np.ndarray], list[dict]]:
    """Empirical-Bayes (Poisson-Gamma) shrunk historical rate per (unit, row-year) using years STRICTLY BEFORE the row's year
    (LEAKAGE-safe by construction), with exponential multi-year decay and global or sido prior.
        rate = (sum_w cases + k * prior) / (sum_w days + k),  w = decay^(r-1-y)
    kind 'a' = all days; kind 'h' = days with same-day T >= HOT_DAY_C (historical days only), k in hot days."""
    years = sorted(P.year.unique())
    uids = np.sort(P.unit_id.unique())
    sido = P.drop_duplicates("unit_id").set_index("unit_id").sido.reindex(uids).to_numpy()
    ui = np.searchsorted(uids, P.unit_id.to_numpy())
    yi = np.searchsorted(years, P.year.to_numpy())
    y = P.y.to_numpy(float)
    hot = (P.t_l0.to_numpy(float) >= HOT_DAY_C)
    def tab(w: np.ndarray) -> np.ndarray:
        return np.bincount(ui * len(years) + yi, weights=w, minlength=len(uids) * len(years)).reshape(len(uids), len(years))
    C, N = tab(y), tab(np.ones(len(P)))
    Ch, Nh = tab(y * hot), tab(hot.astype(float))
    sid = pd.factorize(sido)[0]
    out, meta = {}, []
    specs = [("a", d, k, pr) for d in (1.0, 0.7, 0.5) for k in (30, 130, 400) for pr in ("global", "sido")]
    specs += [("h", d, k, "global") for d in (1.0, 0.7) for k in (10, 40)]
    for kind, d, k, pr in specs:
        Cm, Nm = (C, N) if kind == "a" else (Ch, Nh)
        rate = np.full((len(uids), len(years)), np.nan)
        for r in range(1, len(years)):
            w = d ** (r - 1 - np.arange(r))
            num, den = Cm[:, :r] @ w, Nm[:, :r] @ w
            g = num.sum() / den.sum()
            if pr == "sido":
                ns, ds = np.bincount(sid, weights=num), np.bincount(sid, weights=den)
                gs = ((ns + 2000.0 * g) / (ds + 2000.0))[sid]
            else:
                gs = np.full(len(uids), g)
            rate[:, r] = (num + k * gs) / (den + k)
        name = f"{kind}_d{d}_k{k}_{pr}"
        out[name] = rate[ui, yi]
        meta.append(dict(name=name, kind=kind, decay=d, k=k, prior=pr))
    return out, meta


# ----------------------------------------------------------------------------------------------------------------
# configs and models
# ----------------------------------------------------------------------------------------------------------------
HP_DEFAULT = (300, 0.05, 31, None, 20, 0.0)  # max_iter, lr, max_leaf_nodes, max_depth, min_samples_leaf, l2  (== evaluate.HGB_PARAMS)


@dataclass(frozen=True)
class Cfg:
    kind: str  # heur | hgb | hgbbin | glm
    te: str
    groups: tuple[str, ...] = ()
    hp: tuple = HP_DEFAULT
    beta: float = 0.3
    alpha: float = 0.0

    def key(self) -> str:
        return json.dumps([self.kind, self.te, list(self.groups), list(self.hp), self.beta, self.alpha])


def group_cols(variant: str) -> dict[str, list[str]]:
    F = variant == "F"
    return {
        "lags": (["t_l3"] if F else []) + ["mean7", "max7", "hot7", "ssh"],
        "night": ["tmin_l1", "tn3"] + (["tmin_l0"] if F else []),  # tmin_l0: LOOKAHEAD RISK (F only)
        "rh": ["rh_l1"] + (["rh_l0"] if F else []),  # rh_l0: LOOKAHEAD RISK (F only)
        "dow": ["dow"],
        "dens": ["ldens", "lold"],
    }


def feat_cols(variant: str, cfg: Cfg) -> list[str]:
    base = [f"TE::{cfg.te}" if f == "hist_rate" else f for f in ev.UNIT_FEATS[variant]]
    gc = group_cols(variant)
    return base + [c for g in cfg.groups for c in gc[g]]


class GLMModel:
    """Poisson GLM with natural cubic-ish B-splines on temperature/doy, standardised linear terms, missing indicators and
    key-temperature x (65+ share, density, log hist-rate) interactions. L2 via PoissonRegressor(alpha)."""

    def __init__(self, variant: str, cfg: Cfg):
        self.v, self.cfg = variant, cfg
        gc = group_cols(variant)
        self.temps = (["t_l0", "t_l1", "t_3d", "doy"] if variant == "F" else ["t_l1", "t_l2", "t_3d_d1", "doy"])
        self.key = ev.KEY_T[variant]
        streak = "t_streak" if variant == "F" else "t_streak_d1"
        self.lin = ["lpop", "old_share", "lat", "lon", "weekend", streak, "LOGTE"] + [c for g in cfg.groups for c in gc[g]]

    def _raw(self, df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        d = df.copy()
        d["LOGTE"] = np.log(d[f"TE::{self.cfg.te}"].to_numpy(float) + 1e-4)
        return d[self.temps].to_numpy(float), d[self.lin].to_numpy(float)

    def _design(self, df: pd.DataFrame) -> np.ndarray:
        t, l = self._raw(df)
        t = np.where(np.isfinite(t), t, self.med_t)
        ind = np.isnan(l)[:, self.ind_cols].astype(float)
        l = np.where(np.isfinite(l), l, self.med_l)
        z = (l - self.mu_l) / self.sd_l
        S = self.spl.transform(t)
        ik = self.temps.index(self.key)
        zk = (np.clip(t[:, ik], self.lo_k, self.hi_k) - self.mu_k) / self.sd_k
        inter = zk[:, None] * z[:, [self.lin.index(c) for c in self.int_cols]]
        return np.hstack([S, z, ind, inter, zk[:, None] ** 2])

    def fit(self, df: pd.DataFrame, y: np.ndarray) -> "GLMModel":
        t, l = self._raw(df)
        self.med_t, self.med_l = np.nanmedian(t, 0), np.nanmedian(l, 0)
        self.ind_cols = np.flatnonzero(np.isnan(l).any(0))
        ti, li = np.where(np.isfinite(t), t, self.med_t), np.where(np.isfinite(l), l, self.med_l)
        self.mu_l, self.sd_l = li.mean(0), li.std(0) + 1e-9
        ik = self.temps.index(self.key)
        self.lo_k, self.hi_k = np.percentile(ti[:, ik], [0.5, 99.5])
        kk = np.clip(ti[:, ik], self.lo_k, self.hi_k)
        self.mu_k, self.sd_k = kk.mean(), kk.std() + 1e-9
        self.int_cols = [c for c in ("old_share", "lpop", "LOGTE", "ldens") if c in self.lin]
        self.spl = SplineTransformer(n_knots=6, degree=3, knots="quantile", extrapolation="constant").fit(ti)
        X = self._design(df)
        self.m = PoissonRegressor(alpha=self.cfg.alpha, max_iter=500).fit(X, y)
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        return self.m.predict(self._design(df))


def hgb_kwargs(hp: tuple) -> dict:
    it, lr, leaf, depth, msl, l2 = hp
    return dict(max_iter=it, learning_rate=lr, max_leaf_nodes=leaf, max_depth=depth, min_samples_leaf=msl,
                l2_regularization=l2, early_stopping=False, random_state=SEED)


class Search:
    """Holds the panel, memoised (config, fold-year) -> score arrays, and the per-fold budget structures."""

    def __init__(self, P: pd.DataFrame, variant: str):
        self.P, self.v = P, variant
        self.keyT = ev.KEY_T[variant]
        self.cache: dict[tuple[str, int], np.ndarray] = {}
        self.fold: dict[int, dict] = {}
        for k in sorted(P.year.unique()):
            te = P[P.year == k]
            dates = np.sort(te.date.unique())
            day = np.searchsorted(dates, te.date.to_numpy())
            kd = np.bincount(day, weights=(te[ev.RULES[variant]["B1"]] == 1).to_numpy(float), minlength=len(dates)).astype(int)
            self.fold[int(k)] = dict(idx=np.flatnonzero((P.year == k).to_numpy()), day=day, region=te.unit_id.to_numpy(),
                                     y=te.y.to_numpy(float), kd=kd)
        self.log: list[dict] = []
        self.n_fits = 0

    def capture(self, score: np.ndarray, k: int) -> tuple[float, float]:
        f = self.fold[k]
        sel = ev.select_per_day(score, f["region"], f["day"], f["kd"])
        return float((f["y"] * sel).sum()), float(f["y"].sum())

    def scores(self, cfg: Cfg, k: int) -> np.ndarray:
        """Scores for year k from a model trained on years < k (temporal split). Memoised."""
        ck = (cfg.key(), k)
        if ck in self.cache:
            return self.cache[ck]
        P = self.P
        te_idx = self.fold[k]["idx"]
        te = P.iloc[te_idx]
        trm = ((P.year < k) & P[self.keyT].notna()).to_numpy()
        tr = P[trm]
        assert tr.date.max() < te.date.min(), "temporal leakage"
        set_seed(SEED)
        if cfg.kind == "heur":
            s = te[f"TE::{cfg.te}"].to_numpy(float) * np.exp(cfg.beta * te[self.keyT].fillna(float(tr[self.keyT].mean())).to_numpy(float))
        elif cfg.kind == "hgb":
            cols = feat_cols(self.v, cfg)
            s = HistGradientBoostingRegressor(loss="poisson", **hgb_kwargs(cfg.hp)).fit(tr[cols], tr.y.to_numpy()).predict(te[cols])
            self.n_fits += 1
        elif cfg.kind == "hgbbin":
            cols = feat_cols(self.v, cfg)
            m = HistGradientBoostingClassifier(**hgb_kwargs(cfg.hp)).fit(tr[cols], (tr.y.to_numpy() > 0).astype(int))
            s = m.predict_proba(te[cols])[:, 1]
            self.n_fits += 1
        elif cfg.kind == "glm":
            s = GLMModel(self.v, cfg).fit(tr, tr.y.to_numpy()).predict(te)
            self.n_fits += 1
        else:
            raise ValueError(cfg.kind)
        self.cache[ck] = np.asarray(s, float)
        return self.cache[ck]

    def inner(self, cfgs: list[Cfg] | Cfg, folds: list[int], weights: list[float] | None = None) -> float:
        """Pooled inner capture of a config, or of a rank-average ensemble of configs (weights)."""
        cl = [cfgs] if isinstance(cfgs, Cfg) else cfgs
        c = n = 0.0
        for k in folds:
            f = self.fold[k]
            if len(cl) == 1:
                s = self.scores(cl[0], k)
            else:
                s = rank_avg([self.scores(x, k) for x in cl], weights or [1 / len(cl)] * len(cl), f["day"])
            a, b = self.capture(s, k)
            c, n = c + a, n + b
        return c / n

    def evaluate(self, Y: int, stage: str, cfg: Cfg, folds: list[int]) -> float:
        t0 = time.time()
        v = self.inner(cfg, folds)
        self.log.append(dict(test_year=Y, variant=self.v, stage=stage, cfg=json.loads(cfg.key()), inner_folds=folds,
                             inner_capture=v, per_fold={k: self.capture(self.scores(cfg, k), k)[0] / self.capture(self.scores(cfg, k), k)[1] for k in folds},
                             sec=round(time.time() - t0, 2)))
        return v


def rank_avg(scores: list[np.ndarray], w: list[float], day: np.ndarray) -> np.ndarray:
    """Weighted mean of within-day percentile ranks (the per-day budget only uses within-day order)."""
    out = np.zeros(len(day))
    for s, wi in zip(scores, w):
        r = pd.Series(s).groupby(day).rank(pct=True, method="average").to_numpy()
        out += wi * r
    return out


def argmax_cfg(items: list[tuple[Cfg, float]]) -> tuple[Cfg, float]:
    """Highest inner capture; ties -> first in list (list order = simplicity order)."""
    best = items[0]
    for it in items[1:]:
        if it[1] > best[1] + 1e-12:
            best = it
    return best


def run_selection(S: Search, Y: int, te_names: list[str]) -> dict:
    """All selection for test year Y using folds < Y only. Returns the chosen spec."""
    folds = [k for k in range(INNER_FIRST, Y)][-INNER_MAX_FOLDS:]
    ev_ = lambda stage, c: S.evaluate(Y, stage, c, folds)  # noqa: E731
    # A: target encoding with base features, default hp
    te_hgb = [t for t in te_names if t in HGB_TE]
    resA = [(Cfg("hgb", t), ev_("A_te", Cfg("hgb", t))) for t in te_hgb]
    hgbA, _ = argmax_cfg(resA)
    # B: feature groups, greedy up to 2 rounds, accept only on strict inner improvement
    cur, cur_v = hgbA, dict(resA)[hgbA]
    for _ in range(2):
        cand = [(Cfg("hgb", cur.te, tuple(sorted(cur.groups + (g,)))), None) for g in GROUPS if g not in cur.groups]
        cand = [(c, ev_("B_groups", c)) for c, _ in cand]
        b, bv = argmax_cfg(cand)
        if bv > cur_v + 1e-12:
            cur, cur_v = b, bv
        else:
            break
    # C: hyperparameters, coordinate then combined
    base_hp = list(HP_DEFAULT)
    dims = {(0, 1): [(150, 0.1), (500, 0.03)], 2: [8, 63], 4: [100, 400], 5: [10.0]}
    best_per_dim = {}
    for dim, vals in dims.items():
        res = []
        for val in vals:
            hp = list(base_hp)
            if isinstance(dim, tuple):
                hp[0], hp[1] = val
            else:
                hp[dim] = val
            c = Cfg("hgb", cur.te, cur.groups, tuple(hp))
            res.append((c, ev_("C_hp", c)))
        b, bv = argmax_cfg(res)
        if bv > cur_v + 1e-12:
            best_per_dim[dim] = b.hp
    cands = [(cur, cur_v)]
    for dim, hp in best_per_dim.items():
        cands.append((Cfg("hgb", cur.te, cur.groups, hp), S.inner(Cfg("hgb", cur.te, cur.groups, hp), folds)))
    if len(best_per_dim) > 1:
        hp = list(base_hp)
        for dim, h in best_per_dim.items():
            if isinstance(dim, tuple):
                hp[0], hp[1] = h[0], h[1]
            else:
                hp[dim] = h[dim]
        c = Cfg("hgb", cur.te, cur.groups, tuple(hp))
        cands.append((c, ev_("C_hp_combined", c)))
    hgb_star, hgb_v = argmax_cfg(cands)
    # D: other families
    bin_c = Cfg("hgbbin", hgb_star.te, hgb_star.groups, hgb_star.hp)
    bin_v = ev_("D_hgbbin", bin_c)
    glm_res = []
    for groups in ((), hgb_star.groups, tuple(GROUPS)):
        for alpha in (1e-4, 1e-3, 1e-2, 1e-1):
            c = Cfg("glm", hgb_star.te, groups, alpha=alpha)
            glm_res.append((c, ev_("D_glm", c)))
    glm_star, glm_v = argmax_cfg(glm_res)
    heur_res = []
    for t in te_names:
        for beta in (0.2, 0.3, 0.4, 0.5):
            c = Cfg("heur", t, beta=beta)
            heur_res.append((c, ev_("D_heur", c)))
    heur_star, heur_v = argmax_cfg(heur_res)
    # E: ensembles (rank average of family winners)
    fam = {"heur": heur_star, "hgb": hgb_star, "glm": glm_star}
    ens = []
    for w in (0.25, 0.5, 0.75):
        ens.append((("heur", "hgb"), (w, 1 - w)))
    ens += [(("heur", "glm"), (0.5, 0.5)), (("hgb", "glm"), (0.5, 0.5)), (("heur", "hgb", "glm"), (1 / 3, 1 / 3, 1 / 3))]
    ens_res = []
    for names, w in ens:
        v = S.inner([fam[n] for n in names], folds, list(w))
        ens_res.append(((names, w), v))
        S.log.append(dict(test_year=Y, variant=S.v, stage="E_ens", cfg=[names, list(w)], inner_folds=folds, inner_capture=v))
    (ens_spec, ens_v) = max(ens_res, key=lambda t: (t[1], -len(t[0][0])))
    # F: family winner
    finals = [("HEUR*", heur_star, heur_v), ("HGB*", hgb_star, hgb_v), ("HGBbin*", bin_c, bin_v), ("GLM*", glm_star, glm_v)]
    names_all = [f[0] for f in finals] + ["ENS*"]
    vals_all = [f[2] for f in finals] + [ens_v]
    best = int(np.argmax(np.round(vals_all, 12)))
    cur_heur = Cfg("heur", "orig")
    cur_ours = Cfg("hgb", "orig")
    return dict(test_year=Y, folds=folds, heur=heur_star, hgb=hgb_star, hgbbin=bin_c, glm=glm_star, ens=ens_spec,
                inner={n: v for n, v in zip(names_all, vals_all)}, selected=names_all[best],
                inner_current_HEUR=S.inner(cur_heur, folds), inner_current_OURS=S.inner(cur_ours, folds),
                inner_B1_baseline=None)


def spec_scores(S: Search, sel: dict, name: str, Y: int) -> np.ndarray:
    """Final test-year scores (fit on years < Y) for a named finalist."""
    fam = {"heur": sel["heur"], "hgb": sel["hgb"], "glm": sel["glm"]}
    if name == "HEUR*":
        return S.scores(sel["heur"], Y)
    if name == "HGB*":
        return S.scores(sel["hgb"], Y)
    if name == "HGBbin*":
        return S.scores(sel["hgbbin"], Y)
    if name == "GLM*":
        return S.scores(sel["glm"], Y)
    names, w = sel["ens"]
    return rank_avg([S.scores(fam[n], Y) for n in names], list(w), S.fold[Y]["day"])


# ----------------------------------------------------------------------------------------------------------------
# driver
# ----------------------------------------------------------------------------------------------------------------
def load_panel() -> tuple[pd.DataFrame, list[str]]:
    P = ev.use_family(pd.read_csv(PANEL_PATH, parse_dates=["date"]), "at").sort_values(["date", "unit_id"]).reset_index(drop=True)
    aux = build_aux(P)
    P = pd.concat([P, aux], axis=1)
    tes, meta = target_encodings(P)
    # sanity: the (decay 1, k 130, global) encoding must reproduce build_panel's hist_rate exactly
    ref = tes["a_d1.0_k130_global"]
    ok = np.isfinite(ref)
    assert np.allclose(ref[ok], P.hist_rate.to_numpy()[ok], rtol=1e-4, atol=1e-9), "TE reproduction failed"
    assert (np.isfinite(ref) == P.hist_rate.notna().to_numpy()).all()
    TE = pd.DataFrame({f"TE::{k}": v for k, v in tes.items()})
    TE["TE::orig"] = P.hist_rate.to_numpy()
    P = pd.concat([P, TE], axis=1)
    dump_json(meta, OPT_DIR / "te_grid.json")
    return P, list(tes)


def search_main(variant: str) -> None:
    OPT_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    P, te_names = load_panel()
    logger.info("panel+aux ready %.0fs; rows=%d", time.time() - t0, len(P))
    S = Search(P, variant)
    out = {"variant": variant, "years": {}}
    # reference scores: current pipeline (re-fit through the same code path; checked against evaluate.py below)
    for Y in TEST_YEARS:
        ts = time.time()
        sel = run_selection(S, Y, te_names)
        te_scores = {n: spec_scores(S, sel, n, Y) for n in ("HEUR*", "HGB*", "HGBbin*", "GLM*", "ENS*")}
        te_scores["HEUR"] = S.scores(Cfg("heur", "orig"), Y)
        te_scores["OURS"] = S.scores(Cfg("hgb", "orig"), Y)
        te = P.iloc[S.fold[Y]["idx"]]
        # B1 continuous score (rule extension), as in evaluate.rule_scores
        te_scores["B1"] = ev.rule_scores(te, variant)["B1"]
        te_scores["SEL"] = te_scores[sel["selected"]]
        out["years"][Y] = dict(sel={k: (v.__dict__ if isinstance(v, Cfg) else v) for k, v in sel.items()}, scores=te_scores,
                               day=S.fold[Y]["day"], region=S.fold[Y]["region"], y=S.fold[Y]["y"], kd=S.fold[Y]["kd"],
                               b1_alert=(te[ev.RULES[variant]["B1"]] == 1).to_numpy())
        logger.info("%s Y=%d selected=%s inner=%s (%.0fs, fits=%d)", variant, Y, sel["selected"],
                    {k: round(v, 4) for k, v in sel["inner"].items()}, time.time() - ts, S.n_fits)
    out["n_fits"], out["runtime_s"] = S.n_fits, time.time() - t0
    with open(OPT_DIR / f"sel_{variant}.pkl", "wb") as fh:
        pickle.dump(out, fh)
    with open(OPT_DIR / f"search_log_{variant}.jsonl", "w") as fh:
        for r in S.log:
            fh.write(json.dumps(r, default=str, ensure_ascii=False) + "\n")
    logger.info("done %s %.0fs", variant, out["runtime_s"])


# ----------------------------------------------------------------------------------------------------------------
# report
# ----------------------------------------------------------------------------------------------------------------
REPORT_METHODS = ["B1", "HEUR", "OURS", "SEL", "HEUR*", "HGB*", "HGBbin*", "GLM*", "ENS*"]
PAIRS = [("SEL", "B1"), ("SEL", "HEUR"), ("SEL", "OURS"), ("OURS", "HEUR"), ("OURS", "B1"), ("HEUR", "B1"),
         ("HGB*", "HEUR"), ("HGB*", "HEUR*"), ("ENS*", "HEUR")]


def year_sums(d: dict, m: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    nd = len(d["kd"])
    sel = ev.select_per_day(d["scores"][m], d["region"], d["day"], d["kd"])
    return ev.day_sums(sel, d["y"], d["day"], nd)


def report_main() -> None:
    res: dict = {"seed": SEED, "n_boot": N_BOOT, "block": BLOCK, "variants": {}}
    md = []
    for v in ("F", "O"):
        data = pickle.load(open(OPT_DIR / f"sel_{v}.pkl", "rb"))
        parts, perY = [], {}
        for Y in TEST_YEARS:
            d = data["years"][Y]
            nd = len(d["kd"])
            W = ev.block_weights(nd, np.random.default_rng(SEED + Y), N_BOOT, BLOCK)
            cases_d = np.bincount(d["day"], weights=d["y"], minlength=nd)
            sums = {m: year_sums(d, m) for m in REPORT_METHODS}
            parts.append(dict(W=W, cases_d=cases_d, sums=sums))
            perY[Y] = summarize(sums, W, cases_d)
        pooled = summarize_pooled(parts)
        sels = {Y: {k: data["years"][Y]["sel"][k] for k in ("selected", "inner", "folds", "heur", "hgb", "glm", "ens",
                                                              "inner_current_HEUR", "inner_current_OURS")} for Y in TEST_YEARS}
        res["variants"][v] = {"per_year": perY, "pooled": pooled, "selection": sels, "runtime_s": data["runtime_s"], "n_fits": data["n_fits"]}
        md.append(render(v, perY, pooled, sels))
    dump_json(res, OPT_DIR / "opt_results.json")
    (OPT_DIR / "opt_tables.md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md))


def _ci(x: np.ndarray) -> tuple[float, float]:
    return ev.ci(x)


def summarize(sums: dict, W: np.ndarray, cases_d: np.ndarray) -> dict:
    return summarize_pooled([dict(W=W, cases_d=cases_d, sums=sums)])


def summarize_pooled(parts: list[dict]) -> dict:
    n_cases = sum(p["cases_d"].sum() for p in parts)
    nb = sum(p["W"] @ p["cases_d"] for p in parts)
    out: dict = {"methods": {}, "delta": {}}
    boots = {}
    for m in REPORT_METHODS:
        C = sum(p["sums"][m][0].sum() for p in parts)
        A = sum(p["sums"][m][1].sum() for p in parts)
        H = sum(p["sums"][m][2].sum() for p in parts)
        cb = sum(p["W"] @ p["sums"][m][0] for p in parts)
        ab = sum(p["W"] @ p["sums"][m][1] for p in parts)
        hb = sum(p["W"] @ p["sums"][m][2] for p in parts)
        cap, pr = cb / nb, hb / np.maximum(ab, 1e-12)
        boots[m] = (cap, pr)
        out["methods"][m] = dict(alerts=float(A), caught=float(C), capture=[C / n_cases, *_ci(cap)], precision=[H / max(A, 1), *_ci(pr)])
    for a, b in PAIRS:
        dc, dp = boots[a][0] - boots[b][0], boots[a][1] - boots[b][1]
        out["delta"][f"{a}-{b}"] = dict(
            capture=[out["methods"][a]["capture"][0] - out["methods"][b]["capture"][0], *_ci(dc)],
            precision=[out["methods"][a]["precision"][0] - out["methods"][b]["precision"][0], *_ci(dp)],
            p_le_0=float(np.mean(dc <= 0)))
    return out


def fmt(x: list[float], pct: bool = True) -> str:
    return f"{100*x[0]:.1f} [{100*x[1]:.1f}, {100*x[2]:.1f}]"


def render(v: str, perY: dict, pooled: dict, sels: dict) -> str:
    L = [f"\n### Variant {v}: capture % at per-day B1 budget, 95% block-bootstrap CI\n",
         "| method | " + " | ".join(str(y) for y in TEST_YEARS) + " | pooled |", "|---|" + "---|" * (len(TEST_YEARS) + 1)]
    for m in REPORT_METHODS:
        L.append(f"| {m} | " + " | ".join(fmt(perY[Y]['methods'][m]['capture']) for Y in TEST_YEARS) + f" | {fmt(pooled['methods'][m]['capture'])} |")
    L += ["\nPrecision % (alerted cells with >=1 case)\n", "| method | " + " | ".join(str(y) for y in TEST_YEARS) + " | pooled |",
          "|---|" + "---|" * (len(TEST_YEARS) + 1)]
    for m in REPORT_METHODS[:4]:
        L.append(f"| {m} | " + " | ".join(fmt(perY[Y]['methods'][m]['precision']) for Y in TEST_YEARS) + f" | {fmt(pooled['methods'][m]['precision'])} |")
    L += ["\nPaired capture deltas (pp)\n", "| pair | " + " | ".join(str(y) for y in TEST_YEARS) + " | pooled | P(delta<=0) pooled |",
          "|---|" + "---|" * (len(TEST_YEARS) + 2)]
    for a, b in PAIRS:
        k = f"{a}-{b}"
        L.append(f"| {k} | " + " | ".join(fmt(perY[Y]['delta'][k]['capture']) for Y in TEST_YEARS) + f" | {fmt(pooled['delta'][k]['capture'])} | {pooled['delta'][k]['p_le_0']:.3f} |")
    L += ["\nInner-validation choice\n", "| Y | folds | selected | inner capture (family winners) | cur HEUR | cur OURS | HEUR* | HGB* | GLM* | ENS* |", "|---|---|---|---|---|---|---|---|---|---|"]
    for Y in TEST_YEARS:
        s = sels[Y]
        h, g, gl = s["heur"], s["hgb"], s["glm"]
        L.append(f"| {Y} | {s['folds']} | {s['selected']} | " + ", ".join(f"{k}={100*x:.1f}" for k, x in s["inner"].items())
                 + f" | {100*s['inner_current_HEUR']:.1f} | {100*s['inner_current_OURS']:.1f} | {h['te']} b={h['beta']} | "
                 f"{g['te']} {list(g['groups'])} hp={list(g['hp'])} | {gl['te']} {list(gl['groups'])} a={gl['alpha']} | {s['ens'][0]} w={[round(x,2) for x in s['ens'][1]]} |")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("search", "report"))
    ap.add_argument("--variant", choices=("F", "O"))
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    set_seed(SEED)
    OPT_DIR.mkdir(parents=True, exist_ok=True)
    if a.cmd == "search":
        search_main(a.variant)
    else:
        report_main()


if __name__ == "__main__":
    main()
