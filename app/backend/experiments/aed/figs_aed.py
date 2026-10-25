"""Figures for module C' (ICASSP style; baseline gray first, ours blue). Korean labels."""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from figures.plot_helpers import PALETTE, save_figure, use_style  # noqa: E402

OUT = Path(__file__).parent / "out"; FIG = OUT / "figs"
GRAY, BLUE, LBLUE = PALETTE[0], PALETTE[1], "#56B4E9"


def fig_a() -> None:
    use_style()
    H = pd.read_csv(OUT / "hourly_gap.csv")
    fig, ax = plt.subplots(figsize=(3.5, 2.4), layout="constrained")
    ax.bar(H.hour, H.public_ca, color=GRAY, width=0.8, label="공공장소 심정지(건, 2024.1-11)")
    ax.set_xlabel("시각(출동 시)"); ax.set_ylabel("심정지 건수"); ax.set_xticks(range(0, 24, 3))
    ax.axvspan(-0.5, 5.5, color="0.92", zorder=0); ax.axvspan(21.5, 23.5, color="0.92", zorder=0)
    ax2 = ax.twinx(); ax2.spines["right"].set_visible(True)
    ax2.plot(H.hour, H.pct_open_weekday, color=BLUE, lw=1.2, label="열린 AED 비율, 평일")
    ax2.plot(H.hour, H.pct_open_weekend_W2_placeholder_as_weekday, color=BLUE, lw=1.0, ls="--", label="주말(자리표시자=평일)")
    ax2.plot(H.hour, H.pct_open_weekend_W1_as_reported, color=BLUE, lw=0.8, ls=":", label="주말(공시값 그대로)")
    ax2.set_ylim(0, 100); ax2.set_ylabel("열린 AED 비율(%)")
    h1, l1 = ax.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper center", bbox_to_anchor=(0.5, -0.28), ncol=2, fontsize=6)
    save_figure(fig, "fig_a_night_gap", FIG)


def fig_b() -> None:
    use_style()
    R = pd.read_csv(OUT / "policy_eval.csv")
    order = ["현행", "균등", "인구비례", "수요(시간무시)", "우리-밀도", "우리-공백"]
    cols = [GRAY] * 4 + [BLUE, LBLUE]
    fig, axs = plt.subplots(1, 2, figsize=(7.0, 2.5), layout="constrained")
    for ax, (mt, ttl, yl, scale) in zip(axs, [("dens", "(가) 평일 기준: 현행 대비 열린 AED 증가", "Δ 열린 AED / km² (사건 시점)", 1.0),
                                              ("lt50", "(나) 평일 기준: 열린 AED가 절반 미만인 시간대의 심정지", "비율(%)", 100.0)]):
        d = R[(R.budget == "5%") & (R.schedule == "평일") & (R.metric == mt)].set_index("policy").loc[order]
        if mt == "dens":
            y, lo, hi = d.vs_current, d.vs_current_lo, d.vs_current_hi
        else:
            y, lo, hi = d["mean"] * scale, d.lo * scale, d.hi * scale
        ax.bar(range(6), y, color=cols, yerr=[y - lo, hi - y], error_kw=dict(lw=0.6, capsize=1.5))
        ax.set_xticks(range(6)); ax.set_xticklabels(["현행", "균등", "인구\n비례", "수요\n(시간무시)", "우리\n(밀도)", "우리\n(공백)"], fontsize=6)
        ax.set_ylabel(yl); ax.set_title(ttl, fontsize=7)
    save_figure(fig, "fig_b_policy_5pct", FIG)


if __name__ == "__main__":
    fig_a(); fig_b()
