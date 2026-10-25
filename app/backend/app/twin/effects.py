"""The four ★ effect numbers of the demo (언제 · 어디에 · 무엇을 · 밤), read from the same result files the deck ledger uses.

Nothing is computed here beyond rounding: heat from experiments/heat/out/results.json (the file /heat/verify re-derives),
silver from the silver module tables (Seoul-wide 2024 hotspots), night AED reach from experiments/walk4/out/opt.json, fall
latency from experiments/fall/results (rule vs the shipped descent-gated model, gate read from the model file the frontend
ships). A missing file yields `available: false` with null values, never a substitute number.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

from ..config import APP_ROOT
from ..heat.pooled import results_path
from . import state as twin_state

logger = logging.getLogger("twin.effects")
WALK4_OPT = APP_ROOT / "backend" / "experiments" / "walk4" / "out" / "opt.json"
FALL_CV = APP_ROOT / "backend" / "experiments" / "fall" / "results" / "cv_summary.json"
FALL_GATE = APP_ROOT / "backend" / "experiments" / "fall" / "results" / "opt" / "gate" / "gate_summary.json"
FALL_MODEL = APP_ROOT / "frontend" / "public" / "models" / "fall_model.json"
SILVER_COVERAGE = APP_ROOT / "backend" / "experiments" / "silver" / "out" / "opt" / "promoted" / "coverage_results.csv"
SILVER_VARIANT, SILVER_STRATUM, SILVER_K = "harm", "all", 3918
SILVER_ROWS = {"current": "C", "ours_opt": "OPT"}  # method -> pool
HEAT_VARIANT = "F"
HEAT_BUDGET_KEY = "B1|per_day"
POOLED_PREFIX = "pooled_"
WALK4_BUDGET = "115"  # 서울 AED 1 % (115대) 24시간 개방
FALL_GATE_KEY_TMPL = "ur_cv:gate={gate:g}"
_LOCK = threading.Lock()
_CACHE: dict[str, dict[str, Any]] = {}


def _load(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("effects: cannot read %s: %s", path, exc)
        return None
    return doc if isinstance(doc, dict) else None


def _item(key: str, label: str, baseline: float | None, ours: float | None, unit: str, scope: str, source: str, display_source: str,
          **extra: Any) -> dict[str, Any]:
    return {"key": key, "label": label, "baseline": baseline, "ours": ours, "unit": unit, "scope": scope, "source": source,
            "display_source": display_source, "available": baseline is not None and ours is not None, **extra}


def heat_effect() -> dict[str, Any]:
    """언제: pooled 2022-25 capture at B1's per-day budget, variant F (B1 vs OURS), from results.json."""
    path = results_path()
    doc = _load(path)
    b1 = ours = None
    years: list[int] | None = None
    if doc is not None:
        variant = doc.get("variants", {}).get(HEAT_VARIANT, {})
        pooled_key = next((k for k in variant if k.startswith(POOLED_PREFIX)), None)
        blk = variant.get(pooled_key, {}).get(HEAT_BUDGET_KEY) if pooled_key else None
        if blk:
            b1 = round(100 * float(blk["methods"]["B1"]["capture"]["est"]), 1)
            ours = round(100 * float(blk["methods"]["OURS"]["capture"]["est"]), 1)
            years = [int(y) for y in doc.get("test_years", [])]
    return _item("when", "같은 경보 수로 온열질환자 포착", b1, ours, "%",
                 f"{years[0]}–{years[-1]} 통합, 특보 기준(B1)과 같은 하루 경보 수" if years else "통합 평가",
                 f"{path.name} variants.{HEAT_VARIANT}.pooled.{HEAT_BUDGET_KEY}", "질병관리청 온열질환 신고 × 기상청 ASOS, 저장된 예측 재현 (/heat/verify)",
                 baseline_label="현행 특보 기준", ours_label="에이전트 권고")


def _pct100(v: float) -> float:
    return round(100 * v, 1)


def _silver_rows(conn: sqlite3.Connection) -> dict[str, dict[str, float]]:
    """{method: {coverage, lo, hi, n_hot}} for the same-count national result, from the CSV (else the silver_result table)."""
    out: dict[str, dict[str, float]] = {}
    if SILVER_COVERAGE.is_file():
        import csv

        with SILVER_COVERAGE.open(encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                try:
                    if (r["variant"], r["stratum"], int(r["k"])) != (SILVER_VARIANT, SILVER_STRATUM, SILVER_K):
                        continue
                    if SILVER_ROWS.get(r["method"]) == r["pool"] and r["method"] not in out:
                        out[r["method"]] = {"coverage": float(r["coverage"]), "lo": float(r["lo"]), "hi": float(r["hi"]), "n_hot": float(r["n_hot"])}
                except (KeyError, ValueError):
                    continue
    if len(out) < len(SILVER_ROWS):
        for r in conn.execute("SELECT method, pool, coverage, ci_lo, ci_hi, n_hot FROM silver_result WHERE variant = ? AND stratum = ? AND k = ?",
                              (SILVER_VARIANT, SILVER_STRATUM, SILVER_K)):
            if SILVER_ROWS.get(str(r["method"])) == str(r["pool"]) and str(r["method"]) not in out:
                out[str(r["method"])] = {"coverage": float(r["coverage"]), "lo": float(r["ci_lo"]), "hi": float(r["ci_hi"]), "n_hot": float(r["n_hot"])}
    return out


def silver_effect(conn: sqlite3.Connection) -> dict[str, Any]:
    """어디에: national same-count result (k = 3,918 zones): hotspots of the next year within 300 m, current vs ours_opt.

    Same count is the fair comparison (within Seoul the agent places ~1,400 zones against 194 current ones); the Seoul 9 → 58
    picture stays in /twin/state.silver_eval_seoul with the zone counts for an honest caption.
    """
    rows = _silver_rows(conn)
    cur, ours = rows.get("current"), rows.get("ours_opt")
    pct = _pct100
    return _item("where", "같은 3,918곳으로 다음 해 노인 사고 지점 포착", pct(cur["coverage"]) if cur else None, pct(ours["coverage"]) if ours else None, "%",
                 "전국 같은 3,918곳 · 2023까지 선정 · 2024 평가",
                 f"{SILVER_COVERAGE.name} variant={SILVER_VARIANT} stratum={SILVER_STRATUM} k={SILVER_K} (current pool C / ours_opt pool OPT)",
                 "TAAS 노인 보행사고 다발지 · 2023년까지 자료로 선정, 2024년으로 평가 (현행 실버존 vs 에이전트 제안, 같은 개수)",
                 baseline_ci=[pct(cur["lo"]), pct(cur["hi"])] if cur else None, ours_ci=[pct(ours["lo"]), pct(ours["hi"])] if ours else None,
                 n_hotspots=int(cur["n_hot"]) if cur else None, k=SILVER_K, baseline_label="현행 실버존 (같은 개수)", ours_label="에이전트 제안")


def night_effect() -> dict[str, Any]:
    """밤: share of 65+ within a 2-minute night walk of an open AED, base vs 115 conversions, from walk4 opt.json."""
    doc = _load(WALK4_OPT)
    base = ours = rnd = None
    if doc is not None:
        try:
            base = round(100 * float(doc["base"]["night"]["p65"]), 1)
            ours = round(100 * float(doc["convert"][WALK4_BUDGET]["exact"]["night"]["p65"]), 1)
            r = doc.get("random", {}).get(WALK4_BUDGET)
            rnd = round(100 * (float(doc["base"]["night"]["p65"]) + float(r["mean"])), 1) if r else None
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("effects: walk4 opt.json shape: %s", exc)
    return _item("night", "밤에 걸어서 2분 안에 열린 AED가 있는 65세 이상", base, ours, "%",
                 "서울 AED 1%(115대) 24시간 개방 시, 밤 2분 도달 65세 이상 (모형 추정)", f"{WALK4_OPT.name} base.night.p65 → convert.{WALK4_BUDGET}.exact.night.p65",
                 "보행망 분석 (에이전트) · OSM 보행망 0.8 m/s 2분 · 행정동 65+ 인구 · AED 운영시간표",
                 random_115=rnd, baseline_label="현행 운영시간", ours_label="에이전트 선정 115대 24시간")


def fall_effect() -> dict[str, Any]:
    """무엇을: median detection latency, rule vs the shipped descent-gated model (gate from the frontend model file)."""
    cv, gates, model = _load(FALL_CV), _load(FALL_GATE), _load(FALL_MODEL)
    rule = ours = None
    gate_val: float | None = None
    if cv is not None:
        try:
            rule = round(float(cv["rule"]["lat_med"][0]), 2)
        except (KeyError, TypeError, ValueError, IndexError):
            rule = None
    if gates is not None and model is not None:
        try:
            gate_val = float(model["descent_gate"]["min"])
            ours = round(float(gates[FALL_GATE_KEY_TMPL.format(gate=gate_val)]["lat_med"][0]), 2)
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            logger.warning("effects: fall gate shape: %s", exc)
    return _item("what", "낙상 감지까지 걸린 시간 (중앙값)", rule, ours, "초", "실험실(UR Fall) 교차검증",
                 f"{FALL_CV.name} rule.lat_med[0] → {FALL_GATE.name} ur_cv:gate={gate_val} lat_med[0] (gate = {FALL_MODEL.name} descent_gate.min)",
                 "UR Fall 공개 영상 교차검증 · 자세 추정 + 하강 게이트 모델", gate=gate_val, baseline_label="규칙 기반", ours_label="에이전트 모델",
                 lower_is_better=True)


def effects(conn: sqlite3.Connection) -> dict[str, Any]:
    """All four chips in the ★ order (언제 · 어디에 · 무엇을 · 밤); cached per process."""
    with _LOCK:
        hit = _CACHE.get("effects")
    if hit is not None:
        return hit
    doc = {"items": [heat_effect(), silver_effect(conn), fall_effect(), night_effect()],
           "order": ["when", "where", "what", "night"], "note": "덱 숫자장부와 같은 결과 파일에서 생성; 값은 반올림만 한다",
           "fetched_at": twin_state.fetched_at(conn)}
    with _LOCK:
        _CACHE["effects"] = doc
    return doc


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


__all__ = ["clear_cache", "effects", "fall_effect", "heat_effect", "night_effect", "silver_effect"]
