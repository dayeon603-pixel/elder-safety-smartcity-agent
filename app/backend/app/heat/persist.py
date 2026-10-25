"""Load the experiment's outputs into alert_run / alert / eval_metric (idempotent, immutable).

Inputs (experiments/heat/out/): test_predictions.csv.gz, results.json, panel.csv.gz (only for the
unit_id -> unit_key map; predictions key regions by the panel's integer unit_id).

Storage rules:
  * budget_kind `per_day` (K_d = that day's B1 alert count) is stored as `per_day_b1` and keeps
    every region-day cell (score + alerted) so ranks and score evidence are queryable;
    every other budget keeps only alerted=1 cells.
  * A run is keyed by (method, variant, test_year, budget_kind, dataset_sha, git_hash). A key that
    already exists is skipped whole, never rewritten; changed data or code yields a new run.
  * The whole load is one transaction.
Backtests are hypotheses, not proofs: the stored numbers describe 2023-2025 only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pandas as pd

from .. import db
from .common import IngestError, SEASON_END, SEASON_START, YEARS, nospace, resolve_paths, utc_now_iso

logger = logging.getLogger("heat.persist")

PRED_NAME, RESULTS_NAME, PANEL_NAME = "test_predictions.csv.gz", "results.json", "panel.csv.gz"
PRED_COLS = ("method", "variant", "test_year", "budget_kind", "date", "region_code", "score", "alerted")
METHOD_ORDER = ("B1", "B1S", "B2", "HEUR", "OURS")
BASELINE = "B1"
VARIANTS = ("F", "O")
BUDGET_MAP = {"per_day": "per_day_b1", "season_total": "season_total"}
ALL_CELLS_BUDGET = "per_day_b1"
BUDGET_SOURCE = "B1"  # results.json keys budgets as "<budget source method>|<kind>"
TRAIN_YEAR_MIN = YEARS[0]
NOGIT_PREFIX = "nogit-"
HASH_HEX_LEN = 12
SHA_LEN = 64


class PersistError(IngestError):
    """Raised when experiment outputs are inconsistent with the database."""


class LeakageError(AssertionError):
    """A stored alert or run violates the temporal split."""


def _canonical_sha(obj: object) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def run_identity(results: dict[str, Any]) -> tuple[str, str, int]:
    """(dataset_sha, git_hash, seed) for the runs of this results file.

    dataset_sha: results['dataset_sha'] if it is a 64-char hex, else the SHA-256 of the canonical
    JSON of results['data_sha256'] (the per-file hashes). git_hash: results['git_hash'], or, when
    the tree was not under git, `nogit-` + the first 12 hex of the hash of results['code_sha256']
    so edited code still yields a new run key.

    Raises:
        PersistError: if neither a dataset hash nor a seed is present.
    """
    declared = results.get("dataset_sha")
    if isinstance(declared, str) and len(declared) == SHA_LEN:
        dataset_sha = declared
    elif isinstance(results.get("data_sha256"), dict):
        dataset_sha = _canonical_sha(results["data_sha256"])
    else:
        raise PersistError("results.json has neither dataset_sha nor data_sha256")
    git = results.get("git_hash")
    if not git:
        git = NOGIT_PREFIX + _canonical_sha(results.get("code_sha256", {}))[:HASH_HEX_LEN]
    if "seed" not in results:
        raise PersistError("results.json has no seed")
    return dataset_sha, str(git), int(results["seed"])


def _ci(stat: dict[str, float]) -> tuple[float, float | None, float | None]:
    return float(stat["est"]), stat.get("lo"), stat.get("hi")


def _metric(
    name: str, stat: dict[str, float], n_boot: int, block: int, x: float | None = None
) -> tuple[str, float | None, int | None, float, float | None, float | None, int | None, int | None]:
    value, lo, hi = _ci(stat)
    if lo is not None and hi is not None and not lo <= value <= hi:
        raise PersistError(f"{name}: point estimate {value} outside its CI [{lo}, {hi}]")
    return (name, x, None, value, lo, hi, n_boot if lo is not None else None,
            block if lo is not None else None)


def _fold(results: dict[str, Any], variant: str, year: int) -> dict[str, Any]:
    try:
        fold: dict[str, Any] = results["variants"][variant]["folds"][str(year)]
    except KeyError as exc:
        raise PersistError(f"results.json has no fold {variant}/{year}") from exc
    return fold


def metric_rows(
    results: dict[str, Any], variant: str, year: int, method: str, raw_budget: str
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """(absolute rows, delta-vs-B1 rows) for one run, as eval_metric tuples without run/baseline ids.

    Absolute row = (metric, x, value, ci_lo, ci_hi, n_boot, block_days); delta rows have the same shape
    and are written with baseline_run_id = the B1 run. Rank metrics and the budget curve are stored
    on the per_day_b1 run only (they do not depend on the budget kind).
    """
    n_boot, block = int(results["n_boot"]), int(results["block_days"])
    fold = _fold(results, variant, year)
    key = f"{BUDGET_SOURCE}|{raw_budget}"
    try:
        budget = fold["budget"][key]
        m = budget["methods"][method]
    except KeyError as exc:
        raise PersistError(f"results.json lacks {variant}/{year}/{key}/{method}") from exc
    absolute: list[tuple[Any, ...]] = []
    delta: list[tuple[Any, ...]] = []

    def add(rows: list[tuple[Any, ...]], r: tuple[Any, ...]) -> None:
        name, x, _, value, lo, hi, nb, bd = r
        rows.append((name, x, value, lo, hi, nb, bd))

    add(absolute, _metric("capture", m["capture"], n_boot, block))
    add(absolute, _metric("precision", m["precision"], n_boot, block))
    absolute.append(("n_alerts", None, float(m["alerts"]), None, None, None, None))
    d = budget.get("delta", {}).get(f"{method}-{BASELINE}")
    if method != BASELINE and d is not None:
        add(delta, _metric("capture", d["capture"], n_boot, block))
        add(delta, _metric("precision", d["precision"], n_boot, block))
    if raw_budget == "per_day":
        rank = fold.get("rank", {}).get(method)
        if rank:
            add(absolute, _metric("auc", rank["auc"], n_boot, block))
            add(absolute, _metric("prauc", rank["pr_auc"], n_boot, block))
        if method == "OURS":
            rd = fold.get("rank_delta_OURS_minus", {}).get(BASELINE)
            if rd:
                add(delta, _metric("auc", rd["auc"], n_boot, block))
                add(delta, _metric("prauc", rd["pr_auc"], n_boot, block))
        curve = results["variants"][variant].get("sweep", {}).get(str(year), {}).get("curves", {})
        for pt in curve.get(method, []):
            absolute.append(("capture_curve", float(pt["scale"]), float(pt["capture"]),
                             None, None, None, None))
    return absolute, delta


def run_params(results: dict[str, Any], method: str, variant: str) -> dict[str, Any]:
    """Method hyper-parameters as recorded by the experiment (no tuning is done here)."""
    base: dict[str, Any] = {"input_variant_doc": results.get("variants_doc", {}).get(variant)}
    if method in ("B1", "B1S"):
        base["hot_c"] = results.get("hot_c")
    elif method in ("B2", "OURS"):
        base["hgb_params"] = results.get("hgb_params")
    elif method == "HEUR":
        base["heur_beta"] = results.get("heur_beta")
    return base


def load_unit_map(panel_path: Path) -> dict[int, str]:
    """unit_id -> unit_key ('sido|sgg') from the panel."""
    p = pd.read_csv(panel_path, usecols=["unit_id", "unit_key"]).drop_duplicates()
    if p["unit_id"].duplicated().any():
        raise PersistError("panel maps a unit_id to several unit_keys")
    return {int(i): str(k) for i, k in zip(p["unit_id"], p["unit_key"], strict=True)}


def region_code_map(conn: sqlite3.Connection, unit_map: dict[int, str]) -> dict[int, str]:
    """unit_id -> region.code via (sido, whitespace-free name)."""
    by_key = {f"{r['sido']}|{nospace(r['name'])}": r["code"]
              for r in conn.execute("SELECT code, sido, name FROM region")}
    missing = sorted(k for k in unit_map.values() if k not in by_key)
    if missing:
        raise PersistError(f"{len(missing)} panel units have no region (e.g. {missing[:3]}); "
                           "re-run ingest_regions")
    return {uid: by_key[k] for uid, k in unit_map.items()}


def load_predictions(pred_path: Path, unit_to_code: dict[int, str]) -> pd.DataFrame:
    """Read and validate test_predictions.csv.gz; region_code becomes the 5-digit region code and
    budget_kind the database enum.

    Raises:
        PersistError: on missing columns, unknown labels, or dates outside their test year/season.
    """
    p = pd.read_csv(pred_path, dtype={"method": str, "variant": str, "budget_kind": str, "date": str})
    absent = [c for c in PRED_COLS if c not in p.columns]
    if absent:
        raise PersistError(f"predictions lack columns {absent}")
    for col, allowed in (("method", METHOD_ORDER), ("variant", VARIANTS), ("budget_kind", tuple(BUDGET_MAP))):
        bad = set(p[col]) - set(allowed)
        if bad:
            raise PersistError(f"unknown {col} values {sorted(bad)}")
    no_score = p["score"].isna()
    if (no_score & (p["alerted"] == 1)).any():
        raise PersistError("an alerted cell has no score")
    if no_score.any():
        # Rule methods (B1/B1S) have no score where the station had no Tmax; never alerted.
        logger.warning("%d cells without a score (missing weather) are not stored", int(no_score.sum()))
        p = p[~no_score].copy()
    p["budget_kind"] = p["budget_kind"].map(BUDGET_MAP)
    codes = p["region_code"].map(unit_to_code)
    if codes.isna().any():
        raise PersistError("predictions reference unit_ids missing from the panel")
    p["region_code"] = codes
    if (p["date"].str[:4].astype(int) != p["test_year"]).any():
        raise PersistError("a prediction date lies outside its test_year")
    md = p["date"].str[5:7].astype(int) * 100 + p["date"].str[8:10].astype(int)
    if ((md < SEASON_START[0] * 100 + SEASON_START[1]) | (md > SEASON_END[0] * 100 + SEASON_END[1])).any():
        raise PersistError("a prediction date lies outside the season")
    if p.duplicated(["method", "variant", "test_year", "budget_kind", "date", "region_code"]).any():
        raise PersistError("duplicate prediction cells")
    return p


def assert_no_future_leakage(conn: sqlite3.Connection) -> None:
    """Fail if any stored run trains on or after its test year, or any alert leaves its test season.

    Raises:
        LeakageError: with the offending count.
    """
    checks = {
        "runs with train_year_max >= test_year":
            "SELECT COUNT(*) FROM alert_run WHERE train_year_max >= test_year",
        "alerts dated outside the run's test year":
            "SELECT COUNT(*) FROM alert a JOIN alert_run r ON r.id = a.run_id "
            "WHERE CAST(substr(a.date, 1, 4) AS INTEGER) <> r.test_year",
        "alerts dated outside the season window":
            "SELECT COUNT(*) FROM alert WHERE substr(date, 6, 5) < ? OR substr(date, 6, 5) > ?",
        "budget reference from another year/variant":
            "SELECT COUNT(*) FROM alert_run r JOIN alert_run b ON b.id = r.budget_ref_run_id "
            "WHERE b.test_year <> r.test_year OR b.variant <> r.variant",
    }
    season = (f"{SEASON_START[0]:02d}-{SEASON_START[1]:02d}", f"{SEASON_END[0]:02d}-{SEASON_END[1]:02d}")
    for label, sql in checks.items():
        args: tuple[str, ...] = season if "season window" in label else ()
        n = int(conn.execute(sql, args).fetchone()[0])
        if n:
            raise LeakageError(f"{n} {label}")


def persist(
    conn: sqlite3.Connection, pred_path: Path, results_path: Path, panel_path: Path
) -> dict[str, int]:
    """Idempotently store predictions + metrics; returns counters.

    Raises:
        PersistError: on inconsistent inputs (nothing is written then).
    """
    for path in (pred_path, results_path, panel_path):
        if not path.is_file():
            raise PersistError(f"missing experiment output: {path}")
    results = json.loads(results_path.read_text(encoding="utf-8"))
    dataset_sha, git, seed = run_identity(results)
    preds = load_predictions(pred_path, region_code_map(conn, load_unit_map(panel_path)))
    counts = {"runs_created": 0, "runs_existing": 0, "alerts": 0, "metrics": 0}
    now = utc_now_iso()
    order = {m: i for i, m in enumerate(METHOD_ORDER)}
    groups = sorted(
        preds.groupby(["method", "variant", "test_year", "budget_kind"], sort=False),
        key=lambda kv: (kv[0][1], kv[0][2], kv[0][3], order[kv[0][0]]),
    )
    raw_of = {v: k for k, v in BUDGET_MAP.items()}
    with db.transaction(conn):
        b1_id: dict[tuple[str, int, str], int] = {}
        for (method, variant, year, budget), g in groups:
            year = int(year)
            row = conn.execute(
                "SELECT id FROM alert_run WHERE method=? AND variant=? AND test_year=? AND "
                "budget_kind=? AND dataset_sha=? AND git_hash=?",
                (method, variant, year, budget, dataset_sha, git),
            ).fetchone()
            if method == BASELINE and row is not None:
                b1_id[(variant, year, budget)] = int(row["id"])
            if row is not None:
                counts["runs_existing"] += 1
                continue
            ref = None if method == BASELINE else b1_id.get((variant, year, budget))
            if method != BASELINE and ref is None:
                raise PersistError(f"no {BASELINE} run for {variant}/{year}/{budget} to reference")
            cur = conn.execute(
                "INSERT INTO alert_run (method, variant, train_year_min, train_year_max, test_year, "
                "budget_kind, budget_ref_run_id, params, seed, dataset_sha, git_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (method, variant, TRAIN_YEAR_MIN, year - 1, year, budget, ref,
                 json.dumps(run_params(results, method, variant), sort_keys=True, ensure_ascii=False),
                 seed, dataset_sha, git, now),
            )
            run_id = int(cur.lastrowid or 0)
            if method == BASELINE:
                b1_id[(variant, year, budget)] = run_id
            cells = g if budget == ALL_CELLS_BUDGET else g[g["alerted"] == 1]
            conn.executemany(
                "INSERT INTO alert (run_id, date, region_code, score, alerted) VALUES (?, ?, ?, ?, ?)",
                zip([run_id] * len(cells), cells["date"], cells["region_code"],
                    cells["score"].astype(float).tolist(), cells["alerted"].astype(int).tolist(),
                    strict=True),
            )
            counts["alerts"] += len(cells)
            absolute, delta = metric_rows(results, variant, year, method, raw_of[budget])
            base = b1_id[(variant, year, budget)]
            conn.executemany(
                "INSERT INTO eval_metric (run_id, metric, x, baseline_run_id, value, ci_lo, ci_hi, "
                "n_boot, block_days) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(run_id, r[0], r[1], None, r[2], r[3], r[4], r[5], r[6]) for r in absolute]
                + [(run_id, r[0], r[1], base, r[2], r[3], r[4], r[5], r[6]) for r in delta],
            )
            counts["metrics"] += len(absolute) + len(delta)
            counts["runs_created"] += 1
        assert_no_future_leakage(conn)
    logger.info("persist: %s", counts)
    return counts


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: python -m app.heat.persist [--pred P --results R --panel N]."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    paths = resolve_paths()
    ap = argparse.ArgumentParser(prog="app.heat.persist", description=__doc__)
    ap.add_argument("--pred", type=Path, default=paths.out_dir / PRED_NAME)
    ap.add_argument("--results", type=Path, default=paths.out_dir / RESULTS_NAME)
    ap.add_argument("--panel", type=Path, default=paths.out_dir / PANEL_NAME)
    args = ap.parse_args(argv)
    conn = db.connect(paths.db_path)
    try:
        db.migrate(conn)
        persist(conn, args.pred, args.results, args.panel)
    except (IngestError, LeakageError, sqlite3.Error) as exc:
        logger.error("%s", exc)
        return 2
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
