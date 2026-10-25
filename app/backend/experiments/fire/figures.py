"""Figures (ICASSP single-column, Korean). Baselines gray first, ours blue. Reads out/*.json; writes figs/*.png+pdf.
Run: uv run --project /Users/chloekang/kcf/app/backend python experiments/fire/figures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))
from data import FIG_DIR, OUT_DIR, TEST_YEARS  # noqa: E402
from figures.plot_helpers import PALETTE, SINGLE_COL_IN, save_figure, use_style  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

KO = {"font.family": ["Apple SD Gothic Neo", "AppleGothic", "sans-serif"], "axes.unicode_minus": False}
ORDER = ["B1_fires", "B1_pop", "B2_pop65", "B3_cas", "OURS"]
LAB = {"B1_fires": "전년 주거화재 수", "B1_pop": "인구", "B2_pop65": "65세+ 인구", "B3_cas": "전년 인명피해", "OURS": "우리 모형"}
COL = {"B1_fires": "#8C8C8C", "B1_pop": "#A6A6A6", "B2_pop65": "#6E6E6E", "B3_cas": "#C0C0C0", "OURS": PALETTE[1]}


def fig_capture(R: dict) -> None:
    use_style(); plt.rcParams.update(KO)
    fig, axs = plt.subplots(1, 2, figsize=(SINGLE_COL_IN * 2, 2.5), layout="constrained", sharey=True)
    for ax, fr in zip(axs, ["0.1", "0.2"]):
        groups = [str(y) for y in TEST_YEARS] + ["pooled_2022_2024"]
        x = np.arange(len(groups)); w = 0.8 / len(ORDER)
        for k, m in enumerate(ORDER):
            e = [R["boot"][g][fr]["methods"][m] for g in groups]
            p = np.array([q["capture"] for q in e]); lo = np.array([q["ci95"][0] for q in e]); hi = np.array([q["ci95"][1] for q in e])
            ax.bar(x + (k - 2) * w, p, w * 0.92, yerr=[p - lo, hi - p], color=COL[m], label=LAB[m],
                   error_kw={"elinewidth": 0.5, "capsize": 1, "capthick": 0.5})
        ax.axhline(float(fr), color="k", lw=0.5, ls=":")
        ax.set_xticks(x, ["2022", "2023", "2024", "통합"])
        ax.set(xlabel=f"시험 연도 | 상위 {int(float(fr) * 100)}% 시군구 선정", ylim=(0, 0.6))
    axs[0].set_ylabel("포착률 (선정 지역 인명피해 ÷ 전체)")
    axs[0].legend(fontsize=5.5, ncol=2, loc="upper left")
    save_figure(fig, "f1_capture", FIG_DIR)


def fig_diff(R: dict) -> None:
    use_style(); plt.rcParams.update(KO)
    fig, ax = plt.subplots(figsize=(SINGLE_COL_IN, 2.3), layout="constrained")
    rows = []
    for fr in ["0.1", "0.2"]:
        for b in ["B1_fires", "B2_pop65", "B3_cas"]:
            d = R["boot"]["pooled_2022_2024"][fr]["diff_vs"][f"OURS-{b}"]
            rows.append((f"{LAB[b]} (상위{int(float(fr) * 100)}%)", d["pp"], *d["ci95_pp"]))
    y = np.arange(len(rows))[::-1]
    for yi, (n, p, lo, hi) in zip(y, rows):
        ax.errorbar(p, yi, xerr=[[p - lo], [hi - p]], fmt="o", ms=3, color=PALETTE[1], elinewidth=0.7, capsize=1.5)
    ax.axvline(0, color=PALETTE[0], lw=0.7)
    ax.set_yticks(y, [r[0] for r in rows]); ax.set_xlabel("우리 − 기준선 (%p), 95% 신뢰구간, 통합 2022–2024")
    save_figure(fig, "f2_diff_vs_baselines", FIG_DIR)


def fig_profile() -> None:
    P = json.load(open(OUT_DIR / "profile.json"))
    use_style(); plt.rcParams.update(KO)
    fig, axs = plt.subplots(1, 2, figsize=(SINGLE_COL_IN * 2, 2.4), layout="constrained")
    m = pd.DataFrame(P["by_month"]); h = pd.DataFrame(P["by_hour"])
    for ax, d, xl, key in [(axs[0], m, "월", "month"), (axs[1], h, "시각", "hour")]:
        ax.bar(d[key], d.fires / d.fires.sum(), color=PALETTE[0], label="주거화재 비중")
        ax.set_xlabel(xl); ax.set_ylabel("주거화재 비중")
        ax2 = ax.twinx(); lo = [q[0] for q in d.rate_ci]; hi = [q[1] for q in d.rate_ci]
        ax2.errorbar(d[key], d.cas_fire_rate, yerr=[d.cas_fire_rate - lo, np.array(hi) - d.cas_fire_rate], color=PALETTE[1], lw=0.9,
                     marker="o", ms=2, elinewidth=0.5, capsize=1)
        ax2.set_ylabel("인명피해 동반 비율", color=PALETTE[1]); ax2.set_ylim(0, 0.2)
    axs[1].set_xticks(range(0, 24, 3)); axs[0].set_xticks(range(1, 13))
    save_figure(fig, "f3_season_time_profile", FIG_DIR)


def fig_stability(R: dict) -> None:
    use_style(); plt.rcParams.update(KO)
    fig, ax = plt.subplots(figsize=(SINGLE_COL_IN, 2.3), layout="constrained")
    ms = ["B1_fires", "B1_pop", "B2_pop65", "B3_cas", "OURS"]
    v = [np.mean(R["rank_stability"][f"0.1|{m}"]["jaccard_topK_consecutive"]) for m in ms]
    ax.bar(range(len(ms)), v, color=[COL[m] for m in ms]); ax.set_xticks(range(len(ms)), [LAB[m] for m in ms], rotation=20, ha="right")
    ax.set_ylabel("상위 10% 선정의 연속 연도 일치도\n(Jaccard)"); ax.set_ylim(0, 1)
    save_figure(fig, "f4_rank_stability", FIG_DIR)


if __name__ == "__main__":
    R = json.load(open(OUT_DIR / "results.json"))
    fig_capture(R); fig_diff(R); fig_profile(); fig_stability(R)
