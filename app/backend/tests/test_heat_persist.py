"""persist: idempotence, storage rules, temporal-leakage helper, failure modes."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from app.heat import persist
from tests import heat_fixture as fx


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_persist_stores_runs_and_is_idempotent(conn: sqlite3.Connection, tmp_path: Path) -> None:
    first = fx.build_heat_db(conn, tmp_path)
    assert first == {"runs_created": 4, "runs_existing": 0, "alerts": 24, "metrics": 26}
    n_alert, n_metric, n_run = _count(conn, "alert"), _count(conn, "eval_metric"), _count(conn, "alert_run")
    assert (n_run, n_metric) == (4, 26)
    pred, res, panel = tmp_path / persist.PRED_NAME, tmp_path / persist.RESULTS_NAME, tmp_path / persist.PANEL_NAME
    again = persist.persist(conn, pred, res, panel)
    assert again == {"runs_created": 0, "runs_existing": 4, "alerts": 0, "metrics": 0}
    assert (_count(conn, "alert"), _count(conn, "eval_metric"), _count(conn, "alert_run")) == (n_alert, n_metric, n_run)


def test_per_day_b1_keeps_all_cells_others_only_alerted(conn: sqlite3.Connection, tmp_path: Path) -> None:
    fx.build_heat_db(conn, tmp_path)
    rows = conn.execute(
        "SELECT r.budget_kind AS b, COUNT(*) AS n, SUM(a.alerted) AS k FROM alert a "
        "JOIN alert_run r ON r.id = a.run_id WHERE r.method = 'OURS' GROUP BY r.budget_kind"
    ).fetchall()
    got = {r["b"]: (r["n"], r["k"]) for r in rows}
    assert got == {"per_day_b1": (9, 3), "season_total": (3, 3)}


def test_budget_reference_and_metric_shapes(conn: sqlite3.Connection, tmp_path: Path) -> None:
    fx.build_heat_db(conn, tmp_path)
    b1 = conn.execute("SELECT id FROM alert_run WHERE method='B1' AND budget_kind='per_day_b1'").fetchone()["id"]
    ours = conn.execute("SELECT id, budget_ref_run_id AS ref, params FROM alert_run WHERE method='OURS' AND budget_kind='per_day_b1'").fetchone()
    assert ours["ref"] == b1 and json.loads(ours["params"])["hgb_params"] == {"loss": "poisson"}
    d = conn.execute("SELECT value, ci_lo, ci_hi, n_boot, block_days FROM eval_metric WHERE run_id=? AND metric='capture' AND baseline_run_id=?", (ours["id"], b1)).fetchone()
    assert (d["value"], d["ci_lo"], d["ci_hi"], d["n_boot"], d["block_days"]) == (0.35, 0.1, 0.5, 1000, 7)
    assert conn.execute("SELECT COUNT(*) FROM eval_metric WHERE run_id=? AND metric='capture_curve'", (ours["id"],)).fetchone()[0] == 2
    n = conn.execute("SELECT ci_lo, n_boot FROM eval_metric WHERE run_id=? AND metric='n_alerts'", (ours["id"],)).fetchone()
    assert n["ci_lo"] is None and n["n_boot"] is None
    run = conn.execute("SELECT dataset_sha, git_hash FROM alert_run LIMIT 1").fetchone()
    assert len(run["dataset_sha"]) == 64 and run["git_hash"].startswith(persist.NOGIT_PREFIX)


def test_changed_code_creates_new_run_generation(conn: sqlite3.Connection, tmp_path: Path) -> None:
    fx.build_heat_db(conn, tmp_path)
    pred, res, panel = tmp_path / persist.PRED_NAME, tmp_path / persist.RESULTS_NAME, tmp_path / persist.PANEL_NAME
    r = json.loads(res.read_text(encoding="utf-8"))
    r["code_sha256"] = {"evaluate.py": "e" * 64}
    res.write_text(json.dumps(r), encoding="utf-8")
    out = persist.persist(conn, pred, res, panel)
    assert out["runs_created"] == 4 and _count(conn, "alert_run") == 8


def test_cells_without_score_are_skipped_but_never_alerted(conn: sqlite3.Connection, tmp_path: Path) -> None:
    fx.build_heat_db(conn, tmp_path, nan_b1_cell=True)
    assert conn.execute(
        "SELECT COUNT(*) FROM alert a JOIN alert_run r ON r.id=a.run_id "
        "WHERE r.method='B1' AND r.budget_kind='per_day_b1'").fetchone()[0] == 8


def test_alerted_cell_without_score_is_an_error(conn: sqlite3.Connection, tmp_path: Path) -> None:
    fx.seed_base(conn)
    pred, res, panel = fx.write_experiment_outputs(tmp_path)
    df = fx.prediction_frame()
    df.loc[(df.method == "B1") & (df.alerted == 1), "score"] = float("nan")
    df.to_csv(pred, index=False)
    with pytest.raises(persist.PersistError, match="no score"):
        persist.persist(conn, pred, res, panel)
    assert _count(conn, "alert_run") == 0  # nothing written


def test_unknown_unit_rolls_back(conn: sqlite3.Connection, tmp_path: Path) -> None:
    fx.seed_base(conn)
    pred, res, panel = fx.write_experiment_outputs(tmp_path)
    pd.DataFrame({"unit_id": [0, 1, 2], "unit_key": ["서울특별시|종로구", "서울특별시|중구", "없는도|없는구"]}).to_csv(panel, index=False)
    with pytest.raises(persist.PersistError, match="no region"):
        persist.persist(conn, pred, res, panel)
    assert _count(conn, "alert_run") == 0


def test_date_outside_test_year_or_season_rejected(conn: sqlite3.Connection, tmp_path: Path) -> None:
    fx.seed_base(conn)
    pred, res, panel = fx.write_experiment_outputs(tmp_path)
    df = fx.prediction_frame()
    df.loc[0, "date"] = "2025-07-01"
    df.to_csv(pred, index=False)
    with pytest.raises(persist.PersistError, match="outside its test_year"):
        persist.persist(conn, pred, res, panel)
    df = fx.prediction_frame()
    df.loc[0, "date"] = "2024-10-01"
    df.to_csv(pred, index=False)
    with pytest.raises(persist.PersistError, match="outside the season"):
        persist.persist(conn, pred, res, panel)


def test_point_estimate_outside_ci_is_an_error(conn: sqlite3.Connection, tmp_path: Path) -> None:
    fx.seed_base(conn)
    pred, res, panel = fx.write_experiment_outputs(tmp_path)
    r = json.loads(res.read_text(encoding="utf-8"))
    r["variants"]["F"]["folds"]["2024"]["budget"]["B1|per_day"]["methods"]["B1"]["capture"] = {"est": 0.9, "lo": 0.1, "hi": 0.4}
    res.write_text(json.dumps(r), encoding="utf-8")
    with pytest.raises(persist.PersistError, match="outside its CI"):
        persist.persist(conn, pred, res, panel)
    assert _count(conn, "alert_run") == 0


def test_no_future_leakage_helper_flags_violations(conn: sqlite3.Connection, tmp_path: Path) -> None:
    fx.build_heat_db(conn, tmp_path)
    persist.assert_no_future_leakage(conn)  # clean data passes
    run = conn.execute("SELECT id FROM alert_run WHERE method='OURS' AND budget_kind='season_total'").fetchone()["id"]
    conn.execute("INSERT INTO alert VALUES (?, '2025-07-01', '11110', 1.0, 1)", (run,))
    with pytest.raises(persist.LeakageError, match="outside the run's test year"):
        persist.assert_no_future_leakage(conn)
    conn.execute("DELETE FROM alert WHERE date = '2025-07-01'")
    conn.execute("INSERT INTO alert VALUES (?, '2024-10-02', '11110', 1.0, 1)", (run,))
    with pytest.raises(persist.LeakageError, match="season"):
        persist.assert_no_future_leakage(conn)


def test_run_identity_prefers_declared_sha_and_requires_seed() -> None:
    sha = "f" * 64
    assert persist.run_identity({"dataset_sha": sha, "git_hash": "abc", "seed": 1}) == (sha, "abc", 1)
    with pytest.raises(persist.PersistError):
        persist.run_identity({"seed": 1})
    with pytest.raises(persist.PersistError):
        persist.run_identity({"dataset_sha": sha})
