"""Baseline-vs-ours experiment harness.

Seeded split (random-stratified, group-disjoint, or temporal), metrics with percentile
bootstrap 95% CIs, paired-bootstrap CIs for the difference (ours - baseline, same
resamples), persisted to the `runs` table and to a results JSON used by figures/.

Assumptions / failure modes:
* Binary labels in {0, 1}; each method returns P(y=1) in [0, 1].
* With `groups`, the random split keeps every group on one side (best of
  GROUP_SPLIT_DRAWS GroupShuffleSplit draws by closeness of the test positive rate) and the
  bootstrap resamples whole groups (cluster bootstrap). Temporal splits do NOT enforce
  group disjointness; the bootstrap is still clustered.
* Bootstrap resamples the TEST set only (captures test-sampling noise, not training
  variance or distribution shift). Resamples with a single class are skipped and counted.
* CIs are plain percentile intervals (no bias correction / BCa).
* A backtest-style result is a hypothesis, not a proof: check for leakage in the features
  (`# LEAKAGE RISK:` marks where future data could enter).
"""

from __future__ import annotations

import json
import logging
import random
import re
import sqlite3
import subprocess
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from sklearn.metrics import average_precision_score, brier_score_loss, f1_score, roc_auc_score
from sklearn.model_selection import GroupShuffleSplit, train_test_split

from app import db, repo

logger = logging.getLogger(__name__)

Predictor = Callable[
    [NDArray[np.float64], NDArray[np.int64], NDArray[np.float64], np.random.Generator],
    NDArray[np.float64],
]
DEFAULT_RESULTS_DIR = Path(__file__).resolve().parent / "results"
DECISION_THRESHOLD = 0.5
GROUP_SPLIT_DRAWS = 50
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}")
TIME_RE = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z")
CI_METHOD = "percentile bootstrap (paired; cluster-resampled by group when groups are given)"
EXPERIMENT_ID_UUID_CHARS = 8
ECE_BINS = 10
MIN_BOOT = 100
HIGHER_IS_BETTER: dict[str, bool] = {
    "auroc": True, "auprc": True, "accuracy": True, "f1": True, "brier": False, "ece": False,
}


@dataclass(frozen=True, slots=True)
class Method:
    """A named predictor: fit on (X_train, y_train) and return P(y=1) for X_test.

    `fn` receives a seeded `numpy.random.Generator` as its 4th argument; use it (or draw a
    `random_state` from it) for any stochastic step so runs reproduce.
    """

    name: str
    fn: Predictor
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    """Experiment settings. `temporal=True` holds out the latest `test_size` fraction."""

    name: str
    seed: int = 42
    test_size: float = 0.3
    temporal: bool = False
    n_boot: int = 1000
    ci_level: float = 0.95

    def __post_init__(self) -> None:
        if not NAME_RE.fullmatch(self.name):
            raise ValueError("name must match [A-Za-z0-9][A-Za-z0-9_.-]{0,63} (it names the results file)")
        if not 0.0 < self.test_size < 1.0:
            raise ValueError("test_size must be in (0, 1)")
        if not 0.0 < self.ci_level < 1.0:
            raise ValueError("ci_level must be in (0, 1)")
        if self.n_boot < MIN_BOOT:
            raise ValueError(f"n_boot must be >= {MIN_BOOT}")


def set_seed(seed: int) -> None:
    """Seed python and numpy (torch is not a dependency of this starter)."""
    random.seed(seed)
    np.random.seed(seed)  # noqa: NPY002 - legacy global seed for third-party code


def git_hash(cwd: Path | None = None) -> str:
    """Short HEAD hash, '-dirty' suffixed if tracked files differ; 'unversioned' outside git.

    If the working-tree state cannot be determined the hash is also marked dirty, because
    a clean tree cannot be proven.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd, capture_output=True, text=True, check=True, timeout=10,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return "unversioned"
    head = out.stdout.strip()
    if not head:
        return "unversioned"
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=cwd, capture_output=True, text=True, check=True, timeout=10,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return f"{head}-dirty"
    return f"{head}-dirty" if status.stdout.strip() else head


def _group_split(
    y: NDArray[np.int64], groups: NDArray[Any], cfg: ExperimentConfig
) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    """GroupShuffleSplit, picking the draw whose test positive rate is closest to overall.

    GroupShuffleSplit cannot stratify, so we take GROUP_SPLIT_DRAWS seeded draws, keep those
    with both classes on both sides, and choose the best-balanced one (deterministic).
    """
    if len(np.unique(groups)) < 2:
        raise ValueError("group split needs at least 2 groups")
    splitter = GroupShuffleSplit(n_splits=GROUP_SPLIT_DRAWS, test_size=cfg.test_size, random_state=cfg.seed)
    overall = float(y.mean())
    best: tuple[float, NDArray[np.int64], NDArray[np.int64]] | None = None
    for tr, te in splitter.split(np.zeros(len(y)), y, groups):
        if len(np.unique(y[tr])) < 2 or len(np.unique(y[te])) < 2:
            continue
        gap = abs(float(y[te].mean()) - overall)
        if best is None or gap < best[0]:
            best = (gap, np.sort(tr).astype(np.int64), np.sort(te).astype(np.int64))
    if best is None:
        raise ValueError("no group split keeps both classes on both sides; too few/too lumpy groups")
    return best[1], best[2]


def _temporal_split(
    times: NDArray[Any], cfg: ExperimentConfig
) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    """Cut at a distinct timestamp: train = times < cut, test = times >= cut.

    Ties never straddle the cut. The cut is the distinct timestamp whose train fraction is
    closest to 1 - test_size. A real guard asserts max(train_time) < min(test_time).
    """
    n = len(times)
    distinct = np.unique(times)
    if len(distinct) < 2:
        raise ValueError("temporal split needs at least 2 distinct timestamps")
    order = np.argsort(times, kind="stable")
    sorted_times = times[order]
    # candidate cut k means train = rows with time < distinct[k], k in 1..len(distinct)-1
    train_counts = np.searchsorted(sorted_times, distinct[1:], side="left")
    k = int(np.argmin(np.abs(train_counts / n - (1.0 - cfg.test_size))))
    cut = distinct[1:][k]
    train = np.flatnonzero(times < cut)
    test = np.flatnonzero(times >= cut)
    if len(train) == 0 or len(test) == 0:
        raise ValueError("temporal split leaves an empty side")
    # np.max/min do not support string dtypes (ISO timestamps from the DB), so compare sorted ends.
    if not np.sort(times[train])[-1] < np.sort(times[test])[0]:
        raise ValueError("temporal split leaks future rows into train")
    return train.astype(np.int64), test.astype(np.int64)


def split_indices(
    y: NDArray[np.int64],
    cfg: ExperimentConfig,
    times: NDArray[Any] | None = None,
    groups: NDArray[Any] | None = None,
) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    """Return (train_idx, test_idx).

    Temporal: cut on a distinct timestamp. Else with `groups`: group-disjoint split. Else
    seeded stratified random split.
    """
    n = len(y)
    if groups is not None and len(groups) != n:
        raise ValueError("groups and y must have equal length")
    if cfg.temporal:
        if times is None:
            raise ValueError("temporal split requires `times`")
        if len(times) != n:
            raise ValueError("times and y must have equal length")
        return _temporal_split(np.asarray(times), cfg)
    if groups is not None:
        return _group_split(y, np.asarray(groups), cfg)
    idx = np.arange(n)
    tr, te = train_test_split(idx, test_size=cfg.test_size, random_state=cfg.seed, stratify=y)
    return np.sort(tr).astype(np.int64), np.sort(te).astype(np.int64)


def expected_calibration_error(y: NDArray[np.int64], p: NDArray[np.float64], bins: int = ECE_BINS) -> float:
    """Equal-width ECE: sum_b (n_b / n) * |mean(y_b) - mean(p_b)|."""
    edges = np.linspace(0.0, 1.0, bins + 1)
    which = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    ece = 0.0
    for b in range(bins):
        mask = which == b
        if mask.any():
            ece += mask.mean() * abs(float(y[mask].mean()) - float(p[mask].mean()))
    return float(ece)


def compute_metrics(y: NDArray[np.int64], p: NDArray[np.float64]) -> dict[str, float]:
    """Point metrics; AUROC/AUPRC need both classes present."""
    pred = (p >= DECISION_THRESHOLD).astype(np.int64)
    return {
        "auroc": float(roc_auc_score(y, p)),
        "auprc": float(average_precision_score(y, p)),
        "accuracy": float((pred == y).mean()),
        "f1": float(f1_score(y, pred, zero_division=0)),
        "brier": float(brier_score_loss(y, p)),
        "ece": expected_calibration_error(y, p),
    }


def _ci(samples: NDArray[np.float64], level: float) -> tuple[float, float]:
    a = (1.0 - level) / 2.0
    lo, hi = np.quantile(samples, [a, 1.0 - a])
    return float(lo), float(hi)


def bootstrap(
    y: NDArray[np.int64],
    p_base: NDArray[np.float64],
    p_ours: NDArray[np.float64],
    cfg: ExperimentConfig,
    groups: NDArray[Any] | None = None,
) -> dict[str, Any]:
    """Paired percentile bootstrap over test rows, or over whole groups if `groups` is given.

    Returns per-method {metric: {point, lo, hi}}, delta {metric: {point, lo, hi}} for
    ours - baseline, and the number of skipped single-class resamples.
    """
    rng = np.random.default_rng(cfg.seed)
    n = len(y)
    members: list[NDArray[np.int64]] | None = None
    if groups is not None:
        if len(groups) != n:
            raise ValueError("groups and y must have equal length")
        _, inverse = np.unique(groups, return_inverse=True)
        order = np.argsort(inverse, kind="stable")
        bounds = np.flatnonzero(np.diff(inverse[order])) + 1
        members = [np.asarray(m, dtype=np.int64) for m in np.split(order, bounds)]
        if len(members) < 2:
            raise ValueError("cluster bootstrap needs at least 2 groups in the test set")
    names = list(HIGHER_IS_BETTER)
    store: dict[str, dict[str, list[float]]] = {"baseline": {m: [] for m in names}, "ours": {m: [] for m in names}}
    skipped = 0
    for _ in range(cfg.n_boot):
        if members is None:
            idx = rng.integers(0, n, size=n)
        else:
            picks = rng.integers(0, len(members), size=len(members))
            idx = np.concatenate([members[g] for g in picks])
        yb = y[idx]
        if yb.min() == yb.max():
            skipped += 1
            continue
        mb, mo = compute_metrics(yb, p_base[idx]), compute_metrics(yb, p_ours[idx])
        for m in names:
            store["baseline"][m].append(mb[m])
            store["ours"][m].append(mo[m])
    if skipped > cfg.n_boot // 10:
        raise ValueError(f"{skipped}/{cfg.n_boot} bootstrap resamples were single-class; test set too small/imbalanced")
    points = {"baseline": compute_metrics(y, p_base), "ours": compute_metrics(y, p_ours)}
    out: dict[str, Any] = {"skipped": skipped, "baseline": {}, "ours": {}, "delta": {}}
    for role in ("baseline", "ours"):
        for m in names:
            lo, hi = _ci(np.asarray(store[role][m]), cfg.ci_level)
            out[role][m] = {"point": points[role][m], "lo": lo, "hi": hi}
    for m in names:
        d = np.asarray(store["ours"][m]) - np.asarray(store["baseline"][m])
        lo, hi = _ci(d, cfg.ci_level)
        out["delta"][m] = {"point": points["ours"][m] - points["baseline"][m], "lo": lo, "hi": hi}
    return out


@dataclass(frozen=True, slots=True)
class LoadedData:
    """A dataset ready for `run_experiment`: features only, roles kept separate."""

    x: NDArray[np.float64]
    y: NDArray[np.int64]
    times: NDArray[Any] | None
    groups: NDArray[Any] | None
    feature_names: list[str]
    dataset_id: int
    sha256: str


def load_from_db(
    conn: sqlite3.Connection,
    dataset_name: str,
    feature_columns: Sequence[str] | None = None,
    exclude: Sequence[str] = (),
) -> LoadedData:
    """Load features/label/times/groups from the `records` table using the stored column roles.

    The label, id, time and group columns recorded at ingest are NEVER features. With
    `feature_columns=None` the feature list stored at ingest is used; an explicit list must
    not name a reserved column. Every feature value must be present and finite.

    Raises:
        KeyError: dataset not found.
        ValueError: dataset incomplete, no recorded column roles (legacy rows: re-ingest),
            no label, a reserved column requested as a feature, a missing/non-finite value,
            or a malformed event_time.
    """
    ds = repo.get_dataset_by_name(conn, dataset_name)
    if ds is None:
        raise KeyError(f"dataset not found: {dataset_name}")
    if ds["status"] != "complete":
        raise ValueError(f"dataset {dataset_name} is not fully ingested")
    roles: dict[str, Any] = ds["column_roles"]
    if "features" not in roles:
        raise ValueError(
            f"dataset {dataset_name} has no recorded column roles; re-ingest it with a column mapping"
        )
    reserved = {roles[k] for k in ("id", "time", "label", "group") if roles.get(k) is not None}
    if feature_columns is None:
        names = [c for c in roles["features"] if c not in set(exclude)]
    else:
        leaked = [c for c in feature_columns if c in reserved]
        if leaked:
            raise ValueError(f"reserved columns cannot be features (label/id/time/group): {leaked}")
        names = list(feature_columns)
    if not names:
        raise ValueError("no feature columns: pass feature_columns or ingest with numeric features")
    if len(set(names)) != len(names):
        raise ValueError("duplicate feature column")
    rows = conn.execute(
        "SELECT ext_id, label, event_time, group_key, payload FROM records WHERE dataset_id = ? ORDER BY id",
        (ds["id"],),
    ).fetchall()
    if not rows:
        raise ValueError(f"dataset {dataset_name} has no records")
    if any(r["label"] is None for r in rows):
        raise ValueError("records without label; ingest with label_column")
    matrix = np.empty((len(rows), len(names)), dtype=np.float64)
    for i, r in enumerate(rows):
        payload = json.loads(r["payload"])
        for j, c in enumerate(names):
            if c not in payload:
                raise ValueError(f"record {r['ext_id']!r} has no column {c!r}")
            try:
                matrix[i, j] = float(payload[c])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"record {r['ext_id']!r}: column {c!r} is not numeric") from exc
    if not np.isfinite(matrix).all():
        bad_r, bad_c = np.argwhere(~np.isfinite(matrix))[0]
        raise ValueError(f"non-finite feature {names[int(bad_c)]!r} in record {rows[int(bad_r)]['ext_id']!r}")
    y = np.asarray([r["label"] for r in rows], dtype=np.int64)
    t_raw = [r["event_time"] for r in rows]
    times: NDArray[Any] | None = None
    if all(t is not None for t in t_raw):
        if not all(TIME_RE.fullmatch(t) for t in t_raw):
            raise ValueError("event_time is not normalised ISO-8601 UTC; re-ingest the dataset")
        times = np.asarray(t_raw)
    g_raw = [r["group_key"] for r in rows]
    groups = np.asarray(g_raw) if all(g is not None for g in g_raw) else None
    return LoadedData(matrix, y, times, groups, names, int(ds["id"]), str(ds["sha256"]))


def run_experiment(
    x: NDArray[np.float64],
    y: NDArray[np.int64],
    baseline: Method,
    ours: Method,
    cfg: ExperimentConfig,
    *,
    times: NDArray[Any] | None = None,
    groups: NDArray[Any] | None = None,
    conn: sqlite3.Connection | None = None,
    dataset_id: int | None = None,
    dataset_sha256: str | None = None,
    results_dir: Path = DEFAULT_RESULTS_DIR,
) -> dict[str, Any]:
    """Run both methods on one split, bootstrap, persist to DB (if conn) and JSON.

    The experiment id is `<name>@<UTC time>-<8 hex of uuid4>` and names the results file, so
    two runs never overwrite each other. `dataset_sha256` (looked up from `datasets` when
    `conn` and `dataset_id` are given) is stored in every run's params.

    Raises:
        ValueError: on invalid shapes/labels, non-finite features, a split with one class in
            train, or predictions outside [0, 1].
    """
    if x.ndim != 2 or len(x) != len(y):
        raise ValueError("x must be 2-D with len(x) == len(y)")
    if not np.isfinite(x).all():
        raise ValueError("x contains NaN or infinite values")
    if not set(np.unique(y).tolist()) <= {0, 1} or len(np.unique(y)) < 2:
        raise ValueError("y must contain both classes 0 and 1 and nothing else")
    if dataset_sha256 is None and conn is not None and dataset_id is not None:
        row = conn.execute("SELECT sha256 FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
        dataset_sha256 = str(row["sha256"]) if row else None
    set_seed(cfg.seed)
    started = datetime.now(timezone.utc)
    tr, te = split_indices(y, cfg, times, groups)
    if len(np.unique(y[tr])) < 2:
        raise ValueError("training split has a single class")
    probs: dict[str, NDArray[np.float64]] = {}
    durations: dict[str, float] = {}
    for role, m in (("baseline", baseline), ("ours", ours)):
        set_seed(cfg.seed)
        t0 = time.perf_counter()
        p = np.asarray(m.fn(x[tr], y[tr], x[te], np.random.default_rng(cfg.seed)), dtype=np.float64)
        durations[role] = time.perf_counter() - t0
        if p.shape != (len(te),) or not np.all(np.isfinite(p)) or p.min() < 0.0 or p.max() > 1.0:
            raise ValueError(f"{m.name}: predictions must be finite probabilities of shape (n_test,)")
        probs[role] = p
    boot = bootstrap(y[te], probs["baseline"], probs["ours"], cfg, None if groups is None else groups[te])
    ghash = git_hash(Path(__file__).resolve().parent)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    suffix = f"{stamp}-{uuid.uuid4().hex[:EXPERIMENT_ID_UUID_CHARS]}"
    experiment = f"{cfg.name}@{suffix}"
    split_kind = "temporal" if cfg.temporal else "random"
    result: dict[str, Any] = {
        "experiment": experiment,
        "config": {"seed": cfg.seed, "test_size": cfg.test_size, "temporal": cfg.temporal,
                   "n_boot": cfg.n_boot, "ci_level": cfg.ci_level},
        "ci_method": CI_METHOD, "group_aware": groups is not None,
        "dataset_sha256": dataset_sha256, "duration_s": durations,
        "git_hash": ghash, "n_train": int(len(tr)), "n_test": int(len(te)),
        "bootstrap_skipped": boot["skipped"], "higher_is_better": HIGHER_IS_BETTER,
        "y_true": y[te].tolist(),
        "methods": {
            role: {"name": m.name, "params": dict(m.params), "metrics": boot[role],
                   "y_prob": probs[role].tolist()}
            for role, m in (("baseline", baseline), ("ours", ours))
        },
        "delta_vs_baseline": boot["delta"],
    }
    if conn is not None:
        with db.transaction(conn):
            for role, m in (("baseline", baseline), ("ours", ours)):
                repo.insert_run(conn, {
                    "experiment": experiment, "dataset_id": dataset_id, "role": role, "method": m.name,
                    "params": {**dict(m.params), "dataset_sha256": dataset_sha256},
                    "metrics": {
                        "point_ci": boot[role], "ci_method": CI_METHOD,
                        "delta_vs_baseline": boot["delta"] if role == "ours" else None,
                    },
                    "seed": cfg.seed, "split_kind": split_kind, "n_train": int(len(tr)),
                    "n_test": int(len(te)), "git_hash": ghash, "duration_s": durations[role],
                    "started_at": started.isoformat(timespec="seconds"),
                })
    results_dir.mkdir(parents=True, exist_ok=True)
    out_path = results_dir / f"{cfg.name}_{suffix}.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    result["result_path"] = str(out_path)
    logger.info("experiment %s -> %s", experiment, out_path)
    return result
