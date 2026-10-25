"""Figures + frontend tables for the heat-illness evaluation. Reads out/results.json, out/test_predictions.csv.gz,
out/panel.csv.gz (read-only); writes PNG+PDF to figs/ and CSVs to figs/ and out/.

Series colour rule: baseline = gray (series 0); ours = blue (series 1); intermediate methods are tints.
Run: uv run --project /Users/chloekang/kcf/app/backend python experiments/heat/figures.py
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))  # app/backend
from common import FIG_DIR, OUT_DIR, PANEL_PATH, POOL_LABEL, PRED_PATH, RESULTS_PATH, TEST_YEARS  # noqa: E402
from figures.plot_helpers import PALETTE, SINGLE_COL_IN, save_figure, use_style  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

logger = logging.getLogger("heat.fig")
POOL = f"pooled_{POOL_LABEL}"
POOL_TXT = POOL_LABEL.replace("_", "-")
# series 0 = baseline (gray), series 1 = ours (blue); the two intermediate steps are tints between them
COL = {"B1": PALETTE[0], "B1S": "#C8C8C8", "B2": "#B8B8B8", "HEUR": "#7FB5D9", "OURS": PALETTE[1]}
LAB = {"B1": "특보 규칙", "B1S": "특보 규칙(하루)", "B2": "시도 단위 모델", "HEUR": "인구·취약성 반영", "OURS": "우리 엔진"}
VLAB = {"F": "입력: 당일·익일 체감온도(예보 대용)", "O": "입력: 전날까지 관측만"}
KEY = "B1|per_day"
KO_RC = {"font.family": ["Apple SD Gothic Neo", "AppleGothic", "sans-serif"], "axes.unicode_minus": False}


def new_fig(height_in: float = 2.5):
    """ICASSP single-column figure with a Korean-capable font (rc is set before the Axes exist)."""
    use_style()
    plt.rcParams.update(KO_RC)
    return plt.subplots(figsize=(SINGLE_COL_IN, height_in), layout="constrained")


def fig_f1(R: dict, v: str) -> None:
    fig, ax = new_fig(2.6)
    methods = ["B1", "B2", "HEUR", "OURS"]
    n, w = len(methods), 0.8 / len(methods)
    x = np.arange(len(TEST_YEARS))
    for k, m in enumerate(methods):
        pts = [R["variants"][v]["folds"][str(T)]["budget"][KEY]["methods"][m]["capture"] for T in TEST_YEARS]
        p = np.array([q["est"] for q in pts])
        err = np.array([[q["est"] - q["lo"] for q in pts], [q["hi"] - q["est"] for q in pts]])
        ax.bar(x + (k - (n - 1) / 2) * w, p, w * 0.92, yerr=err, color=COL[m], label=LAB[m],
               error_kw={"elinewidth": 0.6, "capsize": 1.2, "capthick": 0.6})
    ax.set_xticks(x, [str(t) for t in TEST_YEARS])
    ax.set(ylabel="포착률 (경보 지역 환자 ÷ 전체 환자)", ylim=(0, 1.0), xlabel=f"시험 연도  |  {VLAB[v]}")
    ax.legend(loc="upper left", ncol=2, fontsize=6)
    save_figure(fig, f"f1_capture_by_year_{v}", FIG_DIR)


def fig_f2(R: dict, v: str, year: int = 2025) -> None:
    fig, ax = new_fig(2.6)
    sw = R["variants"][v]["sweep"][str(year)]
    thr = pd.DataFrame(sw["b1_threshold_curve"])
    ax.plot(thr.alert_share, thr.capture, color="0.7", ls="--", lw=0.8, label="특보 기준 온도를 28~36℃로 바꿔 본 경우")
    for m in ["B1", "B2", "HEUR", "OURS"]:
        c = pd.DataFrame(sw["curves"][m])
        ax.plot(c.alert_share, c.capture, color=COL[m], lw=1.1, label=LAB[m])
        one = c[c.scale == 1.0].iloc[0]
        if m == "B1":
            ax.plot(one.alert_share, one.capture, "o", color="k", ms=3.5, zorder=5, label="특보 규칙의 실제 경보량")
    ax.set(xlabel="경보 낸 시군구-일 비율 (특보 경보량의 0.25~2배)", ylabel="포착률 (경보 지역 환자 ÷ 전체 환자)")
    ax.set_title(f"{year}년  |  {VLAB[v]}", fontsize=7)
    ax.legend(loc="lower right", fontsize=6)
    save_figure(fig, f"f2_budget_curve_{year}_{v}", FIG_DIR)


def fig_f3(R: dict) -> None:
    fig, ax = new_fig(2.6)
    rows = []
    for v in ("F", "O"):
        for T in [*TEST_YEARS, POOL_TXT]:
            blk = R["variants"][v][POOL] if T == POOL_TXT else R["variants"][v]["folds"][str(T)]["budget"]
            d = blk[KEY]["delta"]["OURS-B1"]["capture"]
            rows.append((v, str(T), 100 * d["est"], 100 * d["lo"], 100 * d["hi"]))
    y = np.arange(len(rows))[::-1]
    for yi, (v, T, e, lo, hi) in zip(y, rows):
        ax.errorbar(e, yi, xerr=[[e - lo], [hi - e]], fmt="o" if T != POOL_TXT else "D", ms=3,
                    color=PALETTE[1] if v == "F" else PALETTE[0], elinewidth=0.8, capsize=1.5)
    ax.axvline(0, color="0.5", lw=0.5)
    ax.set_yticks(y, [f"{'당일·익일 입력' if v == 'F' else '전날까지 입력'}  {'전체 ' if T == POOL_TXT else ''}{T}" for v, T, *_ in rows], fontsize=6)
    ax.set(xlabel="포착률 차이, %p (우리 엔진 - 특보 규칙, 95% 신뢰구간)")
    ax.xaxis.label.set_fontsize(6.5)
    save_figure(fig, "f3_forest_ours_minus_b1", FIG_DIR)


def fig_f4(R: dict) -> None:
    fig, ax = new_fig(2.6)
    steps = [("B2", "B1"), ("HEUR", "B2"), ("OURS", "HEUR")]
    names = ["특보 규칙 → \n시도 단위 모델", "시도 단위 모델 → \n인구·취약성 반영", "인구·취약성 반영 → \n시군구 모델(우리 엔진)"]
    w = 0.38
    for k, v in enumerate(("F", "O")):
        d = R["variants"][v][POOL][KEY]["delta"]
        est = 100 * np.array([d[f"{a}-{b}"]["capture"]["est"] for a, b in steps])
        lo = 100 * np.array([d[f"{a}-{b}"]["capture"]["lo"] for a, b in steps])
        hi = 100 * np.array([d[f"{a}-{b}"]["capture"]["hi"] for a, b in steps])
        ax.bar(np.arange(3) + (k - 0.5) * w, est, w * 0.92, yerr=[est - lo, hi - est],
               color=PALETTE[1] if v == "F" else "#7FB5D9", label=VLAB[v],
               error_kw={"elinewidth": 0.6, "capsize": 1.2, "capthick": 0.6})
    ax.axhline(0, color="0.5", lw=0.5)
    ax.set_xticks(np.arange(3), names, fontsize=6)
    ax.set(ylabel=f"포착률 증가 (%p), {POOL_TXT} 통합")
    ax.legend(loc="upper right", fontsize=6)
    save_figure(fig, "f4_gain_decomposition", FIG_DIR)


def fig_calibration(R: dict, v: str) -> None:
    """Reliability: predicted vs observed cases per 시군구-day, pooled test years (count models only)."""
    fig, ax = new_fig(2.6)
    cal = R["variants"][v]["calibration"]
    for m, mk in (("B2", "s"), ("OURS", "o")):
        rows = pd.DataFrame(cal["bins"][m]["rows"])
        ax.errorbar(rows.mean_pred, rows.mean_obs, yerr=1.96 * rows.se_obs, marker=mk, ms=2.5, lw=0.8, elinewidth=0.5,
                    color=COL["B1"] if m == "B2" else COL["OURS"], label=LAB[m])
    lim = [1e-3, 3]
    ax.plot(lim, lim, color="0.6", lw=0.5, ls="--", label="예측 = 실제")
    ax.set(xscale="log", yscale="log", xlabel="시군구-일 예측 환자 수 (평균)", ylabel="실제 환자 수 (평균)",
           title=f"{POOL_TXT} 통합  |  {VLAB[v]}")
    ax.title.set_fontsize(7)
    ax.legend(loc="upper left", fontsize=6)
    save_figure(fig, f"f6_calibration_{v}", FIG_DIR)


def fig_calib_totals(R: dict, v: str) -> None:
    """Predicted total / observed total per test year, previous run (gray) vs this run (blue); markers = train-only
    rescale attempts that were measured but not adopted."""
    fig, ax = new_fig(2.6)
    tot = R["variants"][v]["calibration"]["per_year_total"]
    prev_path = OUT_DIR / "prev" / "results.json"
    prev = json.loads(prev_path.read_text()) if prev_path.is_file() else None
    x, w = np.arange(len(TEST_YEARS)), 0.38
    if prev is not None:
        pt = prev["variants"][v]["calibration"]["per_year_total"]
        ax.bar(x - w / 2, [pt[str(T)]["OURS"] / pt[str(T)]["observed"] if str(T) in pt else np.nan for T in TEST_YEARS], w * 0.92,
               color=PALETTE[0], label="이전 실행 (최고기온, 관측소 58곳)")
    ax.bar(x + w / 2, [tot[str(T)]["OURS_raw_over_obs"] for T in TEST_YEARS], w * 0.92, color=PALETTE[1],
           label="이번 실행 (체감온도, 관측소 95곳)")
    ax.plot(x + w / 2, [tot[str(T)]["OURS_last_over_obs"] for T in TEST_YEARS], "k_", ms=7, mew=1.2, label="전년 비율로 재조정 (미채택)")
    ax.plot(x + w / 2, [tot[str(T)]["OURS_trend_over_obs"] for T in TEST_YEARS], "kx", ms=4, mew=0.9, label="연도 추세 외삽 (미채택)")
    ax.axhline(1.0, color="k", lw=0.6, ls="--")
    ax.set_xticks(x, [str(t) for t in TEST_YEARS])
    ax.set(ylabel="예측 환자 수 ÷ 실제 환자 수 (1.0이 정확)", xlabel=f"시험 연도  |  {VLAB[v]}", ylim=(0, 2.4))
    ax.legend(loc="upper left", fontsize=5.5)
    save_figure(fig, f"f7_calibration_totals_{v}", FIG_DIR)


def tables(R: dict) -> None:
    P = pd.read_csv(PANEL_PATH, parse_dates=["date"]).rename(columns={"unit_id": "region_code"})
    pr = pd.read_csv(PRED_PATH, parse_dates=["date"])
    pr = pr[(pr.variant == "F") & (pr.budget_kind == "per_day")]
    wide = pr.pivot_table(index=["test_year", "date", "region_code"], columns="method", values="alerted", aggfunc="first").reset_index()
    sc = pr[pr.method == "OURS"][["test_year", "date", "region_code", "score"]].rename(columns={"score": "ours_pred_cases"})
    d = wide.merge(sc, on=["test_year", "date", "region_code"]).merge(
        P[["region_code", "date", "unit_key", "sido", "sgg", "lat", "lon", "y", "at_l0", "pop", "old_share"]],
        on=["region_code", "date"])
    d25 = d[d.test_year == 2025].copy()
    d25["status"] = np.select(
        [(d25.OURS == 1) & (d25.B1 == 0) & (d25.y > 0), (d25.OURS == 0) & (d25.B1 == 1) & (d25.y > 0), (d25.OURS == 1) & (d25.B1 == 1) & (d25.y > 0)],
        ["ours_caught_b1_missed", "b1_caught_ours_missed", "both_caught"], default="no_case_or_neither")
    # (c) per-unit season summary of 2025 B1 misses rescued by OURS (cases counted)
    unit = d25.groupby(["region_code", "unit_key", "sido", "sgg", "lat", "lon"]).apply(
        lambda g: pd.Series({
            "cases_2025": g.y.sum(),
            "cases_b1_missed_ours_caught": g.y[(g.B1 == 0) & (g.OURS == 1)].sum(),
            "cases_b1_caught_ours_missed": g.y[(g.B1 == 1) & (g.OURS == 0)].sum(),
            "days_b1_missed_ours_caught": int(((g.B1 == 0) & (g.OURS == 1) & (g.y > 0)).sum()),
            "ours_alert_days": int(g.OURS.sum()), "b1_alert_days": int(g.B1.sum()),
        }), include_groups=False).reset_index()
    unit["net_cases_rescued"] = unit.cases_b1_missed_ours_caught - unit.cases_b1_caught_ours_missed
    unit.sort_values("net_cases_rescued", ascending=False).to_csv(FIG_DIR / "f5a_2025_unit_blindspots_F.csv", index=False, encoding="utf-8-sig")
    unit.to_csv(OUT_DIR / "map_2025_unit_blindspots_F.csv", index=False, encoding="utf-8-sig")
    # (F5) representative day = 2025 day with the most cases (documented rule; not cherry-picked by model result)
    day = d25.groupby("date").y.sum().idxmax()
    rep = d25[d25.date == day].drop(columns=["test_year", "B1S", "B2", "HEUR"]).rename(
        columns={"B1": "b1_alerted", "OURS": "ours_alerted", "y": "cases", "at_l0": "apparent_temp_d"})
    rep.to_csv(FIG_DIR / "f5_map_day_2025_F.csv", index=False, encoding="utf-8-sig")
    rep.to_csv(OUT_DIR / "map_day_2025_F.csv", index=False, encoding="utf-8-sig")
    logger.info("representative day %s cases=%d", day.date(), rep.cases.sum())
    R["figures_meta"] = {"representative_day_rule": "2025 day with max total cases", "representative_day": str(day.date()),
                         "rep_day_cases": int(rep.cases.sum()), "rep_day_b1_alerts": int(rep.b1_alerted.sum()),
                         "rep_day_ours_alerts": int(rep.ours_alerted.sum()),
                         "rep_day_ours_caught_b1_missed": int((rep.status == "ours_caught_b1_missed").sum()),
                         "n_2025_units_with_net_rescue_gt0": int((unit.net_cases_rescued > 0).sum())}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    R = json.loads(RESULTS_PATH.read_text())
    for v in ("F", "O"):
        fig_f1(R, v)
        fig_f2(R, v)
        fig_calibration(R, v)
        fig_calib_totals(R, v)
    fig_f3(R)
    fig_f4(R)
    tables(R)
    R_meta = R.pop("figures_meta")
    R["figures_meta"] = R_meta
    RESULTS_PATH.write_text(json.dumps(R, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
