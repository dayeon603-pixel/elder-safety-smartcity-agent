"""Leave-sequences-out CV (GroupKFold=5 by sequence) of rule baseline vs LR / MLP, event-level metrics.

Event protocol: alarm = prob>=thr at a window end; alarms within REFRACTORY_S merge. Fall sequence: detected if
any alarm at t>=onset; latency = first such alarm - onset; alarms before onset are false alarms.
ADL sequences: every merged alarm is a false alarm. False alarms/hour = FA / (ADL time + pre-onset time of falls).
Threshold: chosen inside each training fold on inner-CV out-of-fold predictions (no test leakage).
Backtest-style results are hypotheses about this lab dataset, not deployment guarantees.
"""
from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupKFold
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import dataset as D
from features import rule_alarm_times

SEED = 42
REFRACTORY_S = 5.0
TARGET_FA_PER_H = 1.0
GRID = np.round(np.arange(0.05, 1.0, 0.025), 3)
OUT = Path(__file__).parent / "results"


@dataclass
class EventRes:
    detected: bool  # falls only
    latency: float  # falls only (nan if missed)
    fa: int  # false alarms
    neg_s: float  # negative time (s)


def merge(al: list[float]) -> list[float]:
    out: list[float] = []
    for a in al:
        if not out or a - out[-1] >= REFRACTORY_S:
            out.append(a)
    return out


def event(s: D.Seq, alarms: list[float]) -> EventRes:
    alarms = merge(sorted(alarms))
    if s.is_fall:
        pre = [a for a in alarms if a < s.onset]
        post = [a for a in alarms if a >= s.onset]
        return EventRes(bool(post), post[0] - s.onset if post else np.nan, len(pre), s.onset)
    return EventRes(False, np.nan, len(alarms), s.duration)


def model_alarms(s: D.Seq, p: np.ndarray, thr: float) -> list[float]:
    return [float(t) for t in s.t_end[p >= thr]]


def summarize(r: list[EventRes], idx: np.ndarray | None = None) -> dict[str, float]:
    r = [r[i] for i in idx] if idx is not None else r
    falls = [e for e in r if not np.isnan(e.neg_s) and e.detected is not None]
    return r  # placeholder (see metrics)


def metrics(res: list[EventRes], is_fall: np.ndarray, idx: np.ndarray) -> dict[str, float]:
    f = [i for i in idx if is_fall[i]]
    tp = sum(res[i].detected for i in f)
    fa = sum(res[i].fa for i in idx)
    hrs = sum(res[i].neg_s for i in idx) / 3600
    sens = tp / max(len(f), 1)
    prec = tp / (tp + fa) if tp + fa else 0.0
    f1 = 2 * prec * sens / (prec + sens) if prec + sens else 0.0
    lat = [res[i].latency for i in f if res[i].detected]
    return {"sens": sens, "fa_per_h": fa / hrs if hrs else np.nan, "f1": f1,
            "lat_med": float(np.median(lat)) if lat else np.nan}


def choose_thr(seqs: list[D.Seq], p: list[np.ndarray]) -> float:
    ig = np.array([s.is_fall for s in seqs])
    idx = np.arange(len(seqs))
    best, best_key = 0.5, (-1, -1.0)
    for thr in GRID:
        res = [event(s, model_alarms(s, pp, thr)) for s, pp in zip(seqs, p)]
        m = metrics(res, ig, idx)
        key = (1, m["sens"], m["f1"]) if m["fa_per_h"] <= TARGET_FA_PER_H else (0, m["f1"], m["sens"])
        if best_key == (-1, -1.0) or key > best_key:
            best, best_key = float(thr), key
    return best


def make_model(kind: str):
    if kind == "logreg":
        return make_pipeline(StandardScaler(), LogisticRegression(C=1.0, class_weight="balanced", max_iter=2000, random_state=SEED))
    return make_pipeline(StandardScaler(), MLPClassifier((16,), alpha=1e-2, max_iter=600, random_state=SEED))


def fit(kind: str, seqs: list[D.Seq]):
    X = np.vstack([s.X for s in seqs])
    y = np.concatenate([s.y for s in seqs])
    m = make_model(kind)
    if kind == "mlp":  # MLPClassifier has no class_weight: oversample positives to balance
        rng = np.random.default_rng(SEED)
        pos = np.where(y == 1)[0]
        k = int((y == 0).sum() - len(pos))
        extra = rng.choice(pos, k, replace=True)
        X, y = np.vstack([X, X[extra]]), np.concatenate([y, y[extra]])
    return m.fit(X, y)


def oof_probs(kind: str, seqs: list[D.Seq]) -> list[np.ndarray]:
    g = np.arange(len(seqs))
    out: list[np.ndarray] = [None] * len(seqs)  # type: ignore[list-item]
    strat = np.array([s.is_fall for s in seqs])
    # deterministic stratified-ish grouping: GroupKFold on sequences sorted so folds mix classes
    for tr, te in GroupKFold(5).split(np.zeros(len(seqs)), strat, g):
        m = fit(kind, [seqs[i] for i in tr])
        for i in te:
            out[i] = m.predict_proba(seqs[i].X)[:, 1]
    return out


def outer_cv(kind: str, S: list[D.Seq]):
    n = len(S)
    res: list[EventRes] = [None] * n  # type: ignore[list-item]
    thrs, probs = [], [None] * n
    g = np.arange(n)
    # stratify folds: interleave falls and ADL so each fold has both
    order = np.argsort([(not s.is_fall, i) for i, s in enumerate(S)], axis=0)[:, 0] if False else np.arange(n)
    fold_of = np.zeros(n, int)
    rng = np.random.default_rng(SEED)
    for cls in (True, False):
        ids = rng.permutation([i for i, s in enumerate(S) if s.is_fall == cls])
        fold_of[ids] = np.arange(len(ids)) % 5
    for k in range(5):
        tr = [i for i in range(n) if fold_of[i] != k]
        te = [i for i in range(n) if fold_of[i] == k]
        trs = [S[i] for i in tr]
        inner = oof_probs_strat(kind, trs)
        thr = choose_thr(trs, inner)
        thrs.append(thr)
        m = fit(kind, trs)
        for i in te:
            probs[i] = m.predict_proba(S[i].X)[:, 1]
            res[i] = event(S[i], model_alarms(S[i], probs[i], thr))
    return res, thrs, probs, fold_of


def oof_probs_strat(kind: str, seqs: list[D.Seq]) -> list[np.ndarray]:
    n = len(seqs)
    fold = np.zeros(n, int)
    rng = np.random.default_rng(SEED + 1)
    for cls in (True, False):
        ids = rng.permutation([i for i, s in enumerate(seqs) if s.is_fall == cls])
        fold[ids] = np.arange(len(ids)) % 5
    out: list[np.ndarray] = [None] * n  # type: ignore[list-item]
    for k in range(5):
        m = fit(kind, [seqs[i] for i in range(n) if fold[i] != k])
        for i in range(n):
            if fold[i] == k:
                out[i] = m.predict_proba(seqs[i].X)[:, 1]
    return out


def boot(res: list[EventRes], is_fall: np.ndarray, B: int = 2000) -> dict[str, tuple[float, float, float]]:
    rng = np.random.default_rng(SEED)
    F, A = np.where(is_fall)[0], np.where(~is_fall)[0]
    pt = metrics(res, is_fall, np.arange(len(res)))
    ds = {k: [] for k in pt}
    for _ in range(B):
        idx = np.concatenate([rng.choice(F, len(F)), rng.choice(A, len(A))])
        m = metrics(res, is_fall, idx)
        for k, v in m.items():
            ds[k].append(v)
    return {k: (pt[k], *np.nanpercentile(ds[k], [2.5, 97.5])) for k in pt}


def main() -> None:
    np.random.seed(SEED)
    S = D.load_all()
    ig = np.array([s.is_fall for s in S])
    allidx = np.arange(len(S))
    results = {}
    # (B) rule
    rule = [event(s, rule_alarm_times(s.t, s.xy, D.ASPECT)) for s in S]
    results["rule"] = {"res": rule}
    all_p, all_thr = {}, {}
    for kind in ("logreg", "mlp"):
        r, thrs, probs, fold_of = outer_cv(kind, S)
        results[kind] = {"res": r}
        all_p[kind], all_thr[kind] = probs, thrs
    lying_adl = np.array([(not s.is_fall) and s.has_lying_label for s in S])
    other_adl = np.array([(not s.is_fall) and not s.has_lying_label for s in S])
    summary = {}
    for k, v in results.items():
        res = v["res"]
        summary[k] = boot(res, ig)
        # ADL subsets: false alarms count and rate
        for nm, mask in (("adl_lying", lying_adl), ("adl_other", other_adl)):
            fa = sum(res[i].fa for i in np.where(mask)[0])
            hrs = sum(res[i].neg_s for i in np.where(mask)[0]) / 3600
            nseq_fa = sum(res[i].fa > 0 for i in np.where(mask)[0])
            summary[k][nm] = {"n_seq": int(mask.sum()), "fa": int(fa), "seq_with_fa": int(nseq_fa), "hours": hrs}
        summary[k]["latencies"] = [e.latency for e in res if e.detected]
    summary["n_fall"], summary["n_adl"] = int(ig.sum()), int((~ig).sum())
    summary["neg_hours"] = sum(e.neg_s for e in rule) / 3600
    summary["thresholds"] = all_thr
    OUT.mkdir(exist_ok=True)
    json.dump(summary, open(OUT / "cv_summary.json", "w"), indent=1, default=float)
    pickle.dump({"probs": all_p, "names": [s.name for s in S]}, open(OUT / "cv_probs.pkl", "wb"))
    for k in ("rule", "logreg", "mlp"):
        m = summary[k]
        print(k, {a: tuple(round(x, 3) for x in m[a]) for a in ("sens", "fa_per_h", "f1", "lat_med")})
        print("   ", m["adl_lying"], m["adl_other"])
    print("thr", all_thr, "neg_hours", summary["neg_hours"])


if __name__ == "__main__":
    main()
