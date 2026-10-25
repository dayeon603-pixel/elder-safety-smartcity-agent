"""Rolling-origin targeting backtest: which top-K 시군구 should receive 주택용 소방시설 / 점검 at equal budget.

Target: next-year residential-fire casualties (사망+부상) per 시군구 (counts). Metric: capture = share of the
test year's casualties that fall in the selected K 시군구, at equal K. Backtests are hypotheses, not proofs.
Features use only information through year Y-1 (fires of Y-1 and earlier, Dec(Y-1) population).
# LOOKAHEAD RISK: none intended. 발화요인 (cause) is deliberately NOT used: it is an investigation outcome.
Run: uv run --project /Users/chloekang/kcf/app/backend python experiments/fire/run.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from scipy.special import gammaln
from scipy.stats import spearmanr
from sklearn.linear_model import PoissonRegressor

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from data import (K_FRACS, MIN_HIST_FIRES, N_BOOT, OUT_DIR, SEED, TEST_YEARS, YEARS, build_panel, dump_json,  # noqa: E402
                  set_seed, sha256_file, FIRE_CSV, POP_CSV)

logger = logging.getLogger("fire.run")
GLM_ALPHA = 1.0  # fixed a priori (ridge on standardized features); not tuned on test years
METHODS = ["RAND", "B1_fires", "B1_pop", "B2_pop65", "B3_cas", "G_glm", "E_eb", "OURS"]
LABEL = {"RAND": "무작위(기대값)", "B1_fires": "B1 전년 주거화재 건수", "B1_pop": "B1' 인구", "B2_pop65": "B2 65세+ 인구",
         "B3_cas": "B3 전년 인명피해", "G_glm": "공변량 모형(축소 없음)", "E_eb": "축소 추정만(인구 기준)",
         "OURS": "우리: 공변량×경험적 베이즈"}


def features(panel: pd.DataFrame, units: list[str], t: int) -> pd.DataFrame:
    """Features for predicting year t, using information through year t-1 only."""
    h = panel[panel.year <= t - 1]
    agg = h.groupby("unit").agg(hf=("fires", "sum"), hc=("cas", "sum"), hs=("single", "sum"))
    nyr = t - YEARS[0]
    last = panel[panel.year == t - 1].set_index("unit")
    pop = last["pop"].reindex(units)
    pop65 = last["pop65"].reindex(units)
    a = agg.reindex(units).fillna(0)
    s0 = a.hs.sum() / max(a.hf.sum(), 1)
    out = pd.DataFrame(index=units)
    out["pop"], out["pop65"] = pop, pop65
    out["lf"] = last["fires"].reindex(units).fillna(0)
    out["lc"] = last["cas"].reindex(units).fillna(0)
    out["hist_fires"], out["hist_cas"] = a.hf, a.hc
    out["f_rate"] = a.hf / nyr  # mean residential fires / yr
    out["single_share"] = (a.hs + 5 * s0) / (a.hf + 5)  # housing-fire propensity: shrunk 단독·기타주택 share
    out["share65"] = pop65 / pop
    X = pd.DataFrame({"log_pop65": np.log(pop65), "share65": out.share65, "log_frate": np.log1p(out.f_rate),
                      "single_share": out.single_share}, index=units)
    return out.join(X[["log_pop65", "log_frate"]])


FEATS = ["log_pop65", "share65", "log_frate", "single_share"]


def nb_k(y: np.ndarray, m: np.ndarray) -> float:
    """Marginal-ML gamma shape k of the Poisson-Gamma (NB) model: y~Pois(m*theta), theta~Gamma(k, k)."""

    def nll(lk: float) -> float:
        k = np.exp(lk)
        return -float(np.sum(gammaln(y + k) - gammaln(k) - gammaln(y + 1) + k * np.log(k / (k + m))
                             + y * np.log(m / (k + m))))

    r = minimize_scalar(nll, bounds=(-3, 8), method="bounded")
    return float(np.exp(r.x))


def fit_predict(panel: pd.DataFrame, units: list[str], Y: int) -> tuple[pd.DataFrame, dict]:
    """Fit on target years 2021..Y-1 (features through t-1), predict year Y. Returns per-unit method scores."""
    tr = []
    for t in range(YEARS[0] + 1, Y):
        F = features(panel, units, t)
        F["y"] = panel[panel.year == t].set_index("unit")["cas"].reindex(units).values
        F["t"] = t
        tr.append(F)
    T = pd.concat(tr)
    mu, sd = T[FEATS].mean(), T[FEATS].std(ddof=0)
    glm = PoissonRegressor(alpha=GLM_ALPHA / len(T), max_iter=2000).fit(((T[FEATS] - mu) / sd).values, T.y.values)
    T["m"] = glm.predict(((T[FEATS] - mu) / sd).values)
    FY = features(panel, units, Y)
    FY["m"] = glm.predict(((FY[FEATS] - mu) / sd).values)
    # EB: history of casualties vs GLM expectation over training targets
    H = T.groupby(level=0).agg(y=("y", "sum"), m=("m", "sum"))
    H = H.reindex(units)
    k = nb_k(H.y.values, H.m.values)
    theta = (k + H.y) / (k + H.m)
    FY["theta"] = theta.values
    # EB with population-only prior mean (no covariates): m0 = c * pop65-free exposure = total pop
    ex = T.groupby(level=0)["pop"].sum().reindex(units)
    c = H.y.sum() / ex.sum()
    k0 = nb_k(H.y.values, (c * ex).values)
    theta0 = (k0 + H.y) / (k0 + c * ex)
    S = pd.DataFrame(index=units)
    S["RAND"] = 0.0
    S["B1_fires"] = FY.lf
    S["B1_pop"] = FY["pop"]
    S["B2_pop65"] = FY.pop65
    S["B3_cas"] = FY.lc
    S["G_glm"] = FY.m
    S["E_eb"] = (c * FY["pop"] * theta0).values
    S["OURS"] = FY.m * FY.theta
    info = {"k_gamma": k, "k_gamma_pop_only": k0, "n_train_rows": len(T),
            "glm_coef_std": dict(zip(FEATS, map(float, glm.coef_))), "n_hist_years": Y - YEARS[0] - 1}
    FY["flag_low_data"] = FY.hist_fires < MIN_HIST_FIRES * (Y - YEARS[0]) / 4  # >=10 fires per 4 yrs equivalent
    return S.join(FY[["pop", "pop65", "share65", "theta", "m", "hist_fires", "hist_cas", "flag_low_data"]]), info


def select(score: np.ndarray, K: int, rng: np.random.Generator) -> np.ndarray:
    """Top-K indices; ties broken by a seeded random permutation (no tie advantage from row order)."""
    perm = rng.permutation(len(score))
    order = perm[np.lexsort((np.arange(len(score)), -score[perm]))]
    return order[:K]


def capture(y: np.ndarray, sel: np.ndarray) -> float:
    return float(y[sel].sum() / y.sum())


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    set_seed(SEED)
    panel = build_panel()
    units = sorted(panel.unit.unique())
    N = len(units)
    rng = np.random.default_rng(SEED)
    boot_idx = rng.integers(0, N, size=(N_BOOT, N))  # one cluster-bootstrap draw reused for all methods (paired)
    res: dict = {"meta": {"n_units": N, "years": YEARS, "test_years": TEST_YEARS, "seed": SEED,
                          "glm_alpha": GLM_ALPHA, "min_hist_fires_rule": MIN_HIST_FIRES, "n_boot": N_BOOT,
                          "sha256_fire_csv": sha256_file(FIRE_CSV), "sha256_pop_csv": sha256_file(POP_CSV),
                          "note": "Backtest = hypothesis, not proof. Target = residential 사망+부상 per 시군구-year. "
                                  "Primary method OURS fixed a priori; others are ablations/baselines. "
                                  "Costs/slippage are n/a (allocation, not trading)."},
                 "folds": {}}
    sels: dict = {}
    ys: dict = {}
    scores: dict = {}
    for Y in TEST_YEARS:
        S, info = fit_predict(panel, units, Y)
        y = panel[panel.year == Y].set_index("unit")["cas"].reindex(units).values.astype(float)
        ys[Y], scores[Y] = y, S
        fold = {"info": info, "total_cas": float(y.sum()), "n_units_with_cas": int((y > 0).sum()), "K": {}}
        for fr in K_FRACS:
            K = int(np.ceil(fr * N))
            fk = {"K": K, "methods": {}}
            for m in METHODS:
                if m == "RAND":
                    sel = None
                    est = K / N
                else:
                    sel = select(S[m].values, K, np.random.default_rng(SEED + Y))
                    est = capture(y, sel)
                sels[(Y, fr, m)] = sel
                fk["methods"][m] = {"capture": est}
            sel_or = np.argsort(-y, kind="stable")[:K]
            fk["oracle_capture"] = capture(y, sel_or)
            fold["K"][str(fr)] = fk
        res["folds"][str(Y)] = fold
    # bootstrap (cluster = 시군구), per fold and pooled; paired differences vs B1_fires and vs best simple baseline
    def boot_cap(Ys: list[int], fr: float, m: str) -> np.ndarray:
        if m == "RAND":
            return np.full(N_BOOT, np.nan)
        num = np.zeros(N_BOOT)
        den = np.zeros(N_BOOT)
        for Y in Ys:
            ind = np.zeros(N)
            ind[sels[(Y, fr, m)]] = 1.0
            yb = ys[Y][boot_idx]
            num += (yb * ind[boot_idx]).sum(1)
            den += yb.sum(1)
        return num / den

    def point(Ys: list[int], fr: float, m: str) -> float:
        if m == "RAND":
            return float(np.ceil(fr * N) / N)
        return sum(ys[Y][sels[(Y, fr, m)]].sum() for Y in Ys) / sum(ys[Y].sum() for Y in Ys)

    def ci(a: np.ndarray) -> list[float]:
        return [float(np.nanpercentile(a, 2.5)), float(np.nanpercentile(a, 97.5))]

    groups = {str(Y): [Y] for Y in TEST_YEARS}
    groups["pooled_2022_2024"] = list(TEST_YEARS)
    for gname, Ys in groups.items():
        out = {}
        for fr in K_FRACS:
            o = {"methods": {}, "diff_vs": {}}
            B = {m: boot_cap(Ys, fr, m) for m in METHODS}
            for m in METHODS:
                o["methods"][m] = {"capture": point(Ys, fr, m), "ci95": ci(B[m]) if m != "RAND" else None}
            for base in ["B1_fires", "B1_pop", "B2_pop65", "B3_cas"]:
                for m in ["G_glm", "E_eb", "OURS"]:
                    d = B[m] - B[base]
                    o["diff_vs"][f"{m}-{base}"] = {"pp": 100 * (point(Ys, fr, m) - point(Ys, fr, base)),
                                                    "ci95_pp": [100 * v for v in ci(d)],
                                                    "p_boot_le0": float((d <= 0).mean())}
            best = max(["B1_fires", "B1_pop", "B2_pop65", "B3_cas"], key=lambda b: point(Ys, fr, b))
            o["best_simple_baseline"] = best
            d = B["OURS"] - B[best]
            o["diff_vs"]["OURS-best_simple"] = {"base": best, "pp": 100 * (point(Ys, fr, "OURS") - point(Ys, fr, best)),
                                                 "ci95_pp": [100 * v for v in ci(d)], "p_boot_le0": float((d <= 0).mean())}
            out[str(fr)] = o
        res.setdefault("boot", {})[gname] = out
    # rank stability: top-K Jaccard / Spearman between consecutive test-year selections, same method
    stab = {}
    for fr in K_FRACS:
        for m in METHODS[1:]:
            js, rs = [], []
            for Y in TEST_YEARS[1:]:
                a, b = set(sels[(Y - 1, fr, m)]), set(sels[(Y, fr, m)])
                js.append(len(a & b) / len(a | b))
                rs.append(float(spearmanr(scores[Y - 1][m], scores[Y][m]).statistic))
            stab[f"{fr}|{m}"] = {"jaccard_topK_consecutive": js, "spearman_consecutive": rs}
    res["rank_stability"] = stab
    # split-half reliability of the casualty history signal: random halves of last-year's casualties (person-level thinning)
    r2 = np.random.default_rng(SEED)
    sh = {}
    for Y in TEST_YEARS:
        yl = panel[panel.year == Y - 1].set_index("unit")["cas"].reindex(units).values.astype(int)
        j = []
        for _ in range(200):
            a = r2.binomial(yl, 0.5)
            b = yl - a
            K = int(np.ceil(0.1 * N))
            sa, sb = set(select(a + 1e-3 * r2.random(N), K, r2)), set(select(b + 1e-3 * r2.random(N), K, r2))
            j.append(len(sa & sb) / len(sa | sb))
        sh[str(Y)] = {"jaccard_top10pct_halves_mean": float(np.mean(j)), "p2.5": float(np.percentile(j, 2.5)),
                      "p97.5": float(np.percentile(j, 97.5))}
    res["split_half_B3"] = sh
    # data sufficiency
    res["data_sufficiency"] = {str(Y): {"n_flag_low_data": int(scores[Y].flag_low_data.sum()),
                                        "median_units_cas": float(np.median(ys[Y])),
                                        "share_units_zero_cas": float((ys[Y] == 0).mean())} for Y in TEST_YEARS}
    dump_json(res, OUT_DIR / "results.json")
    # map-ready CSV for the latest fold + forward-looking 2025 targeting (train through 2024)
    Sx, info = fit_predict_forward(panel, units)
    Sx.to_csv(OUT_DIR / "map_targeting_2025.csv", encoding="utf-8-sig")
    dump_json({"info": info}, OUT_DIR / "forward_info.json")
    for Y in TEST_YEARS:
        scores[Y].assign(actual_cas=ys[Y]).to_csv(OUT_DIR / f"scores_{Y}.csv", encoding="utf-8-sig")
    logger.info("done")


def fit_predict_forward(panel: pd.DataFrame, units: list[str]) -> tuple[pd.DataFrame, dict]:
    """Score for the NEXT (unobserved) year 2025 using all data through 2024; same recipe as the backtest."""
    ext = panel.copy()
    z = ext[ext.year == 2024].copy()
    z["year"] = 2025
    z["fires"], z["cas"], z["single"], z["deaths"] = 0, 0, 0, 0  # placeholder target rows; 2025 pop not available
    ext = pd.concat([ext, z])  # features(…,2025) read pop of 2024 (t-1) only, target 2025 is placeholder
    S, info = fit_predict(ext, units, 2025)
    N = len(units)
    out = S[["pop", "pop65", "share65", "hist_fires", "hist_cas", "flag_low_data"]].copy()
    out["score_expected_cas_2025"] = S["OURS"]
    out["score_b1_fires_2024"] = S["B1_fires"]
    out["rank"] = out.score_expected_cas_2025.rank(ascending=False, method="first").astype(int)
    for fr in K_FRACS:
        out[f"recommended_top{int(fr * 100)}pct"] = out["rank"] <= int(np.ceil(fr * N))
    out["recommended"] = out["recommended_top20pct"]
    out.index.name = "unit"
    out["sido"] = [u.split("|")[0] for u in out.index]
    out["sigungu"] = [u.split("|")[1] for u in out.index]
    return out.sort_values("rank"), info


if __name__ == "__main__":
    main()
