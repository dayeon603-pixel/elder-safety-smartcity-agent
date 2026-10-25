"""ICASSP-style figures (Korean labels). Baselines gray first, ours blue."""
from __future__ import annotations
import json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
R = json.loads((HERE / "out/results.json").read_text())
plt.rcParams.update({"font.family": "AppleGothic", "axes.unicode_minus": False, "font.size": 8, "axes.spines.top": False,
                     "axes.spines.right": False, "pdf.fonttype": 42})
GRAY, BLUE, LB = "#8c8c8c", "#1f5fbf", "#6f9be0"


def bars(ax, names, vals, cis, colors, xlabel):
    y = np.arange(len(names))[::-1]
    for yi, v, c, col in zip(y, vals, cis, colors):
        ax.barh(yi, v, color=col, height=0.62)
        if c[0] is not None:
            ax.errorbar(v, yi, xerr=[[v - c[0]], [c[1] - v]], color="k", capsize=2, lw=0.8)
        ax.text(max(v, c[1] if c[1] is not None else v) + 0.02, yi, f"{v:.2f}", va="center", fontsize=7)
    ax.set_yticks(y); ax.set_yticklabels(names); ax.set_xlabel(xlabel); ax.axvline(0, color="k", lw=0.5)


# ---- heat
h = R["heat"]["signals"]
order = [("인구", "인구 (기준선)", GRAY), ("65세 이상 인구", "65세 이상 인구 (기준선)", GRAY),
         ("특보 규칙 B1 (F)", "특보 규칙 B1 경보일수 (F)", GRAY), ("특보 규칙 B1 (O)", "특보 규칙 B1 경보일수 (O)", GRAY),
         ("영점 λ (O)", "영점 예측 λ 합 (O)", LB), ("영점 λ (F)", "영점 예측 λ 합 (F)", BLUE)]
fig, ax = plt.subplots(1, 2, figsize=(7.2, 2.7), gridspec_kw={"width_ratios": [1.15, 1]})
bars(ax[0], [o[0] for o in order], [h[o[1]]["pooled100_rho"] for o in order], [h[o[1]]["pooled100_rho_ci"] for o in order],
     [o[2] for o in order], "Spearman 순위상관 (서울 25구 × 6~9월 = 100점, 119 온열손상)")
ax[0].set_title("(a) 구-월 배분 순위상관 (95% CI: 구 단위 부트스트랩)", fontsize=8); ax[0].set_xlim(-0.2, 0.95)
x = np.arange(4); w = 0.2; mm = ["202406", "202407", "202408", "202409"]
sel = [("인구", "인구 (기준선)", GRAY), ("특보 규칙 B1 (F)", "특보 규칙 B1 경보일수 (F)", "#555555"), ("영점 λ (F)", "영점 예측 λ 합 (F)", BLUE)]
for i, (lab, k, col) in enumerate(sel):
    v = [h[k]["by_month"][m]["rho"] for m in mm]
    ax[1].bar(x + (i - 1) * w * 1.4, v, w * 1.35, color=col, label=lab)
ax[1].set_xticks(x); ax[1].set_xticklabels(["6월\n(n=19)", "7월\n(n=36)", "8월\n(n=250)", "9월\n(n=16)"])
ax[1].set_ylabel("Spearman (25구)"); ax[1].axhline(0, color="k", lw=0.5); ax[1].legend(fontsize=6.5, frameon=False, loc="upper left")
ax[1].set_title("(b) 월별 (구 25개, n = 119 건수)", fontsize=8)
fig.tight_layout(); fig.savefig(HERE / "figs/heat_119.pdf"); fig.savefig(HERE / "figs/heat_119.png", dpi=200); plt.close(fig)

# ---- silver
s = R["silver"]["signals"]
order = [("현행 노인보호구역 수", "현행 노인보호구역 수 (기준선)", GRAY), ("65세 이상 인구", "65세 이상 인구 (기준선)", GRAY),
         ("70세 이상 인구", "70세 이상 인구 (기준선)", GRAY), ("영점 선정 지점 수", "영점 선정 지점 수", BLUE)]
fig, ax = plt.subplots(1, 2, figsize=(7.2, 2.6), gridspec_kw={"width_ratios": [1.1, 1]})
bars(ax[0], [o[0] for o in order], [s[o[1]]["ped70"]["rho"] for o in order], [s[o[1]]["ped70"]["ci"] for o in order],
     [o[2] for o in order], "Spearman 순위상관 (서울 25구, 70세 이상 보행자 119 출동 n=1,408)")
ax[0].set_title("(a) 구별 배분 순위상관 (95% CI)", fontsize=8); ax[0].set_xlim(0, 1.15)
hr = np.array(R["silver"]["by_hour_ped70"])
ax[1].bar(range(24), hr, color=GRAY); ax[1].set_xlabel("출동 시각 (시)"); ax[1].set_ylabel("70세 이상 보행자 119 출동 (건)")
ax[1].set_xticks(range(0, 24, 3)); ax[1].set_title("(b) 시간대별 (낮 6~18시 76%)", fontsize=8)
fig.tight_layout(); fig.savefig(HERE / "figs/silver_119.pdf"); fig.savefig(HERE / "figs/silver_119.png", dpi=200); plt.close(fig)
