"""Fall-detector optimisation: nested, sequence-grouped search over window / features / model / event post-processing,
plus cross-dataset validation (UR Fall <-> Le2i).  Backtest-style results are HYPOTHESES about two small lab datasets
(a lab-camera pose pipeline), not deployment guarantees.

Subcommands (python optimize.py <cmd>):
  extract-le2i   MediaPipe pose_landmarker_lite landmarks for Le2i videos (same pipeline as extract_pose.py)
  bank           causal feature banks for every (dataset, sequence, window length)   -> results/opt/cache/*.pkl
  run            all experiments (nested CV + cross-dataset)                         -> results/opt/opt_summary.json
  export         fit selected logistic config on all of UR, write fall_model_v3.json + test vectors

Datasets / licences (verify before redistribution):
  UR Fall Detection (Kwolek & Kepski 2014, Comput Methods Programs Biomed 117(3):489-501), CC BY-NC-SA 4.0.
  Le2i / IMVIA Fall (Charfi, Miteran, Dubois, Atri, Tourki 2013, J Electronic Imaging 22(4):041106). Original: research
  use, Univ. Bourgogne IMVIA lab; the Kaggle mirror tuyenldvn/falldataset-imvia declares licence "unknown" -> used for
  offline evaluation only, no redistribution of video/landmarks.

Protocol: outer 5-fold CV by sequence (identical folds to evaluate.py); EVERYTHING tuned (config, threshold,
post-processing) is chosen on inner-fold out-of-fold predictions of the outer-training sequences only.
Cross-dataset: all selection on the training dataset only; the test dataset is touched once.
Seeds: SEED set before any split / sampling / model fit.
Units: log-return concepts do not apply (no returns); all metrics are event-level.
"""
from __future__ import annotations

import json
import logging
import pickle
import random
import sys
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path

import numpy as np
from joblib import Parallel, delayed

import evaluate as E
import features as F

log = logging.getLogger("opt")
SEED = 42
ROOT = Path("/Users/chloekang/kcf")
UR_DIR = ROOT / "data/cache/extra/fall"
LE_DIR = ROOT / "data/cache/extra/le2i"
OUT = Path(__file__).parent / "results" / "opt"
CACHE = OUT / "cache"
PUB = ROOT / "app/frontend/public/models"
ASPECT = 320 / 240
WINDOWS = (0.5, 1.0, 1.5)
N_RAW = 16  # samples per window for the 1D-CNN input
SIG_NAMES = ("ang", "hy_rel", "aspect", "head_hip", "speed")
V2_N = len(F.FEATURES_V2)  # 13
STAT_N = 3 * len(SIG_NAMES)  # 15
RAW_N = 5 * N_RAW
FSETS = {  # column slices into the bank [v2 (13) | stats (15) | raw (80)]
    "v1": np.arange(0, 9),
    "v2": np.arange(0, V2_N),
    "v1s": np.r_[np.arange(0, 9), np.arange(V2_N, V2_N + STAT_N)],
    "v2s": np.arange(0, V2_N + STAT_N),
    "raw": np.arange(V2_N + STAT_N, V2_N + STAT_N + RAW_N),
}
MAX_NEG_TRAIN = 60000  # training-only stride subsample of negative windows (positives always kept)


def set_seed(seed: int = SEED) -> None:
    random.seed(seed)
    np.random.seed(seed)
    try:
        import torch

        torch.manual_seed(seed)
        torch.use_deterministic_algorithms(False)
    except ImportError:
        pass


# --------------------------------------------------------------------------- data model
@dataclass
class Seq:
    ds: str
    name: str
    is_fall: bool
    onset: float  # nan for ADL
    lying: float
    duration: float  # exact video seconds (negative time for ADL)
    aspect: float  # frame W/H (Le2i has some 320x180 videos)
    t: np.ndarray = field(repr=False)  # valid-detection frame times
    xy: np.ndarray = field(repr=False)  # (T,33,2) causally ffilled
    banks: dict = field(default_factory=dict, repr=False)  # W -> (t_end, B float32 (M,108), y int8)


def load_landmarks(path: Path) -> tuple[np.ndarray, np.ndarray, float, float]:
    z = np.load(path)
    t, lm = z["t"], F.ffill(z["lm"])
    ok = ~np.isnan(lm[:, 0, 0])
    return t[ok], lm[ok][:, :, :2], float(z["fps"]), float(z["t"][-1])


def ur_meta() -> dict[str, tuple[bool, float, float]]:
    import dataset as D

    lab = D.load_labels()
    out = {}
    for n, L in lab.items():
        fps = 30.0
        if n.startswith("fall"):
            fr0 = sorted(k for k, v in L.items() if v == 0)
            fr1 = sorted(k for k, v in L.items() if v == 1 and k > fr0[0])
            out[n] = (True, (fr0[0] - 1) / fps, (fr1[0] - 1) / fps)
        else:
            out[n] = (False, float("nan"), float("nan"))
    return out


# --------------------------------------------------------------------------- window features (causal)
def _window_bank(ts: np.ndarray, xy: np.ndarray, W: float, aspect: float) -> np.ndarray | None:
    """v2 (13) + temporal stats (15) + raw resampled window (80) for the window ending at ts[-1]. Causal."""
    base = F.window_features_v2(ts, xy, aspect)
    if base is None:
        return None
    g = F.frame_signals(xy, aspect)
    L = float(g["tl"].max())  # LOOKAHEAD RISK: none - max over frames <= t_end only
    hip = g["hip"] / L
    hy, hx = hip[:, 1], hip[:, 0]
    dt = np.maximum(np.diff(ts), 1e-6)
    spd = np.r_[0.0, np.hypot(np.diff(hx), np.diff(hy)) / dt]
    sig = np.stack(
        [g["ang"], hy - hy[-1], g["bw"] / np.maximum(g["bh"], 1e-6), (hip[:, 1] - g["nose"][:, 1] / L), spd]
    )
    stats = np.concatenate([sig.min(1), sig.max(1), sig.std(1)])
    grid = np.linspace(ts[-1] - W, ts[-1], N_RAW)
    raw = np.stack(
        [np.interp(grid, ts, g["ang"]) / 90.0, np.interp(grid, ts, hy - hy[-1]),
         np.clip(np.interp(grid, ts, sig[2]), 0, 5), np.interp(grid, ts, sig[3]), np.interp(grid, ts, hx - hx[-1])]
    ).ravel()
    return np.concatenate([base, stats, raw])


def bank_stream(ts: np.ndarray, xy: np.ndarray, W: float, aspect: float = ASPECT) -> tuple[np.ndarray, np.ndarray]:
    """Window length W seconds. Re-uses features.window_features(_v2) with HALF/MIN_SPAN scaled by W (== shipped
    v1/v2 definition at W=1.0, asserted in tests)."""
    old = (F.WINDOW_S, F.HALF_S, F.MIN_SPAN_S)
    F.WINDOW_S, F.HALF_S, F.MIN_SPAN_S = W, 0.5 * W, 0.8 * W
    try:
        ends, rows, lo = [], [], 0
        for i in range(len(ts)):
            while ts[lo] < ts[i] - W - F.EPS_T:
                lo += 1
            f = _window_bank(ts[lo : i + 1], xy[lo : i + 1], W, aspect)
            if f is not None:
                ends.append(ts[i])
                rows.append(f)
    finally:
        F.WINDOW_S, F.HALF_S, F.MIN_SPAN_S = old
    return np.array(ends), np.array(rows, dtype=np.float32).reshape(-1, V2_N + STAT_N + RAW_N)


def _label(te: np.ndarray, W: float, is_fall: bool, onset: float, lying: float) -> np.ndarray:
    y = np.zeros(len(te), np.int8)
    if is_fall:  # window [te-W, te] overlaps the annotated fall [onset, lying]
        y = ((te >= onset) & (te - W <= lying)).astype(np.int8)
    return y


def _build_one(ds: str, name: str, path: Path, is_fall: bool, onset: float, lying: float, aspect: float = ASPECT) -> Seq:
    t, xy, fps, dur = load_landmarks(path)
    s = Seq(ds, name, is_fall, onset, lying, dur, aspect, t, xy)
    for W in WINDOWS:
        te, B = bank_stream(t, xy, W, aspect)
        s.banks[W] = (te, B, _label(te, W, is_fall, onset, lying))
    return s


def le2i_meta() -> dict[str, tuple[bool, float, float]]:
    return json.load(open(LE_DIR / "meta.json"))  # written by extract-le2i


def build_banks() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    jobs = [("ur", n, UR_DIR / "landmarks" / f"{n}.npz", *m) for n, m in sorted(ur_meta().items())
            if (UR_DIR / "landmarks" / f"{n}.npz").exists()]
    if (LE_DIR / "meta.json").exists():
        jobs += [("le2i", n, LE_DIR / "landmarks" / f"{n}.npz", *m) for n, m in le2i_meta().items()
                 if (LE_DIR / "landmarks" / f"{n}.npz").exists()]
    for ds in ("ur", "le2i"):
        js = [j for j in jobs if j[0] == ds]
        if not js:
            continue
        seqs = Parallel(n_jobs=11, verbose=0)(delayed(_build_one)(*j) for j in js)
        pickle.dump(seqs, open(CACHE / f"{ds}.pkl", "wb"))
        log.info("bank %s: %d seqs, %d falls, %.3f h", ds, len(seqs), sum(s.is_fall for s in seqs),
                 sum(s.duration for s in seqs) / 3600)


def load_ds(ds: str) -> list[Seq]:
    return pickle.load(open(CACHE / f"{ds}.pkl", "rb"))


# --------------------------------------------------------------------------- models
CONFIGS: list[tuple[str, str, float]] = []  # (model, fset, W)  ordered simplest-first (ties -> earlier)
for _m, _fs in (("logreg", "v1"), ("logreg", "v2"), ("logreg", "v1s"), ("logreg", "v2s"),
                ("logreg_c", "v2s"), ("hgb", "v2"), ("hgb", "v2s"), ("mlp", "v2"), ("mlp", "v2s"), ("cnn", "raw")):
    for _W in (1.0, 0.5, 1.5):
        CONFIGS.append((_m, _fs, _W))
BASELINE = ("logreg", "v1", 1.0)


class TorchCNN:
    """Small 1D-CNN on the N_RAW-step x 5-channel causal window. Deterministic seed; weighted BCE."""

    def __init__(self, epochs: int = 25, seed: int = SEED):
        self.epochs, self.seed = epochs, seed

    def fit(self, X: np.ndarray, y: np.ndarray) -> "TorchCNN":
        import torch
        from torch import nn

        torch.manual_seed(self.seed)
        torch.set_num_threads(1)
        Xr = X.reshape(len(X), 5, N_RAW).astype(np.float32)
        self.mu, self.sd = Xr.mean((0, 2), keepdims=True), Xr.std((0, 2), keepdims=True) + 1e-6
        Xt = torch.tensor((Xr - self.mu) / self.sd)
        yt = torch.tensor(y, dtype=torch.float32)
        self.net = nn.Sequential(nn.Conv1d(5, 16, 3, padding=1), nn.ReLU(), nn.Conv1d(16, 16, 3, padding=1), nn.ReLU(),
                                 nn.AdaptiveMaxPool1d(1), nn.Flatten(), nn.Linear(16, 1))
        opt = torch.optim.AdamW(self.net.parameters(), 3e-3, weight_decay=1e-2)
        pw = torch.tensor((len(y) - y.sum()) / max(y.sum(), 1))
        lossf = nn.BCEWithLogitsLoss(pos_weight=pw)
        g = torch.Generator().manual_seed(self.seed)
        for _ in range(self.epochs):
            perm = torch.randperm(len(Xt), generator=g)
            for i in range(0, len(Xt), 256):
                b = perm[i : i + 256]
                opt.zero_grad()
                lossf(self.net(Xt[b]).squeeze(1), yt[b]).backward()
                nn.utils.clip_grad_norm_(self.net.parameters(), 1.0)
                opt.step()
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        import torch

        Xr = X.reshape(len(X), 5, N_RAW).astype(np.float32)
        with torch.no_grad():
            p = torch.sigmoid(self.net(torch.tensor((Xr - self.mu) / self.sd))).squeeze(1).numpy()
        return np.c_[1 - p, p]


def subsample(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    neg = np.where(y == 0)[0]
    if len(neg) > MAX_NEG_TRAIN:
        keep = neg[:: int(np.ceil(len(neg) / MAX_NEG_TRAIN))]
        idx = np.sort(np.r_[np.where(y == 1)[0], keep])
        return X[idx], y[idx]
    return X, y


def fit_model(kind: str, X: np.ndarray, y: np.ndarray):
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.neural_network import MLPClassifier
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    X, y = subsample(X, y)
    if kind == "cnn":
        return TorchCNN().fit(X, y)
    if kind in ("logreg", "logreg_c"):
        C = 1.0 if kind == "logreg" else 0.05
        return make_pipeline(StandardScaler(), LogisticRegression(C=C, class_weight="balanced", max_iter=3000,
                                                                  random_state=SEED)).fit(X, y)
    if kind == "hgb":
        return HistGradientBoostingClassifier(max_depth=3, max_iter=120, learning_rate=0.1, min_samples_leaf=40,
                                              l2_regularization=1.0, class_weight="balanced", random_state=SEED).fit(X, y)
    if kind == "mlp":  # oversample positives (MLPClassifier has no class_weight), as in evaluate.py
        rng = np.random.default_rng(SEED)
        pos = np.where(y == 1)[0]
        extra = rng.choice(pos, int((y == 0).sum() - len(pos)), replace=True)
        X, y = np.vstack([X, X[extra]]), np.concatenate([y, y[extra]])
        return make_pipeline(StandardScaler(), MLPClassifier((16,), alpha=1e-2, max_iter=300, random_state=SEED)).fit(X, y)
    raise ValueError(kind)


def _fit_predict(cfg: tuple[str, str, float], Xtr: list, ytr: list, Xte: list) -> list[np.ndarray]:
    kind, fs, _ = cfg
    cols = FSETS[fs]
    X = np.vstack([x[:, cols] for x in Xtr]).astype(np.float64)
    y = np.concatenate(ytr)
    m = fit_model(kind, X, y)
    return [m.predict_proba(x[:, cols].astype(np.float64))[:, 1] if len(x) else np.zeros(0) for x in Xte]


# --------------------------------------------------------------------------- event post-processing
PP_GRID = [dict(thr=float(t), lo=lo, persist=ps, R=R)
           for t in np.round(np.arange(0.1, 0.99, 0.05), 3)
           for lo in (1.0, 0.5)  # hysteresis: stay "active" while p >= lo*thr; 1.0 = none
           for ps in (0.0, 0.2, 0.4)  # persistence: seconds p must stay above threshold before alarming
           for R in (5.0, 10.0)]  # refractory merge (s)
PP_BASE_GRID = [dict(thr=float(t), lo=1.0, persist=0.0, R=5.0) for t in E.GRID]


def alarms_pp(te: np.ndarray, p: np.ndarray, thr: float, lo: float, persist: float, R: float) -> list[float]:
    """hysteresis (enter at p>=thr, stay while p>=lo*thr) -> persistence -> (lo<1: one alarm per episode) -> refractory.
    Causal: decision at window end t uses p[:t] only."""
    if lo >= 1.0:
        act = p >= thr
    else:  # active from first p>=thr inside a run of (p >= lo*thr) until that run ends (vectorised)
        on, low = p >= thr, p >= lo * thr
        act = np.zeros(len(p), bool)
        if on.any():
            d0 = np.diff(np.r_[False, low].astype(np.int8)) == 1
            rid = np.cumsum(d0)  # run id for each low sample
            ion = np.where(on)[0]
            _, first = np.unique(rid[ion], return_index=True)
            starts_on = ion[first]
            endmark = np.diff(np.r_[low, False].astype(np.int8)) == -1  # last sample of run
            ends_ix = np.where(endmark)[0]
            run_end = ends_ix[rid[starts_on] - 1]
            delta = np.zeros(len(p) + 1, np.int32)
            np.add.at(delta, starts_on, 1)
            np.add.at(delta, run_end + 1, -1)
            act = np.cumsum(delta[:-1]) > 0
    if not act.any():
        return []
    d = np.diff(np.r_[False, act, False].astype(np.int8))
    starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]  # run [s, e)
    out: list[float] = []
    for s, e in zip(starts, ends):
        ok = np.where(te[s:e] - te[s] >= persist - 1e-9)[0]
        if len(ok) == 0:
            continue
        cand = te[s:e][ok]
        out.extend([float(cand[0])] if lo < 1.0 else cand.tolist())
    merged: list[float] = []
    for a in sorted(out):
        if not merged or a - merged[-1] >= R:
            merged.append(a)
    return merged


def events(S: list[Seq], P: list[np.ndarray], W: float, pp: dict) -> list[E.EventRes]:
    return [E.event(s, alarms_pp(s.banks[W][0], p, **pp)) for s, p in zip(S, P)]


def key_search(m: dict, floor: float = 0.90) -> tuple:
    """Primary: keep sensitivity >= floor, minimise false alarms (count-equivalent), then F1, then latency."""
    ok = m["sens"] >= floor
    return (int(ok), -m["fa_per_h"] if ok else m["sens"], m["f1"], -(m["lat_med"] if m["lat_med"] == m["lat_med"] else 9))


def key_orig(m: dict) -> tuple:
    return (1, m["sens"], m["f1"]) if m["fa_per_h"] <= E.TARGET_FA_PER_H else (0, m["f1"], m["sens"])


def strat_folds(S: list[Seq], seed: int, k: int = 5) -> np.ndarray:
    fold = np.zeros(len(S), int)
    rng = np.random.default_rng(seed)
    for cls in (True, False):
        ids = rng.permutation([i for i, s in enumerate(S) if s.is_fall == cls])
        fold[ids] = np.arange(len(ids)) % k
    return fold


def inner_oof(S: list[Seq], cfgs: list, n_jobs: int = 11) -> dict:
    """OOF probabilities for every config using 5 stratified sequence folds (rng seed SEED+1 == evaluate.py)."""
    fold = strat_folds(S, SEED + 1)
    tasks = [(c, k) for c in cfgs for k in range(5)]

    def run(c, k):
        W = c[2]
        tr = [i for i in range(len(S)) if fold[i] != k]
        te = [i for i in range(len(S)) if fold[i] == k]
        return _fit_predict(c, [S[i].banks[W][1] for i in tr], [S[i].banks[W][2] for i in tr], [S[i].banks[W][1] for i in te])

    res = Parallel(n_jobs=n_jobs)(delayed(run)(c, k) for c, k in tasks)
    oof = {c: [None] * len(S) for c in cfgs}
    for (c, k), pr in zip(tasks, res):
        for i, p in zip([i for i in range(len(S)) if fold[i] == k], pr):
            oof[c][i] = p
    return oof


def _best_pp(S: list[Seq], P: list[np.ndarray], c: tuple, mode: str):
    ig = np.array([s.is_fall for s in S])
    idx = np.arange(len(S))
    ckey, cbest = None, None
    for pp in (PP_BASE_GRID if mode == "orig" else PP_GRID):
        m = E.metrics(events(S, P, c[2], pp), ig, idx)
        k = key_orig(m) if mode == "orig" else key_search(m)
        if ckey is None or k > ckey:
            ckey, cbest = k, (pp, m)
    return ckey, cbest


def select(S: list[Seq], oof: dict, mode: str):
    slim = [Seq(s.ds, s.name, s.is_fall, s.onset, s.lying, s.duration, s.aspect, s.t[:0], s.xy[:0],
                {W: (b[0], b[1][:0], b[2][:0]) for W, b in s.banks.items()}) for s in S]
    out = Parallel(n_jobs=11)(delayed(_best_pp)(slim, P, c, mode) for c, P in oof.items())
    best, bk, table = None, None, []
    for c, (ckey, cbest) in zip(oof.keys(), out):
        table.append({"cfg": list(c), "pp": cbest[0], "inner": cbest[1], "key": list(map(float, ckey))})
        if bk is None or ckey > bk:
            best, bk = (c, cbest[0]), ckey
    return best, table


def run_split(train: list[Seq], test: list[Seq], mode: str, cfgs: list) -> tuple[list[E.EventRes], dict]:
    oof = inner_oof(train, cfgs)
    (cfg, pp), table = select(train, oof, mode)
    W = cfg[2]
    Pt = _fit_predict(cfg, [s.banks[W][1] for s in train], [s.banks[W][2] for s in train], [s.banks[W][1] for s in test])
    return events(test, Pt, W, pp), {"cfg": list(cfg), "pp": pp, "table": table}


def nested_cv(S: list[Seq], mode: str, cfgs: list) -> tuple[list[E.EventRes], list[dict]]:
    fold = strat_folds(S, SEED)  # identical to evaluate.outer_cv
    res: list = [None] * len(S)
    chosen = []
    for k in range(5):
        tr = [S[i] for i in range(len(S)) if fold[i] != k]
        ti = [i for i in range(len(S)) if fold[i] == k]
        r, info = run_split(tr, [S[i] for i in ti], mode, cfgs)
        for i, e in zip(ti, r):
            res[i] = e
        chosen.append({k2: v for k2, v in info.items() if k2 != "table"})
        log.info("fold %d mode=%s chosen=%s", k, mode, chosen[-1])
    return res, chosen


def rule_events(S: list[Seq]) -> list[E.EventRes]:
    return [E.event(s, F.rule_alarm_times(s.t, s.xy, s.aspect)) for s in S]


def summarize(res: list[E.EventRes], S: list[Seq]) -> dict:
    ig = np.array([s.is_fall for s in S])
    o = E.boot(res, ig)
    o = {k: [float(x) for x in v] for k, v in o.items()}
    o["neg_hours"] = float(sum(e.neg_s for e in res) / 3600)
    o["n_fall"], o["n_adl"] = int(ig.sum()), int((~ig).sum())
    o["fa"] = int(sum(e.fa for e in res))
    o["tp"] = int(sum(e.detected for e in res if e.detected))
    o["seq_with_fa"] = int(sum(e.fa > 0 for e in res))
    return o


def fmt(o: dict) -> str:
    f = lambda k: f"{o[k][0]:.3f} [{o[k][1]:.3f},{o[k][2]:.3f}]"
    return f"sens {f('sens')} | FA/h {o['fa_per_h'][0]:.1f} [{o['fa_per_h'][1]:.1f},{o['fa_per_h'][2]:.1f}] (fa={o['fa']}/{o['neg_hours']:.3f}h) | F1 {f('f1')} | lat {o['lat_med'][0]:.2f}s"


# --------------------------------------------------------------------------- experiments
def run_all(quick: bool = False) -> None:
    set_seed()
    OUT.mkdir(parents=True, exist_ok=True)
    UR = load_ds("ur")
    LE = load_ds("le2i") if (CACHE / "le2i.pkl").exists() else []
    cfgs = CONFIGS[:6] + [BASELINE] if quick else CONFIGS
    if BASELINE not in cfgs:
        cfgs = [BASELINE] + cfgs
    summary: dict = {"configs": [list(c) for c in cfgs], "ur_hours": sum(s.duration for s in UR) / 3600,
                     "le2i_hours": sum(s.duration for s in LE) / 3600}
    path = OUT / "opt_summary.json"

    def save() -> None:
        json.dump(summary, open(path, "w"), indent=1, default=float)

    def report(name: str, res: list, S: list[Seq], extra: dict | None = None) -> None:
        summary[name] = {**summarize(res, S), **(extra or {})}
        log.info("%-28s %s", name, fmt(summary[name]))
        save()

    # --- within-UR nested CV
    report("ur_cv:rule", rule_events(UR), UR)
    r, ch = nested_cv(UR, "orig", [BASELINE])
    report("ur_cv:v1", r, UR, {"chosen": ch})
    r, ch = nested_cv(UR, "search", cfgs)
    report("ur_cv:best", r, UR, {"chosen": ch})
    pickle.dump(r, open(OUT / "ur_cv_best_events.pkl", "wb"))
    if LE:
        # --- cross-dataset
        report("ur2le:rule", rule_events(LE), LE)
        r, info = run_split(UR, LE, "orig", [BASELINE])
        report("ur2le:v1", r, LE, {"chosen": {k: v for k, v in info.items() if k != "table"}})
        r, info = run_split(UR, LE, "search", cfgs)
        report("ur2le:best", r, LE, {"chosen": {k: v for k, v in info.items() if k != "table"}})
        summary["ur2le:selection_table"] = info["table"]
        report("le2ur:rule", rule_events(UR), UR)
        r, info = run_split(LE, UR, "orig", [BASELINE])
        report("le2ur:v1", r, UR, {"chosen": {k: v for k, v in info.items() if k != "table"}})
        r, info = run_split(LE, UR, "search", cfgs)
        report("le2ur:best", r, UR, {"chosen": {k: v for k, v in info.items() if k != "table"}})
        # --- within-Le2i and pooled nested CV
        r, ch = nested_cv(LE, "orig", [BASELINE])
        report("le_cv:v1", r, LE, {"chosen": ch})
        r, ch = nested_cv(LE, "search", cfgs)
        report("le_cv:best", r, LE, {"chosen": ch})
        ALL = UR + LE
        r, ch = nested_cv(ALL, "orig", [BASELINE])
        report("pool_cv:v1", r, ALL, {"chosen": ch})
        r, ch = nested_cv(ALL, "search", cfgs)
        report("pool_cv:best", r, ALL, {"chosen": ch})
    save()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "extract-le2i":
        raise SystemExit("run le2i_extract.py with the backend .venv python (mediapipe 1.1.1; conda mediapipe 1.0.1 crashes: Metal service)")
    elif cmd == "bank":
        build_banks()
    elif cmd == "run":
        run_all(quick="--quick" in sys.argv)
    else:
        raise SystemExit(f"unknown cmd {cmd}")


if __name__ == "__main__":
    main()
