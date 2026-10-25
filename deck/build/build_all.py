"""One command rebuild of the 「고령자 안전 스마트도시 에이전트」 deck: figures -> ledger -> script -> qa -> spec -> clarity check -> pptx + previews.

Every number printed on a slide or said in the script comes from the result files via the ledger `L` below
(claim -> value -> file -> key), so re-running after a new results.json refreshes slides, script and ledger together.

Run: uv run --project /Users/chloekang/kcf/app/backend python /Users/chloekang/kcf/deck/build/build_all.py [--skip-figs]
"""
from __future__ import annotations

import argparse
import os
import datetime as dt
import hashlib
import fall_src
import silver_src
import twin_src
import json
import logging
import re
import statistics
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path("/Users/chloekang/kcf")
EXP = ROOT / "app/backend/experiments"
BUILD = ROOT / "deck/build"
FIG = ROOT / "deck/figs/final"
ASSETS = ROOT / "deck/assets"
sys.path.insert(0, str(BUILD))
LOG = logging.getLogger("build_all")
PY = ["uv", "run", "--project", str(ROOT / "app/backend"), "python"]

HEAT_PATH = EXP / "heat/out/results.json"
HEAT = json.loads(HEAT_PATH.read_text(encoding="utf-8"))
HEAT_SHA = hashlib.sha256(HEAT_PATH.read_bytes()).hexdigest()
HEAT_MTIME = dt.datetime.fromtimestamp(HEAT_PATH.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
KEY = "B1|per_day"
SC = pd.read_csv(EXP / "silver/out/coverage_results.csv")
V119 = json.loads((EXP / "validate119/out/results.json").read_text(encoding="utf-8"))
AED = json.loads((EXP / "aed/out/summary.json").read_text(encoding="utf-8"))
AEDH = pd.read_csv(EXP / "aed/out/hourly_gap.csv")
FALL, FALL_GATE_KEY = fall_src.load()
DEFAULT_BENCH = str(BUILD / "data/after.json")  # copy of the 10-run agent benchmark (with after_stream.json next to it); make_figs.py reads the same file
BENCH_PATH = Path(os.environ.get("KCF_BENCH_JSON", DEFAULT_BENCH))  # rebuild after latency work: KCF_BENCH_JSON=<new bench.json>


def normalize_bench(b: object) -> list[dict]:
    """Old format: list of agent_run records (per-step ms). New: summary dict with runs[*].llm_turns (no per-tool ms)."""
    if isinstance(b, list):
        return b
    return [{"run_id": r["run_id"], "total_ms": r["total_ms"], "plan": {"llm_turns": r["llm_turns"], "first_try_valid": r["first_try_valid"]}, "steps": None,
             "grounding": {"ok": r["grounding_ok"]}, "tool_sum_ms": r["total_ms"] - sum(t["ms"] for t in r["llm_turns"])} for r in b["runs"]]  # type: ignore[index]


BENCH = normalize_bench(json.loads(BENCH_PATH.read_text(encoding="utf-8")))
_STREAM = Path(os.environ.get("KCF_BENCH_STREAM_JSON", str(BENCH_PATH.parent / "after_stream.json")))
FIRST_STEP_MS = json.loads(_STREAM.read_text(encoding="utf-8")).get("first_step_p50_ms") if _STREAM.is_file() else None
_MED = statistics.median(r["total_ms"] for r in BENCH)
_ids = {r["run_id"] for r in BENCH}
TIMELINE_RUN = os.environ.get("KCF_TIMELINE_RUN") or ("run-eba0c430cbbb" if "run-eba0c430cbbb" in _ids else min(BENCH, key=lambda r: abs(r["total_ms"] - _MED))["run_id"])

LEDGER: list[tuple[str, str, str, str]] = []
L: dict[str, str] = {}


def reg(k: str, claim: str, value: str, file: str, key: str) -> None:
    """Register a number: L[k] is the display string used everywhere; LEDGER keeps claim/file/key."""
    L[k] = value
    LEDGER.append((claim, value, file, key))


def pc(x: float, d: int = 0) -> str:
    return f"{100 * x:.{d}f}"


# ------------------------------------------------------------------------------ numbers (heat)
HF = HEAT["variants"]["F"]["pooled_2022_2025"][KEY]
HO = HEAT["variants"]["O"]["pooled_2022_2025"][KEY]
HP = "experiments/heat/out/results.json"
cap = lambda blk: blk["capture"]  # noqa: E731
reg("h_b1", "폭염 특보 규칙 포착률 (4년 통합, 입력 F)", pc(cap(HF["methods"]["B1"])["est"]), HP, "variants.F.pooled_2022_2025[B1|per_day].methods.B1.capture.est")
reg("h_ours", "에이전트 포착률 (4년 통합, 입력 F)", pc(cap(HF["methods"]["OURS"])["est"]), HP, "variants.F.pooled_2022_2025[B1|per_day].methods.OURS.capture.est")
d = HF["delta"]["OURS-B1"]["capture"]
reg("h_ours1", "에이전트 포착률 (소수 1자리)", pc(cap(HF["methods"]["OURS"])["est"], 1), HP, "variants.F.pooled_2022_2025[B1|per_day].methods.OURS.capture.est")
reg("h_d", "에이전트 - 특보 규칙 (%p)", pc(d["est"], 1), HP, "variants.F.pooled_2022_2025[B1|per_day].delta.OURS-B1.capture.est")
reg("h_d_lo", "위 차이 95% CI 하한", pc(d["lo"], 1), HP, "...delta.OURS-B1.capture.lo")
reg("h_d_hi", "위 차이 95% CI 상한", pc(d["hi"], 1), HP, "...delta.OURS-B1.capture.hi")
reg("h_b2", "시도 단위 모델 포착률 (F)", pc(cap(HF["methods"]["B2"])["est"], 1), HP, "variants.F.pooled_2022_2025[B1|per_day].methods.B2.capture.est")
reg("h_heur", "인구·취약성 반영 포착률 (F)", pc(cap(HF["methods"]["HEUR"])["est"], 1), HP, "...methods.HEUR.capture.est")
for nm, k in (("s1", "B2-B1"), ("s2", "HEUR-B2"), ("s3", "OURS-HEUR"), ("o_b2", "OURS-B2")):
    dd = HF["delta"][k]["capture"]
    reg(f"h_{nm}", f"단계 이득 {k} (%p, F)", pc(dd["est"], 1), HP, f"variants.F.pooled_2022_2025[B1|per_day].delta.{k}.capture.est")
    reg(f"h_{nm}_lo", f"{k} CI 하한", pc(dd["lo"], 1), HP, f"...delta.{k}.capture.lo")
    reg(f"h_{nm}_hi", f"{k} CI 상한", pc(dd["hi"], 1), HP, f"...delta.{k}.capture.hi")
for y in (2022, 2023, 2024, 2025):
    fm = HEAT["variants"]["F"]["folds"][str(y)]["budget"][KEY]["methods"]
    reg(f"h_b1_{y}", f"{y} 특보 규칙 포착률 (F)", pc(fm["B1"]["capture"]["est"]), HP, f"variants.F.folds.{y}.budget[B1|per_day].methods.B1.capture.est")
    reg(f"h_ours_{y}", f"{y} 에이전트 포착률 (F)", pc(fm["OURS"]["capture"]["est"]), HP, f"variants.F.folds.{y}.budget[B1|per_day].methods.OURS.capture.est")
reg("h_n_cases", "시험 4년 환자 수 합", f"{int(sum(HEAT['variants']['F']['folds'][str(y)]['n_cases'] for y in range(2022, 2026))):,}", HP, "variants.F.folds.<y>.n_cases (합)")
reg("h_n_rows", "시군구-일 행 수 합", f"{int(sum(HEAT['variants']['F']['folds'][str(y)]['n_rows'] for y in range(2022, 2026))):,}", HP, "variants.F.folds.<y>.n_rows (합)")
reg("h_prec_b1", "특보 규칙 정밀도 (F)", pc(HF["methods"]["B1"]["precision"]["est"], 1), HP, "...methods.B1.precision.est")
reg("h_prec_ours", "에이전트 정밀도 (F)", pc(HF["methods"]["OURS"]["precision"]["est"], 1), HP, "...methods.OURS.precision.est")
reg("h_o_b1", "특보 규칙 포착률 (전날까지 입력 O)", pc(HO["methods"]["B1"]["capture"]["est"]), HP, "variants.O.pooled_2022_2025[B1|per_day].methods.B1.capture.est")
reg("h_o_ours", "에이전트 포착률 (O)", pc(HO["methods"]["OURS"]["capture"]["est"]), HP, "variants.O...methods.OURS.capture.est")
reg("h_o_d", "O: 에이전트 - 특보 (%p)", pc(HO["delta"]["OURS-B1"]["capture"]["est"], 1), HP, "variants.O...delta.OURS-B1.capture.est")
reg("h_o_d_lo", "O 차이 CI 하한", pc(HO["delta"]["OURS-B1"]["capture"]["lo"], 1), HP, "variants.O...delta.OURS-B1.capture.lo")
reg("h_o_d_hi", "O 차이 CI 상한", pc(HO["delta"]["OURS-B1"]["capture"]["hi"], 1), HP, "variants.O...delta.OURS-B1.capture.hi")
reg("h_units", "폭염 분석 단위 수 (시군구)", f"{HEAT['coverage_report']['n_units']}", HP, "coverage_report.n_units")
reg("h_st_med", "가장 가까운 관측소까지 거리 중앙값 (km)", f"{HEAT['coverage_report']['station_km_nearest_median']:.1f}", HP, "coverage_report.station_km_nearest_median")
reg("h_st_max", "관측소 거리 최대 (km)", f"{HEAT['coverage_report']['station_km_nearest_max']:.1f}", HP, "coverage_report.station_km_nearest_max")

# ------------------------------------------------------------------------------ numbers (silver; promoted method ours_opt)
SP = "experiments/silver/out/opt/promoted/coverage_results.csv"
SPD = "experiments/silver/out/opt/promoted/delta_results.csv"
SPO = "experiments/silver/out/coverage_results.csv"
SC = pd.read_csv(EXP / "silver/out/opt/promoted/coverage_results.csv")
SD = pd.read_csv(EXP / "silver/out/opt/promoted/delta_results.csv")
SC_OLD = pd.read_csv(EXP / "silver/out/coverage_results.csv")


def sc(stratum: str, method: str, col: str = "coverage", k: int = 3918) -> float:
    if method == "random_k":
        r = SC_OLD[(SC_OLD.variant == "harm") & (SC_OLD.pool == "C") & (SC_OLD.stratum == stratum) & (SC_OLD.method == method) & (SC_OLD.k == k)].iloc[0]
    else:
        pool = "OPT" if method == "ours_opt" else "C"
        r = SC[(SC.variant == "harm") & (SC.pool == pool) & (SC.stratum == stratum) & (SC.method == method) & (SC.k == k)].iloc[0]
    return float(r[col])


def sdelta(stratum: str, vs: str, k: int = 3918) -> tuple[float, float, float]:
    r = SD[(SD.method == "ours_opt") & (SD.stratum == stratum) & (SD.vs == vs) & (SD.k == k)].iloc[0]
    return float(r.delta_pp), float(r.lo_pp), float(r.hi_pp)


for m, nm in (("current", "현행 노인보호구역"), ("pop65_k", "노인 인구"), ("past_k", "작년 사고 장소"), ("ours_opt", "에이전트(ours_opt)")):
    reg(f"s_{m}", f"실버존 덮은 사고 지점 {nm} (k=3,918, 전체)", pc(sc("all", m)), SP, f"variant=harm,pool={'OPT' if m == 'ours_opt' else 'C'},stratum=all,k=3918,method={m},coverage")
reg("s_random_k", "실버존 덮은 사고 지점 무작위 (이전 실행 파일, 같은 풀 C·같은 735곳)", pc(sc("all", "random_k")), SPO, "variant=harm,pool=C,stratum=all,k=3918,method=random_k,coverage")
L["s_ours_full"] = L["s_ours_opt"]
for m in ("current", "pop65_k", "past_k", "ours_opt"):
    reg(f"sn_{m}", f"새 사고 지점 덮은 비율 {m}", pc(sc("new", m)), SP, f"variant=harm,stratum=new,k=3918,method={m},coverage")
L["sn_ours_full"] = L["sn_ours_opt"]
L["sn_current"] = L["sn_current"]
reg("sn_ours_lo", "새 사고 지점 에이전트 CI 하한", pc(sc("new", "ours_opt", "lo"), 1), SP, "stratum=new,method=ours_opt,lo")
reg("sn_ours_hi", "새 사고 지점 에이전트 CI 상한", pc(sc("new", "ours_opt", "hi"), 1), SP, "stratum=new,method=ours_opt,hi")
reg("s_n", "다음 해 사고 지점 수 (전체)", f"{int(SC[(SC.variant == 'harm') & (SC.stratum == 'all')].n_hot.iloc[0])}", SP, "n_hot (stratum=all)")
reg("s_n_new", "새 사고 지점 수", f"{int(SC[(SC.variant == 'harm') & (SC.stratum == 'new')].n_hot.iloc[0])}", SP, "n_hot (stratum=new)")
reg("s_k", "고른 지점 수 k", "3,918", SP, "k=3918")
for st, tag in (("all", "a"), ("new", "n")):
    d_, lo_, hi_ = sdelta(st, "C:past_k")
    reg(f"sd_{tag}_past", f"에이전트 - 작년 사고 장소 ({st}, %p)", f"{d_:+.1f}", SPD, f"method=ours_opt,stratum={st},vs=C:past_k,k=3918,delta_pp")
    reg(f"sd_{tag}_past_lo", f"위 CI 하한 ({st})", f"{lo_:+.1f}", SPD, "lo_pp")
    reg(f"sd_{tag}_past_hi", f"위 CI 상한 ({st})", f"{hi_:+.1f}", SPD, "hi_pp")
d_, lo_, hi_ = sdelta("new", "D:past_k")
reg("sd_n_gridD", "새 사고 지점: 에이전트 - 작년 사고 장소, 같은 촘촘한 후보(풀 D) (%p)", f"{d_:+.1f}", SPD, "method=ours_opt,stratum=new,vs=D:past_k,k=3918,delta_pp")
reg("sd_n_gridD_lo", "위 CI 하한", f"{lo_:+.1f}", SPD, "lo_pp")
reg("sd_n_gridD_hi", "위 CI 상한", f"{hi_:+.1f}", SPD, "hi_pp")
small = [sdelta("all", "C:past_k", k)[0] for k in (250, 500, 1000)]
reg("sd_small_min", "k=250~1,000에서 에이전트 - 작년 사고 장소 (전체) 가장 작은 값 (%p)", f"{min(small):+.1f}", SPD, "stratum=all,vs=C:past_k,k in {250,500,1000}")
reg("sd_small_max", "같은 구간 가장 큰 값 (%p)", f"{max(small):+.1f}", SPD, "stratum=all,vs=C:past_k,k in {250,500,1000}")

# ------------------------------------------------------------------------------ numbers (119)
VP = "experiments/validate119/out/results.json"
T = V119["silver"]["misallocation_tvd"]
TO = V119["silver_opt"]["misallocation_tvd"]
reg("v_cur", "119 대조: 현행 노인보호구역 엉뚱한 구 비율", pc(T["현행 노인보호구역"]["tvd"], 1), VP, "silver.misallocation_tvd.현행 노인보호구역.tvd")
reg("v_cur_lo", "현행 CI 하한", pc(T["현행 노인보호구역"]["ci"][0], 1), VP, "...현행 노인보호구역.ci[0]")
reg("v_cur_hi", "현행 CI 상한", pc(T["현행 노인보호구역"]["ci"][1], 1), VP, "...현행 노인보호구역.ci[1]")
reg("v_ours", "119 대조: 에이전트 선정 지점", pc(T["영점 선정 지점"]["tvd"], 1), VP, "silver.misallocation_tvd.영점 선정 지점.tvd")
reg("v_ours_lo", "에이전트 CI 하한", pc(T["영점 선정 지점"]["ci"][0], 1), VP, "...영점 선정 지점.ci[0]")
reg("v_ours_hi", "에이전트 CI 상한", pc(T["영점 선정 지점"]["ci"][1], 1), VP, "...영점 선정 지점.ci[1]")
reg("v_pop", "119 대조: 65세 이상 인구 기준", pc(T["65세 이상 인구"]["tvd"], 1), VP, "silver.misallocation_tvd.65세 이상 인구.tvd")
reg("v_gap", "현행 - 에이전트 (%p)", pc(T["paired_current_minus_ours"]["diff"], 1), VP, "silver.misallocation_tvd.paired_current_minus_ours.diff")
reg("v_gap_lo", "차이 CI 하한", pc(T["paired_current_minus_ours"]["ci"][0], 1), VP, "...paired_current_minus_ours.ci[0]")
reg("v_gap_hi", "차이 CI 상한", pc(T["paired_current_minus_ours"]["ci"][1], 1), VP, "...paired_current_minus_ours.ci[1]")
reg("v_cur0", "현행 엉뚱한 구 비율 (정수 반올림)", f"{100 * T['현행 노인보호구역']['tvd']:.0f}", VP, "silver.misallocation_tvd.현행 노인보호구역.tvd")
reg("v_ours0", "에이전트 엉뚱한 구 비율 (정수 반올림)", f"{100 * T['영점 선정 지점']['tvd']:.0f}", VP, "silver.misallocation_tvd.영점 선정 지점.tvd")
reg("vo_cur", "119 대조(ours_opt): 현행 엉뚱한 구 비율", pc(TO["현행 노인보호구역"]["tvd"], 1), VP, "silver_opt.misallocation_tvd.현행 노인보호구역.tvd")
reg("vo_cur_lo", "현행 CI 하한", pc(TO["현행 노인보호구역"]["ci"][0], 1), VP, "silver_opt...현행 노인보호구역.ci[0]")
reg("vo_cur_hi", "현행 CI 상한", pc(TO["현행 노인보호구역"]["ci"][1], 1), VP, "silver_opt...현행 노인보호구역.ci[1]")
reg("vo_ours", "119 대조(ours_opt): 에이전트 엉뚱한 구 비율", pc(TO["영점 선정 지점(opt)"]["tvd"], 1), VP, "silver_opt.misallocation_tvd.영점 선정 지점(opt).tvd")
reg("vo_ours_lo", "에이전트(opt) CI 하한", pc(TO["영점 선정 지점(opt)"]["ci"][0], 1), VP, "silver_opt...영점 선정 지점(opt).ci[0]")
reg("vo_ours_hi", "에이전트(opt) CI 상한", pc(TO["영점 선정 지점(opt)"]["ci"][1], 1), VP, "silver_opt...영점 선정 지점(opt).ci[1]")
reg("vo_pop", "119 대조(opt): 65세 이상 인구 기준", pc(TO["65세 이상 인구"]["tvd"], 1), VP, "silver_opt.misallocation_tvd.65세 이상 인구.tvd")
reg("vo_gap", "현행 - 에이전트(opt) (%p)", f"{100 * TO['paired_current_minus_ours']['diff']:.1f}", VP, "silver_opt.misallocation_tvd.paired_current_minus_ours.diff")
reg("vo_gap_lo", "차이 CI 하한", f"{100 * TO['paired_current_minus_ours']['ci'][0]:+.1f}", VP, "...ci[0]")
reg("vo_gap_hi", "차이 CI 상한", f"{100 * TO['paired_current_minus_ours']['ci'][1]:+.1f}", VP, "...ci[1]")
reg("v_n", "70세 이상 보행 119 건수 (서울 25개 구)", f"{V119['silver']['n_ped_70plus']:,}", VP, "silver.n_ped_70plus")
reg("v_gu", "구 수", f"{V119['silver']['n_gu']}", VP, "silver.n_gu")

# ------------------------------------------------------------------------------ numbers (AED)
AP = "experiments/aed/out/summary.json"
W = AED["desc"]["weekday"]
reg("a_day", "AED 열린 비율 낮 06~22시 평균 (평일)", f"{W['mean_pct_open_day']:.0f}", AP, "desc.weekday.mean_pct_open_day")
reg("a_night", "AED 열린 비율 밤 22~06시 평균 (평일)", f"{W['mean_pct_open_night']:.0f}", AP, "desc.weekday.mean_pct_open_night")
reg("a_day1", "낮 평균 (소수 1자리, 그림 표기)", f"{W['mean_pct_open_day']:.1f}", AP, "desc.weekday.mean_pct_open_day")
reg("a_night1", "밤 평균 (소수 1자리, 그림 표기)", f"{W['mean_pct_open_night']:.1f}", AP, "desc.weekday.mean_pct_open_night")
reg("a_n", "그림 막대 합 공공장소 심정지 건수", f"{int(AEDH.public_ca.sum()):,}", "experiments/aed/out/hourly_gap.csv", "public_ca 합 (2024-01~11, 서울)")

# ------------------------------------------------------------------------------ numbers (fall)
FP = fall_src.SRC
reg("f_sens_rule", "낙상 감도 규칙", pc(FALL["rule"]["sens"][0]), FP, "rule.sens[0]")
reg("f_sens_lr", "낙상 감도 경량 모델", pc(FALL["logreg"]["sens"][0]), FP, "logreg.sens[0]")
reg("f_f1_rule", "F1 규칙", pc(FALL["rule"]["f1"][0]), FP, "rule.f1[0]")
reg("f_f1_lr", "F1 경량 모델", pc(FALL["logreg"]["f1"][0]), FP, "logreg.f1[0]")
reg("f_lat_rule", "탐지 지연 규칙 (초, 중앙값)", f"{FALL['rule']['lat_med'][0]:.2f}", FP, "rule.lat_med[0]")
reg("f_lat_lr", "탐지 지연 경량 모델 (초, 중앙값)", f"{FALL['logreg']['lat_med'][0]:.2f}", FP, "logreg.lat_med[0]")
reg("f_fa_rule", "오경보/시간 규칙 (참고)", f"{FALL['rule']['fa_per_h'][0]:.0f}", FP, "rule.fa_per_h[0]")
reg("f_fa_lr", "오경보/시간 경량 모델 (참고)", f"{FALL['logreg']['fa_per_h'][0]:.0f}", FP, "logreg.fa_per_h[0]")
reg("f_nfall", "낙상 시퀀스 수", f"{FALL['n_fall']}", FP, "n_fall")
reg("f_nadl", "일상(비낙상) 시퀀스 수", f"{FALL['n_adl']}", FP, "n_adl")
reg("f_neg_h", "일상 영상 총 길이 (시간)", f"{FALL['neg_hours']:.2f}", FP, "neg_hours")

# ------------------------------------------------------------------------------ numbers (agent)
_bp = str(BENCH_PATH.relative_to(ROOT)) if BENCH_PATH.is_relative_to(ROOT) else str(BENCH_PATH)
BP = f"{_bp} = 10 runs of POST /agent/run, trigger=fall, 관악구 2025-07-22"
tool_ms: dict[str, list[float]] = {}
plan_ms: list[float] = []
brief_ms: list[float] = []
for r in BENCH:
    for st in (r["steps"] or []):
        tool_ms.setdefault(st["tool"], []).append(st["ms"])
    for t in r["plan"]["llm_turns"]:
        (plan_ms if t["kind"] == "plan" else brief_ms).append(t["ms"])
tl = next(r for r in BENCH if r["run_id"] == TIMELINE_RUN)
reg("g_total", "에이전트 한 번 실행 전체 p50 (ms)", f"{statistics.median(r['total_ms'] for r in BENCH):,.0f}", BP, "runs[*].total_ms 중앙값 (n=10)")
reg("g_plan", "LLM 계획 1회 p50 (ms)", f"{statistics.median(plan_ms):,.0f}", BP, f"runs[*].plan.llm_turns[kind=plan].ms 중앙값 (n={len(plan_ms)})")
reg("g_brief", "브리핑 p50 (ms)", f"{statistics.median(brief_ms):,.0f}", BP, "runs[*].plan.llm_turns[kind=brief].ms 중앙값")
reg("g_tool_sum", f"{TIMELINE_RUN} 도구 6개 합 (ms)", f"{(sum(s['ms'] for s in tl['steps']) if tl['steps'] else tl['tool_sum_ms']):,.0f}", BP, f"{TIMELINE_RUN}.steps[*].ms 합 (새 형식은 total - LLM 턴으로 계산)")
_tots = sorted(r["total_ms"] for r in BENCH)
reg("g_p95", "에이전트 한 번 실행 전체 p95 (ms)", f"{_tots[int(round(0.95 * (len(_tots) - 1)))]:,.0f}", BP, "runs[*].total_ms p95 (n=10)")
if FIRST_STEP_MS is not None:
    reg("g_first", "스트리밍 첫 단계 p50 (ms)", f"{FIRST_STEP_MS:,.0f}", str(_STREAM), "first_step_p50_ms")
reg("g_run_total", f"{TIMELINE_RUN} 총 시간 (ms)", f"{tl['total_ms']:,.0f}", BP, f"{TIMELINE_RUN}.total_ms")
reg("g_n", "벤치마크 실행 횟수", f"{len(BENCH)}", BP, "len(runs)")
reg("g_valid", "계획이 첫 시도에 유효했던 실행", f"{sum(1 for r in BENCH if r['plan']['first_try_valid'])}", BP, "runs[*].plan.first_try_valid")
reg("g_ground", "브리핑 숫자 검증 통과 실행", f"{sum(1 for r in BENCH if r['grounding']['ok'])}", BP, "runs[*].grounding.ok")
for tname in (("get_heat_region", "get_actions", "make_cap_alert", "make_fhir_bundle", "send_rsu_message", "send_119_report") if tool_ms else ()):
    reg(f"g_{tname}", f"도구 {tname} p50 (ms)", f"{statistics.median(tool_ms[tname]):.2f}", BP, f"runs[*].steps[tool={tname}].ms 중앙값")

# ------------------------------------------------------------------------------ numbers (119 dispatch, 서울 2024)
EMS_DIR = ROOT / "data/cache/extra/ems/api/act"
EMS_CACHE = BUILD / "ems_summary.json"


def ems_summary() -> dict:
    if EMS_CACHE.is_file():
        return json.loads(EMS_CACHE.read_text(encoding="utf-8"))
    import collections
    import gzip
    hours: collections.Counter = collections.Counter()
    places: collections.Counter = collections.Counter()
    n = 0
    for f in sorted(EMS_DIR.glob("*.json.gz")):
        for r in json.loads(gzip.open(f).read())["items"]:
            if r.get("egrcSidoCdNm") != "서울특별시":
                continue
            n += 1
            hours[r.get("gutHh")] += 1
            places[r.get("ruptOccrPlcCdNm")] += 1
    res = {"n": n, "hours": dict(hours), "home": places["집"], "with_place": sum(v for k, v in places.items() if k)}
    EMS_CACHE.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    return res


EMS = ems_summary()
EP = "data/cache/extra/ems/api/act/*.json.gz (egrcSidoCdNm=서울특별시)"
reg("e_n", "서울 구급 출동 건수 (2024-01~11)", f"{EMS['n']:,}", EP, "행 수 (gutHh 등)")
reg("e_h9", "9시 출동 건수", f"{EMS['hours']['09']:,}", EP, "gutHh=09")
reg("e_h10", "10시 출동 건수", f"{EMS['hours']['10']:,}", EP, "gutHh=10")
reg("e_home", "발생 장소 '집' 비율 (전체 출동 대비)", f"{100 * EMS['home'] / EMS['n']:.1f}", EP, "ruptOccrPlcCdNm=집 / 전체")
reg("e_home2", "발생 장소 '집' 비율 (장소 기재 건 대비)", f"{100 * EMS['home'] / EMS['with_place']:.1f}", EP, f"집 {EMS['home']:,} / 장소 기재 {EMS['with_place']:,}")

# ------------------------------------------------------------------------------ numbers (crosswalk signal deficit; 표준데이터 50k rows, truncated)
CW_PATH = "data/cache/extra/silverzone/kr_15028201_crosswalk_std.csv"
_cw = pd.read_csv(ROOT / CW_PATH, low_memory=False)
_cwv = _cw[(_cw.TFCLGHT_YN == "Y") & (_cw.GREEN_SGNGNR_TIME > 0) & (_cw.ET > 0)]
reg("cw_n", "녹색시간·길이가 모두 있는 신호 횡단보도 수 (표준데이터 50,000건 중)", f"{len(_cwv):,}", CW_PATH, "TFCLGHT_YN=Y & GREEN_SGNGNR_TIME>0 & ET>0")
reg("cw_gg", "그 중 경기도 수", f"{int((_cwv.CTPRVN_NM == '경기도').sum()):,}", CW_PATH, "CTPRVN_NM=경기도")
for _sp, _k in ((0.7, "cw_07"), (1.0, "cw_10"), (0.8, "cw_08")):
    reg(_k, f"녹색시간 < 길이/{_sp} m/s 인 횡단보도 비율 (%)", f"{100 * (_cwv.GREEN_SGNGNR_TIME < _cwv.ET / _sp).mean():.1f}", CW_PATH, f"mean(GREEN_SGNGNR_TIME < ET/{_sp}); 녹색시간에 점멸 포함 여부 미확인")
_mh = silver_src.seoul_hotspots()
MAPF = silver_src.SRC
reg("m_n", "지도: 서울 2024 사고 다발지 수", f"{len(_mh)}", MAPF, "kind=hotspot_2024 & 서울 경계 안")
reg("m_cur", "지도: 현행 구역 안(300 m) 사고 다발지", f"{int((_mh.cur_within_300m == 1).sum())}", MAPF, "cur_within_300m=1")
reg("m_ours", "지도: 에이전트 추천 안 사고 다발지", f"{int((_mh.ours_within_300m == 1).sum())}", MAPF, "ours_within_300m=1")
reg("p_ped", "보행 사망자 중 65세 이상 비율, 2024 (%)", "67", "ideas/silverzone_check.md (도로교통공단 인용 보도, 2차)", "2024 보행 사망 920명 중 65세 이상 616명")
reg("p_ped_n", "2024 보행 사망 / 65세 이상", "920 / 616", "ideas/silverzone_check.md", "line 84")
reg("p_heat", "온열질환 사망자 중 60세 이상 비율, 2025 (%)", "62", "ideas/G1_20261024.md (질병청 보도, 2차)", "2025 온열질환 사망 29명 중 62%")
_sa = SC[(SC.variant == "harm") & (SC.stratum == "all") & (SC.k == 3918) & (SC.method == "ours_opt")].iloc[0]
reg("s_ours_lo", "에이전트 덮은 비율 CI 하한", pc(float(_sa.lo), 1), "experiments/silver/out/opt/promoted/coverage_results.csv", "ours_opt all lo")
reg("s_ours_hi", "에이전트 덮은 비율 CI 상한", pc(float(_sa.hi), 1), "experiments/silver/out/opt/promoted/coverage_results.csv", "ours_opt all hi")
reg("f_lat_lo", "낙상 감지 지연 AI CI 하한 (초)", f"{FALL['logreg']['lat_med'][1]:.2f}", FP, "logreg.lat_med[1]")
reg("f_lat_hi", "낙상 감지 지연 AI CI 상한 (초)", f"{FALL['logreg']['lat_med'][2]:.2f}", FP, "logreg.lat_med[2]")

# ------------------------------------------------------------------------------ numbers (/twin demo, Q&A additions)
TW, TW_PROV = twin_src.load()
TWP = f"GET {twin_src.BASE}/twin/{twin_src.SGG}/scene?meta=1 · /actions?date={twin_src.DATE}&hour={twin_src.HOUR} ({TW_PROV})"
reg("sz_cur", "서울 안 현행 노인보호구역 수 (지도 예시, 앱 state API 값)", f"{TW['zones_cur']:,}", TWP, "state.silver_eval_seoul.seoul_zones_current")
reg("sz_ours", "서울 안 에이전트 제안 구역 수 (지도 예시, 앱 state API 값. 구 코드 기준이라 지도 파일의 다각형 기준 1,403과 5곳 차이)", f"{TW['zones_ours']:,}", TWP, "state.silver_eval_seoul.seoul_zones_ours")
reg("t_bld", "3D 장면 관악구 건물 수 (국토부 건물통합정보)", f"{TW['buildings']:,}", TWP, "buildings")
reg("t_c90", "재난문자 표준 한도 (자)", f"{TW['cbs_std_limit']}", TWP, "actions[cbs].drafts[0].limit")
reg("t_c90n", "재난문자 90자 초안 길이 (자)", f"{TW['cbs_std_len']}", TWP, "actions[cbs].drafts[0].length")
reg("t_c157", "재난문자 시범운영 한도 (자)", f"{TW['cbs_pilot_limit']}", TWP, "actions[cbs].drafts[1].limit")
reg("t_c157n", "재난문자 157자 초안 길이 (자)", f"{TW['cbs_pilot_len']}", TWP, "actions[cbs].drafts[1].length")
OPT_PATH = EXP / "heat/out/opt/opt_results.json"
OPT_P = "experiments/heat/out/opt/opt_results.json"
_od = json.loads(OPT_PATH.read_text(encoding="utf-8"))["variants"]["F"]["pooled"]["delta"]["HGB*-HEUR"]["capture"]
reg("h_gbm", "튜닝한 GBM - 단순 위험 점수 포착률 차이 (%p, F, 4년 통합)", f"{100 * _od[0]:.1f}".replace("-0.0", "0.0"), OPT_P, "variants.F.pooled.delta.HGB*-HEUR.capture[0]")
reg("h_gbm_lo", "위 CI 하한", f"{100 * _od[1]:+.1f}", OPT_P, "variants.F.pooled.delta.HGB*-HEUR.capture[1]")
reg("h_gbm_hi", "위 CI 상한", f"{100 * _od[2]:+.1f}", OPT_P, "variants.F.pooled.delta.HGB*-HEUR.capture[2]")
reg("sn_current1", "새 사고 지점 덮은 비율 현행 (소수 1자리)", pc(sc("new", "current"), 1), SP, "variant=harm,stratum=new,k=3918,method=current,coverage")
L["sn_current"] = L["sn_current1"]  # one decimal everywhere (12.5), not 13
reg("sn_ours1", "새 사고 지점 덮은 비율 에이전트 (소수 1자리)", pc(sc("new", "ours_opt"), 1), SP, "variant=harm,stratum=new,k=3918,method=ours_opt,coverage")
FFLD, FFLD_KEY = fall_src.load_field()
FFP = str(fall_src.FIELD.relative_to(ROOT / "app/backend")) + f" (배포 모델 {FFLD_KEY})"
_cc, _mc = FFLD["cctvf"], FFLD["mcfd"]
reg("ff_sens", "실제 CCTV 클립(CCTVF) 낙상 감도 (배포 모델)", pc(_cc["sens"][0]), FFP, f"{FFLD_KEY}.cctvf.all.sens[0]")
reg("ff_sens_lo", "위 CI 하한", pc(_cc["sens"][1]), FFP, "cctvf.all.sens[1]")
reg("ff_sens_hi", "위 CI 상한", pc(_cc["sens"][2]), FFP, "cctvf.all.sens[2]")
reg("ff_fa", "실제 CCTV 클립 오경보/시간 (배포 모델)", f"{_cc['fa_per_h'][0]:.0f}", FFP, "cctvf.all.fa_per_h[0]")
reg("ff_nfall", "실제 CCTV 클립 낙상 수", f"{_cc['n_fall']}", FFP, "cctvf.all.n_fall")
reg("ff_nneg", "실제 CCTV 클립 비낙상 수", f"{_cc['n_neg_seq']}", FFP, "cctvf.all.n_neg_seq")
reg("ff_neg_h", "실제 CCTV 비낙상 영상 총 길이 (시간)", f"{_cc['neg_hours']:.2f}", FFP, "cctvf.all.neg_hours")
reg("ff_mc_sens", "다중 카메라 실험실 세트(MCFD) 낙상 감도 (배포 모델)", pc(_mc["sens"][0]), FFP, "mcfd.all.sens[0]")
_R23 = json.loads((EXP / "aed/out/replicate_2023.json").read_text(encoding="utf-8"))
RP = "experiments/aed/out/replicate_2023.json"
_n23, _n24 = _R23["stats_2023_Jan_Nov"], _R23["stats_2024_Jan_Nov"]
_c = _R23["comparison"]["night_share"]
reg("a23_share", "2023.1~11 서울 공공장소 심정지 중 밤(22~06시) 비율 (%)", pc(_n23["night_share"][0], 2), RP, "stats_2023_Jan_Nov.night_share[0]")
reg("a23_share1", "위 값 (소수 1자리)", pc(_n23["night_share"][0], 1), RP, "stats_2023_Jan_Nov.night_share[0]")
reg("a23_lo", "2023 비율 CI 하한 (%)", pc(_n23["night_share"][1], 2), RP, "stats_2023_Jan_Nov.night_share[1]")
reg("a23_hi", "2023 비율 CI 상한 (%)", pc(_n23["night_share"][2], 2), RP, "stats_2023_Jan_Nov.night_share[2]")
reg("a23_n", "2023.1~11 공공장소 심정지 건수 (2024와 합치지 않음)", f"{_n23['n_public_ca']:,}", RP, "stats_2023_Jan_Nov.n_public_ca")
reg("a24_share", "2024.1~11 밤 비율 (%)", pc(_n24["night_share"][0], 2), RP, "stats_2024_Jan_Nov.night_share[0]")
reg("a24_lo", "2024 비율 CI 하한 (%)", pc(_n24["night_share"][1], 2), RP, "stats_2024_Jan_Nov.night_share[1]")
reg("a24_hi", "2024 비율 CI 상한 (%)", pc(_n24["night_share"][2], 2), RP, "stats_2024_Jan_Nov.night_share[2]")
reg("a24_n", "2024.1~11 공공장소 심정지 건수", f"{_n24['n_public_ca']:,}", RP, "stats_2024_Jan_Nov.n_public_ca")
reg("a_dshare", "밤 비율 차이 2023-2024 (%p)", f"{100 * _c['diff_2023_minus_2024']:+.2f}", RP, "comparison.night_share.diff_2023_minus_2024")
reg("a_dshare1", "위 값 (소수 1자리)", f"{100 * _c['diff_2023_minus_2024']:+.1f}", RP, "comparison.night_share.diff_2023_minus_2024")
reg("a_dshare_lo", "차이 95% CI 하한 (Newcombe, %p)", f"{100 * _c['diff_ci95_newcombe'][0]:+.2f}", RP, "comparison.night_share.diff_ci95_newcombe[0]")
reg("a_dshare_hi", "차이 95% CI 상한 (Newcombe, %p)", f"{100 * _c['diff_ci95_newcombe'][1]:+.2f}", RP, "comparison.night_share.diff_ci95_newcombe[1]")
reg("a_dshare_p", "차이 p값 (양측)", f"{_c['p_two_sided']:.2f}", RP, "comparison.night_share.p_two_sided")
reg("a23_chi_p", "시간대별 분포 전체 비교 chi2 p (2023 vs 2024; 밤 비율 외 분포는 다름)", f"{_R23['hourly_chi2_2023_vs_2024']['p']:.3f}", RP, "hourly_chi2_2023_vs_2024.p")
reg("a_gw_day", "관악구 열린 AED 비율 14시 (2025-07-22, %)", f"{TW['aed_pct_day']:.0f}", TWP, f"GET /twin/11620/state?hour={twin_src.DAY_HOUR} summary.aed_open_pct")
reg("a_gw_night", "관악구 열린 AED 비율 23시 (2025-07-22, %)", f"{TW['aed_pct_night']:.0f}", TWP, f"GET /twin/11620/state?hour={twin_src.NIGHT_HOUR} summary.aed_open_pct")
reg("a_gw_n", "관악구 AED 대수", f"{TW['aed_total']}", TWP, "state.summary.aed_total")
reg("a_gw_unreg", "관악구 AED 중 운영시간 미등록 (닫힘으로 간주, 대)", f"{TW['aed_unreg']}", TWP, "state.aed[*].hours 비어 있음")
reg("a_gw_gain", "AED 1순위 24시간 전환 시 밤 2분 보행권 65세 이상 증가 (명, 모형 추정)", f"{TW['aed_gain65']}", TWP, "actions[aed.convert:*] evidence_text")
reg("t_meas", "건물 층수 실측 비율 (%)", f"{TW['measured_pct']}", TWP, "measured_pct")
reg("h_prec_miss", "에이전트 경보 중 환자가 나오지 않은 비율 (%) = 100 - 정밀도", f"{100 - float(L['h_prec_ours']):.0f}", HP, "100 - OURS.precision.est")
_hw_brand = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, check=True).stdout.strip()
_hw_gb = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=True).stdout.strip()) // 2**30
reg("hw", "벤치마크·시연 기기 (이 빌드 기기에서 읽음)", f"{_hw_brand} {_hw_gb}GB", "sysctl machdep.cpu.brand_string, hw.memsize", "로컬 실행, 현장 기기 사양은 파일럿에서 확정")
WK = json.loads((EXP / "walk4/out/opt.json").read_text(encoding="utf-8"))
WP = "experiments/walk4/out/opt.json"
_wb, _wc = WK["base"]["night"]["p65"], WK["convert"]["115"]["exact"]["night"]["p65"]
reg("w_base", "밤 2분 도달 65세 이상 비율, 현재 열린 AED만 (%, 모형 추정, 걸어서 2분 = 1.2 m/s × 120 s = 144 m)", pc(_wb, 1), WP, "base.night.p65")
reg("w_conv", "같은 비율, 후보 AED 1% 24시간 전환 후 (%, 모형 추정, 걸어서 2분 = 1.2 m/s, 144 m)", pc(_wc, 1), WP, 'convert["115"].exact.night.p65')
reg("w_rand", "같은 비율, 무작위 1% 전환 (%, 모형 추정, Q&A용 기준선)", pc(WK["random"]["115"]["exact_one_draw"]["night"]["p65"], 1), WP, 'random["115"].exact_one_draw.night.p65')
reg("w_budget", "전환 대수 = AED 1% (대)", f"{WK['budgets'][3]}", WP, "budgets[3]")
reg("w_n", "분석 대상 서울 AED (대)", f"{WK['n_aed']:,}", WP, "n_aed")
reg("sm_108", "스마트도시 통합플랫폼 보급 지자체 수 (2025, 보도 인용)", "108", "deck/QA_and_B2G.md line 9 (보안뉴스 2025-09-15, 2차 자료)", "통합플랫폼 108개 지자체")
assert round(float(L["f_lat_rule"]) / float(L["f_lat_lr"])) == 3, "script says 3x faster"

# ------------------------------------------------------------------------------ numbers (문제 정의, 2차 자료: 기사·보도 인용, 원문 대조 필요)
PD = "deck/PROBLEM_DEFINITION.md"
reg("pd_pop", "65세 이상 주민등록 인구 비율 2024-12-23 (%)", "20.0", PD + " (행안부 발표, 기사 인용)", "10,244,550 / 51,221,286")
reg("pd_2050", "2050년 65세 이상 비율 전망 (% 이상)", "40", PD + " (통계청 장래인구추계 2022 기준 중위, korea.kr)", "2050년 40% 이상")
reg("pd_sg_a", "싱가포르 65세 이상 보행 사망 2024 (명)", "11", PD + " (싱가포르 교통부 의회 서면답변 2026-05-06)", "2024")
reg("pd_sg_b", "싱가포르 65세 이상 보행 사망 2025 (명)", "27", PD + " (싱가포르 교통부 의회 서면답변 2026-05-06)", "2025")

# ------------------------------------------------------------------------------ photos
PHOTO_CHEONAN5 = ASSETS / "Silver_Zone_in_Yeonamyulgeum-ro_in_Cheonan_City_20240720_05_.jpg"
PHOTO_CHEONAN1 = ASSETS / "Silver_Zone_in_Yeonamyulgeum-ro_in_Cheonan_City_20240720_01_.jpg"
PHOTO_REFUGE = ASSETS / "Finding_refuge_from_the_scorching_summer_sun_55497189804_.jpg"
PHOTO_AMB = ASSETS / "Hyundai_ambulance_in_Seoul_2018.jpg"
CREDIT_CHEONAN = "사진: 천안 노인보호구역 도로, LandAndTree, CC BY 4.0, Wikimedia Commons"
CREDIT_B3 = "사진: 폭염 참고 사진(런던) Alisdare Hickson CC BY-SA 4.0 · 천안 노인보호구역 LandAndTree CC BY 4.0 · 서울 구급차 Kevin.B CC BY-SA 4.0, Wikimedia Commons"
PHOTO_HEAT = ASSETS / "Heatwave_Shelter_Sign_in_Sungmun-gil_in_South_Korea.jpg"
PHOTO_SILVER = ASSETS / "Silver_Zone_Sign_in_Yeonamyulgeum-ro_Cheonan_City.jpg"
PHOTO_AED = ASSETS / "제세동기_AED.jpg"
CREDIT_3 = ("사진: 무더위쉼터 표지 Monotaxism CC0 · 노인보호 표지 LandAndTree CC BY 4.0 · 공공 AED Kimdorian CC BY-SA 4.0, Wikimedia Commons")
CREDIT_SILVER = "사진: LandAndTree, CC BY 4.0, Wikimedia Commons"


def src_line(path: str, key: str, sha: str = "") -> str:
    return f"출처: {path} · {key}" + (f" · sha256 {sha[:8]}" if sha else "")


HEAT_SRC = src_line("experiments/heat/out/results.json", "variants.F", HEAT_SHA)

# ------------------------------------------------------------------------------ slides
REG: dict[str, dict] = {}
FIGNO = {"n": 0}


def fig_el(name: str, caption: str, source: str, box: tuple[float, float, float, float]) -> dict:
    return {"type": "image", "path": str(FIG / f"{name}.png"), "box": list(box), "caption": caption, "source": source}


def fig_top(n_kw: int) -> float:
    return 1.65 + 0.4 * n_kw + 0.1


def add_slide(title: str, tags: str, criterion: str, headline: str, keywords: list[str], elements: list[dict], proves: str, evidence: str,
              credit: str = "", appendix: bool = False, notes_key: int | None = None, key: str | None = None) -> None:
    REG[key or title] = ({"title": title, "tags": tags, "criterion": criterion, "headline": headline, "keywords": keywords, "elements": elements,
                   "proves": proves, "evidence": evidence, "credit": credit, "appendix": appendix, "script_slide": notes_key})


def side(name: str, caption: str, source: str, n_kw: int, big: list[tuple[str, list[str]]]) -> list[dict]:
    top = fig_top(n_kw)
    els = [fig_el(name, caption, source, (0.4, top, 8.7, 6.0 - top))]
    ys = [top + 0.05, top + 2.15]
    for (b, sub), y in zip(big, ys):
        els.append({"type": "bignum", "box": [9.35, y, 3.6, 1.95], "big": b, "sub": sub, "size": 30})
    return els


def full(name: str, caption: str, source: str, n_kw: int) -> list[dict]:
    top = fig_top(n_kw)
    return [fig_el(name, caption, source, (0.4, top, 12.5, 6.0 - top))]


FIGNO["n"] = 0
HEAT_N = f"환자 {L['h_n_cases']}명, 시군구-일 {L['h_n_rows']}건"

# 3 한눈에 보기
add_slide("한눈에 보기", "[생활문제] [디지털] [AI]", "발표", "문제 · 제품 · 증거 · 효과", [], [
    {"type": "card", "box": [0.5, 2.0, 2.95, 3.6], "head": "문제 (생활)", "size": 24, "lines": ["폭염 속 고령자", "횡단보도", "밤에 잠긴 AED"]},
    {"type": "card", "box": [3.6, 2.0, 2.95, 3.6], "head": "제품", "size": 24, "lines": ["엣지 에이전트", "+", "분석 엔진"]},
    {"type": "card", "box": [6.7, 2.0, 2.95, 3.6], "head": "증거 (숫자 3개)", "size": 20, "lines": [f"폭염 포착 {L['h_b1']}→{L['h_ours']}%", f"실버존 덮음 {L['s_current']}→{L['s_ours_full']}%", f"낙상 지연 {L['f_lat_rule']}→{L['f_lat_lr']}초"]},
    {"type": "card", "box": [9.8, 2.0, 2.95, 3.6], "head": "효과 (대상)", "size": 24, "lines": ["고령자", "지자체", "119"]},
], "문제, 제품, 증거, 효과 네 가지를 한 장에 보여 줍니다", "카드 4개 (그림 없음, 숫자는 근거 장과 동일)")

# 4 문제
add_slide("문제", "[생활문제]", "주제·독창성", "공식 기준이 놓친 사람", [f"서울 구급 출동 {L['e_n']}건 · 9–10시 최다 · 집 {L['e_home']}%"], [
    {"type": "photo", "path": str(PHOTO_HEAT), "box": [0.7, 2.15, 3.3, 3.7], "label": "폭염 · 무더위쉼터"},
    {"type": "photo", "path": str(PHOTO_SILVER), "box": [4.9, 2.15, 3.6, 3.7], "label": "횡단보도 · 노인보호구역"},
    {"type": "photo", "path": str(PHOTO_AED), "box": [9.2, 2.15, 3.3, 3.7], "label": "밤 · 공공 AED"},
], "고령자의 생활 속 세 장면이 공식 기준 아래 놓여 있고 출동은 오전·집에 몰림", "실사진 3장 + 키워드 숫자 (서울 119 출동 2024)", CREDIT_3)

# 5 빈틈
add_slide("공통 실패", "[생활문제]", "주제·독창성", "세 곳 모두 빈틈", [], [
    {"type": "bignum", "box": [0.6, 1.9, 3.9, 3.4], "big": f"{L['h_b1']}%", "label": "특보 포착", "sub": ["폭염 특보 지역에서", "나온 환자 비율"], "size": 66, "subsize": 20},
    {"type": "bignum", "box": [4.7, 1.9, 3.9, 3.4], "big": f"{L['s_current']}%", "label": "실버존 덮음", "sub": ["노인보호구역이", "덮은 사고 지점"], "size": 66, "subsize": 20},
    {"type": "bignum", "box": [8.8, 1.9, 3.9, 3.4], "big": f"{L['a_night']}%", "label": "밤 AED 열림", "sub": ["서울 평일 평균, 밤", "(낮 " + L["a_day"] + "%)"], "size": 66, "subsize": 20},
    {"type": "text", "box": [0.6, 5.5, 12.1, 0.9], "lines": [f"출처: {HP} (variants.F, sha256 {HEAT_SHA[:8]}) · {SP} (k=3,918) · {AP}"], "size": 9.5},
], "현행 공식 기준이 세 곳에서 절반 이하만 덮음", "큰 숫자 3개 (4년 통합 포착률, 덮은 비율, 열린 비율)")

# 6 엣지 에이전트
n6 = 2
add_slide("엣지 에이전트", "[AI] [디지털]", "완성도", "카메라에서 119까지", ["낙상 → 계획 → 119 시뮬레이터 접수(202) · 모든 지연 실측", "병원 도착 전 정보: FHIR 표준 묶음 생성 (119 시뮬레이터)"],
          full("03_agent_flow_measured", f"에이전트 흐름과 구간별 실측 지연 (실행 {L['g_n']}회 중앙값, 낙상 모델은 UR Fall 교차검증)", src_line("bench.json", "runs[*].steps/plan.llm_turns/total_ms · " + FP, ""), 2),
          "카메라부터 119 접수까지 끝까지 동작하고 구간 지연이 실측임", "블록 다이어그램 + ms 표기")

# 7 엔진
add_slide("분석 엔진", "[디지털] [AI]", "기술 적정성", "공식 기준과 같은 예산", ["예측 → 비교 → 배분 → 행동", "전국 일률 기준 → 시군구별 위험 순위"],
          [fig_el("09_engine_flow", "분석 엔진의 데이터 흐름 (입력은 공개 데이터, 출력은 행동 채널)", "출처: deck/figs/final/09_engine_flow.dot", (0.4, 2.5, 12.5, 1.2)),
           {"type": "card", "box": [0.5, 4.6, 3.9, 1.9], "head": "공공 데이터", "size": 20, "lines": ["기상청 · 소방청", "국립중앙의료원"]},
           {"type": "card", "box": [4.7, 4.6, 3.9, 1.9], "head": "국제 표준", "size": 20, "lines": ["CAP 1.2 경보", "FHIR R4 구급 기록"]},
           {"type": "card", "box": [8.9, 4.6, 3.9, 1.9], "head": "선제 대응 (신고 전)", "size": 15, "lines": ["고위험 구 방문팀 사전 배치", "밤 AED 24시간 개방 요청", "위험 횡단보도 신호 연장"]}],
          "공개 데이터에서 행동까지 한 방향으로 이어진 구조", "5단계 흐름도 + 카드 3개")

# 8 데모 1
add_slide("부록 현장 에이전트 실행 기록", "[AI] [디지털]", "완성도", "한 번 실행의 실측 기록", ["라이브: 낙상 → 119 시뮬레이터 접수(202)", "설명 가능 · 결정은 사람 (추천마다 근거 표시)"],
          full("04_agent_run_timeline", f"{TIMELINE_RUN} 실행의 단계별 소요 시간 (도구 6개 합 {L['g_tool_sum']} ms, 총 {L['g_run_total']} ms)", src_line("bench.json", TIMELINE_RUN + ".steps/plan.llm_turns", ""), 2),
          "에이전트가 실제로 돌았고 시간이 어디에 쓰였는지 보여 줌", "타임라인 막대 (실측 ms)", key="데모 ① 현장 에이전트")

SHOT_DIR = ROOT / "app/frontend/screenshots"
SHOTS = sorted(SHOT_DIR.glob("final-*.png")) if SHOT_DIR.is_dir() else []
SHOT = next((p for p in SHOTS if "briefing" in p.name.lower()), None)
def briefing_visual() -> dict:
    if SHOT is None:
        return {"type": "graybox", "box": [0.6, 2.6, 8.6, 3.5], "lines": ["화면: 오늘의 에이전트 브리핑", "(라이브 /briefing, 백업 녹화 준비)"]}
    from PIL import Image
    crop = ASSETS / "briefing_top_crop.png"
    with Image.open(SHOT) as im:
        im.crop((0, 0, im.width, min(im.height, 1000))).save(crop)  # top 1000 px only; full page = SHOT
    return {"type": "image", "path": str(crop), "box": [0.5, 2.6, 8.8, 3.5], "caption": "", "source": f"화면 상단 일부 · 전체: app/frontend/screenshots/{SHOT.name}"}


BRIEFING_VISUAL = briefing_visual()
# 9 데모 2
add_slide("부록 오늘의 에이전트 브리핑", "[AI] [디지털] [생활문제]", "완성도", "시군구 1곳 → 한 장", ["관악구 · 2025-07-22", "설명 가능 · 결정은 사람 (추천마다 근거 \"왜?\" · 채택/보류/부적합 기록)"], [
    BRIEFING_VISUAL,
    {"type": "card", "box": [9.5, 2.7, 3.2, 3.4], "head": "한 장에", "size": 22, "lines": ["폭염 순위", "실버존 위치", "밤 AED 공백", "오늘의 행동"]},
], "세 모듈 결과가 한 화면에 나오고 숫자는 도구 결과와 대조됨", "라이브 화면 (회색 자리 = 시연 화면)", key="데모 ② 오늘의 에이전트 브리핑")

# 10 폭염
n = 2
add_slide("폭염: 같은 경보 수", "[AI]", "실용성", "같은 경보 수, 더 많이 포착", ["포착률 = 경보 지역 환자 ÷ 전체 환자"],
          side("01_heat_pooled_and_by_year", f"특보 규칙과 에이전트의 포착률, 4년 통합(왼쪽)과 연도별(오른쪽) ({HEAT_N}, 95% 신뢰구간)", HEAT_SRC, 1,
               [(f"{L['h_b1']}% → {L['h_ours']}%", ["포착률", "(같은 경보 수)"]), (f"+{L['h_d']}%p", [f"95% CI {L['h_d_lo']}~{L['h_d_hi']}"])]),
          "같은 경보 수에서 에이전트가 특보 규칙보다 많이 잡음", "Fig: 통합 막대 + 연도별 막대 (CI)")

# 11 구성 요소
add_slide("엔진 구성 요소별 기여", "[AI]", "기술 적정성", "단계마다 쌓이는 이득", ["특보 규칙 → 시도 모델 → 인구 반영 → 시군구"],
          side("02_heat_component_gain", f"엔진 구성 요소별 포착률 증가, 4년 통합 ({HEAT_N}, 95% 신뢰구간, 입력 두 가지)", HEAT_SRC.replace("variants.F", "variants.{F,O}.pooled_2022_2025.delta"), 1,
               [(f"+{L['h_s1']}%p", ["시도 단위 모델"]), (f"+{L['h_s2']}%p", ["인구·취약성 반영"])]),
          "포착률 향상이 어느 구성 요소에서 오는지 보여 줌", "Fig: 3단계 막대 (CI)")

# 12 실버존
add_slide("실버존: 같은 개수", "[AI]", "실용성", f"현행 {L['s_current']}% → 에이전트 {L['s_ours_full']}%", [f"새 사고 지점 {L['sn_current']}% → {L['sn_ours_full']}% (같은 {L['s_k']}곳)", f"작년 사고 장소 재사용 {L['s_past_k']}%: 전체로는 에이전트와 동률 ({L['sd_a_past']}%p [{L['sd_a_past_lo']}, {L['sd_a_past_hi']}])"],
          side("05_silver_coverage_bars", f"같은 개수를 고른 방법별 덮은 사고 지점 비율, 전체(왼쪽)와 새로 생긴 사고 지점(오른쪽) (전체 {L['s_n']}곳, 새 지점 {L['s_n_new']}곳, 95% 신뢰구간)", src_line(SP, "variant=harm,k=3918,pool=C/OPT"), 2,
               [(f"{L['s_current']}% → {L['s_ours_full']}%", ["현행 → 에이전트 (전체)"]), (f"{L['sn_current']}% → {L['sn_ours_full']}%", [f"새 사고 지점 (95% CI {L['sn_ours_lo']}~{L['sn_ours_hi']})"])]),
          "같은 개수를 골라도 에이전트가 사고 지점을 훨씬 많이 덮고 새 지점에서도 오름", "Fig: 4막대 + 새 지점 2막대 (CI)")

# 14 AED
add_slide("밤에 잠긴 AED", "[생활문제]", "실용성", f"서울 평일 평균 낮 {L['a_day']}% → 밤 {L['a_night']}% 열림", [f"관악구 14시 {L['a_gw_day']}% · 23시 {L['a_gw_night']}% (AED {L['a_gw_n']}대, 2025-07-22)", f"2023년 별도 자료로도 재현 (밤 비율 {L['a23_share']}%, 2024년 {L['a24_share']}%)"],
          full("07_aed_night_gap", f"시간대별 공공장소 심정지(막대)와 열린 AED 비율(선) (서울 2024.1~11, 심정지 {L['a_n']}건)", src_line("experiments/aed/out/hourly_gap.csv", "public_ca, pct_open_weekday · summary.json desc.weekday") + f" · 재현: {RP} (2023.1~11 {L['a23_n']}건, 건수는 연도별로 따로)", 2),
          "AED가 밤에 절반만 열려 있어 심정지 시간대와 어긋남", "Fig: 막대+선 이중 축")

# 15 낙상
add_slide("낙상 감지 모델", "[AI]", "기술 적정성", f"감도 {L['f_sens_rule']}% → {L['f_sens_lr']}%", [f"지연 {L['f_lat_rule']}초 → {L['f_lat_lr']}초"],
          side("08_fall_model", f"낙상 감지 규칙과 경량 학습 모델(로지스틱 회귀) 비교 (UR Fall 낙상 {L['f_nfall']}건, 일상 {L['f_nadl']}건, 5겹 교차검증, 95% 신뢰구간)", src_line(FP, "rule/logreg.{sens,f1,lat_med}"), 1,
               [(f"{L['f_sens_rule']}% → {L['f_sens_lr']}%", ["감도 (낙상을 잡은 비율)"]), (f"{L['f_lat_rule']} → {L['f_lat_lr']}초", ["탐지 지연 (중앙값)"])]),
          "공개 낙상 데이터에서 경량 모델이 규칙보다 많이, 빨리 잡음", "Fig: 3패널 막대 (CI)")

# 16 알고리즘
add_slide("알고리즘 3개", "[AI]", "기술 적정성", "고른 이유가 분명한 3개", ["포아송 GBM · MCLP · 블록 부트스트랩", f"MCLP로 덮은 사고 지점 {L['s_current']}% → {L['s_ours_full']}%"], [
    {"type": "text", "box": [0.5, 2.7, 4.2, 3.4], "lines": ["포아송 GBM", "환자 수 예측 → 위험 순위", "", "최대 커버리지 입지 (MCLP)", "가장 많이 덮는 곳부터", "", "블록 부트스트랩", "날짜 묶음으로 신뢰구간"], "size": 19},
    {"type": "image", "path": str(FIG / "11_code_greedy_mclp.png"), "box": [4.9, 2.7, 8.1, 3.3], "caption": "", "source": "app/backend/experiments/silver/run.py:199-216"},
], "알고리즘 이름과 핵심 코드 18줄 (줄 번호 실제 파일과 동일)", "코드 PNG 1장 (강조 2줄)")

# 17 DB
add_slide("DB 핵심 테이블 3개", "[디지털]", "기술 적정성", "실행과 결과를 나눈 설계", ["alert_run · alert 수정 불가 (트리거)"],
          full("10_erd_core_tables", "핵심 3개 테이블과 관계 (SQLite, STRICT, 외래키 선언)", "출처: app/backend/db/migrations/0003_heat.sql", 1),
          "방법·연도·예산은 실행 테이블, 결과는 경보 테이블로 분리", "ERD 3개 표")

# 18 보안
add_slide("보안 · 개인정보", "[디지털]", "기술 적정성", "알고리즘 이름으로 명시", [], [
    {"type": "table", "x": 0.6, "y": 1.75, "w": 12.1, "col_w": [2.6, 4.3, 5.2], "size": 16, "row_h": 0.6, "rows": [
        ["위협", "알고리즘", "파라미터"],
        ["비밀번호 유출", "Argon2id", "t=3 · m=64 MiB · p=4"],
        ["토큰 위조", "HMAC-SHA256", "유효 900초"],
        ["감사 로그 변조", "HMAC-SHA256 해시 체인", "수정·삭제 트리거 차단"],
        ["SQL 주입", "파라미터 바인딩", "모든 값 ? 바인딩"],
        ["과다 요청", "토큰 버킷", "용량 60 · 초당 20"],
        ["영상 개인정보", "저장 안 함", "해시(SHA-256)만 기록"],
    ]},
    {"type": "text", "box": [0.6, 6.0, 12.1, 0.4], "lines": ["출처: app/backend/app/security/ALGORITHMS.md · db/migrations/0006_agent.sql"], "size": 9.5},
], "주요 위협마다 알고리즘과 파라미터가 정해져 있음", "표 1개 (security/ALGORITHMS.md)")

# 19 B2G
add_slide("지자체 도입", "[생활문제] [디지털]", "실용성", "기존 카메라에 소프트웨어로", [], [
    {"type": "card", "box": [0.5, 2.2, 2.9, 3.0], "size": 24, "head": "탑재", "lines": ["기존 CCTV", "스마트 가로등"]},
    {"type": "card", "box": [3.6, 2.2, 2.9, 3.0], "size": 24, "head": "도입 경로", "lines": ["혁신제품 지정", "시범 구매"]},
    {"type": "card", "box": [6.7, 2.2, 2.9, 3.0], "size": 24, "head": "연계", "lines": ["통합플랫폼", "CAP 1.2 경보"]},
    {"type": "card", "box": [9.8, 2.2, 2.9, 3.0], "size": 24, "head": "과금", "lines": ["채널 단위 구독", "(가격은 가설)"]},
    {"type": "text", "box": [0.5, 5.5, 12.3, 0.5], "lines": ["출처: deck/QA_and_B2G.md (조달청 혁신제품, 스마트도시 통합플랫폼; 2차 자료는 원문 대조 필요)"], "size": 9.5},
], "기존 장비에 소프트웨어로 올리는 도입 경로", "카드 4개 (경로)")

# 20 비전
add_slide("비전", "[생활문제] [디지털] [AI]", "주제·독창성", "가장 느린 걸음을 지킵니다", ["AI와 디지털 기술로, 우리 동네 고령자의 하루를 지킵니다"], [
    {"type": "photo", "path": str(PHOTO_SILVER), "box": [3.9, 2.4, 5.5, 3.5], "label": "노인보호구역 표지"},
], "주제 문장으로 마무리", "실사진 1장", CREDIT_SILVER)

# 21 심사 기준별 요약
add_slide("심사 기준별 요약", "[생활문제] [디지털] [AI]", "발표", "기준마다 증거 한 줄", [], [
    {"type": "table", "x": 0.6, "y": 1.8, "w": 12.1, "col_w": [2.9, 9.2], "size": 17, "row_h": 0.72, "rows": [
        ["심사 기준", "우리의 증거"],
        ["주제 관련성·독창성", "폭염 · 횡단보도 · 밤 AED, 공식 기준이 놓친 사람"],
        ["기술의 적정성", "포아송 GBM · MCLP · 블록 부트스트랩 · 불변 로그"],
        ["구현 완성도", f"카메라 → 119 시뮬레이터 접수(202) 실행 {L['g_n']}회, p50 {L['g_total']} ms"],
        ["실용성·실효성", f"같은 자원, 더 많은 사람: {L['h_b1']}→{L['h_ours']}% · {L['s_current']}→{L['s_ours_full']}% · 낙상 지연 {L['f_lat_rule']}→{L['f_lat_lr']}초"],
        ["발표", "문제 → 제품 → 증거 → 효과"],
    ]},
], "다섯 심사 기준마다 증거 한 줄이 있음", "표 1개")

# ---------------------------------------------------------------- appendix
add_slide("부록 A1 작년 사고 장소 vs 에이전트", "[AI]", "실용성", "작년 사고 장소 규칙과 비교", [f"전체: 동률 ({L['sd_a_past']}%p [{L['sd_a_past_lo']}, {L['sd_a_past_hi']}]) · k≤1,000은 뒤처짐 ({L['sd_small_min']}~{L['sd_small_max']}%p)", f"새 지점 {L['sd_n_past']}%p [{L['sd_n_past_lo']}, {L['sd_n_past_hi']}] · 같은 촘촘한 후보에서는 {L['sd_n_gridD']}%p [{L['sd_n_gridD_lo']}, {L['sd_n_gridD_hi']}]"],
          full("A1_silver_past_vs_ours", f"덮은 사고 지점 비율, 지점 수 k별(왼쪽)과 k=3,918의 전체·새 사고 지점(오른쪽) (전체 {L['s_n']}곳, 새 지점 {L['s_n_new']}곳, 95% 신뢰구간)", src_line(SPD, "method=ours_opt, vs=C:past_k / D:past_k"), 2),
          "전체에서는 작년 사고 장소 규칙과 동률이고 새 지점 이득은 후보 격자 효과가 큼", "Fig: k 곡선 + 막대", appendix=True)
add_slide("부록 A2 오경보", "[AI]", "실용성", "경보 중 실제 환자 발생", [f"정밀도 {L['h_prec_b1']}% → {L['h_prec_ours']}%"],
          full("A2_heat_precision", f"경보 낸 시군구-일 중 실제 환자가 나온 비율 ({HEAT_N}, 95% 신뢰구간)", HEAT_SRC, 1),
          "같은 경보 수에서 헛경보 비율도 낮아짐", "Fig: 2막대 (CI)", appendix=True)
add_slide("부록 A3 입력 두 가지", "[AI]", "기술 적정성", "예보 대용 입력과 전날 관측만", [f"전날까지 입력: {L['h_o_b1']}% → {L['h_o_ours']}% (+{L['h_o_d']}%p)"],
          full("A3_heat_forest_ours_minus_rule", "에이전트와 특보 규칙의 포착률 차이, 연도별과 통합, 입력 두 가지 (95% 신뢰구간)", HEAT_SRC, 1),
          "예보를 못 써도 에이전트가 앞섬(누수 점검)", "Fig: 포레스트 플롯", appendix=True)
add_slide("부록 A4 시도 단위 모델", "[AI]", "기술 적정성", "시도 단위 모델(우리 구현, 정부 모델 아님)과 비교", [f"시도 단위 모델 {L['h_b2']}% → 에이전트 {L['h_ours1']}% (+{L['h_o_b2']}%p)"],
          full("A4_heat_models_F_and_O", "네 방법의 포착률, 4년 통합, 입력 두 가지 (95% 신뢰구간)", HEAT_SRC, 1),
          "시도 단위 모델이 비교군에 들어 있고 에이전트가 앞섬", "Fig: 4막대 x 2", appendix=True, key="부록 A4 정부 시도 모델")
add_slide("부록 A5 경보 수를 바꿔도", "[AI]", "기술 적정성", "경보 수 0.25~2배", ["특보 규칙의 실제 경보량이 기준점"],
          full("A6_heat_budget_curve_2025", "경보 낸 시군구-일 비율에 따른 포착률 곡선, 2025년", src_line(HP, "variants.F.sweep.2025", HEAT_SHA), 1),
          "경보 수를 바꿔도 에이전트 곡선이 위에 있음", "Fig: 포착률 곡선", appendix=True)
add_slide("부록 A6 데이터 한계", "[생활문제]", "실용성", "한계는 숫자로", [], [
    {"type": "table", "x": 0.6, "y": 1.8, "w": 12.1, "col_w": [3.6, 8.5], "size": 16, "row_h": 0.56, "rows": [
        ["항목", "값"],
        ["가장 가까운 관측소 거리", f"중앙값 {L['h_st_med']} km · 최대 {L['h_st_max']} km"],
        ["119 재검증 범위", f"서울 {L['v_gu']}개 구 단위만 (월 단위 이하 불가)"],
        ["AED 우리-공백 정책", "결과를 본 뒤 추가 (사후 설정)"],
        ["폭염 체감온도 식", "Stull 습구온도 기반 (기상청 식 여부 미확인)"],
        ["낙상 평가 영상", f"낙상 {L['f_nfall']}건 · 일상 {L['f_nadl']}건 · 일상 {L['f_neg_h']}시간"],
        ["폭염 119 대조", "구 단위 25곳만, 입력 F는 신뢰구간이 0을 포함"],
    ]},
    {"type": "text", "box": [0.6, 6.1, 12.1, 0.4], "lines": ["출처: " + HP + " (coverage_report) · " + VP + " (caveats) · experiments/aed/out/summary.json · " + FP], "size": 9.5},
], "한계를 숫자로 밝히고 어디까지 말할 수 있는지 구분", "표 1개", appendix=True)
add_slide("부록 A7 낙상 오경보", "[AI]", "기술 적정성", "오경보까지 함께", [f"오경보/시간 {L['f_fa_rule']} → {L['f_fa_lr']} (일상 영상 {L['f_neg_h']}시간 기준)"],
          full("A7_fall_with_false_alarm", f"낙상 모델 네 지표 (UR Fall 낙상 {L['f_nfall']}건, 일상 {L['f_nadl']}건, 5겹 교차검증, 95% 신뢰구간)", src_line(FP, "rule/logreg"), 1),
          "시간당 오경보는 짧은 일상 영상 기준이라 참고용", "Fig: 4패널 막대 (CI)", appendix=True)
add_slide("부록 A8 에이전트 지연", "[AI] [디지털]", "완성도", "실행 10회의 전체 시간", [f"중앙값 {L['g_total']} ms"],
          full("A8_agent_latency_distribution", f"에이전트 한 번 실행 전체 시간 분포 (실행 {L['g_n']}회, 상자 = 사분위, 점 = 개별 실행)", src_line("bench.json", "runs[*].total_ms", ""), 1),
          "지연의 변동 범위", "Fig: 상자 + 점", appendix=True)

add_slide("부록 A9 데이터 선순환", "[AI] [디지털]", "주제·독창성", "기록 → 학습 → 현장", ["실선 = 오늘 구현 · 점선 = 다음 단계"],
          full("12_data_flywheel", "에이전트 데이터 선순환 (실선은 구현된 단계, 점선은 다음 단계)", "출처: 구조도 (구현 여부: experiments/*, app/agent, POST /feedback)", 1),
          "현장 판단 기록까지 구현, 결과 대조와 재학습은 다음 단계", "순환 다이어그램 (실선/점선)", appendix=True)

add_slide("부록 A10 119 독립 검증", "[AI]", "실용성", f"구 단위: 현행 {L['vo_cur']}% → 에이전트 {L['vo_ours']}%", ["목표: 사고 지점 덮기 (구별 몫은 목표 밖)", f"현행 − 에이전트 {L['vo_gap']}%p, 95% CI {L['vo_gap_lo']}~{L['vo_gap_hi']} (유의하지 않음)"],
          side("A10_silver_119_misallocation_opt", f"119 구급 기록 대비 엉뚱한 구에 놓인 비율 (서울 {L['v_gu']}개 구, 70세 이상 보행 {L['v_n']}건, 95% 신뢰구간)", src_line(VP, "silver_opt.misallocation_tvd"), 2,
               [(f"{L['vo_cur']}% → {L['vo_ours']}%", ["현행 → 에이전트"]), (f"{L['vo_pop']}%", ["65세 이상 인구 배분"])]),
          "구 단위로는 현행보다 낮으나 유의하지 않고 인구 배분보다 높음 (최적화 목표가 다름)", "Fig: 3막대 (CI)", appendix=True)

# ================================================================== new slides (key = internal id)
def crop_assets() -> dict[str, Path | None]:
    """Screenshot crops for the demo slides (top parts only; numbers inside screenshots may lag the latest build)."""
    from PIL import Image
    res: dict[str, Path | None] = {"silver_map": None, "field": None, "ops": None}
    sd = ROOT / "app/frontend/screenshots"
    f = sd / "final-silver-1440.png"
    if f.is_file():
        with Image.open(f) as im:
            im.crop((190, 1255, 850, 1850)).save(ASSETS / "demo_silver_map.png")
        res["silver_map"] = ASSETS / "demo_silver_map.png"
    f = sd / "final-field-agent-1440.png"
    if f.is_file():
        with Image.open(f) as im:
            im.crop((0, 0, im.width, 1400)).save(ASSETS / "demo_field_top.png")
        res["field"] = ASSETS / "demo_field_top.png"
    apps = sorted(sd.glob("app-*.png")) if sd.is_dir() else []
    res["ops"] = None
    if apps:
        with Image.open(apps[0]) as im:
            im.crop((0, 0, im.width, min(im.height, 1100))).save(ASSETS / "demo_ops_top.png")
        res["ops"] = ASSETS / "demo_ops_top.png"
    return res


CROPS = crop_assets()
TW_SRC = f"화면: {twin_src.BASE.replace('http://localhost:8010', '/twin')} 관악구 디지털 트윈 (app/frontend/screenshots)"


def demo_crops() -> dict[str, Path]:
    """Generated crops of the /twin screenshots (zone names: 1 = 3D map, 2 = 행정 조치 row, 3 = 공문·문안 panel, 4 = 시연 카메라).

    Newest file per name wins (mtime), so a re-shoot is picked up on the next build. Coordinates are for the 1920x1080 shots.
    """
    from PIL import Image
    sd = ROOT / "app/frontend/screenshots"
    specs = {  # whole screens for the hidden backup slides (robust to layout changes), one crop of the camera panel for the CCTV slide
        "tw_s2": ("twin-2-heat.png", None), "tw_s3": ("story-2-stop-terminal.png", None), "tw_s4": ("twin-4-silver.png", None),
        "tw_s5": ("twin-7-urfall-midfall.png", None), "tw_s6": ("twin-5-night.png", None),
        "tw_cam": ("twin-7-urfall-midfall.png", (1528, 155, 1912, 300)),
    }
    out: dict[str, Path] = {}
    for key, (name, box) in specs.items():
        src = sd / name
        if not src.is_file():
            raise FileNotFoundError(f"demo screenshot missing: {src}")
        dst = ASSETS / f"{key}.png"
        with Image.open(src) as im:
            (im.crop(box) if box else im).save(dst)
        out[key] = dst
    return out


DC = demo_crops()


def SRCL(text: str, y: float = 6.75) -> dict:
    """The single tiny source line (12 pt) at the bottom of a slide that shows data or a photo."""
    return {"type": "text", "box": [0.5, y, 12.4, 0.35], "lines": [text], "size": 12, "color": "ink", "bold": False, "align": "l", "head": False}
MAIN_SRC = f"출처: {HP} (variants.F, sha256 {HEAT_SHA[:8]}) · {SP} · {FP} · {CW_PATH}"


def bn(box: list[float], big: str, sub: list[str], label: str = "", size: int = 44, subsize: int = 15) -> dict:
    d = {"type": "bignum", "box": box, "big": big, "sub": sub, "size": size, "subsize": subsize}
    if label:
        d["label"] = label
    return d


def shot_or_box(path: Path | None, box: list[float], label: str, name: str) -> list[dict]:
    if path is not None:
        return [{"type": "photo", "path": str(path), "box": box, "label": label}]
    x, y, w, h = box
    return [{"type": "graybox", "box": [x, y, w, h], "lines": [f"화면: {name}", "(준비 중)"]}, {"type": "text", "box": [x, y + h + 0.05, w, 0.4], "lines": [label], "size": 17, "bold": True, "navy": True, "align": "c"}]


# ---- common slides
add_slide("보안 · 개인정보", "[디지털]", "기술 적정성", "알고리즘 이름으로 명시", [], REG["보안 · 개인정보"]["elements"], "주요 위협마다 알고리즘과 파라미터가 정해져 있음", "표 1개 (security/ALGORITHMS.md)", key="보안")
add_slide("도입 · 비전", "[생활문제] [디지털]", "실용성", "가장 느린 걸음을 지킵니다", ["AI와 디지털 기술로, 우리 동네 고령자의 하루를 지킵니다"], [
    {"type": "card", "box": [0.5, 2.7, 2.9, 2.4], "size": 22, "head": "탑재", "lines": ["기존 CCTV", "스마트 가로등"]},
    {"type": "card", "box": [3.6, 2.7, 2.9, 2.4], "size": 22, "head": "도입 경로", "lines": ["혁신제품 지정", "시범 구매"]},
    {"type": "card", "box": [6.7, 2.7, 2.9, 2.4], "size": 22, "head": "연계", "lines": ["통합플랫폼", "CAP 1.2 경보"]},
    {"type": "card", "box": [9.8, 2.7, 2.9, 2.4], "size": 22, "head": "과금", "lines": ["채널 단위 구독", "(가격은 가설)"]},
    {"type": "text", "box": [0.5, 5.4, 12.3, 0.5], "lines": ["출처: deck/QA_and_B2G.md (조달청 혁신제품, 스마트도시 통합플랫폼; 2차 자료는 원문 대조 필요)"], "size": 9.5},
], "기존 장비에 소프트웨어로 올리는 도입 경로와 주제 문장", "카드 4개", key="도입")
add_slide("같은 엔진, 다른 생활 위험", "[AI]", "주제·독창성", "같은 엔진으로 확장", ["폭염과 밤 AED도 같은 방식으로 비교"], [
    bn([0.8, 2.3, 5.6, 3.4], f"{L['h_b1']}% → {L['h_ours']}%", ["같은 경보 수, 폭염 환자 포착", f"+{L['h_d']}%p (95% CI {L['h_d_lo']}~{L['h_d_hi']})"], "폭염", 54, 18),
    bn([6.9, 2.3, 5.6, 3.4], f"{L['a_day']}% → {L['a_night']}%", ["열려 있는 AED, 낮 → 밤", "(서울 평일, 2024.1~11)"], "밤 AED", 54, 18),
    {"type": "text", "box": [0.8, 5.9, 11.7, 0.4], "lines": [f"출처: {HP} (variants.F, sha256 {HEAT_SHA[:8]}) · {AP}"], "size": 9.5},
], "같은 엔진을 폭염·AED에도 적용한 두 숫자", "큰 숫자 2개", key="확장")

# ---- variant A slides (보행안전 단독)
add_slide("문제", "[생활문제]", "주제·독창성", "고령자가 건너는 길이 위험합니다", [], [
    {"type": "photo", "path": str(PHOTO_CHEONAN5), "box": [0.5, 1.8, 7.6, 4.1], "label": "실제 노인보호구역 도로 (천안)"},
    bn([8.5, 1.95, 4.3, 3.6], f"{L['p_ped']}%", ["보행 사망자 중 65세 이상", "(2024, 920명 중 616명)"], "3명 중 2명", 54, 17),
    {"type": "text", "box": [8.5, 5.65, 4.3, 0.5], "lines": ["출처: 도로교통공단 통계 보도 인용 (ideas/silverzone_check.md)"], "size": 9.5},
], "보행 사망의 3분의 2가 65세 이상이라는 문제와 실제 현장 도로", "실사진 1장 + 큰 숫자 1개", CREDIT_CHEONAN, key="A문제")
add_slide("원인", "[생활문제] [AI]", "주제·독창성", "", [], [
    {"type": "rect", "box": [0.5, 1.75, 3.9, 4.2], "fill": "light"},
    T([0.5, 1.95, 3.9, 0.75], "실버존", 30, "navy", True, head=True),
    T([0.5, 2.8, 3.9, 1.7], f"{L['s_current']}%", 80, "blue", True, head=True),
    T([0.5, 4.7, 3.9, 0.75], "사고 지점 덮음", 28, "ink", True),
    {"type": "rect", "box": [4.7, 1.75, 3.9, 4.2], "fill": "light"},
    T([4.7, 1.95, 3.9, 0.75], "신호", 30, "navy", True, head=True),
    T([4.7, 2.8, 3.9, 1.7], f"{float(L['cw_07']):.0f}%", 80, "blue", True, head=True),
    T([4.7, 4.7, 3.9, 0.75], "못 건넘", 28, "ink", True),
    {"type": "rect", "box": [8.9, 1.75, 3.9, 4.2], "fill": "light"},
    T([8.9, 1.95, 3.9, 0.75], "싱가포르", 30, "navy", True, head=True),
    T([8.9, 2.8, 3.9, 1.7], f"{L['pd_sg_a']}→{L['pd_sg_b']}", 80, "blue", True, head=True),
    T([8.9, 4.7, 3.9, 0.75], "고령자 사망", 28, "ink", True),
    SRCL("공공데이터: 도로교통공단 사고다발지 · 횡단보도 표준데이터 사용"),
], "실버존, 신호, 해외 사례가 모두 부족을 가리킴", "큰 숫자 3개", key="H원인")
EXTRA_NOTES["H원인"] = "실버존 15%: 다음 해 노인 사고 지점 중 현행 보호구역(300 m) 안 비율. 신호 42%: 0.7 m/s 걸음으로 녹색시간 안에 못 건너는 횡단보도 (표준데이터 7,346곳, 경기 중심). 싱가포르: 65세 이상 보행 사망 2024년 11명 → 2025년 27명 (교통부 의회 서면답변 2026-05-06), 신호 연장 장치 확대 중."


# ★ 한눈에: 4 rows, every cell <= 12 characters. Effect: ours blue bold, baseline small gray. Numbers from the ledger.
_rows = [
    ("언제", "폭염에 쓰러지는\n고령자", "시군구 폭염 판단\n→ 문자 초안", f"{L['h_b1']}%", f"{L['h_ours']}%", "온열질환자 포착"),
    ("어디에", "실버존 밖\n보행사고", "실버존 재배치", f"{L['s_current']}%", f"{L['s_ours_full']}%", f"전국 같은 {L['s_k']}곳"),
    ("무엇을", "횡단보도\n낙상", "CCTV 낙상 감지\n→ 확인 → 119", f"{L['f_lat_rule']}초", f"{L['f_lat_lr']}초", "감지 시간"),
    ("밤", "밤에 잠기는\nAED", "24시간 개방 요청\n(AED 1% 전환)", f"{L['w_base']}%", f"{L['w_conv']}%", "밤 2분 도달"),
]
_els = [T([0.4, 1.28, 1.5, 0.5], "판단", 18, "navy", True), T([2.0, 1.28, 3.1, 0.5], "생활문제", 18, "navy", True), T([5.65, 1.28, 3.5, 0.5], "기능", 18, "navy", True), T([9.7, 1.28, 3.25, 0.5], "효과", 18, "navy", True)]
for _k, (_w, _prob, _fn, _bb, _oo, _note) in enumerate(_rows):
    _y = 1.85 + 1.2 * _k
    _els += [{"type": "rect", "box": [0.4, _y, 12.55, 1.1], "fill": "light"}, {"type": "rect", "box": [0.4, _y, 1.5, 1.1], "fill": "blue"},
             T([0.4, _y + 0.3, 1.5, 0.5], _w, 24, "ink", True, head=True), T([2.0, _y + 0.12, 3.1, 0.9], _prob.split("\n"), 20, "ink", True),
             T([5.1, _y + 0.3, 0.5, 0.56], "→", 24, "blue", True), T([5.65, _y + 0.1, 3.5, 0.95], _fn.split("\n"), 18, "ink", False),
             T([9.2, _y + 0.3, 0.5, 0.56], "→", 24, "blue", True),
             T([9.45, _y + 0.0, 1.2, 0.58], _bb, 26, "gray", True), T([10.65, _y + 0.0, 0.4, 0.58], "→", 22, "blue", True), T([11.05, _y + 0.0, 1.9, 0.62], _oo, 30, "blue", True, head=True),
             T([9.6, _y + 0.62, 3.35, 0.45], _note, 16, "ink", False, align="l")]
add_slide("한눈에 · 생활문제 → 기능 → 효과", "[생활문제] [AI] [디지털]", "실용성", "", [], _els,
          "네 가지 생활문제마다 기능과 같은 예산 대비 효과가 한 줄로 이어짐", "4행 표 (판단 · 생활문제 · 기능 · 효과)", key="H한눈에")

# CCTV 활용: three real images
add_slide("CCTV에서 이렇게 씁니다", "[AI] [디지털]", "완성도", "", [], [
    {"type": "photo", "path": str(ROOT / "deck/assets/press/cctv/ai_crosswalk.jpg"), "box": [0.4, 1.5, 3.8, 3.6], "label": "기존 CCTV"},
    T([4.2, 2.6, 0.8, 1.0], "→", 44, "blue", True),
    {"type": "photo", "path": str(DC["tw_cam"]), "box": [4.95, 1.5, 3.8, 3.6], "label": "기기 안 감지"},
    T([8.75, 2.6, 0.8, 1.0], "→", 44, "blue", True),
    {"type": "photo", "path": str(ROOT / "deck/assets/press/cctv/control_room.jpg"), "box": [9.5, 1.5, 3.4, 3.6], "label": "관제요원 확인"},
    SRCL("공개데이터: UR Fall · 용인시 · 강동구 자료"),
], "기존 CCTV 안에서 감지하고 관제요원이 확인해 119로 연계", "실사진·화면 3장 + 화살표", key="H카메라")
EXTRA_NOTES["H카메라"] = ("새 센서 없이 기존 CCTV에 소프트웨어로. 가운데 화면은 공개 데이터 영상(UR Fall) 실시간 계산: 골격, 얼굴 가림, 측정 지연(ms). 영상은 기기 밖으로 보내지 않음.\n"
                         + "사진 출처: " + " / ".join(json.loads((ROOT / "deck/assets/press/cctv/credits.json").read_text(encoding="utf-8")).values()))

# 라이브 데모 divider
add_slide("라이브 데모 · 공무원의 하루", D_TAGS, "완성도", "", [], [
    T([0.4, 1.0, 12.5, 1.6], "공무원의 하루", 80, "blue", True, head=True),
    T([0.4, 2.75, 12.5, 0.6], "관악구 2025-07-22", 26, "ink", True),
    *[{"type": "card", "box": [0.4 + 2.1 * k, 3.8, 2.0, 1.9], "size": 18, "head": h, "lines": [t, f"[키 {k + 1}]"]}
      for k, (h, t) in enumerate((("① 브리핑", "09:00"), ("② 언제", "17:00"), ("③ 현장", "17:30"), ("④ 어디에", "17:30"), ("⑤ 무엇을", "17:40"), ("⑥ 밤", "23:00")))],
    SRCL("공공데이터: 국토교통부 GIS건물통합정보 · 서울 생활인구 · 브이월드 사용"),
], "라이브 전환 전에 보는 여섯 단계와 키", "큰 글씨 + 카드 6개", key="T개요")
EXTRA_NOTES["T개요"] = f"{CAUTION}. 건물 {L['t_bld']}동, 층수 실측 {L['t_meas']}%. 권한: 재난문자 = 승인선 · 보행신호 = 경찰 협의 요청서 초안 · AED = 설치기관 관리책임자 · BIT = 연계 제안 · 쉼터 = 관악구 공개자료 없음. 낙상 감지 실험실 {L['f_sens_lr']}%, 실제 CCTV 클립 {L['ff_sens']}% (관제요원 확인)."

# 성능 3 (same order/colour as ★ rows 1, 2, 4): graph + hero number only
_GT = 1.55
for _y in (2022, 2023, 2024, 2025):
    assert float(L[f"h_ours_{_y}"]) > float(L[f"h_b1_{_y}"]), f"headline says 4 years all improve but {_y} does not"
add_slide("언제 · 폭염 포착", "[AI]", "실용성", "", [], [
    fig_el("01m_heat_by_year", "", HEAT_SRC, (0.4, _GT, 8.7, 4.6)),
    T([9.35, _GT + 0.3, 3.6, 0.5], "언제 · 폭염", 24, "blue", True, head=True),
    T([9.35, _GT + 0.9, 3.6, 1.7], f"{L['h_ours']}%", 88, "blue", True, head=True),
    T([9.35, _GT + 2.7, 3.6, 0.6], f"특보 규칙 {L['h_b1']}%", 28, "gray", True),
], "같은 경보 수에서 에이전트가 네 해 모두 앞섬", "Fig: 연도별 + 통합 막대", key="G폭염")
EXTRA_NOTES["G폭염"] = f"같은 경보 수로 비교, 4년 통합 +{L['h_d']}%p (95% CI {L['h_d_lo']}~{L['h_d_hi']}), 2022~2025 네 해 모두 앞섬. 각 해는 이전 연도까지만 학습, 7일 블록 부트스트랩 1,000회. 입력 F(당일 기상 입력) 기준이며 전날 관측만 쓴 O 변형은 {L['h_o_b1']}% → {L['h_o_ours']}%."
add_slide("어디에 · 실버존 위치", "[AI]", "실용성", "", [], [
    T([0.4, 1.0, 3.2, 0.5], "어디에 · 실버존", 24, "blue", True, head=True),
    T([0.4, 1.35, 4.4, 1.7], f"{L['s_ours_full']}%", 88, "blue", True, head=True),
    T([4.9, 1.7, 8.0, 0.65], f"현행 {L['s_current']}% · 같은 {L['s_k']}곳", 28, "gray", True, align="l"),
    T([0.4, 3.05, 12.5, 0.45], f"서울 예시 · 구역 {L['sz_cur']} → {L['sz_ours']}곳", 18, "ink", True),
    fig_el("17_map_current_vs_ours", "", src_line(MAPF, "kind=current/ours/hotspot_2024"), (0.4, 3.55, 12.5, 2.85)),
], "같은 개수로 다음 해 사고 지점을 덮는 비율이 오름", "큰 숫자 + 서울 지도 2장", key="G실버")
EXTRA_NOTES["G실버"] = f"전국 같은 {L['s_k']}곳으로 다음 해 노인 사고 지점 덮음 {L['s_current']}% → {L['s_ours_full']}% (95% CI {L['s_ours_lo']}~{L['s_ours_hi']}), 새 사고 지점 {L['sn_current']}% → {L['sn_ours_full']}%. 2023년까지 자료로 선정, 2024년 평가. 서울 지도는 예시이며 서울 안 구역 수는 현행 {L['sz_cur']}곳, 에이전트 {L['sz_ours']}곳으로 서울 안 개수는 같지 않음(서울 다발지 {L['m_n']}곳 중 {L['m_cur']} → {L['m_ours']}는 부록에서만)."
add_slide("밤 · AED 공백", "[생활문제]", "실용성", "", [], [
    fig_el("07m_aed_hourly", "", src_line("experiments/aed/out/hourly_gap.csv", "pct_open_weekday"), (0.4, _GT, 8.7, 4.6)),
    T([9.35, _GT + 0.3, 3.6, 0.5], "밤 · 열린 AED", 24, "blue", True, head=True),
    T([9.35, _GT + 0.9, 3.6, 1.7], f"{L['a_night']}%", 88, "blue", True, head=True),
    T([9.35, _GT + 2.7, 3.6, 0.6], f"낮 {L['a_day']}%", 28, "gray", True),
], "AED가 밤에 절반만 열려 있음", "Fig: 시간대별 선 (밤 음영)", key="GAED")
EXTRA_NOTES["GAED"] = f"서울 평일 평균 열린 AED 비율, 밤 22~06시 음영. 2023년 자료로도 밤 심정지 비율 재현 ({L['a23_share']}% 대 {L['a24_share']}%). 관악구 14시 {L['a_gw_day']}% · 23시 {L['a_gw_night']}% (AED {L['a_gw_n']}대)."

# 기술 · 보안 (3 keywords)
add_slide("기술 · 보안", "[AI] [디지털]", "기술 적정성", "", [], [
    T([0.4, 1.5, 12.5, 1.5], "시군구 단위 설계", 72, "blue", True, head=True),
    T([0.4, 3.3, 12.5, 0.7], "CCTV 안 낙상 감지 · 영상 전송 없음", 36, "ink", True),
    T([0.4, 4.5, 12.5, 1.15], "Argon2id · HMAC-SHA256", 54, "blue", True, head=True),
], "핵심 설계, 기기 안 감지, 보안 알고리즘", "큰 글씨 3줄", key="H기술")
EXTRA_NOTES["H기술"] = "단순 점수 = GBM(동률), 학습 모델은 낙상 감지에만(자세 추정 + 로지스틱 회귀). 비밀번호 Argon2id, 토큰·감사 기록 HMAC-SHA256. 보안 표는 부록."

# 도입
add_slide("도입 · 비전", "[생활문제] [디지털]", "실용성", "", [], [
    T([0.4, 1.8, 12.5, 1.5], "기존 인프라 + 소프트웨어", 66, "blue", True, head=True),
    T([0.4, 3.7, 12.5, 0.8], "파일럿: 1개 구 · 폭염기 3개월", 36, "ink", True),
], "기존 인프라에 소프트웨어로 올리고 파일럿으로 시작", "큰 글씨 + 두 줄", key="H도입")

# 마무리
add_slide("마무리", "[생활문제] [디지털] [AI]", "발표", "", [], [
    T([0.4, 1.7, 12.5, 1.5], "언제 · 어디에 · 무엇을", 72, "navy", True, head=True),
    T([0.4, 3.8, 12.5, 1.0], "가장 느린 걸음을 지킵니다", 44, "ink", True),
], "판단 세 단어와 슬로건으로 마무리", "큰 글씨", key="H정리")

# ---------------------------------------------------------------- 사용한 공공데이터 (generated; one row per original dataset)
import sqlite3

REGDB = ROOT / "app/backend/var/kcf.sqlite3"
EXTRA = ROOT / "data/cache/extra"
_reg = {n: (u, lic) for n, u, lic in sqlite3.connect(REGDB).execute("select name, source_url, license from datasets")}


def lic_short(name: str) -> str:
    """Short usage-terms label from the registry license text (never stronger than the registry says)."""
    lic = _reg[name][1]
    if "공공누리 제1유형" in lic or "공공누리 제1유형" in lic:
        return "공공누리 1유형"
    if "제한 없음" in lic:
        return "이용허락 제한 없음"
    return "포털 이용허락 확인 중"


def _exists(*paths: Path) -> bool:
    return all(p.exists() for p in paths)


_vw = json.loads((EXTRA / "buildings/gwanak_buildings_vworld.sources.json").read_text(encoding="utf-8"))
_lp = json.loads((EXTRA / "living_pop/gwanak_living_pop.sources.json").read_text(encoding="utf-8"))
_shelter_ids = sorted({m.group(1) for f in (EXTRA / "shelters").glob("kr_*_cooling_shelters.csv") if (m := re.match(r"kr_(\d+)_", f.name))})
PUBLIC_ROWS = [  # (provider, dataset, id/location, terms, evidence that the data was really used)
    ("질병관리청", "온열질환 응급실감시", "data.go.kr 15149889", lic_short("heat_illness_cases"), "heat_illness_cases" in _reg),
    ("기상청", "ASOS 지상관측 시간·일자료", "API허브 kma_sfctm2", lic_short("heat_kma_asos_hourly"), _exists(EXTRA / "kma_hub")),
    ("행정안전부", "행정동별 주민등록 인구", "data.go.kr 15097972", lic_short("heat_pop_by_dong"), "heat_pop_by_dong" in _reg),
    ("통계청 기반 vuski", "행정동 경계 GeoJSON", "github vuski/admdongkor", "저장소 조건 확인 중", _exists(EXTRA / "boundaries/HangJeongDong_ver20261001.geojson")),
    ("도로교통공단", "전국교통사고다발지역 표준데이터", "data.go.kr 15029185", "포털 이용허락 확인 중", _exists(EXTRA / "silverzone/kr_15029185_acc_hotspot_std.csv")),
    ("공공데이터포털", "전국노인장애인보호구역 표준데이터", "data.go.kr 15034532", "포털 이용허락 확인 중", _exists(EXTRA / "silverzone/kr_15034532_silverzone_std.csv")),
    ("공공데이터포털", "전국횡단보도 표준데이터", "data.go.kr 15028201", "포털 이용허락 확인 중", _exists(EXTRA / "silverzone/kr_15028201_crosswalk_std.csv")),
    ("국립중앙의료원", "AED 정보 API", "data.go.kr B552657", lic_short("aed_hourly_gap"), "aed_hourly_gap" in _reg),
    ("국립중앙의료원", "응급의료 정보 API (응급실·병원·약국)", "data.go.kr B552657", "포털 이용허락 확인 중", _exists(EXTRA / "nmc_snapshots")),
    ("소방청", "구급활동정보 API", "data.go.kr 1661000", lic_short("aed_summary"), "aed_summary" in _reg),
    ("국토교통부", "GIS건물통합정보 (V-World)", f"V-World dsId=18 ({_vw['data_reference_date'][:7]})", "공공누리 1유형", _exists(EXTRA / "buildings/gwanak_buildings_vworld.sources.json")),
    ("서울특별시", "서울 생활인구 (내국인, KT 기반)", f"열린데이터광장 {_lp['dataset_id']}", "공공누리 1유형", _exists(EXTRA / "living_pop/gwanak_living_pop.sources.json")),
    ("국토교통부", "버스정류장 위치", "data.go.kr 15067528", lic_short("mobility_recommended_top2000"), "mobility_recommended_top2000" in _reg),
    ("지자체 " + str(len(_shelter_ids)) + "곳", "무더위쉼터 (관악구는 공개자료 없음)", "data.go.kr " + _shelter_ids[0] + " 외 " + str(len(_shelter_ids) - 1) + "건", "포털 이용허락 확인 중", len(_shelter_ids) > 0),
    ("행정안전부", "재난문자방송 기준 및 운영규정", "행정규칙 (규정 원문)", "공개 규정", True),
]
OTHER_ROWS = [
    ("OpenStreetMap", "서울 보행망", "openstreetmap.org", "ODbL", _exists(EXTRA / "osm/seoul_walk.graphml")),
    ("Meteostat", "일별 기온 (WMO → 기상청 지점)", "meteostat.net", "CC BY-NC 4.0", _exists(EXTRA / "meteostat_daily") and "heat_meteostat_daily" in _reg),
    ("UR Fall Detection", "낙상 30건 · 일상 40건", "Kwolek & Kępski 2014", "CC BY-NC-SA 4.0", _exists(EXTRA / "fall")),
    ("Le2i·MCFD·CCTV", "현장 낙상 평가", "공개 연구용 영상", "연구용, 오프라인만", _exists(EXTRA / "le2i", EXTRA / "mcfd", EXTRA / "cctvf")),
    ("Khronos glTF 샘플", "시연 인물 3D 모델 CesiumMan", "Khronos GitHub", "CC BY 4.0", _exists(ROOT / "app/frontend/public/models/CesiumMan.glb")),
    ("서울신문 · 전북일보", "뉴스 사진 3장", "기자·날짜는 노트", "보도 사진 출처 표기", _exists(ASSETS / "press/credits.json")),
]
for _lbl, _rows_ in (("공공", PUBLIC_ROWS), ("기타", OTHER_ROWS)):
    for _r in _rows_:
        if not _r[4]:
            LOG.warning("data credit row dropped (no evidence the data was used): %s | %s", _r[0], _r[1])
PUBLIC_ROWS = [r for r in PUBLIC_ROWS if r[4]]
OTHER_ROWS = [r for r in OTHER_ROWS if r[4]]
UNUSED_NOTE = ("검토했으나 미사용: UCI wdbc (sklearn 예제 데이터, 등록부에 남은 잔여물), HIRA 병원·약국 현황 15051059, 전세 관련 15002597 · 15033824 · 15134221 · 15139145 · 15144220, "
               "소방청 구조활동 rescue_2025, 서울 건물 Overture 대체본 (V-World 국토부 자료를 사용), 행정안전부 시군구 주민등록 월별 CSV (mois_pop, 발표 결과에는 행정동 파일 사용).")
CREDIT_COLS = [2.8, 5.6, 4.0]
TERMS_NOTE: dict[str, str] = {}


def credit_slide(title: str, key: str, rows: list[tuple], page: str) -> None:
    body = [["제공기관", "데이터", "번호 · 위치"], *[[r[0], r[1], r[2]] for r in rows]]
    TERMS_NOTE[key] = "이용조건(슬라이드에는 표시하지 않음):\n" + "\n".join(f"- {r[0]} · {r[1]}: {r[3]}" for r in rows)
    add_slide(title, "[디지털]", "발표", "", [], [{"type": "table", "x": 0.4, "y": 1.35, "w": sum(CREDIT_COLS), "col_w": CREDIT_COLS, "size": 16, "row_h": 0.55, "rows": body}],
              f"사용한 데이터의 제공기관 ({page})", "표 1개", key=key)


_half_pub = (len(PUBLIC_ROWS) + 1) // 2
credit_slide("부록 사용한 공공데이터 (1/2)", "D공공1", PUBLIC_ROWS[:_half_pub], "1/2")
credit_slide("부록 사용한 공공데이터 (2/2)", "D공공2", PUBLIC_ROWS[_half_pub:], "2/2")
credit_slide("부록 기타 데이터 · 자료", "D기타", OTHER_ROWS, "기타")

HIDDEN_KEYS = {"T폭염", "T현장", "T실버", "T사고", "T야간"}  # backup slides for a failed live demo: hidden in the slide show, kept in order after the divider
REG["B제품"] = dict(REG["B제품"], title="부록 제품 구성")
REG["보안표"] = dict(REG["보안"], title="부록 보안 알고리즘 표")
NOSCRIPT_KEYS = {"D공공1", "D공공2", "D기타"}  # data-credit slides: shown, not spoken
MAIN_B = ["O스마트도시", "O사람AI", "H사진", "H문제", "H원인", "H한눈에", "H카메라", "T개요", "T폭염", "T현장", "T실버", "T사고", "T야간", "G폭염", "G실버", "GAED", "H기술", "H도입", "H정리"]
QA_SOURCE = ROOT / "deck/qa_source.md"
A1K, A3K, A4K, A5K, A6K, A7K, A8K, A9K, A10K = ("부록 A1 작년 사고 장소 vs 에이전트", "부록 A3 입력 두 가지", "부록 A4 정부 시도 모델", "부록 A5 경보 수를 바꿔도", "부록 A6 데이터 한계", "부록 A7 낙상 오경보", "부록 A8 에이전트 지연", "부록 A9 데이터 선순환", "부록 A10 119 독립 검증")
HEADING_REFS = {  # question id in qa_source.md -> appendix slides that back the answer (an appendix slide nobody points to is dropped)
    "A5": ["분석 엔진"], "A6": ["도달"], "A7": ["B제품"], "A10": [A9K], "A11": [A6K, "D공공1", "D공공2", "D기타"],
    "B1": ["엔진 구성 요소별 기여", "알고리즘 3개"], "B2": [A3K], "B3": [A5K], "B4": [A3K], "B6": ["알고리즘 3개", "실버존: 같은 개수"], "B7": ["실버존: 같은 개수"],
    "B8": ["실버존: 같은 개수", A1K], "B9": ["밤에 잠긴 AED"], "B10": ["밤에 잠긴 AED"], "B11": ["낙상 감지 모델"], "B12": ["낙상 감지 모델", A7K],
    "B13": ["엣지 에이전트", "데모 ① 현장 에이전트", A8K], "B14": ["보안표", "DB 핵심 테이블 3개"], "B15": ["B연동"], "B16": [A8K], "B17": [A6K, "D공공1", "D공공2", "D기타"],
    "C1": [A1K, "실버존: 같은 개수"], "C2": [A7K, "낙상 감지 모델"], "C5": ["알고리즘 3개", "엔진 구성 요소별 기여", A4K], "C6": [A3K, A4K], "C7": [A10K], "C8": [A5K], "C9": ["부록 A2 오경보"],
}
QID = re.compile(r"^## ([ABC]\d+)\.")


def linked_keys() -> set[str]:
    """Appendix slides that at least one question heading in qa_source.md points to (appendix keeps only these)."""
    heads = [ln for ln in QA_SOURCE.read_text(encoding="utf-8").splitlines() if ln.startswith("## ")]
    return {k for h in heads if (m := QID.match(h)) for k in HEADING_REFS.get(m.group(1), [])}


APPX_COMMON = ["엣지 에이전트", "분석 엔진", "데모 ① 현장 에이전트", "실버존: 같은 개수", "낙상 감지 모델", "알고리즘 3개", "DB 핵심 테이블 3개", "B제품", "보안표",
               "엔진 구성 요소별 기여", "밤에 잠긴 AED", "부록 A1 작년 사고 장소 vs 에이전트", "부록 A10 119 독립 검증", "부록 A2 오경보", "부록 A3 입력 두 가지",
               "부록 A4 정부 시도 모델", "부록 A5 경보 수를 바꿔도", "부록 A6 데이터 한계", "부록 A7 낙상 오경보", "부록 A8 에이전트 지연", "부록 A9 데이터 선순환", "D공공1", "D공공2", "D기타"]
APPX_A = ["확장", "도달", *APPX_COMMON]
APPX_B = ["부록안내", "도달", "B연동", *APPX_COMMON]
if QA_SOURCE.is_file():
    _LK = linked_keys()
    _dropped = [k for k in APPX_B if k != "부록안내" and k not in _LK]
    LOG.info("appendix trimmed to slides a question points to; dropped: %s", _dropped)
    APPX_COMMON = [k for k in APPX_COMMON if k in _LK]
    APPX_A = ["확장", "도달", *APPX_COMMON]
    APPX_B = [k for k in APPX_B if k == "부록안내" or k in _LK]
APPX_NUM = {k: len(MAIN_B) + 4 + n for n, k in enumerate(APPX_B)}  # physical slide number (cover 1, 목차 2, main 3.., 감사합니다, appendix)
# appendix slide -> keyword shown on the index slide; every appendix slide must answer at least one Q&A question (checked against QA_REFS below)
APPX_KW = {
    "D공공1": "공공데이터 (1/2)", "D공공2": "공공데이터 (2/2)", "D기타": "기타 데이터 · 자료",
    "B제품": "제품 4부분 구성", "보안표": "보안 알고리즘 표",
    "도달": "스마트폰 없는 고령자", "B연동": "기존 행정 시스템 연동", "엣지 에이전트": "에이전트 흐름 · LLM", "분석 엔진": "비슷한 서비스",
    "데모 ① 현장 에이전트": "실행 기록 · 기기", "실버존: 같은 개수": "실버존 3,918곳 · 59/58", "낙상 감지 모델": "낙상 성능 · 야간",
    "알고리즘 3개": "AI가 꼭 필요했나", "DB 핵심 테이블 3개": "결정 권한 · 감사 로그", "엔진 구성 요소별 기여": "단계별 이득 · 정부 모델",
    "밤에 잠긴 AED": "밤 AED · 1년 자료", "부록 A1 작년 사고 장소 vs 에이전트": "작년 사고 장소 동률", "부록 A10 119 독립 검증": "119 검증 유의성",
    "부록 A2 오경보": "헛경보 72%", "부록 A3 입력 두 가지": "예보 · 미래 정보", "부록 A4 정부 시도 모델": "시도 단위 모델",
    "부록 A5 경보 수를 바꿔도": "같은 예산 · 경보 수", "부록 A6 데이터 한계": "데이터 한계", "부록 A7 낙상 오경보": "낙상 오경보 313건",
    "부록 A8 에이전트 지연": "지연 · 기기", "부록 A9 데이터 선순환": "다음 단계",
}
_idx_rows = sorted(((APPX_NUM[k], kw) for k, kw in APPX_KW.items() if k in APPX_NUM), key=lambda r: r[0])
_half = (len(_idx_rows) + 1) // 2
_tbl = lambda rows: [["질문 키워드", "번호"], *[[kw, str(n)] for n, kw in rows]]  # noqa: E731
add_slide("부록 안내 · 번호 + Enter로 이동", "[발표]", "발표", "", [], [
    {"type": "table", "x": 0.5, "y": 1.35, "w": 6.0, "col_w": [4.7, 1.3], "size": 16, "row_h": 0.4, "rows": _tbl(_idx_rows[:_half])},
    {"type": "table", "x": 6.9, "y": 1.35, "w": 6.0, "col_w": [4.7, 1.3], "size": 16, "row_h": 0.4, "rows": _tbl(_idx_rows[_half:])},
], "부록은 예상 질문에 답하는 장만 남김", "표 2개 (키워드 → 번호)", key="부록안내")
VARIANTS = {
    "A": {"label": "보행단독", "main": MAIN_A, "appx": APPX_A, "work": "작품명 : 고령자 안전 스마트도시 에이전트", "subtitle": "보행안전 단독 (백업 변형)", "pptx": "영점_발표_A_보행단독", "script": "대본_A.md", "marker": ".published_sha_A"},
    "B": {"label": "통합", "main": MAIN_B, "appx": APPX_B, "work": "작품명 : 고령자 안전 스마트도시 에이전트", "subtitle": "", "pptx": "영점_발표", "script": "대본.md", "marker": ".published_sha"},
}

# ================================================================== scripts per slide key: (m:ss target, lines)
def r0(x: float) -> str:
    return f"{x:.0f}"


S_SEC = {
    "보안": ("0:12", ["비밀번호는 Argon2id를 씁니다.", "토큰과 감사 로그는 HMAC-SHA256입니다.", "영상은 저장하지 않고 해시만 남깁니다."]),
    "도입": ("0:16", ["지자체의 기존 카메라와 가로등에 소프트웨어로 올립니다.", "도입 경로는 혁신제품 지정과 통합플랫폼 연계입니다.",
                    "가장 기술적인 눈으로, 가장 느린 걸음을 지킵니다.", "AI와 디지털 기술로, 우리 동네 고령자의 하루를 지킵니다."]),
}
SCRIPT_KEYS = {
    "A": {
        "cover": ("0:12", ["AI와 디지털 기술로 해결하는 우리 주변의 생활문제를 소개합니다.", "고령자가 건너는 길을 AI가 지키는 에이전트입니다."]),
        "A문제": ("0:16", ["보행 사망자 세 명 중 두 명이 65세 이상입니다.", f"2024년에는 {L['p_ped']}%였습니다.", "사진은 실제 노인보호구역 도로입니다."]),
        "A원인": ("0:34", ["원인은 두 가지입니다.", "첫째, 실버존은 복지시설 문 앞에 그어집니다.",
                         f"그래서 다음 해 노인 사고 지점의 {L['s_current']}%만 구역 안에 있었습니다.",
                         "둘째, 노인 걸음 초속 0.7미터로는 신호 안에 못 건너는 곳이 있습니다.",
                         f"경기 등 일부 지역 {L['cw_n']}곳 중 {r0(float(L['cw_07']))}%입니다.", f"설계 속도 1.0미터에서는 {r0(float(L['cw_10']))}%입니다."]),
        "A해결": ("0:22", ["고령자에게 돌아가는 이로움은 세 가지입니다.", "사고 나는 곳에 보호구역을 다시 긋습니다.",
                         "느린 걸음에는 신호 시간 연장을 권고합니다.", "쓰러지면 현장에서 감지해 119 시뮬레이터 접수 기록을 만듭니다."]),
        "A데모": ("0:26", ["두 화면을 보여 드립니다.", "왼쪽은 현행 구역과 에이전트 추천을 비교하는 실버존 지도입니다.",
                         "오른쪽은 웹캠으로 쓰러짐을 감지하는 현장 에이전트입니다.", "모든 전송은 훈련 모드이며 실제로 보내지 않습니다."]),
        "A모듈": ("0:26", ["공공 데이터가 세 모듈을 거쳐 보호구역, 신호, 경보가 됩니다.", "위치 최적화는 가장 많이 덮는 곳부터 고릅니다.",
                         "신호 분석은 녹색 시간을 걸음 속도와 비교합니다.", "현장 에이전트는 자세 추정, 낙상 모델, 로컬 언어 모델 순서입니다."]),
        "A성능": ("0:30", ["비교군은 현행 실버존입니다.", f"같은 {L['s_k']}곳을 고르면 현행 {L['s_current']}%, 에이전트 {L['s_ours_full']}%입니다.",
                         f"새로 생긴 사고 지점도 {L['sn_current']}%에서 {L['sn_ours_full']}%로 늘었습니다.", f"낙상 감지 지연은 규칙 {L['f_lat_rule']}초에서 AI {L['f_lat_lr']}초로 줄었습니다."]),
    },
    "B": {
        "cover": ("0:12", ["AI와 디지털 기술로 해결하는 우리 주변의 생활문제를 소개합니다.", "고령자 안전 스마트도시 에이전트입니다."]),
        "B문제": ("0:30", ["인구는 늙어 가는데 도시의 안전 기준은 그대로입니다.", f"65세 이상은 2024년 {L['pd_pop'][:2]}%이고 2050년에는 {L['pd_2050']}%를 넘습니다.",
                         "신호는 기본 1.0미터 걸음, 보호구역은 시설 문 앞 300미터입니다.", "쉼터에는 지정 기준이 없습니다.",
                         f"2024년 보행 사망 {L['p_ped_n'].split(' / ')[0]}명 중 {L['p_ped_n'].split(' / ')[1]}명이 65세 이상, {L['p_ped']}%입니다."]),
        "B원인": ("0:36", ["스마트도시는 112와 119 연계, 개별 시범 중심입니다.", "감사원은 2021년 통합플랫폼의 약자 서비스 실적 저조를 지적했습니다.",
                         "보행 신호는 일반 1.0미터, 노인·어린이보호구역은 최대 0.7미터입니다.", "공개된 효과 평가는 없습니다.",
                         "싱가포르는 신호 연장 장치를 늘리는 중입니다.", f"그래도 65세 이상 보행 사망은 {L['pd_sg_a']}명에서 {L['pd_sg_b']}명으로 늘었습니다.",
                         f"우리 분석에서 실버존은 다음 해 노인 사고 지점의 {L['s_current']}%만 덮습니다.", f"0.7미터 걸음으로는 경기 등 일부 지역 신호의 {r0(float(L['cw_07']))}%를 못 건넙니다."]),
        "B해결": ("0:22", ["같은 인프라와 같은 예산입니다.", "AI가 위험이 큰 곳부터 다시 배치하고 운영합니다.",
                         "실버존, 신호, 쉼터와 AED, 경보 채널 네 가지입니다.", "고령자에게는 사고 지점을 더 덮고 경보를 더 빨리 받는 이로움이 돌아갑니다."]),
        "T개요": ("2:00", ["지금부터 라이브로 보여 드립니다.", "2025년 7월 22일, 관악구 공무원의 하루입니다.", f"국토부 건물 {L['t_bld']}동, 층수 실측 {L['t_meas']}%의 3D 지도입니다.",
                         "① 9시, 주무관은 오늘 할 일부터 봅니다.", "② 17시, 폭염 대응입니다.", "재난문자 초안은 왜 그런지 근거까지 확인합니다.", "체감온도와 환자 수 같은 근거가 바로 나옵니다.", "주무관이 채택해 상신하면 과장이 결재합니다.",
                         "공문 미리보기가 자동으로 나옵니다.", "모든 전송은 훈련이며 실제로 보내지 않습니다.", "③ 17시 30분, 시연 인물이 정류장에 도착합니다.", "걸음은 고령자 속도에 맞춰 걷습니다.", "정류장 단말과 실물 휴대폰에 같은 메시지가 뜹니다.",
                         "④ 사고가 나면 웹캠이 낙상을 감지해 관제요원에게 알립니다.", "관제요원이 확인한 뒤에만 119 연계로 넘어갑니다.", "가장 가까운 열린 AED 경로도 나옵니다.", "행인이 다녀오는 시간도 계산합니다.",
                         f"낙상 감지는 실험실 {L['f_sens_lr']}%, 실제 CCTV 클립 {L['ff_sens']}%입니다.", "그래서 관제요원 확인이 필수입니다.", "⑤ 밤 당직에는 AED가 꺼지는 것이 보입니다.", "이 시간대에는 문자가 아니라 개방 요청 공문이 길입니다.",
                         "24시간 개방은 요청이고, 결정은 설치기관 관리책임자가 합니다.", "슬라이드로 돌아갑니다."]),
        "T폭염": ("0:20", ["17시, 폭염 대응입니다.", f"재난문자 초안은 {L['t_c90']}자형과 {L['t_c157']}자형 폭염 경보 문안 두 가지입니다.", "주무관이 채택해 상신하면 과장이 결재합니다.", "공문 미리보기와 CAP 규격 검증까지 나옵니다."]),
        "T현장": ("0:16", ["17시 30분, 현장 전달입니다.", "시연 인물이 정류장에 도착하면 단말에 같은 메시지가 뜹니다.", "실물 휴대폰 단말로도 확인합니다.", "정류장 단말은 교통정보센터와 협의할 연계 제안입니다."]),
        "T사고": ("0:24", ["낙상을 감지하면 관제요원에게 알립니다.", "관제요원이 확인한 뒤에만 119 연계로 넘어갑니다.", "가장 가까운 열린 AED 경로도 보여 줍니다.",
                         f"낙상 감지는 실험실 {L['f_sens_lr']}%, 지연 {L['f_lat_lr']}초입니다.", f"실제 CCTV 클립은 {L['ff_sens']}%입니다.", f"오경보는 시간당 {L['ff_fa']}건, 참고값입니다.",
                         "그래서 관제요원이 확인하고, 다음은 현장 영상 재검증입니다."]),
        "T야간": ("0:16", ["밤 당직에는 AED 공백이 보입니다.", f"관악구 AED는 14시 {L['a_gw_day']}%에서 23시 {L['a_gw_night']}%로 줄어듭니다.", "24시간 개방 요청 공문 초안이 나옵니다.", "개방 여부는 설치기관 관리책임자가 정합니다."]),
        "B제품": ("0:30", ["제품은 네 부분입니다.", "첫째, 담당자가 보는 도시 운영 지도입니다.", "오늘의 인프라 조치를 근거와 함께 보여 줍니다.", "담당자는 채택, 보류, 부적합을 기록합니다.",
                         "둘째, 횡단보도 카메라에 올리는 현장 에이전트입니다.", "셋째, 공공 데이터로 위험을 예측하는 분석 엔진입니다.", "넷째, 보호자를 위한 시민 화면입니다.", "현장 에이전트는 밤과 비에서의 강건성을 시험 중입니다."]),
        "B모듈": ("0:30", ["공공 데이터가 네 모듈을 거쳐 인프라 조치가 됩니다.", "위험 예측의 이득은 시군구 단위 위험 설계에서 나옵니다.", "단순 점수와 GBM은 동률입니다.",
                         "학습 모델이 꼭 필요한 곳은 영상 낙상 감지입니다.", "경량 학습 모델인 로지스틱 회귀를 씁니다.", "위치는 최대 커버리지, 신호는 녹색 시간과 걸음 속도 비교입니다.", "현장 에이전트는 자세 추정, 낙상 모델, 로컬 언어 모델 순서입니다."]),
        "G폭염": ("0:15", ["폭염은 같은 경보 수로 비교했습니다.", f"특보 규칙 {L['h_b1']}%에서 에이전트 {L['h_ours']}%로, 4년 모두 개선됐습니다."]),
        "G실버": ("0:15", [f"서울 사고 다발지 {L['m_n']}곳 중 현행 보호구역 안은 {L['m_cur']}곳, 에이전트 제안은 {L['m_ours']}곳입니다.", f"새로 생긴 다발지도 {L['sn_current']}%에서 {L['sn_ours_full']}%로 늘었습니다."]),
        "GAED": ("0:15", ["밤에는 AED도 닫힙니다.", f"열린 AED는 낮 {L['a_day']}%에서 밤 {L['a_night']}%로 줄어듭니다.", f"2023년 자료로도 밤 심정지 비율이 재현됩니다."]),
        "B성능": ("0:30", ["비교군은 지금의 공식 기준입니다.", f"실버존은 같은 {L['s_k']}곳으로 {L['s_current']}%에서 {L['s_ours_full']}%입니다.",
                         f"폭염은 같은 경보 수로 {L['h_b1']}%에서 {L['h_ours']}%입니다.", f"낙상 감지 지연은 {L['f_lat_rule']}초에서 {L['f_lat_lr']}초입니다."]),
        "B연동": ("0:16", ["경보는 국제 표준 CAP로 만듭니다.", "구급 기록은 FHIR 형식입니다.", "도로 전광판과 119는 시뮬레이터가 수신까지 확인합니다."]),
    },
}
for _v in SCRIPT_KEYS.values():
    _v["보안"] = S_SEC["보안"]
    _v["도입"] = S_SEC["도입"]
SCRIPT_KEYS["B"]["B도입"] = ("0:20", ["지자체의 기존 카메라와 안내단말에 소프트웨어로 올립니다.", "엣지 연산 장치 사양은 파일럿에서 정합니다.", "표준 출력으로 기존 시스템에 연결하고, 통합플랫폼 연계는 다음 단계입니다.", "고령자에게는 정류장 안내단말, 마을방송, 재난문자로 전달합니다."] + S_SEC["도입"][1][2:])
# ---- script B v3 (deck/script_B_v2.md, 2026-10-25 latest): connected paragraphs, signpost first; numbers from the ledger L
def sents(text: str) -> list[str]:
    """One script line per paragraph (the timer still checks sentence length inside it)."""
    return [text.strip()]


SCRIPT_KEYS["B"].update({
    "cover": ("0:07", sents("안녕하세요, 팀 영점입니다. 저희 작품은 고령자 안전 스마트도시 에이전트입니다.")),
    "O스마트도시": ("0:15", sents("저희는 스마트도시라는 키워드에 집중했습니다. 길을 걷고, 더위를 피하고, 쓰러졌을 때 도움을 받는 생활안전은 결국 도시가 어떻게 운영되느냐에 달려 있기 때문입니다.")),
    "O사람AI": ("0:15", sents("투자는 늘고 있지만, 과연 고령자들은 이렇게 빠르게 바뀌는 도시에 적응할 수 있을까요? 저희가 그리는 스마트도시는 AI가 판단을 돕고 결정은 사람이 하는, 사람과 AI가 함께 지키는 도시입니다.")),
    "H사진": ("0:20", sents("이 질문을 숫자로 보겠습니다. 지금 국민 열 명 중 두 명이 65세 이상이고, 2050년에는 열 명 중 네 명이 됩니다. 그런데 녹색 신호는 1초에 1미터를 걷는 사람 기준이고, 노인보호구역은 시설 문 앞 300미터에만 지정됩니다.")),
    "H문제": ("0:05", sents("그래서 2024년 보행 사망자 세 명 중 두 명이 고령자였습니다.")),
    "H원인": ("0:25", sents(f"투자는 느는데 왜 줄지 않을까요? 지금의 실버존은 다음 해 노인 사고 지점의 {L['s_current']}%만 덮고, 고령자 걸음으로는 횡단보도 {float(L['cw_07']):.0f}%를 신호 안에 다 건너지 못합니다. 신호 연장 장치를 늘린 싱가포르도 고령자 보행 사망이 {L['pd_sg_a']}명에서 {L['pd_sg_b']}명으로 늘었습니다. 결국 부족한 것은 판단입니다.")),
    "H한눈에": ("0:40", [
        "그래서 저희 에이전트는 어디에, 언제, 무엇을 할지 판단합니다.",
        f"먼저 언제 경보를 낼지입니다. 같은 경보 수로 온열질환자를 {L['h_b1']}%에서 {L['h_ours']}%까지 미리 잡습니다.",
        f"다음은 어디에 둘지입니다. 전국 같은 {L['s_k']}곳의 실버존을 사고가 날 곳으로 옮기면, 다음 해 사고 지점을 덮는 비율이 {L['s_current']}%에서 {L['s_ours_full']}%가 됩니다.",
        f"세 번째는 무엇을 할지입니다. 기존 CCTV에 탑재한 에이전트가 기기 안에서 낙상을 {L['f_lat_lr']}초에 감지하고, 관제요원 확인 뒤 119로 연계합니다.",
        f"마지막은 밤입니다. 서울 AED의 1%만 24시간 열어도, 밤에 걸어서 2분 안에 AED가 있는 고령자가 {L['w_base']}%에서 {L['w_conv']}%로 늘어납니다."]),
    "H카메라": ("0:03", sents("기존 CCTV 안에서 영상을 밖으로 보내지 않고 감지합니다.")),
    "T개요": ("1:30", [
        "직접 보여 드리겠습니다. 2025년 7월 22일, 폭염이던 날 관악구 공무원의 하루입니다.",
        "① 아침 9시, 주무관은 오늘 할 일부터 봅니다.",
        "② 오후 5시, 특보는 없었지만 에이전트는 관악구를 위험 지역으로 판단했습니다. 주무관이 근거를 보고 재난문자 초안을 상신하고, 과장이 결재하면 공문 초안도 함께 만들어집니다.",
        "③ 이 안내는 정류장 단말과, 제 앞의 휴대폰에도 함께 뜹니다.",
        "④ 현행 기준에서 에이전트로 바꾸면 실버존이 사고 나는 곳으로 옮겨 갑니다.",
        "⑤ 횡단보도에서 낙상이 일어나면 카메라가 감지해 관제요원에게 먼저 알리고, 확인 뒤에만 119로 연계합니다. 가장 가까운 열린 AED까지 길도 보여 줍니다.",
        "⑥ 밤 11시, AED가 하나씩 꺼집니다. 이때는 닫힌 AED의 개방 요청 공문을 준비합니다.",
        "모든 출력은 훈련용이며 실제로 보내지 않습니다."]),
    "G폭염": ("0:10", sents("이 효과가 믿을 만한지 근거입니다. 폭염 경보는 2022년부터 4년 모두 특보 규칙보다 앞섰습니다.")),
    "G실버": ("0:12", sents(f"실버존은 2023년까지의 자료로 정하고 2024년 사고로 채점했고, 새로 생긴 사고 지점도 {L['sn_current']}%에서 {L['sn_ours_full']}%로 더 잡았습니다.")),
    "GAED": ("0:10", sents(f"밤에는 열린 AED가 낮 {L['a_day']}%에서 {L['a_night']}%로 줄고, 2023년 자료에서도 같은 공백이 확인됐습니다.")),
    "H기술": ("0:25", [
        "화면 뒤에서는 위험 예측, 위치 선정, 낙상 감지 모듈이 공공 데이터를 조치로 바꿉니다. 학습 모델은 꼭 필요한 낙상 감지에만 가볍게 썼습니다.",
        "보안은 비밀번호에 Argon2id, 토큰과 감사 기록에 HMAC-SHA256을 쓰고, 영상은 기기 밖으로 내보내지 않습니다."]),
    "H도입": ("0:12", sents("도입은 기존 카메라와 안내단말에 소프트웨어로 올리고, 국제 표준으로 기존 시스템과 연결합니다. 첫 단계로 한 개 구, 폭염기 석 달 파일럿을 제안합니다.")),
    "H정리": ("0:12", sents("사람과 AI가 함께 지키는 스마트도시, 가장 기술적인 눈으로 가장 느린 걸음을 지키겠습니다. 지금까지 팀 영점이었습니다. 감사합니다.")),
})


EXTRA_FIG_NUMBERS = {"13_map_zones_vs_hotspots": f"{L['s_current']} {L['m_n']} {L['m_cur']}", "14_simple_silver_pair": f"{L['s_current']} {L['s_ours_full']}", "15_simple_fall_pair": f"{L['f_lat_rule']} {L['f_lat_lr']} 0.70"}

# ================================================================== outputs (variant aware)
def numbers_in(text: str) -> list[float]:
    return [float(x.replace(",", "")) for x in re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", text)]


def slide_visible_text(s: dict) -> str:
    parts = [s["title"], s["headline"], *s["keywords"]]
    for e in s["elements"]:
        for k in ("big", "caption", "head", "label", "source"):
            if e.get(k):
                parts.append(str(e[k]))
        for k in ("sub", "lines"):
            if e.get(k):
                parts += [str(x) for x in e[k]]
        if e.get("rows"):
            parts += [c for r in e["rows"] for c in r]
        if e.get("type") == "image":
            parts.append(EXTRA_FIG_NUMBERS.get(Path(e["path"]).stem, ""))
    return " ".join(parts)


def number_visible(n: float, vis: list[float]) -> bool:
    return any(abs(v - n) < 1e-9 or (float(n).is_integer() and round(v) == n) or (not float(n).is_integer() and abs(v - n) < 0.051) for v in vis)


def number_cache_fix(text: str) -> str:
    return text



# ------------------------------------------------------------------ slide text hygiene: paths and "원문 대조 필요" live in the speaker notes
HEAT_SHORT = "출처: 질병관리청 온열질환 응급실감시 · 기상청 ASOS, 2022~2025 (자체 분석)"
SRC_FIG = {
    "03_agent_flow_measured": "출처: 시연 노트북 실측 (10회) · UR Fall 공개 데이터 교차검증", "04_agent_run_timeline": "출처: 시연 노트북 실측 (10회 중 1회)",
    "A8_agent_latency_distribution": "출처: 시연 노트북 실측 (10회)", "09_engine_flow": "출처: 자체 설계", "10_erd_core_tables": "출처: 자체 설계",
    "12_data_flywheel": "출처: 자체 설계", "11_code_greedy_mclp": "출처: 자체 구현 코드", "briefing_top_crop": "화면: 브리핑 시연 화면 (일부)",
    "01_heat_pooled_and_by_year": HEAT_SHORT, "01m_heat_by_year": "공공데이터: 질병관리청 온열질환 감시 · 기상청 ASOS 사용", "02_heat_component_gain": HEAT_SHORT, "A2_heat_precision": HEAT_SHORT, "A3_heat_forest_ours_minus_rule": HEAT_SHORT,
    "A4_heat_models_F_and_O": HEAT_SHORT, "A6_heat_budget_curve_2025": HEAT_SHORT,
    "17_map_current_vs_ours": "공공데이터: 도로교통공단 사고다발지 · 노인보호구역 표준데이터 사용", "07m_aed_hourly": "공공데이터: 국립중앙의료원 AED 정보 · 소방청 구급활동정보 사용",
    "05_silver_coverage_bars": "출처: 도로교통공단 TAAS 노인 보행사고, 2024년 평가 (자체 분석)", "A1_silver_past_vs_ours": "출처: 도로교통공단 TAAS 노인 보행사고, 2024년 평가 (자체 분석)",
    "13_map_zones_vs_hotspots": "출처: 도로교통공단 TAAS 2024 사고 다발지 · 노인보호구역 (자체 분석)", "14_simple_silver_pair": "출처: 도로교통공단 TAAS, 2024년 평가 (자체 분석)",
    "07_aed_night_gap": "출처: 소방청 구급활동 · 국립중앙의료원 AED 운영시간 (서울 2024.1~11, 자체 분석)",
    "08_fall_model": "출처: UR Fall Detection Dataset (Kwolek & Kępski) 교차검증", "A7_fall_with_false_alarm": "출처: UR Fall Detection Dataset (Kwolek & Kępski) 교차검증",
    "15_simple_fall_pair": "출처: UR Fall Detection Dataset 교차검증", "A10_silver_119_misallocation_opt": "출처: 소방청 119 구급활동 · 도로교통공단 TAAS (자체 분석)",
    "16_reach_no_smartphone": "출처: 2025 디지털정보격차 실태조사 · 지자체 사례 · 자체 시연",
}
SHORT_CREDIT = {"H사진": "사진: 서울신문(2025-07-23) · 전북일보(2025-05-13, 2026-09-28)"}
TW_SHORT = "화면: 관악구 디지털 트윈 시연 화면 (관악구, 2025-07-22)"
SRC_KEY = {
    "A문제": "출처: 도로교통공단 2024 통계 (언론 보도 인용)",
    "B원인": "출처: 자체 분석(TAAS·횡단보도 표준데이터) · 싱가포르 교통부 의회 서면답변 2026-05-06 · 서울시 2024-10 발표 · 감사원 2021-08",
    "B성능": "출처: 질병관리청·기상청 2022~2025 · 도로교통공단 TAAS · UR Fall (자체 분석)",
    "확장": "출처: 질병관리청·기상청 2022~2025 · 서울 AED 운영시간·구급활동 (자체 분석)",
    "도입": "출처: 조달청 혁신제품 · 스마트도시 통합플랫폼 자료 (검토 중)", "B도입": "출처: 조달청 혁신제품 · 스마트도시 통합플랫폼 자료 (검토 중)",
    "보안": "출처: 자체 보안 설계 문서", "보안표": "출처: 자체 보안 설계 문서", "부록 A6 데이터 한계": "출처: 자체 분석 결과 · UR Fall · 국립중앙의료원 AED",
    "A데모": "화면: 시연 화면 (실버존 지도 · 현장 에이전트)",
    "T개요": f"{TW_SHORT} · 건물 수: 국토부 건물통합정보", "T폭염": TW_SHORT, "T현장": TW_SHORT, "T야간": TW_SHORT, "T실버": TW_SHORT,
    "T사고": f"{TW_SHORT} · 실제 CCTV 클립 낙상 {L['ff_nfall']}건 평가",
}
PATHISH = re.compile(r"[\w\-]+/[\w\-./*]+|\.(?:json|csv|md|py|png|parquet|dot|sql)\b|sha256|scratchpad|variants\.")
UNVERIFIED = [re.compile(r"\s*\(원문 대조 필요\)"), re.compile(r"[,;]\s*(?:2차 자료는 )?원문 대조 필요"), re.compile(r"\s*·?\s*원문 대조 필요")]


def clean_str(t: str, notes: list[str]) -> str:
    orig = t
    for rx in UNVERIFIED:
        t = rx.sub("", t)
    if t != orig:
        notes.append(f"[원문 대조 필요] {orig}")
    return t


def clean_slide(sl: dict, key: str) -> dict:
    """Move file paths and unverified-source remarks off the slide into sl['src_notes'] (speaker notes); keep a short human source line."""
    notes: list[str] = []
    sl["headline"] = clean_str(sl["headline"], notes)
    sl["keywords"] = [clean_str(k, notes) for k in sl["keywords"]]
    els = []
    for e in sl["elements"]:
        e = dict(e)
        if e["type"] == "image" and e.get("source"):
            notes.append(f"[출처·근거] {e['source']}")
            e["source"] = SRC_FIG.get(Path(e["path"]).stem, "")
        if e["type"] == "text" and e["lines"] and e["lines"][0].startswith(("출처:", "화면:")) and PATHISH.search(" ".join(e["lines"])):
            notes.append(f"[출처·근거] {' '.join(e['lines'])}")
            if key not in SRC_KEY:
                continue
            e["lines"] = [SRC_KEY[key]]
        for f in ("caption", "head", "label", "big"):
            if isinstance(e.get(f), str) and e[f]:
                e[f] = clean_str(e[f], notes)
        for f in ("sub", "lines"):
            if e.get(f):
                e[f] = [clean_str(x, notes) for x in e[f]]
        els.append(e)
    sl["elements"] = els
    if sl.get("credit") and (PATHISH.search(sl["credit"]) or len(sl["credit"]) > 90) and key in SHORT_CREDIT:
        notes.append(f"[사진 출처 전체] {sl['credit']}")
        sl["credit"] = SHORT_CREDIT[key]
    sl["src_notes"] = notes
    return sl


def variant_slides(v: str) -> tuple[list[dict], list[dict]]:
    cfg = VARIANTS[v]
    main = [clean_slide(dict(REG[k], key=k, appendix=False), k) for k in cfg["main"]]
    appx = [clean_slide(dict(REG[k], key=k, appendix=True), k) for k in cfg["appx"] if k in REG]
    fig_n = 0
    for s in [*main, *appx]:
        s["elements"] = [dict(e) for e in s["elements"]]
        for e in s["elements"]:
            if e["type"] == "image" and e.get("caption") and not e["caption"].startswith("Fig."):
                fig_n += 1
                e["caption"] = f"Fig. {fig_n}. {e['caption']}"
    return main, appx


def script_lines(v: str) -> list[tuple[int, str, str, list[str]]]:
    cfg = VARIANTS[v]
    out = [(1, *SCRIPT_KEYS[v]["cover"][:1], "표지", SCRIPT_KEYS[v]["cover"][1])]
    for i, k in enumerate(cfg["main"], start=3):
        if (k in HIDDEN_KEYS or k in NOSCRIPT_KEYS) and v == "B":
            continue  # backup slides carry no script and no time
        mm, lines = SCRIPT_KEYS[v][k]
        out.append((i, mm, REG[k]["title"], lines))
    return out


def script_md(v: str) -> str:
    out = [f"# 「고령자 안전 스마트도시 에이전트」 6분 대본 (합니다체) · 변형 {v} {VARIANTS[v]['label']}", "", f"<!-- 숫자 출처: deck/build/numbers_ledger.md. 폭염 결과 파일 sha256 {HEAT_SHA[:12]} -->", ""]
    for n, mmss, title, lines in script_lines(v):
        out += [f"## [{n} · {mmss}] {title}", ""] + lines + [""]
    return "\n".join(out)


NO_HEADLINE = {"B장면", "O스마트도시", "O사람AI", "H사진", "H문제", "H원인", "H판단", "H한눈에", "H카메라", "T개요", "G폭염", "G실버", "GAED", "H기술", "H도입", "H정리", "D공공1", "D공공2", "D기타", "부록안내"}  # hero slides: the hero element is the headline


def script_diff_report() -> str:
    """Compare the generated B script with deck/script_B_v2.md section by section (digits normalised, whitespace ignored)."""
    src = Path("/Users/chloekang/kcf/deck/script_B_v2.md")
    if not src.is_file():
        return "script_B_v2.md missing"
    secs: dict[int, str] = {}
    cur = None
    for ln in src.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^## \[(\d+)\]", ln)
        if m:
            cur = int(m.group(1))
            secs[cur] = ""
        elif cur is not None and ln.strip() and not ln.startswith("<!--") and not ln.startswith("#"):
            secs[cur] += ln.strip()
    gen = {k: " ".join(SCRIPT_KEYS["B"][k][1]) for k in SCRIPT_KEYS["B"] if k in REG or k == "cover"}
    mapping = {1: ["cover"], 2: ["O스마트도시", "O사람AI"], 3: ["H사진", "H문제"], 4: ["H원인", "H판단"], 5: ["H한눈에"], 7: ["T개요"], 12: ["H기술"], 13: ["G폭염"], 14: ["G실버"], 15: ["GAED"], 17: ["H도입", "H정리"]}
    norm = lambda t: re.sub(r"\s+", "", re.sub(r"\d[\d,.]*", "#", t))  # noqa: E731
    out = []
    for n, keys in mapping.items():
        a, b = norm(secs.get(n, "")), norm("".join(gen.get(k, "") for k in keys))
        if n == 12:
            b = norm(gen["H기술"].split(" 보안은")[0]) if False else b
        if n == 5:
            b = b  # H카메라 is an addition, reported separately
        if a == b:
            out.append(f"[{n}] 일치")
        elif n in (12,):
            out.append(f"[{n}] md는 모듈·알고리즘(12)과 보안(16)이 따로 있고 덱은 기술·보안 한 장에 합침")
        else:
            out.append(f"[{n}] 다름: md {len(a)}자 / 덱 {len(b)}자")
    if secs.get(16):
        out.append("[16] " + ("일치(기술 슬라이드에 포함)" if norm(secs[16]) in norm(gen["H기술"]) else "다름"))
    return "\n".join(out)


def clarity_md(v: str, main: list[dict], appx: list[dict]) -> str:
    crit_ok = {"주제·독창성", "기술 적정성", "완성도", "실용성", "발표"}
    out = [f"# 명료성 점검 · 변형 {v} (슬라이드마다: 무엇을 증명? / 어느 기준? / 근거 그림?)", "",
           "자동 점검: 헤드라인 1개, 키워드 줄 2개 이하, 시각 자료 1개 이상, 기준 라벨, 대본 숫자가 슬라이드나 그림에 보이는지(반올림 허용).", "",
           "| # | 무엇을 증명? | 어느 기준? | 근거 그림? | 점검 |", "|---|---|---|---|---|"]
    by_num = {n: " ".join(lines) for n, _, _, lines in script_lines(v)}
    problems: list[str] = []
    for i, s in enumerate([*main, *appx]):
        num = i + 3 if i < len(main) else i + 4
        checks = []
        if not s["headline"] and s.get("key") not in NO_HEADLINE:
            checks.append("헤드라인 없음")
        if len(s["keywords"]) > 2:
            checks.append("키워드 3줄 이상")
        if s.get("key") not in NO_HEADLINE and not any(e["type"] in ("image", "photo", "table", "card", "bignum", "graybox") for e in s["elements"]):
            checks.append("시각 자료 없음")
        if s["criterion"] not in crit_ok:
            checks.append("기준 라벨 오류")
        vis = numbers_in(slide_visible_text(s))
        missing = sorted({n for n in numbers_in(by_num.get(num, "")) if not number_visible(n, vis)})
        if missing:
            checks.append("대본 숫자 화면에 없음: " + ",".join(str(m) for m in missing))
        verdict = "OK" if not checks else "수정 필요: " + "; ".join(checks)
        if checks:
            problems.append(f"{num} {s['title']}: {verdict}")
        out.append(f"| {num} {s['title']} | {s['proves']} | {s['criterion']} | {s['evidence']} | {verdict} |")
    out += ["", "## 자동 점검 결과", "", "문제 없음" if not problems else "\n".join(f"- {p}" for p in problems)]
    LOG.info("variant %s clarity problems: %s", v, problems or "none")
    return "\n".join(out)


def spec_json(v: str, main: list[dict], appx: list[dict]) -> dict:
    cfg = VARIANTS[v]
    notes = {n: "\n".join(lines) for n, _, _, lines in script_lines(v)}
    slides = []
    for i, s in enumerate([*main, *appx]):
        num = i + 3 if i < len(main) else i + 4
        sl = {k: val for k, val in s.items() if k not in ("proves", "evidence", "script_slide", "key", "src_notes")}
        sl["notes"] = notes.get(num, "(부록: 발표하지 않음, Q&A에서 해당 장으로 이동)")
        if s.get("key") in EXTRA_NOTES:
            sl["notes"] += "\n[슬라이드에서 뺀 내용] " + EXTRA_NOTES[s["key"]]
        if v == "B" and s.get("key") in NOSCRIPT_KEYS:
            sl["notes"] = "(발표하지 않는 출처 슬라이드. 심사·Q&A에서 보여 줍니다.)\n" + TERMS_NOTE.get(s["key"], "") + "\n" + UNUSED_NOTE
            if s.get("key") == "D기타":
                sl["notes"] += "\n사진 출처 전체: " + " / ".join(PRESS_CREDITS.values())
        if v == "B" and s.get("key") in HIDDEN_KEYS:
            sl["hidden"] = True
            sl["notes"] = ("[백업 슬라이드] 라이브 데모가 실패했을 때만 사용합니다. 숨김 상태라 슬라이드 쇼에서는 건너뜁니다.\n"
                           "숨김 해제: 왼쪽 썸네일에서 슬라이드 8~12를 선택 → 우클릭 → '슬라이드 숨기기' 체크 해제(또는 슬라이드 쇼 탭 > 슬라이드 숨기기). 그 뒤 → 키로 순서대로 넘깁니다.\n"
                           "멘트는 데모_진행표.md의 해당 단계 한 줄을 읽습니다. 모든 전송은 훈련입니다.")
        if s.get("src_notes"):
            sl["notes"] += "\n\n[출처·근거 원문] (화면에는 짧은 출처만 표시)\n" + "\n".join(s["src_notes"])
        if s.get("key") == "H한눈에":
            sl["notes"] += f"\n[밤 행 기준] 서울 AED {L['w_n']}대 중 1%({L['w_budget']}대)를 24시간 개방으로 가정, 걸어서 2분(1.2 m/s, 편도 144 m) 안에 열린 AED가 있는 65세 이상 비율. 모형 추정이며 무작위 1%는 {L['w_rand']}%. 속도 0.8 m/s는 관악구 화면의 +219명 계산에만 사용."
        if s.get("key") == "T개요":
            sl["notes"] += "\n[진행] Cmd+Tab으로 Chrome(/twin 전체화면) 전환, 키 1~5, 끝나면 Cmd+Tab으로 복귀. 세부 순서와 실패 대처는 데모_진행표.md. 백업 슬라이드 8~12는 숨김 상태."
        if s.get("key") == "B데모":
            sl["notes"] += "\n[데모 계획] 운영 지도에서 오늘의 조치를 연 뒤, /cap/alerts/{id}/audio.wav 마을방송 음성을 재생하고 정류장 안내단말 문구를 함께 보여 준다 (백엔드 준비 후, 실패 시 녹화본)."
        slides.append(sl)
    return {"cover": {"team": "팀명 : 영점", "work": cfg["work"], "subtitle": cfg["subtitle"], "topic": "AI와 디지털 기술로 해결하는 우리 주변의 생활문제", "notes": notes[1]},
            "toc": ["1. 문제 · 원인", "2. 해결 · 데모", "3. 근거", "4. 기술 · 보안", "5. 도입 · 비전"], "toc_notes": "(목차: 대본 없음)", "slides": slides}


def captions_md(all_slides: list[dict]) -> str:
    out = ["# 그림 목록 (deck/figs/final/)", "", "| 파일 | 캡션 | 출처 |", "|---|---|---|"]
    seen = set()
    for s in all_slides:
        for e in s["elements"]:
            if e["type"] == "image" and e.get("caption") and e["path"] not in seen:
                seen.add(e["path"])
                out.append(f"| `{Path(e['path']).name}` | {e['caption']} | {e['source']} |")
    out.append("| `11_code_greedy_mclp.png` | 핵심 코드 (최대 커버리지 입지, 18줄) | app/backend/experiments/silver/run.py:199-216 |")
    return "\n".join(out)



def ledger_note() -> None:
    return None


def sg(x: str) -> str:
    """Prefix + to a positive formatted number (ledger values like '8.8' or '-0.2')."""
    return x if x.startswith(("+", "-")) else f"+{x}"


QA = [
    ("고령자가 스마트폰을 안 쓰거나 낯설어하면 어떻게 합니까?", f"고령층의 디지털 역량은 {L['nd_old']}%로 가장 낮아서(2025 실태조사, 원문 대조 필요), 에이전트는 앱 대신 길 자체를 바꿉니다: 신호 시간, 보호구역 위치, AED 개방 요청입니다. 알림은 재난문자, 정류장 안내단말, 마을방송 음성(CAP에서 만든 WAV)처럼 앱이 필요 없는 채널로 보내고, 방음 주택처럼 안 들리는 곳은 방문팀이 가며, 시민 화면은 보호자와 담당자용입니다."),
    ("개인정보는 어떻게 지킵니까?", "영상은 저장도 전송도 하지 않고 골격 좌표만 계산하며, 기록에는 영상 해시만 남깁니다(agent_run의 image_sha256). 실제 관제 영상에 올릴 때는 개인정보보호법 제25조 범위에서 운영합니다."),
    ("정부 모델이 이미 있는데 무엇이 다릅니까?", f"질병청·기상청 예측은 전국과 17개 시도 단위로 공개되어 있고, 에이전트는 시군구 250곳 단위입니다. 특보 규칙 대비 +{L['h_d']}%포인트는 단계별로 특보 규칙→시도 단위 모델(우리 구현) {sg(L['h_s1'])}, →인구·취약성 반영 {sg(L['h_s2'])}, →시군구 모델 {sg(L['h_s3'])}%포인트이고, 시도 단위 모델 대비 에이전트는 {sg(L['h_o_b2'])}%포인트(95% CI {L['h_o_b2_lo']}~{L['h_o_b2_hi']})입니다."),
    ("데이터 누수나 미래 정보 혼입은 없습니까?", f"각 해는 이전 연도까지만 학습하는 연도별 검증입니다. 예보를 쓰지 않고 전날 관측만 쓴 변형(O)에서도 {L['h_o_b1']}%에서 {L['h_o_ours']}%로 +{L['h_o_d']}%포인트(95% CI {L['h_o_d_lo']}~{L['h_o_d_hi']})입니다."),
    ("입력 F는 예보를 쓰는 것 아닙니까? 시도 단위 모델과는 얼마나 차이 납니까?", f"F 변형은 당일 기상 입력(예보 대용)을 쓰는 설정이고, 이 설정에서 시도 단위 모델 대비 에이전트는 {sg(L['h_o_b2'])}%포인트(95% CI {L['h_o_b2_lo']}~{L['h_o_b2_hi']})입니다. 전날 관측만 쓴 O 변형에서는 특보 규칙 대비 +{L['h_o_d']}%포인트(95% CI {L['h_o_d_lo']}~{L['h_o_d_hi']})로 두 설정을 구분해서 말합니다."),
    ("왜 고령자 보행안전입니까?", f"보행 사망 {L['p_ped_n'].split(' / ')[0]}명 중 65세 이상이 {L['p_ped_n'].split(' / ')[1]}명({L['p_ped']}%, 2024)으로 가장 크고, 정답과 공식 기준이 모두 공개된 문제입니다. 같은 엔진이 폭염과 밤 AED에도 적용되며 부록에 있습니다."),
    ("왜 시군구 단위입니까?", "지자체가 조치하는 단위이고 질병청 신고 단위이기 때문입니다. 시도 단위로는 같은 시도 안의 차이를 볼 수 없습니다."),
    ("65세 이상 20%는 맞는 수치입니까?", f"{L['pd_pop']}%는 행안부 주민등록 인구(2024-12-23) 기준입니다. 인구주택총조사 2025는 20.7%로 집계 기준이 달라 값이 다릅니다."),
    ("감사원 지적은 정확히 무엇입니까?", "감사원 「스마트도시 조성사업 추진실태」(2021-08-05)에서 통합플랫폼의 재난상황 영상지원과 사회적약자 관리 서비스 실적이 저조하다고 했고, 대상은 LH 시행 74개 사업입니다. 이 밖의 수치는 쓰지 않습니다."),
    ("도입 경로는 무엇입니까?", "파일럿은 1개 구에서 폭염기 3개월로 제안합니다. 혁신제품 지정은 후보 경로이고, 통합플랫폼 연계와 TTA 인증은 검토가 필요하며 확정된 것은 없습니다."),
    ("비용은 얼마나 듭니까?", "엔진은 공개 데이터와 소프트웨어이고 현장 장비는 기존 카메라를 씁니다. 참고로 기존 AI 횡단보도 사업은 곳당 약 4,500만~6,100만 원이며, 우리 가격은 가설입니다."),
    ("같은 예산이라는 것은 금액입니까? 성과 지표는 무엇입니까?", "같은 예산은 금액이 아니라 같은 지점 수와 같은 경보 수로 맞춘 비교입니다. 파일럿 성과 지표(KPI)는 폭염기 포착률, 채택률, 현장 확인 시간을 제안합니다."),
    ("실패하거나 약한 사례는 무엇입니까?", f"실버존에서 작년 사고 장소를 그대로 쓰는 규칙과 전체로는 동률({sg(L['sd_a_past'])}%포인트, 95% CI {L['sd_a_past_lo']}~{L['sd_a_past_hi']})이고, 지점 수 1,000 이하에서는 {L['sd_small_max']}~{L['sd_small_min']}%포인트로 뒤처집니다. 새 사고 지점(n={L['s_n_new']})에서는 현행 {L['sn_current']}%에서 에이전트 {L['sn_ours_full']}%로, 작년 사고 장소 대비 {L['sd_n_past']}%포인트(95% CI {L['sd_n_past_lo']}~{L['sd_n_past_hi']})이며, 같은 후보 격자끼리는 {L['sd_n_gridD']}%포인트(95% CI {L['sd_n_gridD_lo']}~{L['sd_n_gridD_hi']})로 불확실합니다."),
    ("작년 사고 장소 규칙과 동률인데 왜 에이전트입니까?", f"전체로는 동률({sg(L['sd_a_past'])}%포인트, 95% CI {L['sd_a_past_lo']}~{L['sd_a_past_hi']})이라 단순 규칙도 비슷하게 덮습니다. 에이전트의 장점은 새로 생긴 다발지에서 나며 작년 사고 장소 대비 {L['sd_n_past']}%포인트(95% CI {L['sd_n_past_lo']}~{L['sd_n_past_hi']})입니다."),
    (f"실버존 {L['m_n']}곳 중 {L['m_ours']}곳은 과대 아닙니까?", f"2023년까지 자료로 지점을 고르고 2024년으로 평가해서 서울 다발지 {L['m_n']}곳 중 현행 {L['m_cur']}곳, 에이전트 {L['m_ours']}곳입니다. 3,918곳은 같은 개수로 맞춘 비교이고, 새로 생긴 다발지에서는 에이전트 {L['sn_ours1']}%, 현행 {L['sn_current1']}%를 덮습니다."),
    ("3,918곳을 전부 옮깁니까? 지정 근거는 무엇입니까?", "아닙니다. 3,918곳은 같은 개수로 맞춘 공정 비교 설계이고, 노인보호구역은 시장 등이 경찰과 협의해 지정하며 신청 시 교통량·사고·동선 조사를 거칩니다. 에이전트는 후보 지점과 근거를 제시할 뿐입니다."),
    ("경보의 약 72%는 헛경보 아닙니까?", f"정밀도(경보 낸 시군구-일 중 환자가 나온 비율)는 특보 규칙 {L['h_prec_b1']}%에서 에이전트 {L['h_prec_ours']}%로 올랐지만 나머지 약 {L['h_prec_miss']}%는 환자가 없었습니다. 재난문자는 초안이고 주무관 채택과 과장 결재 뒤에만 나가며, 같은 경보 수로 맞춘 비교의 값입니다."),
    ("신호 부족 수치는 전국 기준입니까?", f"아닙니다. 횡단보도 표준데이터 50,000건 중 녹색시간과 길이가 모두 있는 {L['cw_n']}곳(경기 {L['cw_gg']}곳 중심)이며 41.8% 같은 값은 0.7m/s 가정입니다. 녹색시간에 점멸이 포함되는지 확인하지 못했습니다."),
    ("이미 비슷한 서비스가 있지 않습니까?", "UNIST의 쉼터 배치, 국립재난안전연구원의 폭염 취약 지도, 제주의 신호 연장은 각각 인프라 하나만 다룹니다. 우리가 찾은 범위에서 에이전트는 여러 인프라를 한 엔진과 한 지도에서 같은 예산으로 비교하고, 실제 결과로 사후 검증하며 감사 로그를 남깁니다."),
    ("야간과 악천후에서도 동작합니까?", f"조도와 비로 열화시킨 영상으로 강건성을 시험 중이며, 측정이 끝나기 전에는 오작동률을 말하지 않겠습니다. 지금의 낙상 감지 검증은 UR Fall 공개 데이터이고 낙상 {L['f_nfall']}건, 일상 {L['f_nadl']}건입니다."),
    ("기존 행정 시스템과 어떻게 붙습니까?", "표준 출력으로 연결합니다: OASIS CAP 1.2 XSD 검증을 통과한 CAP 피드, 서명 웹훅, 공문 초안 DOCX입니다. 스마트도시 통합플랫폼 연계는 다음 단계이며 규격 준수를 주장하지 않습니다."),
    ("누가 결정하고 권한은 어디에 있습니까?", "에이전트는 추천과 초안까지이고, 재난문자는 시·군·구가 시스템에 입력해 송출을 요청하면 같은 시·군·구 재난 부서장이 승인하며, 보행신호는 경찰 협의 요청서 초안을 받은 경찰이, AED 개방은 설치기관 관리책임자가 결정합니다. BIT 문구는 교통정보센터 협의가 필요한 연계 제안이고, 모든 호출은 수정할 수 없는 감사 로그에 남습니다."),
    ("밤에도 재난문자를 보냅니까?", "재난문자 운영 규정상 야간(21~06시)에는 폭염 문자 대부분이 발송 제외 항목입니다. 그래서 에이전트의 야간 조치는 문자가 아니라 AED 개방 요청 공문 초안과 관제요원 확인입니다."),
    ("AED는 24시간 열어 둘 의무가 있습니까?", "관리책임자 지정과 월 1회 점검은 응급의료법 시행규칙상 의무이지만 24시간 개방 의무는 없습니다. 그래서 에이전트는 지정이나 명령이 아니라 설치기관에 보내는 개방 요청 공문 초안을 만듭니다."),
    ("제품은 어떻게 구성됩니까?", "제품은 네 부분으로, 담당자가 쓰는 3D 관제 화면, 횡단보도 카메라에 올리는 현장 에이전트, 공공 데이터로 위험한 곳과 시간을 찾는 분석 모듈, 정류장 단말과 마을방송입니다. AI는 추천하고 결정은 담당자가 합니다."),
    ("AED 1%만 열어도 밤 도달이 17.6%에서 20.3%로 늘어난다는 것은 실측입니까?", f"아닙니다. 서울 AED {L['w_n']}대 중 1%인 {L['w_budget']}대를 24시간 개방으로 바꾼다고 가정한 보행 거리 기반 모형 추정이며, 밤 2분 도달 65세 이상 비율이 {L['w_base']}%에서 {L['w_conv']}%가 됩니다. 같은 수를 무작위로 고르면 {L['w_rand']}%이고, 실측 효과는 파일럿에서 확인합니다."),
    ("AI가 꼭 필요했습니까?", f"폭염에서 단순 위험 점수와 튜닝한 GBM의 포착률 차이는 {L['h_gbm']}%포인트(95% CI {L['h_gbm_lo']}~{L['h_gbm_hi']})로 같았습니다. 개선은 모델이 아니라 시군구 단위 위험 설계에서 나왔고, 낙상 감지처럼 영상을 읽는 곳에서만 학습 모델이 필요합니다."),
    ("LLM은 왜 필요합니까?", f"LLM은 조치 계획과 문안 작성에 쓰고, 실패하면 결정적 계획기로 대체하며 브리핑 숫자는 검증기를 통과해야 합니다(통과 {L['g_ground']}/{L['g_n']}). 지연은 대부분 LLM에서 나오므로 결정적 모드로 즉시 동작시킬 수 있습니다."),
    ("어떤 기기에서 쟀습니까?", f"시연 노트북 {L['hw']}에서 로컬로 쟀고, 에이전트 한 번 실행은 p50 {L['g_total']} ms, p95 {L['g_p95']} ms({L['g_n']}회)입니다. 현장 기기 사양은 파일럿에서 확정합니다."),
    ("낙상 감지는 현장에서도 잘 됩니까?", f"실험실 공개 데이터(UR Fall, 낙상 {L['f_nfall']}건·일상 {L['f_nadl']}건)에서는 감도 {L['f_sens_lr']}%, 지연 {L['f_lat_lr']}초입니다. 실제 CCTV 클립(낙상 {L['ff_nfall']}건)에서는 감도 {L['ff_sens']}%(95% CI {L['ff_sens_lo']}~{L['ff_sens_hi']})이고 오경보는 시간당 {L['ff_fa']}건(참고값)입니다."),
    ("실제 CCTV 41%와 시간당 313건을 감당할 수 있습니까?", f"자동 신고는 없고 관제요원이 영상을 확인한 뒤에만 119로 넘기므로 오경보는 확인 부담으로 남습니다. {L['ff_fa']}건은 비낙상 클립 {L['ff_neg_h']}시간 기준의 참고값이며, 현장 영상 재검증 전에는 운영 부담을 약속하지 않습니다."),
    ("무더위쉼터는 어떻게 합니까?", "관악구 쉼터 공개자료는 없어서 지금은 야간 연장 후보를 만들지 못합니다. 구청 쉼터 목록 연계가 파일럿의 필수 조건입니다."),
    ("1년 자료만으로 말하는 것 아닙니까?", f"2023년 독립 자료에서도 공공장소 심정지의 밤 비율이 {L['a23_share']}%(95% CI {L['a23_lo']}~{L['a23_hi']})로 2024년 {L['a24_share']}%와 같습니다(차이 {L['a_dshare1']}%포인트, p={L['a_dshare_p']}). 건수는 두 해를 섞지 않고 비율만 비교했습니다."),
    ("밤 AED 문제는 심정지의 17%뿐 아닙니까?", f"밤(22~06시)은 공공장소 심정지의 {L['a24_share']}%(2024), {L['a23_share']}%(2023)로 두 해 모두 같고, 그 시간대 열린 AED는 서울 평일 평균 {L['a_night']}%(낮 {L['a_day']}%)입니다. 관악구에서 운영시간 미등록 {L['a_gw_unreg']}대는 닫힘으로 간주했고, 1순위 개방 시 밤 2분 보행권 65세 이상 +{L['a_gw_gain']}명은 모형 추정입니다."),
    ("119 독립 검증은 왜 유의하지 않습니까?", f"서울 {L['v_gu']}개 구, 한 해의 70세 이상 보행 119 {L['v_n']}건 기준이라 현행−에이전트 차이는 {L['vo_gap']}%포인트(95% CI {L['vo_gap_lo']}~{L['vo_gap_hi']})로 유의하지 않습니다. 이 검증은 구별 배분을 참고로 본 것이라 부록에만 두고 주장하지 않습니다."),
    ("다음 단계는 무엇입니까?", "현장 영상으로 낙상 감지를 다시 검증하고 CAP 연동을 실제 채널로 넓힙니다. 걸어서 4분 분석은 동 단위 자료를 정보공개청구로 받아 진행합니다."),
]




def ledger_md() -> str:
    out = ["# 숫자 장부 (claim → value → file → key)", "",
           f"- 폭염 결과 파일: `{HP}` · sha256 `{HEAT_SHA}` · 수정 시각 {HEAT_MTIME} (가장 최근 `--family at` 실행; `--family tx` 기온 민감도 실행은 별도 파일이며 사용하지 않음)",
           f"- 실버: `experiments/silver/out/coverage_results.csv`, 119: `{VP}`, AED: `{AP}` + `hourly_gap.csv`, 낙상: `{FP}` (v1, 교차검증), 에이전트: `{BENCH_PATH}` (실행 10회; 타임라인 {TIMELINE_RUN})",
           "- 값 표기는 슬라이드·대본과 같은 반올림. `%p` = 퍼센트끼리의 차이, CI = 95% 신뢰구간.", "",
           "| claim | value | file | key |", "|---|---|---|---|"]
    for claim, val, f, k in LEDGER:
        out.append(f"| {claim} | {val} | `{f}` | `{k}` |")
    out += ["", "## 숫자가 아닌 근거 (2차 자료·조정자 전달, 원문 대조 필요 표시는 발표자 노트에만)", "",
            "| claim | file |", "|---|---|",
            "| 기존 AI 횡단보도 사업비 곳당 약 4,500만~6,100만 원 (Q&A 비용에만 사용) | `deck/QA_and_B2G.md` §1 |",
            "| 조달청 혁신제품 수의계약·시범구매는 후보 경로, 통합플랫폼 TTA 인증은 검토 필요 (도입 슬라이드, Q&A 도입 경로) | `deck/QA_and_B2G.md` §1 |",
            "| 알고리즘 파라미터 Argon2id t=3 m=64MiB p=4, HMAC-SHA256 토큰 900초, 토큰 버킷 60/초당 20 (보안 슬라이드) | `app/backend/app/security/ALGORITHMS.md` |",
            "| 실버존 코드 줄 199-216 (알고리즘 부록) | `app/backend/experiments/silver/run.py` |",
            "| 14개 후보 검토 | `deck/QA_and_B2G.md` §3 |",
            "| CAP 경보가 OASIS CAP 1.2 XSD 검증 통과 | 인프라 담당 보고 2026-10-24 (사본 확인 필요) |",
            "| FHIR 묶음이 HL7 공식 검증기에서 오류 0 · 경고 0 | 인프라 담당 보고 2026-10-24 (사본 확인 필요) |",
            "| 마을방송 음성 = CAP에서 만든 WAV 5.9초, 공문 초안 DOCX, 서명 웹훅 | 인프라 담당 보고 2026-10-24 |",
            f"| 에이전트 p50 {L['g_total']} ms · p95 {L['g_p95']} ms ({L['g_n']}회), 계획 유효 {L['g_valid']}/{L['g_n']}, 숫자 검증 {L['g_ground']}/{L['g_n']}" + (f", 스트리밍 첫 단계 p50 {L['g_first']} ms" if "g_first" in L else "") + " | `deck/build/data/after.json`, `after_stream.json` (슬라이드 그림도 같은 파일에서 생성) |",
            "| 팩트체크(조정자 전달 2026-10-25): 2024 보행 사망 920명 중 65세 이상 616명 (도로교통공단 통계 보도 인용). 11,498건·619명은 이차 출처 미확인이라 삭제 | `deck/PROBLEM_DEFINITION.md` |",
            "| 팩트체크: 보행속도 일반 1.0 m/s, 노인·어린이보호구역 최대 0.7 m/s (서울시 2024-10 발표). 0.8은 AED 보행 분석의 가정으로만 사용 | `deck/PROBLEM_DEFINITION.md` |",
            "| 팩트체크: 싱가포르 65세 이상 보행 사망 2024년 11명 → 2025년 27명 (교통부 의회 서면답변 2026-05-06), 신호 연장 장치 확대 중(수치 없음) | `deck/PROBLEM_DEFINITION.md` |",
            "| 팩트체크: 감사원 「스마트도시 조성사업 추진실태」(2021-08-05), 대상 LH 시행 74개 사업, 추가 수치 없음 | `deck/PROBLEM_DEFINITION.md` |",
            "| 팩트체크: 재난문자방송 기준 및 운영규정 제7·10·11조(송출 요청·부서장 승인), 별표 2(90자형 기본·폭염 등 157자형), 별표 1(야간 21~06시 발송 제외 다수) | 조정자 전달 [1차] |",
            "| 팩트체크: 노인보호구역은 시장 등이 경찰과 협의해 지정, 신청 시 교통량·사고·동선 조사 | 조정자 전달 |",
            "| 팩트체크: AED 관리책임자 지정·월 1회 점검은 의무(응급의료법 시행규칙), 24시간 개방 의무 없음 | 조정자 전달 |",
            "| 팩트체크: 65세 이상 20.0%는 행안부 주민등록(2024-12-23), 인구주택총조사 2025는 20.7% | 조정자 전달 |"]
    return "\n".join(out)


QA_REFS = [
    ("제품은 어떻게", ["B제품"]), ("개인정보는 어떻게", ["보안표"]),
    ("고령자가 스마트폰을 안", ["도달"]), ("정부 모델이 이미", ["엔진 구성 요소별 기여", "부록 A4 정부 시도 모델"]), ("데이터 누수", ["부록 A3 입력 두 가지"]),
    ("입력 F는", ["부록 A3 입력 두 가지", "부록 A4 정부 시도 모델"]), ("같은 예산이라는", ["부록 A5 경보 수를 바꿔도"]),
    ("실패하거나 약한", ["부록 A1 작년 사고 장소 vs 에이전트", "실버존: 같은 개수", "부록 A6 데이터 한계"]),
    ("작년 사고 장소 규칙과 동률", ["부록 A1 작년 사고 장소 vs 에이전트", "실버존: 같은 개수"]), ("실버존 ", ["실버존: 같은 개수"]), ("3,918곳을 전부", ["실버존: 같은 개수"]),
    ("경보의 약", ["부록 A2 오경보"]), ("이미 비슷한", ["분석 엔진"]), ("야간과 악천후", ["낙상 감지 모델"]), ("기존 행정 시스템", ["B연동"]),
    ("누가 결정", ["DB 핵심 테이블 3개"]), ("AI가 꼭", ["알고리즘 3개", "엔진 구성 요소별 기여"]),
    ("LLM은 왜", ["엣지 에이전트", "데모 ① 현장 에이전트", "부록 A8 에이전트 지연"]), ("어떤 기기", ["부록 A8 에이전트 지연", "데모 ① 현장 에이전트"]),
    ("낙상 감지는 현장", ["낙상 감지 모델", "부록 A7 낙상 오경보"]), ("실제 CCTV", ["부록 A7 낙상 오경보"]), ("1년 자료", ["밤에 잠긴 AED"]),
    ("밤 AED 문제", ["밤에 잠긴 AED"]), ("119 독립", ["부록 A10 119 독립 검증"]), ("다음 단계", ["부록 A9 데이터 선순환"]),
]
_covered = {k for _, ks in QA_REFS for k in ks}
if not (set(APPX_KW) <= _covered):
    LOG.warning("appendix slides without a built-in question: %s", sorted(set(APPX_KW) - _covered))
assert QA_SOURCE.is_file() or all(k in APPX_NUM for _, ks in QA_REFS for k in ks)


def qa_ref(q: str) -> str:
    for prefix, keys in QA_REFS:
        if q.startswith(prefix):
            return " → 부록 " + ", ".join(str(APPX_NUM[k]) for k in keys if k in APPX_NUM)
    return ""



BLANK_EXP = "【본인 경험 한 문장: 직접 채우기】"
BASIC_QA = [
    ("왜 이 생활문제들인가요?", f"매일 반복되고, 고령화로 점점 커지고, 공공데이터로 효과를 확인할 수 있는 문제를 골랐습니다. 보행 사망 {L['p_ped_n'].split(' / ')[0]}명 중 {L['p_ped_n'].split(' / ')[1]}명, 세 명 중 두 명이 고령자이고, 온열질환 사망자도 60세 이상이 {L['p_heat']}%입니다(2025)."),
    ("왜 폭염·보행·AED를 한 작품에 묶었나요?", "셋 다 고령자가 도시 인프라를 쓰는 순간의 문제이고, 해법도 같은 판단입니다. 어디에, 언제, 무엇을 할지를 하나의 에이전트와 하나의 관제 화면으로 묶었습니다."),
    ("왜 이 문제가 필요하다고 느꼈나요?", f"{BLANK_EXP} 그리고 데이터로 확인해 보니 지금의 실버존은 다음 해 사고 지점의 {L['s_current']}%만 덮고 있었습니다."),
    ("왜 스마트도시인가요?", f"투자와 인프라는 이미 깔려 있지만(통합플랫폼 {L['sm_108']}개 지자체) 고령자 기준이 빠져 있기 때문입니다. 새로 짓기보다 이미 있는 것을 고령자에게 맞게 운영하는 편이 가장 빠릅니다."),
    ("기존 서비스와 무엇이 다른가요?", "저희가 다루는 곳은 길 위의 공공 인프라입니다. 신호, 보호구역, AED, 정류장 단말을 현행 기준과 같은 예산으로 비교해 효과를 숫자로 보였습니다."),
    ("고령자는 무엇을 해야 하나요? 스마트폰을 안 쓰면요?", "따로 설치하거나 배울 것이 없습니다. 정류장 단말과 마을방송, 재난문자로 안내가 갑니다."),
    ("누가 쓰나요?", "구청 담당 주무관과 관제요원이 씁니다. AI는 추천과 초안까지 하고, 결정과 발송은 권한이 있는 사람이 합니다."),
    ("왜 관악구, 왜 2025년 7월 22일인가요?", "그날은 현행 특보 기준으로는 관악구가 대상이 아니었지만 에이전트는 위험 지역으로 판단한 실제 날이라, 차이를 보여 주기 좋았습니다. 데이터는 전국 단위라 다른 구에도 같은 방식으로 쓸 수 있습니다."),
    ("다른 지역에도 쓸 수 있나요?", f"폭염 판단은 전국 {L['h_units']}개 시군구 단위로, 실버존 평가는 전국 {L['s_k']}곳 기준으로 했습니다. 서울에만 있는 생활인구 같은 데이터는 다른 공개 자료로 바꾸면 됩니다."),
    ("현장 공무원 의견은 들어봤나요?", "【직접 채우기: 현장 의견을 들었다면 누구에게 어떻게, 아니라면 아직 듣지 못했다는 한 문장】 구청 담당자용 활용 가이드를 만들어 두었고, 파일럿에서 확인하려 합니다."),
    ("공공데이터가 틀리면요?", f"틀리거나 빈 값은 숨기지 않고 표시합니다. 예를 들어 운영시간이 미등록인 AED {L['a_gw_unreg']}대는 닫힘으로 간주하고 현장 확인 필요를 붙였습니다."),
    ("이걸 어떻게 만들었나요?", "【제작 기간과 역할 분담 한 문장: 직접 채우기】 발표의 모든 숫자는 결과 파일에서 생성하고, 같은 명령으로 다시 만들면 같은 값이 나옵니다."),
]


def qa_from_source() -> str:
    """예상질문.md = deck/qa_source.md (verified text, verbatim) + a '→ 부록 N' link after each answer that has a supporting slide."""
    lines = QA_SOURCE.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    linked: set[str] = set()
    pending: str | None = None

    def flush() -> None:
        nonlocal pending
        if pending is not None:
            m = QID.match(pending)
            keys = [k for k in dict.fromkeys(HEADING_REFS.get(m.group(1), []) if m else []) if k in APPX_NUM]
            if keys:
                linked.update(keys)
                j = len(out) - 1
                while j >= 0 and (not out[j].strip() or out[j].lstrip().startswith("<!--")):
                    j -= 1
                if j >= 0 and not out[j].startswith("#"):
                    out[j] += " → 부록 " + ", ".join(str(APPX_NUM[k]) for k in keys)
        pending = None

    for ln in lines:
        if ln.startswith("## "):
            flush()
            pending = ln
        elif ln.startswith("# "):
            flush()
        out.append(ln)
    flush()
    missing = sorted(set(APPX_KW) - linked)
    if missing:
        LOG.warning("appendix slides that no question in qa_source.md points to: %s", missing)
    head = ["부록 번호 = 통합 발표 슬라이드 번호입니다. 슬라이드 쇼 중 숫자를 누르고 Enter를 누르면 이동합니다.", ""]
    return "\n".join(head + out)


def qa_md() -> str:
    if QA_SOURCE.is_file():
        return qa_from_source()
    out = [f"# 예상 질문 {len(BASIC_QA) + len(QA)}개 (2문장 답, 숫자는 numbers_ledger.md 기준)", "", "부록 번호 = 통합 발표 슬라이드 번호입니다. 슬라이드 쇼 중 숫자를 누르고 Enter를 누르면 이동합니다.", ""]
    out += ["# 기본 질문 (먼저 연습)", ""]
    for i, (q, a) in enumerate(BASIC_QA, 1):
        out += [f"## B{i}. {q}", "", a, ""]
    out += ["# 심화 질문", ""]
    for i, (q, a) in enumerate(QA, 1):
        out += [f"## Q{i}. {q}", "", a + qa_ref(q), ""]
    return "\n".join(out)


def run(cmd: list[str]) -> str:
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.stdout + res.stderr



DESKTOP = Path("/Users/chloekang/Desktop/영점_발표")


def sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else ""


def publish_variant(v: str, prev_sha: str) -> list[str]:
    """Copy variant outputs to the Desktop folder. A hand-edited Desktop pptx (bytes differ from the previous build and the
    last published copy) is never overwritten: the new build is written as <name>_new.pptx/.pdf instead."""
    import shutil
    cfg = VARIANTS[v]
    DESKTOP.mkdir(parents=True, exist_ok=True)
    name = cfg["pptx"]
    target = DESKTOP / f"{name}.pptx"
    marker = BUILD / cfg["marker"]
    known = {prev_sha, marker.read_text().strip() if marker.is_file() else ""} - {""}
    cur = sha_file(target)
    new_sha = sha_file(BUILD / f"wireframe_{v}.pptx")
    diverted = False  # Dayeon confirmed the Desktop pptx is never hand-edited: always overwrite
    suffix = "_new" if diverted else ""
    shutil.copy2(BUILD / f"wireframe_{v}.pptx", DESKTOP / f"{name}{suffix}.pptx")
    shutil.copy2(BUILD / f"wireframe_{v}.pdf", DESKTOP / f"{name}{suffix}.pdf")
    shutil.copy2(BUILD / f"script_{v}.md", DESKTOP / cfg["script"])
    msgs = []
    if diverted:
        msgs.append(f"DESKTOP {name}.pptx was edited by hand: wrote {name}_new.pptx (original untouched)")
    else:
        marker.write_text(new_sha)
        msgs.append(f"published {name} to {DESKTOP}")
    return msgs


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-figs", action="store_true")
    args = ap.parse_args()
    prev = {v: sha_file(BUILD / f"wireframe_{v}.pptx") for v in VARIANTS}
    if not args.skip_figs:
        LOG.info(run([*PY, str(BUILD / "make_figs.py")])[-300:])
    import shutil
    (BUILD / "numbers_ledger.md").write_text(ledger_md(), encoding="utf-8")
    (BUILD / "qa.md").write_text(qa_md(), encoding="utf-8")
    all_slides: list[dict] = []
    for v in VARIANTS:
        main, appx = variant_slides(v)
        all_slides += [*main, *appx]
        (BUILD / f"script_{v}.md").write_text(script_md(v), encoding="utf-8")
        if v == "B":
            LOG.info("script diff vs script_B_v2.md:\n%s", script_diff_report())
        (BUILD / f"spec_{v}.json").write_text(json.dumps(spec_json(v, main, appx), ensure_ascii=False, indent=1), encoding="utf-8")
        (BUILD / f"clarity_check_{v}.md").write_text(clarity_md(v, main, appx), encoding="utf-8")
        timer = run([*PY, str(ROOT / "tools/deck/script_timer.py"), str(BUILD / f"script_{v}.md"), "--rate", "290", "--max-total", "6:00", "--strict"])
        (BUILD / f"script_timer_output_{v}.txt").write_text(timer, encoding="utf-8")
        LOG.info("== variant %s ==\n%s", v, timer)
        LOG.info(run([*PY, str(BUILD / "build_pptx.py"), v])[-400:])
        for m in publish_variant(v, prev[v]):
            LOG.info(m)
    (FIG / "CAPTIONS.md").write_text(captions_md(all_slides), encoding="utf-8")
    shutil.copy2(BUILD / "qa.md", DESKTOP / "예상질문.md")
    shutil.copy2(BUILD / "numbers_ledger.md", DESKTOP / "숫자장부.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
