"""ICASSP-style figures (Korean labels). Series 0 = baseline (gray), series 1 = ours (blue), baseline passed first."""
from __future__ import annotations

import json
import pickle

import geopandas as gpd
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import engine as E
import w4lib as L

mpl.rcParams.update({"font.family": "Apple SD Gothic Neo", "font.size": 7.5, "axes.labelsize": 7.5, "axes.titlesize": 7.5,
                     "legend.fontsize": 6.5, "xtick.labelsize": 7, "ytick.labelsize": 7, "axes.unicode_minus": False,
                     "axes.linewidth": 0.6, "lines.linewidth": 1.1, "savefig.dpi": 300, "pdf.fonttype": 42})
GRAY, BLUE, LGRAY, RED = "#8c8c8c", "#1f5fa8", "#d9d9d9", "#b03030"
FIG = L.OUT / "figs"; FIG.mkdir(exist_ok=True, parents=True)


def shade_night(ax):
    for a, b in ((-0.5, 5.5), (21.5, 23.5)):
        ax.axvspan(a, b, color="#f0f0f0", lw=0, zorder=0)


def fig_hourly(res):
    fig, axs = plt.subplots(1, 2, figsize=(7.0, 2.3), sharey=False)
    for ax, rk, ttl in ((axs[0], "v1.2_2min", "편도 2분 (왕복 4분), 1.2 m/s = 144 m"), (axs[1], "v1.2_4min", "편도 4분, 1.2 m/s = 288 m")):
        k = res["radii"].index(rk); j = res["names"].index(("w_main", "p65")); den = res["denom"][j].sum()
        st = res["static"][k, j].sum() / den * 100
        wd = res["cov"]["primary"][0, :, k, j, :].sum(axis=1) / den * 100
        we = (res["cov"]["primary"][1, :, k, j, :].sum(axis=1) + res["cov"]["primary"][2, :, k, j, :].sum(axis=1)) / 2 / den * 100
        lo = res["cov"]["asreport"][1, :, k, j, :].sum(axis=1) / den * 100
        shade_night(ax)
        ax.axhline(st, color=GRAY, lw=1.2, ls="--", label="기준선: 등록 AED 전부 열림 가정")
        ax.plot(range(24), wd, color=BLUE, marker="o", ms=2.2, label="운영시간 반영: 평일")
        ax.plot(range(24), we, color=BLUE, ls=":", marker="s", ms=2.0, label="운영시간 반영: 주말(토·일 평균)")
        ax.set_xlim(-0.5, 23.5); ax.set_xticks(range(0, 24, 3)); ax.set_xlabel("시각 (시)"); ax.set_title(ttl, loc="left")
        ax.set_ylabel("65세 이상 중 열린 AED 도달 비율 (%)"); ax.grid(alpha=0.25, lw=0.4)
        ax.set_ylim(0, max(st * 1.12, 5))
    axs[0].legend(loc="lower left", frameon=False)
    fig.tight_layout(pad=0.4); fig.savefig(FIG / "fig_hourly_coverage.pdf"); fig.savefig(FIG / "fig_hourly_coverage.png"); plt.close(fig)


def fig_budget(opt):
    fig, ax = plt.subplots(figsize=(3.5, 2.5))
    na = opt["n_aed"]; B = [b for b in opt["budgets"]]
    x = np.array(B) / na * 100
    base = opt["base"]["night"]["p65"] * 100; top = opt["all24"]["night"]["p65"] * 100
    r_m = np.array([opt["random"][str(b)]["mean"] for b in B]) * 100 + base
    r_lo = np.array([opt["random"][str(b)]["lo"] for b in B]) * 100 + base; r_hi = np.array([opt["random"][str(b)]["hi"] for b in B]) * 100 + base
    ts = np.array([opt["topstatic"][str(b)]["model_night_gain_p65"] for b in B]) * 100 + base
    g = np.array([opt["convert"][str(b)]["model_night_gain_p65"] for b in B]) * 100 + base
    ax.fill_between(x, r_lo, r_hi, color=LGRAY, lw=0)
    ax.plot(x, r_m, color=GRAY, lw=1.1, label="기준선: 무작위 24시간 전환 (95% 구간)")
    ax.plot(x, ts, color=GRAY, ls="--", lw=1.0, label="기준선: 개별 후보 점수 상위 B개")
    ax.plot(x, g, color=BLUE, marker="o", ms=2.5, label="그리디 최대 도달 (전환)")
    for b, mk in ((115, "s"), (575, "s")):
        if str(b) in opt["relocate"]:
            v = opt["relocate"][str(b)]["exact"]["night"]["p65"] * 100
            ax.plot(b / na * 100, v, marker=mk, color=BLUE, mfc="white", ms=4.5, ls="none", label="재배치 (B개 철거 후 B개 신설)" if b == 115 else None)
    ax.axhline(top, color="k", lw=0.6, ls=":"); ax.text(x[-1], top + 0.4, "상한: 전 AED 24시간", ha="right", fontsize=6)
    ax.axhline(base, color="k", lw=0.4); ax.text(0.2, base + 0.4, "현재 야간", fontsize=6)
    ax.set_xlabel("예산 B (서울 AED 11,490대 대비 %)"); ax.set_ylabel("야간(22–06시) 65세 이상 도달 비율 (%)")
    ax.legend(loc="lower right", frameon=False, fontsize=5.8); ax.grid(alpha=0.25, lw=0.4)
    fig.tight_layout(pad=0.4); fig.savefig(FIG / "fig_budget_curve.pdf"); fig.savefig(FIG / "fig_budget_curve.png"); plt.close(fig)


def fig_map(gu="관악구"):
    I = E.load_inputs(); eng = E.Engine(I["A"], I["sites"]); dem = I["dem"]; S = I["sched"]["primary"]; sites = I["sites"]
    d = dem[(dem.gu == gu) & (dem.kind == "bldg") & (dem.p65_w_main > 0)].reset_index(drop=True)
    dg = gpd.read_file(L.DONG_GJ); dg = dg[(dg.sido == "11") & (dg.sggnm == gu)].to_crs(L.CRS_M)
    R = L.RADII[L.PRIMARY]
    conv = np.load(L.OUT / "convert_pick_sites.npy")
    m24 = np.zeros(len(sites), bool); m24[conv[: int(round(0.05 * I["aed"].shape[0]))]] = True
    panels = [("낮 13시 (평일)", S["wd"][:, 13], False), ("밤 3시 (평일)", S["wd"][:, 3], False), ("밤 3시, 5% 24시간 전환 후", S["wd"][:, 3] | m24, True)]
    fig, axs = plt.subplots(1, 3, figsize=(7.0, 2.6))
    for ax, (ttl, mask, conv_flag) in zip(axs, panels):
        dist = eng.dist(mask)
        cov = (dist[d.node.to_numpy()] + d.snap.to_numpy()) <= R
        dg.boundary.plot(ax=ax, color="k", lw=0.3)
        ax.scatter(d.x[~cov], d.y[~cov], s=0.7, c=GRAY, lw=0, rasterized=True, label="미도달")
        ax.scatter(d.x[cov], d.y[cov], s=0.9, c=BLUE, lw=0, rasterized=True, label="도달")
        inside = sites.gugun.eq(gu).to_numpy()
        op = mask & inside
        ax.scatter(sites.x[op], sites.y[op], s=5, marker="^", c="k", lw=0, label="열린 AED")
        if conv_flag:
            sel = m24 & inside
            ax.scatter(sites.x[sel], sites.y[sel], s=14, marker="*", c=RED, lw=0, label="24시간 전환")
        p = (d.p65_w_main.to_numpy())
        ax.set_title(f"{ttl}: {(p*cov).sum()/p.sum()*100:.1f}%", loc="left"); ax.set_aspect("equal"); ax.axis("off")
    axs[0].legend(loc="lower left", frameon=False, markerscale=3, fontsize=5.5)
    fig.tight_layout(pad=0.3); fig.savefig(FIG / f"fig_map_{gu}.pdf", dpi=300); fig.savefig(FIG / f"fig_map_{gu}.png", dpi=300); plt.close(fig)


if __name__ == "__main__":
    import sys
    res = pickle.load(open(L.OUT / "cov.pkl", "rb"))
    fig_hourly(res)
    if (L.OUT / "opt.json").exists():
        fig_budget(json.load(open(L.OUT / "opt.json")))
        fig_map("관악구")
