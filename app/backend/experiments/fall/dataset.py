"""Build per-sequence windows, labels and event annotations from UR Fall landmarks + CSV labels."""
from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from features import ffill, stream, stream_v2

BASE = Path("/Users/chloekang/kcf/data/cache/extra/fall")
ASPECT = 320 / 240


@dataclass
class Seq:
    name: str
    is_fall: bool
    t: np.ndarray  # all frame times (valid ones)
    xy: np.ndarray  # (T,33,2) ffilled
    t_end: np.ndarray
    X: np.ndarray
    y: np.ndarray
    onset: float  # nan for ADL
    lying: float
    has_lying_label: bool
    duration: float
    X2: np.ndarray | None = None


def load_labels() -> dict[str, dict[int, int]]:
    d: dict[str, dict[int, int]] = defaultdict(dict)
    for n in ("falls", "adls"):
        for r in csv.reader(open(BASE / f"raw/urfall-cam0-{n}.csv")):
            d[r[0]][int(r[1])] = int(r[2])
    return d


def load_all() -> list[Seq]:
    lab = load_labels()
    out = []
    for f in sorted((BASE / "landmarks").glob("*.npz")):
        z = np.load(f)
        name = f.stem
        t, lm = z["t"], ffill(z["lm"])
        ok = ~np.isnan(lm[:, 0, 0])
        t, xy = t[ok], lm[ok][:, :, :2]
        is_fall = name.startswith("fall")
        fps = float(z["fps"])
        L = lab[name]
        onset = lying = float("nan")
        if is_fall:
            fr0 = sorted(k for k, v in L.items() if v == 0)
            fr1 = sorted(k for k, v in L.items() if v == 1 and k > fr0[0])
            onset, lying = (fr0[0] - 1) / fps, (fr1[0] - 1) / fps
        te, X = stream(t, xy, ASPECT)
        te2, X2 = stream_v2(t, xy, ASPECT)
        assert np.array_equal(te, te2) and np.array_equal(X, X2[:, :X.shape[1]])
        y = np.zeros(len(te), int)
        if is_fall:  # window [te-1, te] overlaps [onset, lying]
            y = ((te >= onset) & (te - 1.0 <= lying)).astype(int)
        out.append(Seq(name, is_fall, t, xy, te, X, y, onset, lying,
                       any(v == 1 for v in L.values()), float(z["t"][-1]), X2))
    return out
