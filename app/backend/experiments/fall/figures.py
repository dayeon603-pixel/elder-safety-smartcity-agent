"""ICASSP-style figures: baseline (gray) first, learned model (blue). Korean labels."""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams.update({"font.family": "AppleGothic", "axes.unicode_minus": False, "font.size": 9,
                     "axes.spines.top": False, "axes.spines.right": False})
R = Path(__file__).parent / "results"
OUT = Path("/Users/chloekang/kcf/app/backend/figures")
OUT.mkdir(exist_ok=True)
s = json.load(open(R / "cv_summary.json"))
G, B = "#9a9a9a", "#1f5fbf"
names = [("rule", "규칙(60° 1.5초)", G), ("logreg", "학습 모델(로지스틱)", B)]

fig, ax = plt.subplots(1, 3, figsize=(7.2, 2.5))
for a, (k, ttl, unit) in zip(ax, [("sens", "민감도 (낙상 탐지율)", ""), ("fa_per_h", "오경보 (건/시간)", ""), ("f1", "F1", "")]):
    for i, (m, lab, c) in enumerate(names):
        v, lo, hi = s[m][k]
        a.bar(i, v, color=c, width=0.6, yerr=[[v - lo], [hi - v]], capsize=3, error_kw={"lw": 0.8})
        a.text(i, hi, f"{v:.2f}" if k != "fa_per_h" else f"{v:.0f}", ha="center", va="bottom", fontsize=8)
    a.set_xticks([0, 1], ["규칙", "학습"])
    a.set_title(ttl, fontsize=9)
    a.set_ylim(0, (1.15 if k != "fa_per_h" else max(s[m][k][2] for m, *_ in names) * 1.2))
fig.tight_layout()
fig.savefig(OUT / "fall_compare.png", dpi=300)
fig.savefig(OUT / "fall_compare.pdf")

fig, a = plt.subplots(figsize=(3.6, 2.5))
bins = np.arange(0, 4.01, 0.25)
for m, lab, c in names:
    l = np.array(s[m]["latencies"])
    a.hist(l, bins=bins, color=c, alpha=0.75, label=f"{lab} (탐지 {len(l)}/30, 중앙값 {np.median(l):.2f}초)")
a.set_xlabel("낙상 시작 → 경보까지 지연 (초)")
a.set_ylabel("낙상 시퀀스 수")
a.legend(fontsize=6.5, frameon=False)
fig.tight_layout()
fig.savefig(OUT / "fall_latency.png", dpi=300)
fig.savefig(OUT / "fall_latency.pdf")
