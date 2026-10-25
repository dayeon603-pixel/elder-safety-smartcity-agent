"""Descent gate for the shipped v1 logistic model: an alarm window must also satisfy hip_drop_L > G (hip y at window end minus
at window start, in torso lengths; image y points down, so >0 = the hips went DOWN inside the 1 s window). Blocks the
'lying still from frame 0' false alarm (wide silhouette + angle, no descent). Same nested protocol as optimize.py: the threshold
is re-selected on inner-OOF of the *gated* probabilities. G values are compared as a grid; the shipped G is the smallest value
whose pooled (UR + Le2i, within-dataset nested CV) sensitivity loss vs. no gate is <= 1 fall (judgement call, stated in the report).
FA/h carries exact Poisson (Garwood) 95% CIs; bootstrap CIs over sequences for the rest.
Usage: python gate_eval.py run
"""
from __future__ import annotations

import json
import logging
import pickle
import sys

import numpy as np
from scipy.stats import chi2

import evaluate as E
import features as F
import optimize as O

log = logging.getLogger("gate")
COL_DROP = F.FEATURES.index("hip_drop_L")
GATES = [None, 0.0, 0.05, 0.1, 0.2, 0.3, 0.5]
OUTG = O.OUT / "gate"


def poisson_ci(k: int, hours: float) -> list[float]:
    lo = 0.0 if k == 0 else chi2.ppf(0.025, 2 * k) / 2
    hi = chi2.ppf(0.975, 2 * k + 2) / 2
    return [k / hours, lo / hours, hi / hours]


def gate_p(s: O.Seq, p: np.ndarray, g: float | None) -> np.ndarray:
    if g is None:
        return p
    return np.where(s.banks[1.0][1][:, COL_DROP] > g, p, 0.0)  # LOOKAHEAD RISK: none, window ending at t only


def cache_split(train: list[O.Seq], test: list[O.Seq]):
    """clean inner-OOF probs on train, model fit on train, probs on test. (gate-independent)"""
    oof = O.inner_oof(train, [O.BASELINE], n_jobs=6)[O.BASELINE]
    X = np.vstack([s.banks[1.0][1][:, O.FSETS["v1"]] for s in train]).astype(np.float64)
    y = np.concatenate([s.banks[1.0][2] for s in train])
    m = O.fit_model("logreg", X, y)
    Pt = [m.predict_proba(s.banks[1.0][1][:, O.FSETS["v1"]].astype(np.float64))[:, 1] if len(s.banks[1.0][0]) else np.zeros(0) for s in test]
    return oof, Pt


def apply_gate(train, oof, test, Pt, g):
    Pg = [gate_p(s, p, g) for s, p in zip(train, oof)]
    (cfg, pp), _ = O.select(train, {O.BASELINE: Pg}, "orig")
    return O.events(test, [gate_p(s, p, g) for s, p in zip(test, Pt)], 1.0, pp), pp["thr"]


def summarize(res, S) -> dict:
    o = O.summarize(res, S)
    o["fa_poisson"] = poisson_ci(o["fa"], max(o["neg_hours"], 1e-9))
    return o


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    O.set_seed()
    OUTG.mkdir(parents=True, exist_ok=True)
    UR, LE = O.load_ds("ur"), O.load_ds("le2i")
    out: dict = {}
    # within-dataset nested CV (folds identical to evaluate.py) -> cache per fold
    for nm, S in (("ur_cv", UR), ("le_cv", LE)):
        fold = O.strat_folds(S, O.SEED)
        cache = []
        for k in range(5):
            tr = [S[i] for i in range(len(S)) if fold[i] != k]
            ti = [i for i in range(len(S)) if fold[i] == k]
            oof, Pt = cache_split(tr, [S[i] for i in ti])
            cache.append((tr, oof, ti, Pt))
        for g in GATES:
            res = [None] * len(S)
            for tr, oof, ti, Pt in cache:
                r, thr = apply_gate(tr, oof, [S[i] for i in ti], Pt, g)
                for i, e in zip(ti, r):
                    res[i] = e
            out[f"{nm}:gate={g}"] = summarize(res, S)
            log.info("%-18s gate=%s %s", nm, g, O.fmt(out[f"{nm}:gate={g}"]))
    # cross-dataset
    for nm, tr, te in (("ur2le", UR, LE), ("le2ur", LE, UR)):
        oof, Pt = cache_split(tr, te)
        for g in GATES:
            r, thr = apply_gate(tr, oof, te, Pt, g)
            out[f"{nm}:gate={g}"] = summarize(r, te)
            out[f"{nm}:gate={g}"]["thr"] = thr
            log.info("%-18s gate=%s %s", nm, g, O.fmt(out[f"{nm}:gate={g}"]))
    # share of Le2i / UR sequences that start already lying (angle>60 deg in the first valid window)
    def starts_lying(S):
        return [s.name for s in S if len(s.banks[1.0][0]) and s.banks[1.0][1][0, 0] > 60]
    out["starts_lying"] = {"ur": starts_lying(UR), "le2i": starts_lying(LE)}
    json.dump(out, open(OUTG / "gate_summary.json", "w"), indent=1, default=float)


if __name__ == "__main__":
    run() if len(sys.argv) < 2 or sys.argv[1] == "run" else sys.exit("usage")
