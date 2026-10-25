"""Figures (Korean, ICASSP single-column style). Baseline = gray (series 0), ours = blue (series 1).
uv run --project /Users/chloekang/kcf/app/backend python experiments/mobility/figs.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import lib as L  # noqa: E402
from figures.plot_helpers import PALETTE, SINGLE_COL_IN, use_style  # noqa: E402

KO = {"font.family": ["Apple SD Gothic Neo", "AppleGothic", "sans-serif"], "axes.unicode_minus": False}
GRAY, BLUE, TINT, LIGHT = PALETTE[0], PALETTE[1], "#7FB5D9", "#C8C8C8"


def fig(h: float = 2.5):
    use_style(); plt.rcParams.update(KO)
    return plt.subplots(figsize=(SINGLE_COL_IN, h), layout="constrained")


def save(f, name: str) -> None:
    L.FIGS.mkdir(exist_ok=True)
    for ext in ("png", "pdf"):
        f.savefig(L.FIGS / f"{name}.{ext}", dpi=300)
    plt.close(f)


def main() -> None:
    res = json.load(open(L.OUT / "results.json", encoding="utf-8"))
    tab = pd.DataFrame(res["siting_table"])
    # F1 equal-budget curves
    f, ax = fig(2.6)
    spec = [("random_mean", "무작위(200회)", GRAY, "--"), ("stop_density_proxy", "정류장 밀집도(승차량 자료 없어 대용)", LIGHT, "-"),
            ("top_n_by_p65_static", "65+ 상위 N(중복 미제거)", "#B0B0B0", "-"), ("pop_greedy", "인구만 반영 탐욕", TINT, "-"),
            ("ours_greedy_heat", "폭염 위험 가중 탐욕(우리)", BLUE, "-")]
    for m, lab, c, ls in spec:
        d = tab[tab.method == m].sort_values("N")
        ax.plot(d.N, d.heat_weighted * 100, ls, color=c, marker="o", label=lab, lw=1.6 if m == "ours_greedy_heat" else 1.0)
        if m == "random_mean":
            ax.fill_between(d.N, d.heat_weighted_lo * 100, d.heat_weighted_hi * 100, color=GRAY, alpha=0.25, lw=0)
    ax.set_xscale("log"); ax.set_xlabel("업그레이드 정류장 수 N (동일 예산)"); ax.set_ylabel("폭염 가중 65+ 도보 5분 포괄률 (%)")
    ax.legend(loc="upper left", fontsize=6)
    save(f, "f1_siting_equal_budget")
    # F2 시군구 distribution
    s = pd.read_csv(L.OUT / "sigungu_access.csv", encoding="utf-8-sig")
    f, ax = fig(2.3)
    ok = s[s.flag.isna()]
    ax.hist(ok.share_65_walk5 * 100, bins=np.arange(0, 101, 5), color=BLUE, alpha=0.9, label=f"신뢰 가능 {len(ok)}개 시군구")
    ax.hist(s[s.flag.notna()].share_65_walk5 * 100, bins=np.arange(0, 101, 5), color=LIGHT, label=f"자료 한계 플래그 {int(s.flag.notna().sum())}개(해석 제외)")
    ax.set_xlabel("65+ 중 도보 5분(0.8 m/s) 내 정류장 보유 비율 (%)"); ax.set_ylabel("시군구 수"); ax.legend(fontsize=6)
    save(f, "f2_sigungu_access")
    # F3 시도 low-floor
    t = pd.read_csv(L.OUT / "sido_lowfloor_gap.csv", encoding="utf-8-sig")
    f, ax = fig(2.6)
    ax.scatter(t.vuln_ratio * 100, t.lowfloor_rate, s=14, color=BLUE)
    for _, r in t.iterrows():
        ax.annotate(r.sido, (r.vuln_ratio * 100, r.lowfloor_rate), fontsize=6, xytext=(2, 2), textcoords="offset points")
    c = res["sido"]["lowfloor_rate~vuln_ratio"]
    ax.set_xlabel("교통약자 비율 (%)"); ax.set_ylabel("저상버스 보급률 2021 (%)")
    ax.text(0.98, 0.97, f"n=17, Spearman ρ={c['rho']:.2f}\n95% CI [{c['lo']:.2f}, {c['hi']:.2f}]", transform=ax.transAxes, ha="right", va="top", fontsize=6)
    save(f, "f3_sido_lowfloor")
    # F4 Seoul outcome
    g = pd.read_csv(L.OUT / "seoul_gu_outcome.csv", encoding="utf-8-sig")
    f, ax = fig(2.6)
    ax.scatter(g.unc70_building_straight * 100, g.ped70_per1000, s=14, color=BLUE)
    c = res["outcome_seoul"]["ped70_per1000~unc70_building_straight"]
    ax.set_xlabel("70+ 정류장 미포괄 비율 (%, 건물 가중·직선×1.3)"); ax.set_ylabel("70+ 보행 119 출동 (1000명당)")
    ax.text(0.02, 0.97, f"서울 25개 구, Spearman ρ={c['rho']:.2f}\n95% CI [{c['lo']:.2f}, {c['hi']:.2f}], 검출 가능 |ρ|≈{res['outcome_seoul']['min_detectable_rho_n25_80pct_power']:.2f}",
            transform=ax.transAxes, ha="left", va="top", fontsize=6)
    save(f, "f4_seoul_outcome")


if __name__ == "__main__":
    main()
