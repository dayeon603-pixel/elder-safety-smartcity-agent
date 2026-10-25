"""Figure for 2025-07-17 (hourly-rain proxy of 호우주의보 vs ours) + frontend CSVs. Baseline gray first.

Hypothesis display, not proof: 1 day of 1 storm episode; ours trained on the other 6 evaluable episodes (LOEO).
Baseline = OBSERVED-accumulation proxy (6h>=70 or 12h>=110 mm, KMA 주의보 criterion) on API허브 hourly ASOS, not the issued 특보.
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from common import OUT

STYLE = Path("/Users/chloekang/kcf/app/backend/figures/icassp.mplstyle")
DAY = pd.Timestamp("2025-07-17")
GRAY, BLUE = "#7f7f7f", "#0072B2"


def curve(score: np.ndarray, y: np.ndarray) -> np.ndarray:
    idx = np.argsort(-score, kind="stable")
    return np.concatenate([[0], np.cumsum(y[idx]) / y.sum()])


def main() -> None:
    plt.style.use(str(STYLE))
    sgg = pd.read_csv(OUT / "sgg_table.csv", dtype={"sgg_cd": str})
    c = pd.read_csv(OUT / "cells_daily_ep3.csv", dtype={"sgg_cd": str}, parse_dates=["date"])
    c = c[c.date == DAY].merge(sgg[["sgg_cd", "sido", "sgg"]], on="sgg_cd").reset_index(drop=True)
    y = c["y"].to_numpy()
    k = int(c["proxy_adv"].sum())
    kw = int(c["proxy_warn"].sum())
    s_base = c["proxy_adv"].astype(float).to_numpy() + 1e-6 * c["max12"].to_numpy()
    s_ours = c["score_ours"].to_numpy()
    short = {"충청남도": "충남", "경상남도": "경남", "경기도": "경기", "전남광주통합특별시": "광주전남", "인천광역시": "인천", "충청북도": "충북"}
    c["label"] = c["sido"].map(lambda s: short.get(s, s[:2])) + " " + c["sgg"]
    c["base_alert"] = c["proxy_adv"].astype(bool)
    c["ours_alert"] = (-s_ours).argsort(kind="stable").argsort() < k
    cb, co = curve(s_base, y), curve(s_ours, y)

    fig, (a1, a2) = plt.subplots(2, 1, figsize=(3.5, 4.3), gridspec_kw=dict(height_ratios=[1, 1.35], hspace=0.6))
    xs = np.arange(len(c) + 1)
    a1.plot(xs, cb, color=GRAY, label="Baseline: hourly-rain 주의보 proxy")
    a1.plot(xs, co, color=BLUE, label="Ours: rain x local prior")
    a1.scatter([k], [cb[k]], marker="s", color=GRAY, zorder=3, s=14)
    a1.axvline(k, color=GRAY, lw=0.4, ls=":")
    a1.set_xlim(0, 120)
    a1.set_ylim(0, 1)
    a1.set_xlabel(f"alerts issued (proxy fires {k}; of 256 sigungu)")
    a1.set_ylabel("share of rescues covered")
    a1.set_title(f"(a) 2025-07-17, {int(y.sum()):,} rescues", loc="left", fontsize=8)
    a1.legend(loc="lower right")

    top = c[~c.base_alert].sort_values("y", ascending=False).head(12).iloc[::-1]
    missed = int(c.loc[~c.base_alert, "y"].sum())
    yy = np.arange(len(top))
    a2.barh(yy, top["y"], color="#d9d9d9", height=0.7)
    a2.set_yticks(yy, top["label"])
    xm = top["y"].max() * 1.02
    for j, (_, r) in enumerate(top.iterrows()):
        a2.scatter(xm * 1.10, j, marker="s", s=16, color=GRAY if r.base_alert else "white", edgecolor=GRAY, lw=0.5)
        a2.scatter(xm * 1.22, j, marker="s", s=16, color=BLUE if r.ours_alert else "white", edgecolor=BLUE, lw=0.5)
    a2.text(xm * 1.10, len(top) - 0.1, "Base", ha="center", fontsize=6.5, color=GRAY)
    a2.text(xm * 1.22, len(top) - 0.1, "Ours", ha="center", fontsize=6.5, color=BLUE)
    a2.set_xlim(0, xm * 1.3)
    a2.set_xlabel("rescue calls (flood, 2025-07-17)")
    a2.set_title(f"(b) top 12 NOT alerted by baseline ({missed} rescues); filled = alerted, budget {k}", loc="left", fontsize=6.5)
    fig.savefig(OUT / "fig_rain_20250717.pdf")
    fig.savefig(OUT / "fig_rain_20250717.png")
    print(f"07-17: proxy adv cells={k} warn={kw}; capture proxy={cb[k]:.3f} ours={co[k]:.3f}")
    c[["sgg_cd", "label", "y", "max3", "max6", "max12", "day24", "base_alert", "ours_alert", "score_ours"]].rename(
        columns={"y": "rescues", "label": "sigungu", "base_alert": "proxy_adv_alert"}).sort_values("rescues", ascending=False).to_csv(
        OUT / "sgg_20250717.csv", index=False)

    res = pd.read_csv(OUT / "hourly_results.csv")
    r = res[res["mode"] == "loeo"]
    ev = pd.read_csv(OUT / "episodes.csv")
    keep = ["episode", "H_Kadv", "H_Kwarn", "D_Kadv", "D_Kwarn", "D_proxy_adv@Kadv", "D_rain_only@Kadv", "D_ours@Kadv", "D_rain_x_prior@Kadv",
            "D_proxy_adv@20/d", "D_ours@20/d", "H_proxy_adv@Kadv", "H_ours@Kadv"]
    t = ev.merge(r[keep], on="episode", how="left")
    t["note"] = ("baseline=observed-accumulation proxy of 호우주의보 (6h70/12h110) on hourly ASOS, NOT issued 특보; LOEO; "
                 "ep1 has no hourly data; n=7 episodes, 1 year")
    t.to_csv(OUT / "episode_table.csv", index=False)
    print(t.drop(columns=["top_sgg", "note"]).round(3).to_string(index=False))


if __name__ == "__main__":
    sys.exit(main())
