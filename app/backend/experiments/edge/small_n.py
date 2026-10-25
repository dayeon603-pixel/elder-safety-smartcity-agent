"""Small-sample audit of every ranking / estimate the product shows.

Each block answers: how unstable is this number at the n we actually have, and what minimum-n or
shrinkage rule follows. Every number is computed from the experiments' stored outputs or the panel.

  A. 실버존 NEW stratum (n_hot = 73 pooled; 32 / 15 / 28 per fold): Wilson intervals of the coverage
     shares, per-fold spread, and the n needed for a ±5 %p half-width.
  B. 119 external validation (321 heat cases over 25 구, 4 months: 19 / 36 / 250 / 16): Spearman and
     top-5 capture per month with bootstrap CIs; width vs n.
  C. AED 구 night-gap rank (25 구; night public arrests per 구 are 12-33 events per year):
     Poisson resampling of the arrest counts -> rank distribution per 구; how many 구 have a rank interval
     wider than 5 places; top-5 membership stability.
  D. Heat per-region history (hist_rate shown as drivers.hist_rate, feature of OURS): regions with <= 5
     training cases per fold; empirical-Bayes (gamma-Poisson) shrinkage of the seasonal rate and the rank
     changes it causes; bootstrap-over-days CI of each region's rank in a test season vs its case count.
  E. Fall model negatives: 0.093 h of ADL footage -> exact Poisson interval of the false-alarm rate;
     Wilson interval of sensitivity 27/30.

Run: uv run python experiments/edge/small_n.py
"""

from __future__ import annotations

import json
import logging
from typing import Any
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from edge_common import (  # noqa: E402
    AED_OUT, FALL_RESULTS, FIGS, OUT, PANEL, SEED, SILVER_OUT, TEST_YEARS, V119_OUT, dump, n_for_half_width, poisson_ci,
    set_seed, use_style, wilson, wilson_half_width,
)

logger = logging.getLogger("edge.small_n")
N_BOOT = 2000
RANK_WIDE = 5
TOPK = 5
HALF_TARGET = 0.05
SMALL_CASES = 5
MONTHS = ("202406", "202407", "202408", "202409")
AED_MONTHS_OBSERVED = 11  # 2024-01..11 -> per-year estimates are counts x 12/11


# ----------------------------------------------------------------------------- A. silver NEW
def silver_new() -> dict[str, Any]:
    cov = pd.read_csv(SILVER_OUT / "opt" / "promoted" / "coverage_results.csv")
    fold = pd.read_csv(SILVER_OUT / "coverage_by_fold.csv")
    fold = fold[(fold.variant == "harm") & (fold.k == 3918) & (fold.pool == "C")]
    rows = cov[(cov.k == 3918) & (cov.method.isin(["current", "ours_opt"]))]
    out: dict[str, Any] = {"pooled": {}, "per_fold_n_new": {}, "required_n": {}}
    for _, r in rows.iterrows():
        n = int(r.n_hot)
        k = int(round(r.coverage * n))
        p, lo, hi = wilson(k, n)
        out["pooled"][f"{r.method}|{r.stratum}"] = {
            "n_hot": n, "coverage": float(r.coverage), "wilson_lo": lo, "wilson_hi": hi, "wilson_half": (hi - lo) / 2,
            "bootstrap_lo": float(r.lo), "bootstrap_hi": float(r.hi),
        }
    for _, r in fold[fold.method == "current"].iterrows():
        out["per_fold_n_new"][str(int(r.fold))] = {"n_new": int(r.n_new), "n_all": int(r.n),
                                                   "current_new_coverage": float(r.new)}
    # ours_full (v1) per fold is what coverage_by_fold holds; it shows the fold-to-fold spread at n = 15..32
    ours_fold = fold[fold.method == "ours_full"]
    out["ours_full_v1_new_by_fold"] = {str(int(r.fold)): float(r.new) for _, r in ours_fold.iterrows()}
    out["ours_full_v1_new_spread_pp"] = float((ours_fold.new.max() - ours_fold.new.min()) * 100) if len(ours_fold) else None
    p_new = out["pooled"]["ours_opt|new"]["coverage"]
    out["required_n"] = {
        "target_half_width_pp": HALF_TARGET * 100,
        "n_for_half_5pp_at_p_new": n_for_half_width(p_new, HALF_TARGET),
        "half_width_at_n73_pp": wilson_half_width(p_new, 73) * 100,
        "half_width_at_n15_pp": wilson_half_width(p_new, 15) * 100,
        "half_width_at_n32_pp": wilson_half_width(p_new, 32) * 100,
    }
    # minimum n for which a 2-sided Wilson interval of a 39 % share excludes the 12.5 % current value
    for n in range(5, 200):
        _, lo, _ = wilson(int(round(p_new * n)), n)
        if lo > out["pooled"]["current|new"]["coverage"]:
            out["required_n"]["min_n_ci_excludes_current"] = n
            break
    return out


# ----------------------------------------------------------------------------- B. 119 validation
def validate119(rng: np.random.Generator) -> dict[str, Any]:
    res = json.loads((V119_OUT / "results.json").read_text(encoding="utf-8"))
    heat = res["heat"]
    counts = heat["counts_by_gu_month"]
    gus = sorted(counts)
    Y = np.array([counts[g] for g in gus], dtype=float)  # 25 x 4
    arrays = np.load(V119_OUT / "arrays.npz", allow_pickle=True)
    hY = arrays["hY"]
    assert hY.shape == Y.shape
    out: dict[str, Any] = {"n_total": int(heat["n_119_heat_JunSep_total"]), "n_by_month": heat["n_by_month"], "signals": {}}
    # width of the reported CIs vs n (from results.json; the bootstrap there is paired over 구)
    for name, sig in heat["signals"].items():
        rows = {}
        for m in MONTHS:
            b = sig["by_month"].get(m)
            if b is None:
                continue
            rho_ci = b.get("rho_ci") or [None, None]
            top_ci = b.get("top5_ci") or [None, None]
            rows[m] = {
                "n": int(heat["n_by_month"][m]), "rho": b.get("rho"), "rho_ci": rho_ci,
                "rho_ci_includes_0": (rho_ci[0] is not None and rho_ci[0] <= 0 <= rho_ci[1]),
                "top5_capture": b.get("top5_capture"), "top5_ci": top_ci,
                "top5_ci_width": (None if top_ci[0] is None else top_ci[1] - top_ci[0]),
            }
        out["signals"][name] = {"season_rho": sig.get("season_rho"), "season_rho_ci": sig.get("season_rho_ci"),
                                "by_month": rows}
    # how unstable is the 구 ranking by 119 counts itself (Poisson noise on 0-19 counts)?
    season = Y.sum(1)
    boot = rng.poisson(season[None, :], size=(N_BOOT, len(gus)))
    ranks = (-boot).argsort(1).argsort(1) + 1
    lo, hi = np.percentile(ranks, [2.5, 97.5], axis=0)
    top = (ranks <= TOPK).mean(0)
    out["gu_rank_by_119_count"] = {
        "season_counts": dict(zip(gus, season.astype(int).tolist(), strict=True)),
        "rank_ci_width_median": float(np.median(hi - lo)),
        "share_gu_rank_ci_wider_than_5": float(np.mean((hi - lo) > RANK_WIDE)),
        "top5_membership_prob": {g: float(t) for g, t in zip(gus, top, strict=True)},
        "n_gu_top5_prob_ge_0_8": int((top >= 0.8).sum()),
    }
    return out


# ----------------------------------------------------------------------------- C. AED 구 rank
def aed_gu(rng: np.random.Generator) -> dict[str, Any]:
    gu = pd.read_csv(AED_OUT / "seoul_gu_night_gap_map.csv", encoding="utf-8-sig")
    gu = gu.sort_values("night_gap_score_weekday", ascending=False).reset_index(drop=True)
    names = gu.gu.tolist()
    night_per_yr = gu.public_ca_per_yr_night_est.to_numpy(float)
    observed = night_per_yr * AED_MONTHS_OBSERVED / 12.0  # events actually in the 11-month window
    gap = gu.night_gap_score_weekday.to_numpy(float)
    # gap_score = arrests_night x (1 - night_open_share) (reconstructed; check against the file)
    recon = night_per_yr * (1 - gu.night_pct_open_weekday.to_numpy(float) / 100)
    rel_err = float(np.max(np.abs(recon - gap) / np.maximum(gap, 1e-9)))
    boot_counts = rng.poisson(observed[None, :], size=(N_BOOT, len(names))) * 12.0 / AED_MONTHS_OBSERVED
    boot_gap = boot_counts * (1 - gu.night_pct_open_weekday.to_numpy(float) / 100)[None, :]
    ranks = (-boot_gap).argsort(1).argsort(1) + 1
    lo, hi = np.percentile(ranks, [2.5, 97.5], axis=0)
    top = (ranks <= TOPK).mean(0)
    return {
        "n_gu": len(names), "gap_formula_check_max_rel_err": rel_err,
        "night_arrests_observed_11mo": dict(zip(names, np.round(observed, 1).tolist(), strict=True)),
        "rank_point": dict(zip(names, range(1, len(names) + 1), strict=True)),
        "rank_ci": {n: [int(a), int(b)] for n, a, b in zip(names, lo, hi, strict=True)},
        "rank_ci_width_median": float(np.median(hi - lo)),
        "share_gu_rank_ci_wider_than_5": float(np.mean((hi - lo) > RANK_WIDE)),
        "top5_membership_prob": dict(zip(names, np.round(top, 3).tolist(), strict=True)),
        "n_gu_top5_prob_ge_0_8": int((top >= 0.8).sum()),
        "rank1_prob": float(top[0] if False else (ranks[:, 0] == 1).mean()),
    }


# ----------------------------------------------------------------------------- D. heat region history
def gamma_poisson_shrink(cases: np.ndarray, exposure: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Method-of-moments gamma prior on rates; posterior mean (a + y) / (a / mu0 + e)."""
    rate = cases / exposure
    mu0 = float(cases.sum() / exposure.sum())
    var = float(rate.var(ddof=1))
    # Var(rate) = mu0 / e_mean + mu0^2 / a  ->  a = mu0^2 / (var - mu0 / e_mean)
    excess = var - mu0 / float(exposure.mean())
    a = float(mu0 * mu0 / excess) if excess > 0 else float("inf")
    post = (a + cases) / (a / mu0 + exposure) if np.isfinite(a) else np.full_like(rate, mu0)
    return post, mu0, a


def heat_history(rng: np.random.Generator) -> dict[str, Any]:
    panel = pd.read_csv(PANEL, usecols=["unit_id", "date", "year", "y", "sido", "sgg"])
    out: dict[str, Any] = {"folds": {}}
    for T in TEST_YEARS:
        tr = panel[panel.year < T]
        per = tr.groupby("unit_id").agg(cases=("y", "sum"), days=("y", "size"))
        cases, days = per.cases.to_numpy(float), per.days.to_numpy(float)
        raw_rate = cases / days
        post, mu0, a = gamma_poisson_shrink(cases, days)
        raw_rank = (-raw_rate).argsort().argsort() + 1
        post_rank = (-post).argsort().argsort() + 1
        small = cases <= SMALL_CASES
        shrink_ratio = np.where(raw_rate > 0, post / raw_rate, np.nan)
        # bootstrap over test-season days: rank of each region by observed cases in year T
        te = panel[panel.year == T]
        mat = te.pivot_table(index="date", columns="unit_id", values="y", aggfunc="sum").fillna(0.0).to_numpy()
        nd = mat.shape[0]
        idx = rng.integers(0, nd, size=(N_BOOT, nd))
        tot = mat[idx].sum(1)  # N_BOOT x regions
        rk = (-tot).argsort(1).argsort(1) + 1
        lo, hi = np.percentile(rk, [2.5, 97.5], axis=0)
        width = hi - lo
        season_cases = mat.sum(0)
        bins = [(0, 5), (6, 10), (11, 20), (21, 50), (51, 10_000)]
        width_by_bin = {}
        for b0, b1 in bins:
            sel = (season_cases >= b0) & (season_cases <= b1)
            if sel.any():
                width_by_bin[f"{b0}-{b1}"] = {"n_regions": int(sel.sum()), "median_rank_ci_width": float(np.median(width[sel])),
                                              "p90_rank_ci_width": float(np.percentile(width[sel], 90))}
        out["folds"][str(T)] = {
            "train_years": f"2019-{T - 1}", "n_regions": int(len(per)),
            "regions_with_le5_train_cases": int(small.sum()),
            "prior_mean_rate_per_day": mu0, "gamma_a": a,
            "shrink_ratio_median_small": float(np.nanmedian(shrink_ratio[small])) if small.any() else None,
            "shrink_ratio_median_large": float(np.nanmedian(shrink_ratio[~small])),
            "rank_moves_ge10_after_shrink": int((np.abs(raw_rank - post_rank) >= 10).sum()),
            "rank_moves_ge10_among_small": int((np.abs(raw_rank - post_rank)[small] >= 10).sum()),
            "test_season_rank_ci_width_by_cases": width_by_bin,
            "test_season_regions_le5_cases": int((season_cases <= SMALL_CASES).sum()),
        }
    return out


# ----------------------------------------------------------------------------- E. fall
def fall() -> dict[str, Any]:
    cv = json.loads((FALL_RESULTS / "cv_summary.json").read_text(encoding="utf-8"))
    opt = json.loads((FALL_RESULTS / "opt" / "opt_summary.json").read_text(encoding="utf-8"))
    neg_h = float(cv["neg_hours"])
    out: dict[str, Any] = {"neg_hours": neg_h, "n_fall": cv["n_fall"], "n_adl": cv["n_adl"], "models": {}}
    for name, blk in (("rule", opt["ur_cv:rule"]), ("logreg_v1(shipped)", opt["ur_cv:v1"]), ("best(opt)", opt["ur_cv:best"])):
        fa, tp, n = int(blk["fa"]), int(blk["tp"]), int(blk["n_fall"])
        lo, hi = poisson_ci(fa)
        p, wl, wh = wilson(tp, n)
        out["models"][name] = {
            "fa": fa, "fa_per_h": fa / neg_h, "fa_per_h_poisson_ci": [lo / neg_h, hi / neg_h],
            "sens": p, "sens_wilson_ci": [wl, wh], "reported_sens_ci": blk["sens"][1:], "reported_fa_ci": blk["fa_per_h"][1:],
        }
    # hours of negatives needed so that the FA/h interval half-width is <= 5 per hour at the shipped rate
    rate = out["models"]["logreg_v1(shipped)"]["fa_per_h"]
    out["neg_hours_for_fa_half_5_per_h"] = float((1.96 ** 2) * rate / (5.0 ** 2))
    out["le2i_transfer"] = {k: opt[k]["sens"] for k in ("ur2le:rule", "ur2le:v1") if k in opt}
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    set_seed()
    rng = np.random.default_rng(SEED)
    out = {"seed": SEED, "n_boot": N_BOOT, "silver_new": silver_new(), "validate119": validate119(rng),
           "aed_gu": aed_gu(rng), "heat_history": heat_history(rng), "fall": fall()}
    dump(out, OUT / "small_n.json")

    use_style()
    import matplotlib.pyplot as plt

    FIGS.mkdir(parents=True, exist_ok=True)
    # Wilson half-width vs n at the NEW-stratum share
    p_new = out["silver_new"]["pooled"]["ours_opt|new"]["coverage"]
    ns = np.arange(10, 400)
    fig, ax = plt.subplots(figsize=(3.5, 2.4))
    ax.plot(ns, [wilson_half_width(p_new, int(n)) * 100 for n in ns], color="#0072B2")
    for n, lab in ((15, "폴드 2023"), (32, "폴드 2022"), (73, "합산")):
        ax.axvline(n, color="#858c98", lw=0.6, ls="--")
        ax.text(n + 3, 28, f"n={n}\n{lab}", fontsize=6)
    ax.axhline(5, color="#D55E00", lw=0.6)
    ax.set_xlabel("신규 다발지 수 n")
    ax.set_ylabel("95% 구간 반폭 (%p)")
    ax.set_ylim(0, 32)
    fig.savefig(FIGS / "small_n_silver_new.pdf")
    fig.savefig(FIGS / "small_n_silver_new.png")

    # AED 구 rank intervals
    a = out["aed_gu"]
    names = list(a["rank_point"])
    fig, ax = plt.subplots(figsize=(3.5, 3.2))
    y = np.arange(len(names))
    lo = np.array([a["rank_ci"][n][0] for n in names])
    hi = np.array([a["rank_ci"][n][1] for n in names])
    ax.hlines(y, lo, hi, color="#858c98", lw=1.2)
    ax.plot([a["rank_point"][n] for n in names], y, "o", color="#0072B2", ms=2.5)
    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=5.5)
    ax.invert_yaxis()
    ax.set_xlabel("밤 공백 순위 (점 = 보고값, 선 = 95% 구간)")
    fig.savefig(FIGS / "small_n_aed_rank.pdf")
    fig.savefig(FIGS / "small_n_aed_rank.png")

    # heat test-season rank CI width by case count (2025)
    h = out["heat_history"]["folds"]["2025"]["test_season_rank_ci_width_by_cases"]
    fig, ax = plt.subplots(figsize=(3.5, 2.4))
    keys = list(h)
    ax.bar(range(len(keys)), [h[k]["median_rank_ci_width"] for k in keys], color="#0072B2")
    ax.set_xticks(range(len(keys)))
    ax.set_xticklabels([f"{k}\n(n={h[k]['n_regions']})" for k in keys], fontsize=6)
    ax.set_xlabel("2025 시즌 시군구 환자 수")
    ax.set_ylabel("순위 95% 구간 너비 (중앙값)")
    fig.savefig(FIGS / "small_n_heat_rank_width.pdf")
    fig.savefig(FIGS / "small_n_heat_rank_width.png")


if __name__ == "__main__":
    main()
