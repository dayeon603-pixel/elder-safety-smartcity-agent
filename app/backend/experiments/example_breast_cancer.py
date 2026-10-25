"""Worked example / smoke test on a REAL public dataset (UCI WDBC via sklearn's bundled copy).

Provenance, stated exactly: the rows come from the copy of WDBC bundled inside scikit-learn
(`sklearn.datasets.load_breast_cancer`), not from a file downloaded from UCI. The recorded
SHA-256 is NOT a hash of any CSV: it is SHA-256(feature array bytes || label array bytes),
float64 little-endian C-order features (569 x 30) followed by int64 little-endian labels
(1 = malignant, i.e. sklearn's target flipped). `source_url` and `license` are declared, not
verified; the numbers are the UCI/sklearn ones only insofar as sklearn's copy is faithful.

Domain swap: replace `register_wdbc` with an ingest of a CSV from data/cache
(POST /datasets/ingest) and `load_from_db(...)`; replace the two Methods.

Baseline = logistic regression on ONE feature (a single-measurement rule, like a clinical
guideline cut-off). Ours = L2 logistic regression on all 30 standardised features.
This is a plumbing test of the harness, not a research claim.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from sklearn.datasets import load_breast_cancer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from app import db, repo
from app.config import DEFAULT_DB_PATH
from experiments.run_experiment import ExperimentConfig, Method, load_from_db, run_experiment

logger = logging.getLogger(__name__)

DATASET_NAME = "wdbc-sklearn"
SOURCE_URL = "https://archive.ics.uci.edu/dataset/17/breast+cancer+wisconsin+diagnostic"
LICENSE = "CC BY 4.0"
BASELINE_FEATURE = "mean radius"
RS_MAX = 2**31 - 1
LABEL_COL = "malignant"  # sklearn target 0=malignant,1=benign; flipped so 1 = malignant


def register_wdbc(conn: sqlite3.Connection) -> int:
    """Insert the dataset (with provenance hash over the raw bytes) once; return its id."""
    existing = repo.get_dataset_by_name(conn, DATASET_NAME)
    if existing and "features" in existing["column_roles"]:
        return int(existing["id"])
    if existing:  # pre-0002 row without column roles: replace it (runs keep their rows, FK -> NULL)
        logger.warning("re-registering %s: stored copy has no column roles", DATASET_NAME)
        repo.delete_dataset(conn, int(existing["id"]))
    bunch = load_breast_cancer()
    x, y = bunch.data.astype(np.float64), (1 - bunch.target).astype(np.int64)
    digest = hashlib.sha256(x.astype("<f8").tobytes() + y.astype("<i8").tobytes()).hexdigest()
    names = [str(n) for n in bunch.feature_names]
    records = [
        (str(i), None, int(y[i]), None, json.dumps({n: float(v) for n, v in zip(names, x[i])}))
        for i in range(len(y))
    ]
    ds = repo.insert_dataset(
        conn, name=DATASET_NAME, source_url=SOURCE_URL, license_=LICENSE, sha256=digest,
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), file_name=None,
        description=(
            "Wisconsin Diagnostic Breast Cancer, 569 rows, 30 features, from scikit-learn's bundled "
            "copy (not the UCI file). sha256 = SHA-256 of the float64 feature array bytes followed by "
            "the int64 label array bytes (little-endian, C order), not of a CSV file."
        ),
        created_by=None, records=records,
        column_roles={
            "id": None, "time": None, "label": LABEL_COL, "group": None, "label_values": [0, 1],
            "features": names, "features_inferred": False,
        },
    )
    return int(ds["id"])


def make_baseline(columns: list[str]) -> Method:
    col = columns.index(BASELINE_FEATURE)

    def fn(
        xt: NDArray[np.float64], yt: NDArray[np.int64], xe: NDArray[np.float64], rng: np.random.Generator
    ) -> NDArray[np.float64]:
        m = make_pipeline(
            StandardScaler(), LogisticRegression(max_iter=1000, random_state=int(rng.integers(RS_MAX)))
        )
        m.fit(xt[:, [col]], yt)
        return np.asarray(m.predict_proba(xe[:, [col]])[:, 1])

    return Method("logreg_single_feature", fn, {"feature": BASELINE_FEATURE})


def make_ours(c: float = 1.0) -> Method:
    def fn(
        xt: NDArray[np.float64], yt: NDArray[np.int64], xe: NDArray[np.float64], rng: np.random.Generator
    ) -> NDArray[np.float64]:
        m = make_pipeline(
            StandardScaler(), LogisticRegression(C=c, max_iter=5000, random_state=int(rng.integers(RS_MAX)))
        )
        m.fit(xt, yt)
        return np.asarray(m.predict_proba(xe)[:, 1])

    return Method("logreg_l2_all_features", fn, {"C": c, "features": "all 30 standardised"})


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    db_path = Path(os.environ.get("KCF_DB_PATH", str(DEFAULT_DB_PATH)))
    conn = db.connect(db_path)
    try:
        db.migrate(conn)
        register_wdbc(conn)
        data = load_from_db(conn, DATASET_NAME)
        res = run_experiment(
            data.x, data.y, make_baseline(data.feature_names), make_ours(),
            ExperimentConfig(name="wdbc_smoke"), times=data.times, groups=data.groups,
            conn=conn, dataset_id=data.dataset_id, dataset_sha256=data.sha256,
        )
    finally:
        conn.close()
    for role in ("baseline", "ours"):
        a = res["methods"][role]["metrics"]["auroc"]
        logger.info("%-9s AUROC %.4f [%.4f, %.4f]", role, a["point"], a["lo"], a["hi"])


if __name__ == "__main__":
    main()
