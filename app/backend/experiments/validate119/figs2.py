"""Misallocation figures. Baseline gray first, ours blue."""
from __future__ import annotations
import json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
HERE = Path(__file__).resolve().parent
R = json.loads((HERE / "out/results.json").read_text())
plt.rcParams.update({"font.family": "AppleGothic", "axes.unicode_minus": False, "font.size": 8, "axes.spines.top": False, "axes.spines.right": False, "pdf.fonttype": 42})
GRAY, BLUE, LB = "#8c8c8c", "#1f5fbf", "#6f9be0"
def vbars(ax, labels, vals, cis, cols):
    for i, (v, c, col) in enumerate(zip(vals, cis, cols)):
        ax.bar(i, v * 100, color=col, width=0.6)
        ax.errorbar(i, v * 100, yerr=[[(v - c[0]) * 100], [(c[1] - v) * 100]], color="k", capsize=2, lw=0.8)
        ax.text(i, c[1] * 100 + 1, f"{v*100:.1f}%", ha="center", fontsize=7.5)
    ax.set_xticks(range(len(labels))); ax.set_xticklabels(labels)
S = R["silver"]["misallocation_tvd"]
fig, ax = plt.subplots(1, 2, figsize=(7.2, 2.8), gridspec_kw={"width_ratios": [0.8, 1.4]})
vbars(ax[0], ["현행\n노인보호구역", "영점"], [S["현행 노인보호구역"]["tvd"], S["영점 선정 지점"]["tvd"]], [S["현행 노인보호구역"]["ci"], S["영점 선정 지점"]["ci"]], [GRAY, BLUE])
ax[0].set_ylabel("실제 119 출동 대비 잘못 배치된 비율 (%)"); ax[0].set_title("(a) 70세 이상 보행자 119 출동 기준 (25구)", fontsize=8)
rt = S["ratio_per_gu"]; gs = list(rt["현행 노인보호구역"]); order = np.argsort([-S_ for S_ in [R["silver"]["counts_by_gu"][g]["ped70"] for g in gs]])
x = np.arange(25)
ax[1].axhline(1, color="k", lw=0.6)
ax[1].plot(x, [rt["현행 노인보호구역"][gs[i]] for i in order], "o", color=GRAY, ms=4, label="현행")
ax[1].plot(x, [rt["영점 선정 지점"][gs[i]] for i in order], "s", color=BLUE, ms=4, label="영점")
ax[1].set_xticks(x); ax[1].set_xticklabels([gs[i].replace("구", "") for i in order], rotation=90, fontsize=6.5)
ax[1].set_ylabel("배치 비중 ÷ 실제 출동 비중 (1 = 일치)"); ax[1].legend(frameon=False, fontsize=7); ax[1].set_title("(b) 구별 비율 (출동 많은 구 순)", fontsize=8)
fig.tight_layout(); fig.savefig(HERE / "figs/silver_misalloc.pdf"); fig.savefig(HERE / "figs/silver_misalloc.png", dpi=200); plt.close(fig)

H = R["heat"]["misallocation_tvd"]
labs = [("인구", "인구"), ("65세 이상 인구", "65세 이상\n인구"), ("B1 경보일수 F", "B1 규칙\n(F)"), ("B1 경보일수 O", "B1 규칙\n(O)"), ("영점 λ O", "영점\n(O)"), ("영점 λ F", "영점\n(F)")]
cols = [GRAY, GRAY, GRAY, GRAY, LB, BLUE]
fig, ax = plt.subplots(1, 2, figsize=(7.2, 2.8), sharey=True)
for a, key, t in [(ax[0], "pooled100", "(a) 25구 × 6~9월 = 100칸"), (ax[1], "season25", "(b) 시즌 합계 25구")]:
    vbars(a, [l[1] for l in labs], [H[key][l[0]]["tvd"] for l in labs], [H[key][l[0]]["ci"] for l in labs], cols); a.set_title(t, fontsize=8)
ax[0].set_ylabel("실제 119 온열손상 대비 잘못 배치된 비율 (%)")
fig.tight_layout(); fig.savefig(HERE / "figs/heat_misalloc.pdf"); fig.savefig(HERE / "figs/heat_misalloc.png", dpi=200); plt.close(fig)
