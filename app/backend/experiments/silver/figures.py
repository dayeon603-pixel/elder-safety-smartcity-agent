"""Figures for module B. Run: uv run --project <backend> python -I experiments/silver/figures.py"""
from __future__ import annotations
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

HERE = Path(__file__).resolve().parent
plt.style.use("/Users/chloekang/kcf/app/backend/figures/icassp.mplstyle")
r = pd.read_csv(HERE / "out/coverage_results.csv")
r = r[(r.variant == "harm") & (r.pool == "C")]
LAB = {"current": "현행 실버존", "random_k": "무작위", "pop65_k": "노인 인구 많은 곳", "past_k": "과거 사고 지점",
       "ours_full": "우리 엔진", "ours_nohist": "우리 엔진(과거 사고 빼고)", "oracle": "이론상 최대"}
COL = {"current": "#7f7f7f", "random_k": "#56B4E9", "pop65_k": "#E69F00", "past_k": "#009E73", "ours_full": "#D55E00",
       "ours_nohist": "#CC79A7", "oracle": "#000000"}

fig, axs = plt.subplots(1, 2, figsize=(7.0, 2.5), sharey=False)
for ax, strat, ttl in zip(axs, ("all", "new"), ("전체 다발지", "신규 다발지(과거 300m 안 다발지 없음)")):
    s = r[r.stratum == strat]
    cur = s[s.method == "current"].iloc[0]
    ax.scatter([cur.k], [100 * cur.coverage], marker="*", s=40, color=COL["current"], label=LAB["current"], zorder=5)
    for m in ("random_k", "pop65_k", "past_k", "ours_nohist", "ours_full", "oracle"):
        t = s[s.method == m].sort_values("k")
        ax.plot(t.k, 100 * t.coverage, label=LAB[m], color=COL[m], ls="--" if m in ("oracle", "ours_nohist") else "-")
        if m in ("ours_full", "random_k"):
            ax.fill_between(t.k, 100 * t.lo, 100 * t.hi, color=COL[m], alpha=0.15, lw=0)
    ax.set_xscale("log"); ax.set_xlabel("선택 지점 수 k"); ax.set_title(ttl, fontsize=8)
axs[0].set_ylabel("포착률 (%)")
axs[0].legend(loc="lower right", fontsize=6)
fig.savefig(HERE / "figs/coverage_curve.png"); fig.savefig(HERE / "figs/coverage_curve.pdf"); plt.close(fig)

fig, axs = plt.subplots(1, 2, figsize=(7.0, 2.5))
order = ["current", "random_k", "pop65_k", "past_k", "ours_nohist", "ours_full"]
for ax, strat, ttl in zip(axs, ("all", "new"), ("전체 다발지", "신규 다발지")):
    s = r[(r.stratum == strat) & (r.k == 3918)].set_index("method").loc[order]
    y = 100 * s.coverage
    err = [y - 100 * s.lo, 100 * s.hi - y]
    ax.bar(range(len(order)), y, yerr=err, color=[COL[m] for m in order], capsize=2, error_kw=dict(lw=0.6))
    ax.set_xticks(range(len(order)))
    ax.set_xticklabels([LAB[m].replace("(과거 사고 빼고)", "\n(과거 제외)") for m in order], rotation=40, ha="right", fontsize=6)
    ax.set_title(f"{ttl}, k=3,918", fontsize=8)
axs[0].set_ylabel("포착률 (%) 95% 신뢰구간")
fig.savefig(HERE / "figs/bar_k3918.png"); fig.savefig(HERE / "figs/bar_k3918.pdf"); plt.close(fig)
