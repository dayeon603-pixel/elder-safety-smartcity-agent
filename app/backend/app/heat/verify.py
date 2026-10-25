"""재현 (live re-computation) of the headline heat result from stored per-day predictions, no model refit.

Reads, per variant (F, O) and test year, the latest stored B1 and OURS runs (`alert`: score + alerted per 시군구-day at
the equal per-day budget K_d = B1's alert count) and the KDCA ground truth (`heat_case_daily`), recomputes the pooled
2022-2025 capture (cases inside alerted cells / all cases) and the paired circular moving-block bootstrap CI
(7-day blocks, 1000 reps, seed 42) with the same draw order as experiments/heat/evaluate.py, then compares with
experiments/heat/out/results.json. Runs in a few seconds; prints large plain Korean lines for a projector terminal.

    python -m app.heat.verify          (make verify-heat)        GET /heat/verify
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from .. import db, repo_heat
from ..config import load_settings
from .common import EXPERIMENT_OUT_DIR
from .pooled import results_path

logger = logging.getLogger("heat.verify")
FloatArray = npt.NDArray[np.float64]
VARIANTS: tuple[str, ...] = ("F", "O")
N_BOOT_DEFAULT = 1000
BLOCK_DAYS = 7
SEED = 42
ALPHA = 0.05
BUDGET_KEY = "B1|per_day"
POOLED_KEY_PREFIX = "pooled_"
TOL_EST = 1e-9  # same integers divided -> identical floats; anything larger means different inputs
TOL_CI = 1e-9  # identical draws -> identical percentiles
PRED_FILE = "test_predictions.csv.gz"
PANEL_FILE = "panel.csv.gz"
VARIANT_LABEL = {"F": "예보 대용 (당일 체감 포함)", "O": "관측만 (전날까지)"}


class VerifyError(RuntimeError):
    """The database has no stored runs to verify against."""


def block_weights(nd: int, rng: np.random.Generator, reps: int, block: int) -> FloatArray:
    """Circular moving-block bootstrap multiplicities (reps x nd); byte-for-byte the experiment's draw."""
    nb = int(np.ceil(nd / block))
    starts = rng.integers(0, nd, size=(reps, nb))
    idx = ((starts[:, :, None] + np.arange(block)[None, None, :]) % nd).reshape(reps, -1)[:, :nd]
    w = np.zeros((reps, nd))
    np.add.at(w, (np.repeat(np.arange(reps), nd), idx.ravel()), 1.0)
    return w


def per_day(conn: sqlite3.Connection, run_id: int) -> tuple[list[str], FloatArray, FloatArray]:
    """(dates, cases per day over the run's cells, cases caught per day) of one stored run."""
    rows = conn.execute(
        "SELECT a.date AS d, SUM(COALESCE(c.n_cases, 0)) AS cases, SUM(COALESCE(c.n_cases, 0) * a.alerted) AS caught "
        "FROM alert a LEFT JOIN heat_case_daily c ON c.region_code = a.region_code AND c.date = a.date "
        "WHERE a.run_id = ? GROUP BY a.date ORDER BY a.date", (run_id,),
    ).fetchall()
    dates = [str(r["d"]) for r in rows]
    return dates, np.array([float(r["cases"]) for r in rows]), np.array([float(r["caught"]) for r in rows])


def _sha(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _pp(x: float) -> str:
    return f"{100 * x:+.1f}%p"


def verify(conn: sqlite3.Connection, n_boot: int = N_BOOT_DEFAULT, results: Path | None = None) -> dict[str, Any]:
    """Recompute and compare. Never refits; reads only stored runs, cases and results.json.

    Raises:
        VerifyError: no completed runs for a variant.
    """
    t0 = time.perf_counter()
    rpath = results or results_path()
    stored = json.loads(rpath.read_text(encoding="utf-8")) if rpath.is_file() else None
    rng = np.random.default_rng(SEED)
    out_variants: dict[str, Any] = {}
    all_match = True
    pooled_seen = False
    lines: list[str] = []
    skipped: list[str] = []
    for variant in VARIANTS:
        years = repo_heat.completed_years(conn, variant)
        if not years:
            skipped.append(variant)
            continue
        parts: list[dict[str, Any]] = []
        for year in years:
            b1 = repo_heat.latest_run_id(conn, repo_heat.BASELINE, variant, year)
            ours = repo_heat.latest_run_id(conn, repo_heat.OURS, variant, year)
            if b1 is None or ours is None:
                raise VerifyError(f"variant {variant} {year}: B1/OURS pair missing")
            dates, cases, caught_b1 = per_day(conn, b1)
            dates_o, cases_o, caught_ours = per_day(conn, ours)
            if dates != dates_o or not np.array_equal(cases, cases_o):
                raise VerifyError(f"variant {variant} {year}: B1 and OURS runs cover different cells/days")
            parts.append({"year": year, "nd": len(dates), "cases": cases, "b1": caught_b1, "ours": caught_ours,
                          "runs": {"B1": b1, "OURS": ours}})
        # the experiment draws one fold matrix per year first (used for per-year CIs), then the pooled matrices
        for p in parts:
            block_weights(p["nd"], rng, n_boot, BLOCK_DAYS)
        for p in parts:
            p["W"] = block_weights(p["nd"], rng, n_boot, BLOCK_DAYS)
        n_cases = float(sum(p["cases"].sum() for p in parts))
        nb = sum(p["W"] @ p["cases"] for p in parts)
        est: dict[str, float] = {}
        boot: dict[str, FloatArray] = {}
        for m in ("b1", "ours"):
            est[m] = float(sum(p[m].sum() for p in parts)) / n_cases
            boot[m] = sum(p["W"] @ p[m] for p in parts) / nb
        delta_boot = boot["ours"] - boot["b1"]
        lo, hi = (float(v) for v in np.nanpercentile(delta_boot, [100 * ALPHA / 2, 100 * (1 - ALPHA / 2)]))
        delta = est["ours"] - est["b1"]
        rec: dict[str, Any] = {
            "years": years, "n_cases": int(n_cases), "n_days": int(sum(p["nd"] for p in parts)),
            "b1_capture": est["b1"], "ours_capture": est["ours"], "delta": delta, "delta_ci": [lo, hi],
            "share_boot_delta_le_0": float(np.mean(delta_boot <= 0)), "n_boot": n_boot, "block_days": BLOCK_DAYS, "seed": SEED,
            "runs": {str(p["year"]): p["runs"] for p in parts}, "stored": None, "match": None,
        }
        if stored is not None:
            pooled_key = next((k for k in stored["variants"][variant] if k.startswith(POOLED_KEY_PREFIX)), None)
            if pooled_key is not None:
                pooled_seen = True
                blk = stored["variants"][variant][pooled_key][BUDGET_KEY]
                s_b1, s_ours = blk["methods"]["B1"]["capture"]["est"], blk["methods"]["OURS"]["capture"]["est"]
                s_d = blk["delta"]["OURS-B1"]["capture"]
                same_boot = stored.get("n_boot") == n_boot and stored.get("block_days") == BLOCK_DAYS and stored.get("seed") == SEED
                match = (abs(s_b1 - est["b1"]) <= TOL_EST and abs(s_ours - est["ours"]) <= TOL_EST and abs(s_d["est"] - delta) <= TOL_EST
                         and (not same_boot or (abs(s_d["lo"] - lo) <= TOL_CI and abs(s_d["hi"] - hi) <= TOL_CI)))
                rec["stored"] = {"b1_capture": s_b1, "ours_capture": s_ours, "delta": s_d["est"], "delta_ci": [s_d["lo"], s_d["hi"]],
                                 "pooled_key": pooled_key, "n_boot": stored.get("n_boot"), "ci_comparable": same_boot}
                rec["match"] = match
                all_match &= match
        out_variants[variant] = rec
        tag = "일치" if rec["match"] else ("비교 불가" if rec["match"] is None else "불일치")
        lines.append(f"[{variant}] {VARIANT_LABEL[variant]}  {years[0]}~{years[-1]}  환자 {int(n_cases):,}명  {rec['n_days']}일")
        lines.append(f"    특보 기준(B1) 포착 {_pct(est['b1'])}  →  에이전트 {_pct(est['ours'])}   차이 {_pp(delta)}  [{_pp(lo)}, {_pp(hi)}]"
                     f"  (95% CI, 7일 블록 붓스트랩 {n_boot}회)")
        lines.append(f"    results.json 과 {tag}" + (f": 저장값 {_pct(rec['stored']['b1_capture'])} → {_pct(rec['stored']['ours_capture'])}, "
                                                   f"{_pp(rec['stored']['delta'])} [{_pp(rec['stored']['delta_ci'][0])}, {_pp(rec['stored']['delta_ci'][1])}]"
                                                   if rec["stored"] else ""))
    if not out_variants:
        raise VerifyError("no completed runs for any variant (make heat-eval)")
    dataset_shas = sorted({str(r["dataset_sha"]) for r in conn.execute("SELECT DISTINCT dataset_sha FROM alert_run")})
    results_sha, pred_sha = _sha(rpath), _sha(EXPERIMENT_OUT_DIR / PRED_FILE)
    shas: dict[str, Any] = {
        "results.json": results_sha, PRED_FILE: pred_sha, PANEL_FILE: _sha(EXPERIMENT_OUT_DIR / PANEL_FILE),
        "alert_run.dataset_sha": dataset_shas,
        "datasets": {str(r["name"]): str(r["sha256"]) for r in conn.execute(
            "SELECT name, sha256 FROM datasets WHERE name IN ('heat_illness_cases', 'heat_kma_asos_hourly', 'heat_pop_by_dong') ORDER BY name")},
    }
    secs = time.perf_counter() - t0
    if stored is None:
        status = "no_results_json"
    elif not pooled_seen:
        status = "no_pooled_block"
    else:
        status = "ok" if all_match else "mismatch"
    head = [
        "고령자 안전 스마트도시 에이전트 · 폭염 경보 재현 (저장된 예측 + 질병관리청 신고 + 특보 기준, 모델 재학습 없음)",
        f"입력 해시  results.json {str(results_sha)[:12]}…  predictions {str(pred_sha)[:12]}…  "
        f"dataset_sha {', '.join(x[:12] + '…' for x in dataset_shas)}",
    ]
    verdict = {"ok": "결과 일치 ✔", "mismatch": "결과 불일치 ✘", "no_results_json": "results.json 없음 (비교 생략)",
               "no_pooled_block": "results.json 에 통합(pooled) 블록 없음 (비교 생략)"}[status]
    tail = [f"판정: {verdict}   소요 {secs:.1f}초"]
    if skipped:
        lines.append(f"    (저장된 실행 없음: variant {', '.join(skipped)})")
    return {"status": status, "variants": out_variants, "skipped_variants": skipped, "sha256": shas, "seconds": round(secs, 2),
            "method": "pooled capture at B1's per-day budget; paired circular moving-block bootstrap (shared draws), "
                      "draw order identical to experiments/heat/evaluate.py (fold matrices first, then pooled)",
            "lines": head + lines + tail}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    n_boot = int(args[0]) if args else N_BOOT_DEFAULT
    logging.basicConfig(level=logging.WARNING)
    settings = load_settings()
    conn = db.connect(settings.db_path)
    try:
        rep = verify(conn, n_boot)
    except VerifyError as exc:
        sys.stdout.write(f"재현 불가: {exc}\n")
        return 2
    finally:
        conn.close()
    sys.stdout.write("\n" + "\n".join(rep["lines"]) + "\n\n")
    return 0 if rep["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["BLOCK_DAYS", "N_BOOT_DEFAULT", "SEED", "VerifyError", "block_weights", "per_day", "verify"]
