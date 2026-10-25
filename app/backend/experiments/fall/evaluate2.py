"""Stage-2 confirm + descent features. Same CV protocol as evaluate.py (sequence-grouped 5-fold outer,
inner 5-fold OOF for ALL tuned choices: threshold, confirm T, descent gate, stillness gate). No test-fold tuning.

Confirm (stage 2) for a stage-1 alarm at t_a (prob>=thr):
  gate  : max over windows ending in [t_a-1.0, t_a] of peak_hip_vy_L_s >= VMIN   (fast descent; deliberate lying is slower)
  wait  : t_c = min(t_a + T, last frame of sequence)   (if the clip ends first, evaluated on the frames available; counted)
  check : mean torso angle over (t_c-0.5, t_c] >= 60 deg  AND  mean hip speed (L/s) over same span <= STILL_MAX
  alarm : fired at t_c (latency counts the wait).  T=0 disables the stage.
"""
from __future__ import annotations

import dataclasses
import json
import pickle

import numpy as np

import dataset as D
import evaluate as E
import features as F

CONF_HALF = 0.5
ANG_MIN = 60.0
PEAK_COL = F.FEATURES_V2.index("peak_hip_vy_L_s")
GRID_T = (0, 1.0, 2.0, 3.0)
GRID_V = (0.0, 1.0, 2.0)
GRID_S = (np.inf, 1.0)
GRID = np.round(np.arange(0.05, 1.0, 0.05), 3)
E.GRID = GRID  # coarser thr grid for the nested search

_SIG: dict[str, tuple[np.ndarray, np.ndarray]] = {}


def sig(s: D.Seq) -> tuple[np.ndarray, np.ndarray]:
    """Per-frame torso angle and hip speed (torso-lengths/s, L = causal 1 s max torso length)."""
    if s.name not in _SIG:
        g = F.frame_signals(s.xy, D.ASPECT)
        n = len(s.t)
        L = np.empty(n)
        lo = 0
        for i in range(n):
            while s.t[lo] < s.t[i] - 1.0 - F.EPS_T:
                lo += 1
            L[i] = g["tl"][lo : i + 1].max()
        sp = np.zeros(n)
        sp[1:] = np.hypot(*(np.diff(g["hip"], axis=0).T)) / np.maximum(np.diff(s.t), 1e-6) / np.maximum(L[1:], 1e-6)
        _SIG[s.name] = (g["ang"], sp)
    return _SIG[s.name]


def alarms(s: D.Seq, p: np.ndarray, thr: float, T: float, vmin: float, smax: float) -> tuple[list[float], int]:
    """Returns (alarm times, number of confirms evaluated on truncated clips)."""
    hit = np.where(p >= thr)[0]
    if T == 0:
        return [float(s.t_end[i]) for i in hit], 0
    ang, sp = sig(s)
    out, trunc = [], 0
    for i in hit:
        ta = s.t_end[i]
        w = (s.t_end >= ta - 1.0) & (s.t_end <= ta)
        if s.X2[w, PEAK_COL].max() < vmin:
            continue
        tc = min(ta + T, float(s.t[-1]))
        if ta + T > s.t[-1]:
            trunc += 1
        m = (s.t > tc - CONF_HALF) & (s.t <= tc)
        if m.sum() < 2:
            continue
        if ang[m].mean() >= ANG_MIN and sp[m].mean() <= smax:
            out.append(float(tc))
    return sorted(out), trunc


def cfgs(mode: str) -> list[tuple[float, float, float]]:
    if mode == "none":
        return [(0.0, 0.0, np.inf)]
    if mode.startswith("T"):
        return [(float(mode[1:]), 0.0, np.inf)]
    return [(T, v, s) for T in GRID_T for v in GRID_V for s in GRID_S if T > 0 or (v == 0 and s == np.inf)]


def evs(S, P, thr, c):
    return [E.event(s, alarms(s, p, thr, *c)[0]) for s, p in zip(S, P)]


def select(S, P, mode):
    ig = np.array([s.is_fall for s in S])
    idx = np.arange(len(S))
    best, bk = None, None
    for c in cfgs(mode):
        for thr in GRID:
            m = E.metrics(evs(S, P, thr, c), ig, idx)
            ok = m["fa_per_h"] <= E.TARGET_FA_PER_H
            key = (1, m["sens"], m["f1"]) if ok else (0, m["f1"], m["sens"])
            if bk is None or key > bk:
                best, bk = (float(thr), c), key
    return best


def with_x(S, v2: bool):
    return [dataclasses.replace(s, X=s.X2 if v2 else s.X) for s in S]


def run(S, v2: bool, modes: list[str]):
    Sx = with_x(S, v2)
    n = len(S)
    fold = np.zeros(n, int)
    rng = np.random.default_rng(E.SEED)
    for cls in (True, False):
        ids = rng.permutation([i for i, s in enumerate(S) if s.is_fall == cls])
        fold[ids] = np.arange(len(ids)) % 5
    res = {m: [None] * n for m in modes}
    chosen = {m: [] for m in modes}
    trunc = {m: 0 for m in modes}
    for k in range(5):
        tr = [i for i in range(n) if fold[i] != k]
        te = [i for i in range(n) if fold[i] == k]
        trs = [Sx[i] for i in tr]
        inner = E.oof_probs_strat("logreg", trs)
        m_full = E.fit("logreg", trs)
        for mode in modes:
            thr, c = select(trs, inner, mode)
            chosen[mode].append((thr, list(map(float, c))))
            for i in te:
                p = m_full.predict_proba(Sx[i].X)[:, 1]
                al, tc = alarms(Sx[i], p, thr, *c)
                trunc[mode] += tc > 0
                res[mode][i] = E.event(Sx[i], al)
    return res, chosen, trunc


def main() -> None:
    S = D.load_all()
    ig = np.array([s.is_fall for s in S])
    lying = np.array([(not s.is_fall) and s.has_lying_label for s in S])
    other = np.array([(not s.is_fall) and not s.has_lying_label for s in S])
    out = {}
    rule = [E.event(s, F.rule_alarm_times(s.t, s.xy, D.ASPECT)) for s in S]
    runs = {"rule": (rule, None)}
    for nm, v2, modes in (("v1", False, ["none", "T1", "T2", "T3", "auto"]), ("v2", True, ["none", "T2", "auto"])):
        r, ch, tr = run(S, v2, modes)
        for m in modes:
            runs[f"{nm}:{m}"] = (r[m], ch[m])
            print(nm, m, "chosen", ch[m], "clips truncated before confirm:", tr[m])
    # rule fairness subset: lying posture labelled for >= 1.5 s after the fall completes
    feasible = np.array([s.is_fall and (s.duration - s.lying) >= 1.5 for s in S])
    for k, (res, ch) in runs.items():
        o = E.boot(res, ig)
        for nm, mask in (("adl_lying", lying), ("adl_other", other)):
            ii = np.where(mask)[0]
            o[nm] = {"n_seq": int(mask.sum()), "seq_with_fa": int(sum(res[i].fa > 0 for i in ii)), "fa": int(sum(res[i].fa for i in ii))}
        fi = np.where(feasible)[0]
        o["sens_feasible"] = {"n": int(len(fi)), "tp": int(sum(res[i].detected for i in fi)), "sens": float(np.mean([res[i].detected for i in fi]))}
        o["latencies"] = [e.latency for e in res if e.detected]
        o["chosen"] = ch
        out[k] = o
        print(k, {a: tuple(round(float(x), 3) for x in o[a]) for a in ("sens", "fa_per_h", "f1", "lat_med")},
              "lying-FAseq", o["adl_lying"]["seq_with_fa"], "/", o["adl_lying"]["n_seq"], "other-FAseq", o["adl_other"]["seq_with_fa"], "/", o["adl_other"]["n_seq"],
              "sens_feasible", o["sens_feasible"])
    json.dump(out, open(E.OUT / "cv_summary_v2.json", "w"), indent=1, default=float)


if __name__ == "__main__":
    main()
