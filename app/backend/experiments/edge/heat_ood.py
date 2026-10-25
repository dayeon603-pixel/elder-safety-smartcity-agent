"""Heat engine under extreme / out-of-range weather and on days with no alert budget.

Questions (all answered from experiments/heat/out, nothing refit):
  1. Hottest 5 % of season days per test year (by the national mean of the daily max 체감온도 at_l0):
     capture of B1 vs OURS at the per-day B1 budget on those days only, vs all days.
  2. Rows whose 체감온도 exceeds the maximum ever seen in training (tree models cannot extrapolate):
     how many, how many cases, were they alerted?
  3. Days with K_d = 0 (the 특보 rule alerts nowhere, so our same-budget design alerts nowhere either):
     share of the season's cases that fall on such days = cases no same-budget method can reach.
  4. Low-budget days (0 < K_d <= 5): capture on those days (small-K instability).
  5. Model saturation on the hottest rows: predicted λ vs observed cases per 체감온도 bin above 35 ℃.

Run: uv run python experiments/edge/heat_ood.py
"""

from __future__ import annotations

import logging
from typing import Any
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from edge_common import FIGS, OUT, PANEL, PRED, TEST_YEARS, dump, set_seed, use_style, wilson  # noqa: E402

logger = logging.getLogger("edge.heat_ood")
HOT_SHARE = 0.05
LOW_K_MAX = 5
BINS = (35.0, 36.0, 37.0, 38.0, 99.0)
METHODS = ("B1", "OURS", "HEUR", "B2")


def capture(df: pd.DataFrame, method: str, variant: str) -> tuple[float, float, int]:
    """(capture, caught, cases) on the rows of `df` for one method/variant (per_day budget)."""
    sub = df[(df.method == method) & (df.variant == variant)]
    cases = float(sub.y.sum())
    caught = float((sub.y * sub.alerted).sum())
    return (caught / cases if cases > 0 else float("nan")), caught, int(cases)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    set_seed()
    panel = pd.read_csv(PANEL)
    pred = pd.read_csv(PRED)
    pred = pred[pred.budget_kind == "per_day"].merge(
        panel[["unit_id", "date", "year", "at_l0", "tx_l0", "y", "b1_F", "b1_O"]],
        left_on=["region_code", "date"], right_on=["unit_id", "date"], how="left",
    )
    assert pred.y.notna().all(), "prediction rows without panel rows"
    out: dict[str, Any] = {"hot_share": HOT_SHARE, "low_k_max": LOW_K_MAX, "years": {}}

    for T in TEST_YEARS:
        te = pred[pred.test_year == T]
        tr_max = float(panel[panel.year < T].at_l0.max())
        day_mean = te[(te.method == "OURS") & (te.variant == "F")].groupby("date").at_l0.mean()
        thr = float(day_mean.quantile(1 - HOT_SHARE))
        hot_days = set(day_mean[day_mean >= thr].index)
        year: dict[str, Any] = {"train_max_apparent_c": tr_max, "hot_day_threshold_mean_apparent_c": thr,
                      "n_hot_days": len(hot_days), "hot_days": sorted(hot_days), "variants": {}}
        for v in ("F", "O"):
            k_col = f"b1_{v}"
            kd = te[(te.method == "B1") & (te.variant == v)].groupby("date")[k_col].sum()
            zero_days = set(kd[kd == 0].index)
            low_days = set(kd[(kd > 0) & (kd <= LOW_K_MAX)].index)
            cases_by_day = te[(te.method == "B1") & (te.variant == v)].groupby("date").y.sum()
            block: dict[str, Any] = {
                "k0_days": len(zero_days), "k0_cases": int(cases_by_day.reindex(sorted(zero_days)).sum()),
                "season_cases": int(cases_by_day.sum()),
                "lowk_days": len(low_days), "lowk_cases": int(cases_by_day.reindex(sorted(low_days)).sum()),
                "methods": {},
            }
            block["k0_case_share"] = block["k0_cases"] / block["season_cases"]
            for m in METHODS:
                c_all = capture(te, m, v)
                c_hot = capture(te[te.date.isin(hot_days)], m, v)
                c_low = capture(te[te.date.isin(low_days)], m, v)
                p_hot, lo_hot, hi_hot = wilson(int(c_hot[1]), c_hot[2])
                block["methods"][m] = {
                    "capture_all": c_all[0], "capture_hot": c_hot[0], "hot_ci_binomial": [lo_hot, hi_hot],
                    "hot_caught": c_hot[1], "hot_cases": c_hot[2],
                    "capture_lowk": c_low[0], "lowk_caught": c_low[1], "lowk_cases": c_low[2],
                }
            ours = te[(te.method == "OURS") & (te.variant == v)]
            above = ours[ours.at_l0 > tr_max]
            block["above_train_max"] = {
                "rows": int(len(above)), "cases": int(above.y.sum()), "alerted_rows": int(above.alerted.sum()),
                "cases_caught": int((above.y * above.alerted).sum()),
                "regions": sorted(set(above.region_code.astype(int))) if len(above) else [],
            }
            # saturation: mean predicted λ vs mean observed cases in 체감온도 bins above 35 ℃
            bins = []
            for lo, hi in zip(BINS[:-1], BINS[1:], strict=True):
                sel = ours[(ours.at_l0 >= lo) & (ours.at_l0 < hi)]
                if len(sel):
                    bins.append({"bin": f"[{lo}, {hi})", "n": int(len(sel)), "mean_lambda": float(sel.score.mean()),
                                 "mean_obs": float(sel.y.mean()), "max_lambda": float(sel.score.max()),
                                 "max_obs": int(sel.y.max())})
            block["saturation_bins"] = bins
            year["variants"][v] = block
        out["years"][str(T)] = year
        logger.info("%d hot days %d (thr %.2f) F: B1 %.3f OURS %.3f all B1 %.3f OURS %.3f", T, len(hot_days), thr,
                    year["variants"]["F"]["methods"]["B1"]["capture_hot"],
                    year["variants"]["F"]["methods"]["OURS"]["capture_hot"],
                    year["variants"]["F"]["methods"]["B1"]["capture_all"],
                    year["variants"]["F"]["methods"]["OURS"]["capture_all"])

    # pooled over test years
    pooled: dict[str, Any] = {}
    for v in ("F", "O"):
        hot_all = []
        for T in TEST_YEARS:
            hot_all += [(T, d) for d in out["years"][str(T)]["hot_days"]]
        key = set(hot_all)
        sel = pred[[(t, d) in key for t, d in zip(pred.test_year, pred.date, strict=True)]]
        pooled[v] = {m: dict(zip(("capture_hot", "caught", "cases"), capture(sel, m, v), strict=True)) for m in METHODS}
        k0_cases = sum(out["years"][str(T)]["variants"][v]["k0_cases"] for T in TEST_YEARS)
        season = sum(out["years"][str(T)]["variants"][v]["season_cases"] for T in TEST_YEARS)
        pooled[v]["k0_case_share"] = k0_cases / season
        pooled[v]["k0_cases"] = k0_cases
        pooled[v]["season_cases"] = season
    out["pooled"] = pooled
    dump(out, OUT / "heat_ood.json")

    use_style()
    import matplotlib.pyplot as plt

    FIGS.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(3.5, 2.4))
    x = np.arange(len(TEST_YEARS))
    w = 0.2
    for i, (m, color) in enumerate((("B1", "#858c98"), ("OURS", "#0072B2"))):
        ax.bar(x + (i - 0.5) * w * 2 - w / 2, [out["years"][str(T)]["variants"]["F"]["methods"][m]["capture_all"] for T in TEST_YEARS],
               w, color=color, alpha=0.45, label=f"{m} 모든 날")
        ax.bar(x + (i - 0.5) * w * 2 + w / 2, [out["years"][str(T)]["variants"]["F"]["methods"][m]["capture_hot"] for T in TEST_YEARS],
               w, color=color, label=f"{m} 가장 더운 5% 날")
    ax.set_xticks(x)
    ax.set_xticklabels([str(T) for T in TEST_YEARS])
    ax.set_ylabel("포착률 (변형 F, B1 예산)")
    ax.set_ylim(0, 1)
    ax.legend(ncol=2, loc="upper left")
    fig.savefig(FIGS / "heat_ood_hot_days.pdf")
    fig.savefig(FIGS / "heat_ood_hot_days.png")

    fig, ax = plt.subplots(figsize=(3.5, 2.4))
    for v, color in (("F", "#0072B2"), ("O", "#D55E00")):
        ax.plot(TEST_YEARS, [out["years"][str(T)]["variants"][v]["k0_case_share"] for T in TEST_YEARS], "o-", color=color, label=f"변형 {v}")
    ax.set_ylabel("경보 예산 0인 날의 환자 비율")
    ax.set_ylim(0, 0.4)
    ax.set_xticks(TEST_YEARS)
    ax.legend()
    fig.savefig(FIGS / "heat_ood_k0_share.pdf")
    fig.savefig(FIGS / "heat_ood_k0_share.png")


if __name__ == "__main__":
    main()
