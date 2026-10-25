"""Build every deck figure (Korean, ICASSP style) from the experiment result files.

Run: uv run --project /Users/chloekang/kcf/app/backend python /Users/chloekang/kcf/deck/build/make_figs.py
Outputs: /Users/chloekang/kcf/deck/figs/final/*.png|pdf  (+ figs_manifest.json with source file + key per figure)

Rules (coordinator): never truncate axes in a way that exaggerates, keep CI/n/units/baseline, baseline gray first,
ours blue. Only Korean labels, larger fonts, one highlighted value per figure.
"""
from __future__ import annotations

import fall_src
import silver_src
import json
import logging
import statistics
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

ROOT = Path("/Users/chloekang/kcf")
EXP = ROOT / "app/backend/experiments"
sys.path.insert(0, str(ROOT / "app/backend"))
from figures.plot_helpers import PALETTE, save_figure, use_style  # noqa: E402

LOG = logging.getLogger("make_figs")
FINAL = ROOT / "deck/figs/final"
import os  # noqa: E402

BENCH = Path(os.environ.get("KCF_BENCH_JSON", str(Path(__file__).resolve().parent / "data/after.json")))
GRAY, BLUE, LBLUE, ORANGE = PALETTE[0], PALETTE[1], "#7FB5D9", "#D55E00"
LGRAY = "#C8C8C8"
FS = {"font.size": 12, "axes.labelsize": 12, "axes.titlesize": 12, "xtick.labelsize": 11, "ytick.labelsize": 11,
      "legend.fontsize": 10.5, "axes.unicode_minus": False, "axes.linewidth": 0.7}
W = 7.4  # figure width in inches (slide content width ~ 12 in; scaled up on the slide)
ERR = {"elinewidth": 0.9, "capsize": 3, "capthick": 0.9}
HEAT = json.loads((EXP / "heat/out/results.json").read_text(encoding="utf-8"))
YEARS = [2022, 2023, 2024, 2025]
KEY = "B1|per_day"
LAB = {"B1": "특보 규칙", "B2": "시도 단위 모델", "HEUR": "인구·취약성 반영", "OURS": "우리 엔진(시군구)"}
COL = {"B1": GRAY, "B2": LGRAY, "HEUR": LBLUE, "OURS": BLUE}
VLAB = {"F": "입력: 당일·익일 체감온도(예보 대용)", "O": "입력: 전날까지 관측만"}
MANIFEST: dict[str, dict[str, str]] = {}


def pct(x: float) -> float:
    return 100.0 * x


def setup() -> None:
    use_style()
    plt.rcParams.update(FS)


def out(fig: plt.Figure, name: str, src: str, key: str) -> None:
    save_figure(fig, name, FINAL)
    MANIFEST[name] = {"source": src, "key": key}


def cap(blk: dict) -> tuple[float, float, float]:
    c = blk["capture"]
    return pct(c["est"]), pct(c["lo"]), pct(c["hi"])


def errbars(vals: list[tuple[float, float, float]]) -> list[list[float]]:
    return [[v[0] - v[1] for v in vals], [v[2] - v[0] for v in vals]]


# ----------------------------------------------------------------------------------- heat
def fig_heat_pooled_year() -> None:
    setup()
    fig, (a, b) = plt.subplots(1, 2, figsize=(8.7, 3.85), gridspec_kw={"width_ratios": [1, 2.3]}, layout="constrained")
    P = HEAT["variants"]["F"]["pooled_2022_2025"][KEY]
    m = P["methods"]
    vals = [cap(m["B1"]), cap(m["OURS"])]
    a.bar([0, 1], [v[0] for v in vals], 0.62, yerr=errbars(vals), color=[GRAY, BLUE], error_kw=ERR)
    for i, v in enumerate(vals):
        a.text(i, v[2] + 2.0, f"{v[0]:.0f}%", ha="center", fontsize=20, fontweight="bold", color="k")
    a.set_xticks([0, 1], ["특보 규칙", "에이전트"])
    a.set_ylim(0, 100)
    a.set_ylabel("포착률 % (경보 지역 환자 ÷ 전체 환자)")
    d = P["delta"]["OURS-B1"]["capture"]
    a.set_title(f"4년 통합  +{pct(d['est']):.1f}%p\n(95% 신뢰구간 {pct(d['lo']):.1f}~{pct(d['hi']):.1f})", fontsize=11.5)
    methods = ["B1", "B2", "HEUR", "OURS"]
    n, w = len(methods), 0.8 / len(methods)
    x = np.arange(len(YEARS))
    for k, mm in enumerate(methods):
        pts = [cap(HEAT["variants"]["F"]["folds"][str(y)]["budget"][KEY]["methods"][mm]) for y in YEARS]
        b.bar(x + (k - (n - 1) / 2) * w, [p[0] for p in pts], w * 0.92, yerr=errbars(pts), color=COL[mm], label=LAB[mm], error_kw=ERR)
    b.set_xticks(x, [str(y) for y in YEARS])
    b.set_ylim(0, 100)
    b.set_xlabel("시험 연도 (각 해는 이전 연도까지만 학습)")
    b.set_ylabel("포착률 %")
    b.legend(loc="upper left", ncol=2, fontsize=10)
    out(fig, "01_heat_pooled_and_by_year", "experiments/heat/out/results.json", "variants.F.pooled_2022_2025[B1|per_day] ; variants.F.folds.<year>.budget[B1|per_day]")


def fig_heat_decomp() -> None:
    setup()
    fig, ax = plt.subplots(figsize=(8.7, 3.85), layout="constrained")
    steps = [("B2", "B1"), ("HEUR", "B2"), ("OURS", "HEUR")]
    names = ["특보 규칙 →\n시도 단위 모델", "시도 단위 모델 →\n+ 인구·취약성 반영", "+ 인구·취약성 반영 →\n시군구 모델(에이전트)"]
    w = 0.38
    for k, v in enumerate(("F", "O")):
        d = HEAT["variants"][v]["pooled_2022_2025"][KEY]["delta"]
        vals = [cap(d[f"{a}-{b}"]) for a, b in steps]
        ax.bar(np.arange(3) + (k - 0.5) * w, [x[0] for x in vals], w * 0.92, yerr=errbars(vals), color=BLUE if v == "F" else LBLUE,
               label=VLAB[v], error_kw=ERR)
        for i, x in enumerate(vals):
            ax.text(i + (k - 0.5) * w, x[2] + 0.35, f"{x[0]:+.1f}", ha="center", fontsize=10.5)
    ax.axhline(0, color="0.4", lw=0.6)
    ax.set_xticks(np.arange(3), names, fontsize=10.5)
    ax.set_ylabel("포착률 증가 (%p, 4년 통합)")
    ax.legend(loc="upper right")
    out(fig, "02_heat_component_gain", "experiments/heat/out/results.json", "variants.{F,O}.pooled_2022_2025[B1|per_day].delta[B2-B1, HEUR-B2, OURS-HEUR].capture")


def fig_heat_forest() -> None:
    setup()
    fig, ax = plt.subplots(figsize=(9.0, 3.85), layout="constrained")
    rows = []
    for v in ("F", "O"):
        for t in [*YEARS, "통합"]:
            blk = HEAT["variants"][v]["pooled_2022_2025"][KEY] if t == "통합" else HEAT["variants"][v]["folds"][str(t)]["budget"][KEY]
            rows.append((v, str(t), cap(blk["delta"]["OURS-B1"])))
    y = np.arange(len(rows))[::-1]
    for yi, (v, t, e) in zip(y, rows):
        ax.errorbar(e[0], yi, xerr=[[e[0] - e[1]], [e[2] - e[0]]], fmt="D" if t == "통합" else "o", ms=5, color=BLUE if v == "F" else GRAY, **{"elinewidth": 1.0, "capsize": 3})
    ax.axvline(0, color="0.4", lw=0.7)
    ax.set_yticks(y, [f"{'당일·익일 입력' if v == 'F' else '전날까지 입력'}  {t}" for v, t, _ in rows], fontsize=10.5)
    ax.set_xlabel("포착률 차이 (에이전트 − 특보 규칙, %p)  점 = 추정, 선 = 95% 신뢰구간")
    out(fig, "A3_heat_forest_ours_minus_rule", "experiments/heat/out/results.json", "variants.{F,O}.{folds.<y>.budget,pooled_2022_2025}[B1|per_day].delta[OURS-B1].capture")


def fig_heat_budget() -> None:
    setup()
    fig, ax = plt.subplots(figsize=(9.0, 3.85), layout="constrained")
    sw = HEAT["variants"]["F"]["sweep"]["2025"]
    thr = pd.DataFrame(sw["b1_threshold_curve"])
    ax.plot(100 * thr.alert_share, 100 * thr.capture, color="0.7", ls="--", lw=1.0, label="특보 기준 온도를 28~36℃로 바꿔 본 경우")
    for mm in ["B1", "B2", "HEUR", "OURS"]:
        c = pd.DataFrame(sw["curves"][mm])
        ax.plot(100 * c.alert_share, 100 * c.capture, color=COL[mm] if mm != "B2" else "#9A9A9A", lw=1.8 if mm == "OURS" else 1.2, ls=":" if mm == "B2" else "-", label=LAB[mm])
        if mm == "B1":
            one = c[c.scale == 1.0].iloc[0]
            ax.plot(100 * one.alert_share, 100 * one.capture, "o", color="k", ms=7, zorder=5, label="특보 규칙의 실제 경보량 (비교 기준점)")
    ax.set_xlabel("경보 낸 시군구-일 비율 % (특보 경보량의 0.25~2배)")
    ax.set_ylabel("포착률 %")
    ax.set_title("2025년, 입력: 당일·익일 체감온도", fontsize=11)
    ax.legend(loc="lower right", fontsize=9.5)
    out(fig, "A6_heat_budget_curve_2025", "experiments/heat/out/results.json", "variants.F.sweep.2025.{curves,b1_threshold_curve}")


def fig_heat_precision() -> None:
    setup()
    fig, ax = plt.subplots(figsize=(7.0, 3.85), layout="constrained")
    m = HEAT["variants"]["F"]["pooled_2022_2025"][KEY]["methods"]
    vals = []
    for mm in ("B1", "OURS"):
        p = m[mm]["precision"]
        vals.append((pct(p["est"]), pct(p["lo"]), pct(p["hi"])))
    ax.bar([0, 1], [v[0] for v in vals], 0.55, yerr=errbars(vals), color=[GRAY, BLUE], error_kw=ERR)
    for i, v in enumerate(vals):
        ax.text(i, v[2] + 1.0, f"{v[0]:.1f}%", ha="center", fontsize=16, fontweight="bold")
    ax.set_xticks([0, 1], ["특보 규칙", "에이전트"])
    ax.set_ylim(0, 45)
    ax.set_ylabel("정밀도 % (경보 중 실제 환자 발생 비율)")
    out(fig, "A2_heat_precision", "experiments/heat/out/results.json", "variants.F.pooled_2022_2025[B1|per_day].methods.{B1,OURS}.precision")


def fig_heat_variants() -> None:
    setup()
    fig, axs = plt.subplots(1, 2, figsize=(10.0, 3.85), sharey=True, layout="constrained")
    for ax, v in zip(axs, ("F", "O")):
        m = HEAT["variants"][v]["pooled_2022_2025"][KEY]["methods"]
        vals = [cap(m[k]) for k in ("B1", "B2", "HEUR", "OURS")]
        ax.bar(range(4), [x[0] for x in vals], 0.65, yerr=errbars(vals), color=[COL[k] for k in ("B1", "B2", "HEUR", "OURS")], error_kw=ERR)
        for i, x in enumerate(vals):
            ax.text(i, x[2] + 1.5, f"{x[0]:.1f}", ha="center", fontsize=10.5)
        ax.set_xticks(range(4), ["특보\n규칙", "시도\n모델", "인구·취약\n성 반영", "에이전트"], fontsize=10)
        ax.set_title(VLAB[v], fontsize=10.5)
        ax.set_ylim(0, 100)
    axs[0].set_ylabel("포착률 % (4년 통합)")
    out(fig, "A4_heat_models_F_and_O", "experiments/heat/out/results.json", "variants.{F,O}.pooled_2022_2025[B1|per_day].methods.*.capture")


# --------------------------------------------------------------------------------- silver
SILVER_PROMOTED = EXP / "silver/out/opt/promoted/coverage_results.csv"  # method ours_opt (nested inner validation, leak fixed)
SILVER_OLD = EXP / "silver/out/coverage_results.csv"  # only for random_k (independent of our method; same pool C, n=735)
SC = pd.read_csv(SILVER_PROMOTED)
SC_OLD = pd.read_csv(SILVER_OLD)
SLAB = {"current": "현행 노인보호구역", "random_k": "무작위", "pop65_k": "노인 인구", "past_k": "작년 사고 장소", "ours_opt": "에이전트"}


def _sc(stratum: str, method: str, k: int = 3918) -> tuple[float, float, float, int]:
    if method == "random_k":
        r = SC_OLD[(SC_OLD.variant == "harm") & (SC_OLD.pool == "C") & (SC_OLD.stratum == stratum) & (SC_OLD.method == method) & (SC_OLD.k == k)].iloc[0]
    else:
        pool = "OPT" if method == "ours_opt" else "C"
        r = SC[(SC.variant == "harm") & (SC.pool == pool) & (SC.stratum == stratum) & (SC.method == method) & (SC.k == k)].iloc[0]
    return pct(r.coverage), pct(r.lo), pct(r.hi), int(r.n_hot)


def fig_silver_main() -> None:
    setup()
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(8.7, 3.85), gridspec_kw={"width_ratios": [2.3, 1]}, layout="constrained")
    order = ["current", "random_k", "pop65_k", "past_k", "ours_opt"]
    vals = [_sc("all", m)[:3] for m in order]
    ax.bar(range(5), [v[0] for v in vals], 0.62, yerr=errbars(vals), color=[GRAY, GRAY, GRAY, GRAY, BLUE], error_kw=ERR)
    for i, v in enumerate(vals):
        ax.text(i, v[2] + 2.0, f"{v[0]:.0f}%", ha="center", fontsize=18 if i in (0, 4) else 13, fontweight="bold" if i in (0, 4) else "normal")
    ax.set_xticks(range(5), [SLAB[m].replace(" ", "\n", 1) for m in order], fontsize=9.5)
    ax.set_ylim(0, 105)
    ax.set_ylabel("덮은 사고 지점 % (반경 300 m)")
    ax.set_xlabel("같은 개수(3,918곳) · 다음 해 사고 735곳", fontsize=10.5)
    nv = [_sc("new", m)[:3] for m in ("current", "past_k", "ours_opt")]
    bx.bar(range(3), [v[0] for v in nv], 0.6, yerr=errbars(nv), color=[GRAY, GRAY, BLUE], error_kw=ERR)
    for i, v in enumerate(nv):
        bx.text(i, v[2] + 2.0, f"{v[0]:.0f}%", ha="center", fontsize=16, fontweight="bold")
    bx.set_xticks(range(3), ["현행", "작년\n사고 장소", "에이전트"], fontsize=9.5)
    bx.set_ylim(0, 105)
    bx.set_title(f"새로 생긴 사고 지점 (n={_sc('new', 'ours_opt')[3]})", fontsize=10.5)
    out(fig, "05_silver_coverage_bars", "experiments/silver/out/opt/promoted/coverage_results.csv (+ random_k: silver/out/coverage_results.csv)", "variant=harm, k=3918, pool C (current/pop65_k), pool OPT (ours_opt), strata all/new")


def fig_silver_appendix() -> None:
    setup()
    fig, (a, b) = plt.subplots(1, 2, figsize=(12.4, 3.45), gridspec_kw={"width_ratios": [1.15, 1.35]}, layout="constrained")
    ks = [250, 500, 1000, 2000, 3918]
    for m, c, ls in (("random_k", GRAY, "-"), ("pop65_k", "#7A7A7A", "--"), ("past_k", ORANGE, "-."), ("ours_opt", BLUE, "-")):
        ys = [_sc("all", m, k)[0] for k in ks]
        a.plot(ks, ys, color=c, ls=ls, lw=2.0 if m == "ours_opt" else 1.3, marker="o", ms=4, label=SLAB[m])
    cur = _sc("all", "current")
    a.plot([3918], [cur[0]], "s", color="k", ms=7, label=SLAB["current"] + " (3,918곳)")
    a.set_xscale("log")
    a.set_xticks(ks, [f"{k:,}" for k in ks], fontsize=10)
    a.set_xlabel("고른 지점 수 k")
    a.set_ylabel("덮은 사고 지점 % (전체)")
    a.set_ylim(0, 100)
    a.legend(loc="lower right", fontsize=9.5)
    order = ["current", "pop65_k", "past_k", "ours_opt"]
    w = 0.38
    for j, st in enumerate(("all", "new")):
        vals = [_sc(st, m)[:3] for m in order]
        cols = [GRAY, GRAY, ORANGE, BLUE]
        xs = np.arange(4) + (j - 0.5) * w
        b.bar(xs, [v[0] for v in vals], w * 0.92, yerr=errbars(vals), color=cols, alpha=1.0 if j == 0 else 0.55, error_kw=ERR)
        for x, v in zip(xs, vals):
            b.text(x, v[2] + 1.5, f"{v[0]:.0f}", ha="center", fontsize=9)
    n_all, n_new = _sc("all", "current")[3], _sc("new", "current")[3]
    b.set_xticks(range(4), [SLAB[m].replace(" ", "\n", 1) for m in order], fontsize=9.5)
    b.set_ylim(0, 120)
    b.set_yticks(range(0, 101, 20))
    b.set_ylabel("덮은 사고 지점 % (k=3,918)")
    b.legend(handles=[plt.Rectangle((0, 0), 1, 1, color="0.3", label=f"전체 (n={n_all}, 진한 색)"),
                      plt.Rectangle((0, 0), 1, 1, color="0.3", alpha=0.5, label=f"새 사고 지점 (n={n_new}, 연한 색)")],
             loc="upper center", ncol=2, fontsize=9.5)
    out(fig, "A1_silver_past_vs_ours", "experiments/silver/out/opt/promoted/coverage_results.csv (+ random_k: silver/out/coverage_results.csv)", "variant=harm, pool C/OPT, strata all/new, k in {250..3918}")


def fig_silver119() -> None:
    setup()
    R = json.loads((EXP / "validate119/out/results.json").read_text(encoding="utf-8"))["silver_opt"]["misallocation_tvd"]
    fig, ax = plt.subplots(figsize=(8.7, 3.85), layout="constrained")
    keys = [("현행 노인보호구역", "현행 노인보호구역"), ("영점 선정 지점(opt)", "에이전트 (ours_opt)"), ("65세 이상 인구", "65세 이상 인구")]
    vals = [(pct(R[k]["tvd"]), pct(R[k]["ci"][0]), pct(R[k]["ci"][1])) for k, _ in keys]
    ax.bar(range(3), [v[0] for v in vals], 0.58, yerr=errbars(vals), color=[GRAY, BLUE, GRAY], error_kw=ERR)
    for i, v in enumerate(vals):
        ax.text(i, v[2] + 1.0, f"{v[0]:.1f}%", ha="center", fontsize=17 if i < 2 else 13, fontweight="bold" if i < 2 else "normal")
    ax.set_xticks(range(3), [n for _, n in keys])
    ax.set_ylim(0, 32)
    ax.set_ylabel("엉뚱한 구에 배치된 비율 % (낮을수록 좋음)")
    ax.set_xlabel("정답 = 119 구급 기록의 70세 이상 보행 사고 분포 (서울 25개 구)", fontsize=10.5)
    out(fig, "A10_silver_119_misallocation_opt", "experiments/validate119/out/results.json", "silver_opt.misallocation_tvd.{현행 노인보호구역, 영점 선정 지점(opt), 65세 이상 인구}.{tvd,ci}")


# ------------------------------------------------------------------------------------ aed
def fig_aed() -> None:
    setup()
    H = pd.read_csv(EXP / "aed/out/hourly_gap.csv")
    S = json.loads((EXP / "aed/out/summary.json").read_text(encoding="utf-8"))["desc"]["weekday"]
    fig, ax = plt.subplots(figsize=(12.4, 3.85), layout="constrained")
    ax.bar(H.hour, H.public_ca, color=GRAY, width=0.8, label="공공장소 심정지 (건, 2024.1~11)")
    ax.set_xlabel("출동 시각 (시)")
    ax.set_ylabel("심정지 건수")
    ax.set_xticks(range(0, 24, 3))
    ax.axvspan(-0.5, 5.5, color="0.93", zorder=0)
    ax.axvspan(21.5, 23.5, color="0.93", zorder=0)
    ax2 = ax.twinx()
    ax2.spines["right"].set_visible(True)
    ax2.plot(H.hour, H.pct_open_weekday, color=BLUE, lw=2.2, label="열린 AED 비율 (평일)")
    ax2.plot(H.hour, H.pct_open_weekend_W1_as_reported, color=BLUE, lw=1.1, ls=":", label="열린 AED 비율 (주말, 공시값 그대로)")
    ax2.set_ylim(0, 100)
    ax2.set_ylabel("열린 AED 비율 %")
    ax.text(0.62, 1.03, f"낮(06~22시) 평균 {S['mean_pct_open_day']:.1f}%", transform=ax.transAxes, ha="center", fontsize=13, color=BLUE, fontweight="bold")
    ax.text(0.14, 1.03, f"밤(22~06시) 평균 {S['mean_pct_open_night']:.1f}%", transform=ax.transAxes, ha="center", fontsize=13, color=BLUE, fontweight="bold")
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper center", bbox_to_anchor=(0.5, -0.17), ncol=3, fontsize=9.5)
    out(fig, "07_aed_night_gap", "experiments/aed/out/hourly_gap.csv + summary.json", "hourly_gap.csv ; summary.desc.weekday.mean_pct_open_{day,night}")


# ----------------------------------------------------------------------------------- main-body evidence figures (projector sized)
MAIN_FS = {"font.size": 15, "axes.labelsize": 15, "xtick.labelsize": 15, "ytick.labelsize": 14, "legend.fontsize": 14}


def fig_heat_main() -> None:
    """Same-alert-budget capture per test year and pooled: 특보 규칙 (gray) vs 에이전트 (blue), 95% CI."""
    setup()
    plt.rcParams.update(MAIN_FS)
    fig, ax = plt.subplots(figsize=(8.7, 3.6), layout="constrained")
    groups = [str(y) for y in YEARS] + ["4년 통합"]
    P = HEAT["variants"]["F"]["pooled_2022_2025"][KEY]["methods"]
    data = {mm: [cap(HEAT["variants"]["F"]["folds"][str(y)]["budget"][KEY]["methods"][mm]) for y in YEARS] + [cap(P[mm])] for mm in ("B1", "OURS")}
    x = np.arange(len(groups))
    w = 0.38
    for k, (mm, col, lab) in enumerate((("B1", GRAY, "특보 규칙"), ("OURS", BLUE, "에이전트"))):
        pts = data[mm]
        ax.bar(x + (k - 0.5) * w, [p[0] for p in pts], w * 0.94, yerr=errbars(pts), color=col, label=lab, error_kw=ERR)
        for xi, pnt in zip(x, pts):
            big = xi == len(groups) - 1
            ax.text(xi + (k - 0.5) * w, pnt[2] + 1.5, f"{pnt[0]:.0f}", ha="center", fontsize=17 if big else 13, fontweight="bold" if big else "normal")
    ax.axvline(len(groups) - 1.5, color="0.7", lw=0.8, ls="--")
    ax.set_xticks(x, groups)
    ax.set_ylim(0, 100)
    ax.set_ylabel("포착률 %")
    ax.legend(loc="upper left", ncol=2, frameon=False)
    out(fig, "01m_heat_by_year", "experiments/heat/out/results.json", "variants.F.folds.<y>.budget[B1|per_day].methods.{B1,OURS}.capture ; variants.F.pooled_2022_2025")


def fig_map_main() -> None:
    """Seoul-only map, two panels: current 노인보호구역 vs 에이전트 zones (300 m circles); hotspots covered = black, uncovered = orange."""
    setup()
    plt.rcParams.update(MAIN_FS)
    d = silver_src.load_map()
    d = d[d.in_seoul]
    h = d[d.kind == "hotspot_2024"]
    lat0, lon0 = h.lat.min() - 0.01, h.lon.min() - 0.01
    kx, ky = 111.32 * np.cos(np.radians(37.5)), 110.57
    X = lambda lon: (lon - lon0) * kx  # noqa: E731
    Y = lambda lat: (lat - lat0) * ky  # noqa: E731
    xmax, ymax = X(h.lon.max() + 0.01), Y(h.lat.max() + 0.01)
    fig, axs = plt.subplots(1, 2, figsize=(12.4, 4.0))
    fig.subplots_adjust(left=0.005, right=0.995, top=0.84, bottom=0.13, wspace=0.02)
    for ax, kind, col, flag, title, tcol in ((axs[0], "current", GRAY, "cur_within_300m", "현행 노인보호구역", "k"), (axs[1], "ours", BLUE, "ours_within_300m", "에이전트 제안", BLUE)):
        z = d[d.kind == kind]
        for lat, lon in zip(z.lat, z.lon):
            ax.add_patch(plt.Circle((X(lon), Y(lat)), 0.3, color=col, alpha=0.4, lw=0))
        cov, unc = h[h[flag] == 1], h[h[flag] == 0]
        ax.scatter(X(unc.lon), Y(unc.lat), s=46, color=ORANGE, edgecolor="white", lw=0.6, zorder=3)
        ax.scatter(X(cov.lon), Y(cov.lat), s=46, color="k", edgecolor="white", lw=0.6, zorder=4)
        ax.set_xlim(0, xmax)
        ax.set_ylim(0, ymax)
        ax.set_aspect("equal", adjustable="datalim")
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_title(f"{title}", fontsize=20, fontweight="bold", color=tcol, pad=8)
        for sp in ax.spines.values():
            sp.set_visible(False)
    fig.legend(handles=[plt.Line2D([], [], marker="o", ls="", color="k", ms=9, label="사고 다발지, 구역 안"), plt.Line2D([], [], marker="o", ls="", color=ORANGE, ms=9, label="사고 다발지, 구역 밖"),
                        plt.Line2D([], [], marker="o", ls="", color="0.6", ms=12, alpha=0.6, label="보호구역 (반경 300 m)")], loc="lower center", ncol=3, frameon=False, fontsize=14)
    out(fig, "17_map_current_vs_ours", silver_src.SRC, "kind in {current, ours, hotspot_2024}, in_seoul, cur_within_300m / ours_within_300m")


def fig_aed_main() -> None:
    """Hourly share of AEDs open (Seoul weekday) with night shading; day/night means annotated."""
    setup()
    plt.rcParams.update(MAIN_FS)
    H = pd.read_csv(EXP / "aed/out/hourly_gap.csv")
    S = json.loads((EXP / "aed/out/summary.json").read_text(encoding="utf-8"))["desc"]["weekday"]
    fig, ax = plt.subplots(figsize=(8.7, 3.6), layout="constrained")
    ax.axvspan(-0.5, 5.5, color="0.9", zorder=0)
    ax.axvspan(21.5, 23.5, color="0.9", zorder=0)
    ax.plot(H.hour, H.pct_open_weekday, color=BLUE, lw=3.6)
    ax.set_xlim(-0.5, 23.5)
    ax.set_ylim(0, 100)
    ax.set_xticks(range(0, 24, 3))
    ax.set_xlabel("시각 (시)")
    ax.set_ylabel("열린 AED 비율 %")
    ax.text(0.5, 14, f"밤 22~06시\n평균 {S['mean_pct_open_night']:.0f}%", fontsize=16, fontweight="bold", color=BLUE, va="center")
    ax.text(13.5, 72, f"낮 06~22시 평균 {S['mean_pct_open_day']:.0f}%", fontsize=16, fontweight="bold", color=BLUE, ha="center")
    out(fig, "07m_aed_hourly", "experiments/aed/out/hourly_gap.csv + summary.json", "hourly_gap.csv pct_open_weekday ; summary.desc.weekday.mean_pct_open_{day,night}")


# ----------------------------------------------------------------------------------- fall
def fig_fall(with_fa: bool = False) -> None:
    setup()
    C = fall_src.load()[0]
    mets = [("sens", "감도 % (낙상을 잡은 비율)", 100.0), ("f1", "F1 (%)", 100.0), ("lat_med", "탐지 지연 초 (낮을수록 좋음)", 1.0)]
    if with_fa:
        mets.append(("fa_per_h", "오경보 / 시간 (참고용)", 1.0))
    fig, axs = plt.subplots(1, len(mets), figsize=(8.7, 3.85), layout="constrained")
    models = [("rule", "규칙", GRAY), ("logreg", "경량 모델", BLUE)]
    for ax, (k, lab, sc) in zip(axs, mets):
        vals = [tuple(sc * x for x in C[m][k]) for m, _, _ in models]
        ax.bar(range(2), [v[0] for v in vals], 0.6, yerr=[[v[0] - v[1] for v in vals], [v[2] - v[0] for v in vals]], color=[c for *_, c in models], error_kw=ERR)
        for i, v in enumerate(vals):
            ax.text(i, v[2] + (0.03 * max(x[2] for x in vals)), f"{v[0]:.0f}" if sc == 100.0 or k == "fa_per_h" else f"{v[0]:.2f}", ha="center", fontsize=15, fontweight="bold")
        ax.set_xticks(range(2), [n for _, n, _ in models])
        ax.set_ylabel(lab, fontsize=10.5)
        ax.set_ylim(0, max(x[2] for x in vals) * (1.15 if sc != 100.0 else 1.12))
        if sc == 100.0:
            ax.set_yticks(range(0, 101, 20))
    fig.supxlabel("UR Fall 공개 데이터 (낙상 30건 · 일상 40건), 5겹 교차검증, 막대 위 선 = 95% 신뢰구간", fontsize=10)
    out(fig, "A7_fall_with_false_alarm" if with_fa else "08_fall_model", fall_src.SRC, "rule/logreg.{sens,f1,lat_med" + (",fa_per_h" if with_fa else "") + "}")


# ---------------------------------------------------------------------------- agent figures
def normalize_bench(b: object) -> list[dict]:
    """Old format: list of full agent_run records (per-step ms). New format: summary dict with runs[*].llm_turns only."""
    if isinstance(b, list):
        return b
    out_runs = []
    for r in b["runs"]:  # type: ignore[index]
        llm = sum(t["ms"] for t in r["llm_turns"])
        out_runs.append({"run_id": r["run_id"], "total_ms": r["total_ms"], "plan": {"llm_turns": r["llm_turns"], "first_try_valid": r["first_try_valid"]},
                         "steps": None, "n_steps": r["steps"], "grounding": {"ok": r["grounding_ok"]}, "tool_sum_ms": r["total_ms"] - llm})
    return out_runs


def bench() -> list[dict]:
    return normalize_bench(json.loads(BENCH.read_text(encoding="utf-8")))


def first_step_ms() -> float | None:
    p = Path(os.environ.get("KCF_BENCH_STREAM_JSON", str(BENCH.parent / "after_stream.json")))
    if p.is_file():
        d = json.loads(p.read_text(encoding="utf-8"))
        return d.get("first_step_p50_ms")
    return None


def agent_stats() -> dict[str, float]:
    runs = bench()
    tool: dict[str, list[float]] = {}
    plan, brief = [], []
    for r in runs:
        for s in (r["steps"] or []):
            tool.setdefault(s["tool"], []).append(s["ms"])
        for t in r["plan"]["llm_turns"]:
            (plan if t["kind"] == "plan" else brief).append(t["ms"])
    st = {f"tool_{k}": statistics.median(v) for k, v in tool.items()}
    st["plan_p50"] = statistics.median(plan)
    st["brief_p50"] = statistics.median(brief)
    st["total_p50"] = statistics.median(r["total_ms"] for r in runs)
    tots = sorted(r["total_ms"] for r in runs)
    st["total_p95"] = tots[int(round(0.95 * (len(tots) - 1)))] if len(tots) < 20 else float(np.percentile(tots, 95))
    if all(r["steps"] is None for r in runs):
        st["tool_sum_p50"] = statistics.median(r["tool_sum_ms"] for r in runs)
    fs = first_step_ms()
    if fs is not None:
        st["first_step"] = fs
    st["n_runs"] = len(runs)
    st["n_plan_turns"] = len(plan)
    return st


def _box(ax: plt.Axes, x: float, y: float, w: float, h: float, text: str, accent: bool = False) -> None:
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.0,rounding_size=0.06", fc=LBLUE if accent else "white", ec="k", lw=0.9))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=13, fontweight="bold" if accent else "normal")


def _arrow(ax: plt.Axes, p0: tuple[float, float], p1: tuple[float, float], label: str = "", dy: float = 0.2, rad: float = 0.0) -> None:
    ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle="-|>", mutation_scale=13, lw=1.0, color="k", connectionstyle=f"arc3,rad={rad}"))
    if label:
        ax.text((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2 + dy, label, ha="center", va="bottom", fontsize=11, color=BLUE)


def fig_agent_flow() -> None:
    setup()
    st = agent_stats()
    C = fall_src.load()[0]
    fig, ax = plt.subplots(figsize=(12.4, 3.85), layout="constrained")
    ax.set_xlim(0, 13.4)
    ax.set_ylim(-0.3, 5.4)
    ax.axis("off")
    bw, bh = 1.6, 1.0
    top = [("현장 카메라", False), ("자세 추정\nMediaPipe", False), ("낙상 모델", False), ("분석 엔진", True), ("로컬 LLM 계획\nqwen2.5:3b", False)]
    bot = [("도구 실행", False), ("CAP · FHIR\n생성", False), ("RSU · 119\n시뮬레이터 (202)", False), ("감사 로그", False)]
    pitch = 2.8
    xs_top = [0.2 + i * pitch for i in range(5)]
    xs_bot = [0.2 + i * pitch for i in range(4)]
    yt, yb = 4.15, 1.45
    for (t, a), x in zip(top, xs_top):
        _box(ax, x, yt, bw, bh, t, a)
    for (t, a), x in zip(bot, xs_bot):
        _box(ax, x, yb, bw, bh, t, a)
    lab_top = ["", "", f"탐지 지연\n{C['logreg']['lat_med'][0]:.2f} s*", f"계획 1회\np50 {st['plan_p50']:,.0f} ms"]
    for i in range(4):
        _arrow(ax, (xs_top[i] + bw, yt + bh / 2), (xs_top[i + 1], yt + bh / 2), lab_top[i], dy=0.12)
    xe, ymid = xs_top[4] + bw / 2, 3.2
    ax.plot([xe, xe], [yt, ymid], color="k", lw=1.0)
    ax.plot([xe, xs_bot[0] + bw / 2], [ymid, ymid], color="k", lw=1.0)
    _arrow(ax, (xs_bot[0] + bw / 2, ymid), (xs_bot[0] + bw / 2, yb + bh))
    new_fmt = "tool_get_heat_region" not in st
    if new_fmt:
        first = f" · 첫 단계 {st['first_step']:,.0f} ms (스트리밍)" if "first_step" in st else ""
        ax.text(6.0, ymid - 0.12, f"도구 6개 합 p50 {st['tool_sum_p50']:,.0f} ms{first}", ha="center", va="top", fontsize=11.5, color=BLUE)
        lab_bot = ["CAP: OASIS XSD\n검증 통과", "FHIR: 공식 검증기\n오류 0 · 경고 0", f"브리핑\np50 {st['brief_p50']:,.0f} ms"]
    else:
        ax.text(6.0, ymid - 0.12, f"엔진 조회  get_heat_region p50 {st['tool_get_heat_region']:,.0f} ms · get_actions p50 {st['tool_get_actions']:,.0f} ms",
                ha="center", va="top", fontsize=11.5, color=BLUE)
        lab_bot = [f"CAP p50 {st['tool_make_cap_alert']:.2f} ms\nFHIR p50 {st['tool_make_fhir_bundle']:.2f} ms",
                   f"RSU p50 {st['tool_send_rsu_message']:,.0f} ms\n119 p50 {st['tool_send_119_report']:,.0f} ms",
                   f"브리핑\np50 {st['brief_p50']:,.0f} ms"]
    for i in range(3):
        _arrow(ax, (xs_bot[i] + bw, yb + bh / 2), (xs_bot[i + 1], yb + bh / 2), lab_bot[i], dy=0.12)
    p95 = f" · p95 {st['total_p95']:,.0f} ms" if "total_p95" in st else ""
    ax.text(6.7, 0.5, f"한 번 실행 전체 p50 {st['total_p50']:,.0f} ms{p95} (10회)", ha="center", fontsize=14, fontweight="bold")
    ax.text(6.7, -0.1, "* 낙상 모델 탐지 지연은 UR Fall 교차검증 중앙값, 나머지는 에이전트 10회 실행 중앙값 (2025-07-22 관악구). 라벨 없는 화살표는 미측정.",
            ha="center", fontsize=9.2, color="0.3")
    out(fig, "03_agent_flow_measured", "bench.json (scratchpad) + " + fall_src.SRC, "runs[*].steps[*].ms ; runs[*].plan.llm_turns ; runs[*].total_ms ; logreg.lat_med")


def fig_agent_timeline(run_id: str | None = None) -> None:
    setup()
    runs = bench()
    med = statistics.median(x["total_ms"] for x in runs)
    ids = {x["run_id"] for x in runs}
    run_id = run_id or os.environ.get("KCF_TIMELINE_RUN") or ("run-eba0c430cbbb" if "run-eba0c430cbbb" in ids else min(runs, key=lambda x: abs(x["total_ms"] - med))["run_id"])
    r = next(x for x in runs if x["run_id"] == run_id)
    turns, steps = r["plan"]["llm_turns"], r["steps"]
    plan_turns = [t for t in turns if t["kind"] == "plan"]
    brief = next(t for t in turns if t["kind"] == "brief")
    derived = steps is None
    events: list[tuple[str, str, float]] = []
    if steps is None:  # new agent: one plan turn, tools run back to back (sum derived = total - LLM turns), then briefing
        events = [("LLM", "계획", plan_turns[0]["ms"]), ("도구", "도구 6개 합", r["tool_sum_ms"]), ("LLM", "브리핑", brief["ms"])]
        steps = [{"tool": "도구 6개 합", "ms": r["tool_sum_ms"]}]
    else:
        # Sequential reconstruction: plan turn i is followed by tool step i (run records give durations, not wall-clock starts).
        for i, s in enumerate(steps):
            events.append(("LLM", f"계획 {i + 1}", plan_turns[i]["ms"]))
            events.append(("도구", s["tool"], s["ms"]))
        events.append(("LLM", "브리핑", brief["ms"]))
    t0 = 0.0
    fig, ax = plt.subplots(figsize=(12.4, 3.85), layout="constrained")
    lanes = {"LLM": 1, "도구": 0}
    labels_done = set()
    for lane, name, ms in events:
        ax.barh(lanes[lane], ms, left=t0, height=0.55, color=BLUE if lane == "LLM" else ORANGE, ec="white", lw=0.5,
                label=("로컬 LLM (계획·브리핑)" if lane == "LLM" else "도구 호출") if lane not in labels_done else None)
        labels_done.add(lane)
        if ms >= 600:
            ax.text(t0 + ms / 2, lanes[lane], f"{ms:,.0f}", ha="center", va="center", fontsize=9.5, color="white", fontweight="bold")
        elif lane == "도구" and ms > 100:
            ax.text(t0 + ms / 2, lanes[lane] + 0.38, f"{name}\n{ms:,.0f}", ha="center", va="bottom", fontsize=8.5)
        t0 += ms
    acc = 0.0
    for lane, name, ms in events:
        if lane == "도구" and not derived:
            ax.text(acc + ms / 2, -0.52, str(next(i + 1 for i, s in enumerate(steps) if s["tool"] == name)), ha="center", fontsize=8.8)
        acc += ms
    ax.set_yticks([0, 1], ["도구 호출", "로컬 LLM"])
    ax.set_ylim(-0.85, 2.4)
    ax.set_xlabel("누적 시간 (ms)  막대 안 숫자 = 그 단계 걸린 시간 (ms)")
    tool_total = sum(s["ms"] for s in steps)
    ax.text(0.99, 0.98, f"{run_id} · 단계 합 {t0:,.0f} ms (총 {r['total_ms']:,.0f} ms)\n도구 6개 합 {tool_total:,.0f} ms", transform=ax.transAxes, ha="right", va="top", fontsize=11)
    ax.legend(loc="upper left", fontsize=10)
    if not derived:
        ax.text(0.0, -0.22, "도구 번호: 1 get_heat_region · 2 get_actions · 3 make_cap_alert · 4 make_fhir_bundle · 5 send_rsu_message · 6 send_119_report", transform=ax.transAxes, fontsize=8.8)
    else:
        ax.text(0.0, -0.22, "도구 6개 합 = 전체 시간 − 언어 모델 두 번(계획·브리핑)으로 계산한 값, 도구별 시간은 이번 측정에 없음", transform=ax.transAxes, fontsize=9.5)
    out(fig, "04_agent_run_timeline", "bench.json (scratchpad) run-eba0c430cbbb (agent_run.steps/plan.llm_turns)", f"{run_id}: steps[*].ms, plan.llm_turns[*].ms")


def fig_map_zones() -> None:
    """Seoul 2024: current 노인보호구역 circles (300 m) vs real accident hotspots; covered = black, uncovered = orange."""
    setup()
    d = silver_src.load_map()
    h = d[(d.kind == "hotspot_2024") & d.in_seoul]
    z = d[d.kind == "current"]
    lat0, lon0 = h.lat.min() - 0.01, h.lon.min() - 0.01
    kx, ky = 111.32 * np.cos(np.radians(37.5)), 110.57
    X = lambda lon: (lon - lon0) * kx  # noqa: E731
    Y = lambda lat: (lat - lat0) * ky  # noqa: E731
    fig, ax = plt.subplots(figsize=(8.7, 3.85), layout="constrained")
    xmax, ymax = X(h.lon.max() + 0.01), Y(h.lat.max() + 0.01)
    for lat, lon in zip(z.lat, z.lon):
        if X(lon) > -1 and X(lon) < xmax + 1 and Y(lat) > -1 and Y(lat) < ymax + 1:
            ax.add_patch(plt.Circle((X(lon), Y(lat)), 0.3, color=GRAY, alpha=0.45, lw=0))
    cov = h[h.cur_within_300m == 1]
    unc = h[h.cur_within_300m == 0]
    ax.scatter(X(unc.lon), Y(unc.lat), s=30 + 6 * unc.cas.fillna(1), color=ORANGE, edgecolor="white", lw=0.5, zorder=3, label=f"사고 다발지, 현행 구역 밖 ({len(unc)}곳)")
    ax.scatter(X(cov.lon), Y(cov.lat), s=30 + 6 * cov.cas.fillna(1), color="k", edgecolor="white", lw=0.5, zorder=4, label=f"사고 다발지, 현행 구역 안 ({len(cov)}곳)")
    ax.scatter([], [], s=60, color=GRAY, alpha=0.45, label="현행 노인보호구역 (반경 300 m)")
    ax.set_xlim(0, xmax)
    ax.set_ylim(0, ymax)
    ax.set_aspect("equal")
    ax.set_xlabel("동서 거리 (km)")
    ax.set_ylabel("남북 거리 (km)")
    ax.legend(loc="lower left", fontsize=9.5, frameon=True, framealpha=0.9)
    out(fig, "13_map_zones_vs_hotspots", silver_src.SRC, "kind in {current, hotspot_2024}, cur_within_300m")


def _pair(name: str, labels: list[str], vals: list[float], fmt: str, ylabel: str, ymax: float, src: str, key: str, note: str = "") -> None:
    setup()
    fig, ax = plt.subplots(figsize=(6.1, 3.3), layout="constrained")
    ax.bar(range(2), vals, 0.55, color=[GRAY, BLUE])
    for i, v in enumerate(vals):
        ax.text(i, v + ymax * 0.03, fmt.format(v), ha="center", fontsize=26, fontweight="bold", color="k")
    ax.set_xticks(range(2), labels, fontsize=14)
    ax.set_ylim(0, ymax * 1.18)
    ax.set_ylabel(ylabel, fontsize=12)
    out(fig, name, src, key)


def fig_simple_pairs() -> None:
    sp = SC[(SC.variant == "harm") & (SC.stratum == "all") & (SC.k == 3918)]
    cur = float(sp[(sp.method == "current") & (sp.pool == "C")].coverage.iloc[0]) * 100
    ours = float(sp[(sp.method == "ours_opt") & (sp.pool == "OPT")].coverage.iloc[0]) * 100
    _pair("14_simple_silver_pair", ["현행", "에이전트"], [cur, ours], "{:.0f}%", "덮은 사고 지점 %", 100, "experiments/silver/out/opt/promoted/coverage_results.csv", "variant=harm, stratum=all, k=3918, current / ours_opt")
    C = fall_src.load()[0]
    _pair("15_simple_fall_pair", ["규칙", "AI 모델"], [C["rule"]["lat_med"][0], C["logreg"]["lat_med"][0]], "{:.2f}초", "낙상 감지 지연 (초, 낮을수록 좋음)", 2.5, fall_src.SRC, "rule.lat_med[0], logreg.lat_med[0]")


def fig_reach() -> None:
    """Channels that reach 고령자 without a smartphone. Dashed = not yet available."""
    setup()
    fig, ax = plt.subplots(figsize=(12.4, 3.85), layout="constrained")
    ax.set_xlim(0, 13)
    ax.set_ylim(0, 6.3)
    ax.axis("off")
    _box(ax, 0.1, 2.5, 2.0, 1.3, "분석 엔진", True)
    ch = [("신호 시간 연장 권고", "거리 자체가 바뀜", True), ("실버존 재배치", "거리 자체가 바뀜", True), ("횡단보도 전광판 (RSU)", "", True),
          ("버스정류장 안내단말", "", True), ("마을방송 음성", "CAP → WAV 5.9초", True), ("재난문자", "휴대폰 기본 수신", True), ("방문팀", "사람이 직접", True)]
    y = 5.55
    for name, note, ok in ch:
        ax.add_patch(FancyBboxPatch((3.6, y - 0.32), 4.6, 0.62, boxstyle="round,pad=0.0,rounding_size=0.05", fc="white", ec="k" if ok else GRAY, lw=1.0, ls="-" if ok else (0, (4, 3))))
        ax.text(3.75, y, name, va="center", fontsize=12.5, color="k" if ok else GRAY)
        if note:
            ax.text(8.05, y, note, va="center", ha="right", fontsize=10.5, color=BLUE if ok else GRAY)
        ax.add_patch(FancyArrowPatch((2.1, 3.15), (3.6, y), arrowstyle="-|>", mutation_scale=11, lw=0.9, color="k" if ok else GRAY, linestyle="-" if ok else (0, (4, 3))))
        ax.add_patch(FancyArrowPatch((8.2, y), (10.0, 3.15), arrowstyle="-|>", mutation_scale=11, lw=0.9, color="k" if ok else GRAY, linestyle="-" if ok else (0, (4, 3))))
        y -= 0.82
    _box(ax, 10.0, 2.5, 2.8, 1.3, "고령자\n(앱 설치 불필요)", False)
    ax.text(6.4, 0.0, "시민 화면(앱)은 보호자 · 담당자용 보조 화면", ha="center", fontsize=11, color="0.3")
    out(fig, "16_reach_no_smartphone", "구조도 (채널 구현 상태: app/actions, app/agent, CAP→음성 WAV 5.9초)", "diagram")


def fig_flywheel() -> None:
    """Data flywheel: solid arrows exist today, dashed arrows are next steps."""
    setup()
    fig, ax = plt.subplots(figsize=(9.0, 3.85), layout="constrained")
    ax.set_xlim(0, 12.2)
    ax.set_ylim(0, 5.2)
    ax.axis("off")
    bw, bh = 2.3, 1.0
    N = {"rec": ("현장 기록\n(119 · 신고)", 0.1, 3.7, False), "eng": ("분석 엔진 학습", 3.2, 3.7, True), "plan": ("오늘의 대응안", 6.3, 3.7, False),
         "fb": ("현장 판단 기록\n(채택 · 보류 · 부적합)", 9.5, 3.7, False), "cmp": ("다음 날 결과 대조\n(감사 로그)", 9.5, 1.3, False),
         "re": ("재학습", 4.9, 1.3, False)}
    for txt, x, y, acc in N.values():
        _box(ax, x, y, bw, bh, txt, acc)

    def pt(k: str, side: str) -> tuple[float, float]:
        _, x, y, _ = N[k]
        return {"r": (x + bw, y + bh / 2), "l": (x, y + bh / 2), "t": (x + bw / 2, y + bh), "b": (x + bw / 2, y)}[side]

    def arr(p0, p1, label, today, dy=0.18):
        ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle="-|>", mutation_scale=15, lw=1.5, color="k" if today else GRAY,
                                     linestyle="-" if today else (0, (4, 3))))
        ax.text((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2 + dy, label, ha="center", fontsize=10.5, color=BLUE if today else GRAY)
    arr(pt("rec", "r"), pt("eng", "l"), "오늘", True)
    arr(pt("eng", "r"), pt("plan", "l"), "오늘", True)
    arr(pt("plan", "r"), pt("fb", "l"), "오늘", True)
    arr(pt("fb", "b"), pt("cmp", "t"), "다음 단계", False, dy=-0.05)
    arr(pt("cmp", "l"), pt("re", "r"), "다음 단계", False)
    arr(pt("re", "t"), pt("eng", "b"), "다음 단계", False, dy=0.0)
    ax.plot([0.4, 1.2], [0.4, 0.4], color="k", lw=1.5)
    ax.text(1.35, 0.3, "오늘 구현", fontsize=11)
    ax.plot([3.2, 4.0], [0.4, 0.4], color=GRAY, lw=1.5, ls=(0, (4, 3)))
    ax.text(4.15, 0.3, "다음 단계", fontsize=11, color=GRAY)
    out(fig, "12_data_flywheel", "구조도 (실선 = 구현된 단계: experiments/*, app/agent, POST /feedback)", "diagram")


def fig_agent_box() -> None:
    setup()
    runs = bench()
    fig, ax = plt.subplots(figsize=(9.0, 3.85), layout="constrained")
    tot = [r["total_ms"] for r in runs]
    ax.boxplot([tot], vert=False, widths=0.35, showfliers=False, boxprops={"color": BLUE, "lw": 1.5}, medianprops={"color": ORANGE, "lw": 2.0},
               whiskerprops={"color": BLUE}, capprops={"color": BLUE})
    rng = np.random.default_rng(42)
    ax.scatter(tot, 1 + rng.uniform(-0.12, 0.12, len(tot)), s=22, color=GRAY, zorder=3)
    med = statistics.median(tot)
    ax.annotate(f"중앙값 {med:,.0f} ms", xy=(med, 1.2), xytext=(med, 1.45), ha="center", fontsize=12, fontweight="bold", color=ORANGE)
    ax.set_yticks([])
    ax.set_xlim(0, 16500)
    ax.set_ylim(0.5, 1.7)
    ax.set_xlabel(f"에이전트 한 번 실행 전체 시간 (ms), 10회, 최소 {min(tot):,.0f} · 최대 {max(tot):,.0f}")
    out(fig, "A8_agent_latency_distribution", "bench.json (scratchpad)", "runs[*].total_ms (n=10)")


# ---------------------------------------------------------------------------- dot figures
def run_dot(name: str, dot: str) -> None:
    import subprocess
    p = FINAL / f"{name}.dot"
    p.write_text(dot, encoding="utf-8")
    subprocess.run(["uv", "run", "--project", str(ROOT / "app/backend"), "python", str(ROOT / "tools/deck/diagram.py"), str(p), "--out-dir", str(FINAL)], check=True)
    sub = FINAL / name
    for f in (sub / f"{name}.png", sub / f"{name}.pdf"):
        if f.is_file():
            f.replace(FINAL / f.name)
    MANIFEST[name] = {"source": "deck/figs/final/" + name + ".dot", "key": "diagram"}


ENGINE_DOT = """digraph engine {
  ranksep=0.28; nodesep=0.2; node [fontsize=24, margin="0.16,0.30"];
  data  [label="공공 데이터\\n기상 · 인구 · 119 · 시설\\n(SHA-256 출처 기록)"];
  risk  [label="위험 예측\\n폭염 · 보행 · 심정지", fillcolor="@ACCENT@", style="filled"];
  cmp   [label="같은 예산 비교\\n공식 기준 vs 에이전트"];
  alloc [label="자원 배분\\n경보 지역 · 설치 위치"];
  act   [label="행동 채널\\n재난문자 · 전광판 · 119"];
  data -> risk -> cmp -> alloc -> act;
}
"""

ERD_DOT = """digraph erd {
  rankdir=LR; splines=true; nodesep=0.5; ranksep=1.0;
  node [shape=plaintext, fontsize=18];
  region [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="6">
    <tr><td bgcolor="#7FB5D9"><b>region</b>  시군구 250곳</td></tr>
    <tr><td align="left">PK  code (5자리)</td></tr>
    <tr><td align="left">pop · pop65 · lat · lon</td></tr></table>>];
  alert_run [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="6">
    <tr><td bgcolor="#7FB5D9"><b>alert_run</b>  방법 · 연도 · 예산</td></tr>
    <tr><td align="left">PK  id</td></tr>
    <tr><td align="left">method · variant · test_year</td></tr>
    <tr><td align="left">budget_kind · dataset_sha · git_hash</td></tr></table>>];
  alert [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="6">
    <tr><td bgcolor="#7FB5D9"><b>alert</b>  하루 · 시군구 한 줄</td></tr>
    <tr><td align="left">PK  (run_id, date, region_code)</td></tr>
    <tr><td align="left">FK  run_id → alert_run.id</td></tr>
    <tr><td align="left">FK  region_code → region.code</td></tr>
    <tr><td align="left">score · alerted</td></tr></table>>];
  alert_run -> alert [label="1 : N", fontsize=16];
  region -> alert [label="1 : N", fontsize=16];
}
"""


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    FINAL.mkdir(parents=True, exist_ok=True)
    for fn in (fig_heat_pooled_year, fig_heat_decomp, fig_heat_forest, fig_heat_budget, fig_heat_precision, fig_heat_variants,
               fig_silver_main, fig_silver_appendix, fig_silver119, fig_aed, fig_fall, lambda: fig_fall(True),
               fig_agent_flow, fig_agent_timeline, fig_agent_box, fig_flywheel, fig_map_zones, fig_simple_pairs, fig_reach,
               fig_heat_main, fig_map_main, fig_aed_main):
        fn()
    run_dot("09_engine_flow", ENGINE_DOT)
    run_dot("10_erd_core_tables", ERD_DOT)
    (FINAL / "figs_manifest.json").write_text(json.dumps(MANIFEST, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
