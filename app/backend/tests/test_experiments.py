"""Experiment harness tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray
from sklearn.datasets import load_breast_cancer

from experiments.run_experiment import (
    ExperimentConfig, Method, bootstrap, compute_metrics, expected_calibration_error,
    run_experiment, split_indices,
)

F = NDArray[np.float64]
IA = NDArray[np.int64]
RNG = np.random.default_rng(0)


def _data() -> tuple[F, IA]:
    b = load_breast_cancer()
    return b.data.astype(np.float64), b.target.astype(np.int64)


def _prior(xt: F, yt: IA, xe: F, rng: np.random.Generator) -> F:
    return np.full(len(xe), float(yt.mean()))


def _oracle_ish(xt: F, yt: IA, xe: F, rng: np.random.Generator) -> F:
    z = (xe[:, 0] - xt[:, 0].mean()) / xt[:, 0].std()
    return np.asarray(1 / (1 + np.exp(2.0 * z)))


def test_random_split_deterministic_stratified_disjoint() -> None:
    _, y = _data()
    cfg = ExperimentConfig("t", seed=1)
    a = split_indices(y, cfg)
    b = split_indices(y, cfg)
    assert all(np.array_equal(i, j) for i, j in zip(a, b))
    assert not set(a[0]) & set(a[1]) and len(a[0]) + len(a[1]) == len(y)
    assert abs(y[a[1]].mean() - y.mean()) < 0.02
    assert not np.array_equal(a[1], split_indices(y, ExperimentConfig("t", seed=2))[1])


def test_temporal_split_no_lookahead() -> None:
    y = np.array([0, 1] * 50)
    times = np.random.default_rng(0).permutation(100)
    tr, te = split_indices(y, ExperimentConfig("t", temporal=True, test_size=0.2), times)
    assert times[tr].max() < times[te].min() and len(te) == 20
    with pytest.raises(ValueError, match="requires"):
        split_indices(y, ExperimentConfig("t", temporal=True))


def test_config_validation() -> None:
    for kw in ({"test_size": 0.0}, {"test_size": 1.0}, {"n_boot": 5}, {"ci_level": 1.5}):
        with pytest.raises(ValueError):
            ExperimentConfig("t", **kw)


def test_metrics_known_values() -> None:
    y = np.array([0, 0, 1, 1])
    perfect = compute_metrics(y, np.array([0.1, 0.2, 0.8, 0.9]))
    assert perfect["auroc"] == 1.0 and perfect["accuracy"] == 1.0 and perfect["f1"] == 1.0
    assert expected_calibration_error(np.array([0, 1]), np.array([0.0, 1.0])) == 0.0
    assert expected_calibration_error(np.array([0, 0]), np.array([1.0, 1.0])) == pytest.approx(1.0)


def test_bootstrap_ci_contains_point_and_orders_methods() -> None:
    x, y = _data()
    cfg = ExperimentConfig("t", n_boot=200)
    tr, te = split_indices(y, cfg)
    pb, po = _prior(x[tr], y[tr], x[te], RNG), _oracle_ish(x[tr], y[tr], x[te], RNG)
    out = bootstrap(y[te], pb, po, cfg)
    for role in ("baseline", "ours"):
        for m, v in out[role].items():
            assert v["lo"] <= v["hi"], m
    a = out["ours"]["auroc"]
    assert a["lo"] <= a["point"] <= a["hi"]
    assert out["delta"]["auroc"]["lo"] > 0  # informative method beats constant prior


def test_run_experiment_end_to_end_persists(tmp_path: Path, conn: sqlite3.Connection) -> None:
    x, y = _data()
    cfg = ExperimentConfig("unit", seed=3, n_boot=150)
    res = run_experiment(x, y, Method("prior", _prior, {"k": 1}), Method("z", _oracle_ish), cfg,
                         conn=conn, results_dir=tmp_path)
    rows = conn.execute("SELECT role, seed, split_kind, n_test, json_valid(metrics) AS ok FROM runs ORDER BY role").fetchall()
    assert [r["role"] for r in rows] == ["baseline", "ours"]
    assert all(r["seed"] == 3 and r["split_kind"] == "random" and r["ok"] == 1 for r in rows)
    saved = json.loads(Path(res["result_path"]).read_text())
    assert saved["experiment"] == res["experiment"] and len(saved["y_true"]) == res["n_test"]
    again = run_experiment(x, y, Method("prior", _prior), Method("z", _oracle_ish), cfg, results_dir=tmp_path)
    assert again["methods"]["ours"]["metrics"] == res["methods"]["ours"]["metrics"]  # seeded reproducibility


def test_run_experiment_rejects_bad_inputs(tmp_path: Path) -> None:
    x, y = _data()
    cfg = ExperimentConfig("bad", n_boot=100)
    with pytest.raises(ValueError, match="probabilities"):
        run_experiment(x, y, Method("p", _prior), Method("bad", lambda a, b, c, r: np.full(len(c), 2.0)), cfg, results_dir=tmp_path)
    with pytest.raises(ValueError, match="nan|finite"):
        run_experiment(x, y, Method("p", _prior), Method("nan", lambda a, b, c, r: np.full(len(c), np.nan)), cfg, results_dir=tmp_path)
    with pytest.raises(ValueError, match="both classes"):
        run_experiment(x, np.ones_like(y), Method("p", _prior), Method("z", _oracle_ish), cfg, results_dir=tmp_path)


def test_worked_example_runs(tmp_path: Path, conn: sqlite3.Connection) -> None:
    from experiments.example_breast_cancer import (
        DATASET_NAME, LABEL_COL, make_baseline, make_ours, register_wdbc,
    )
    from experiments.run_experiment import load_from_db

    ds_id = register_wdbc(conn)
    assert register_wdbc(conn) == ds_id
    data = load_from_db(conn, DATASET_NAME)
    assert data.x.shape == (569, 30) and data.dataset_id == ds_id and data.times is None
    assert LABEL_COL not in data.feature_names
    res = run_experiment(data.x, data.y, make_baseline(data.feature_names), make_ours(),
                         ExperimentConfig("ex", n_boot=100), conn=conn, dataset_id=ds_id,
                         dataset_sha256=data.sha256, results_dir=tmp_path)
    assert res["delta_vs_baseline"]["auroc"]["point"] > 0
