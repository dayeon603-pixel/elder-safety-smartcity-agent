"""Field-like re-validation: train on lab data only, test (no tuning on test) on
  (a) MCFD  - Multiple Cameras Fall Dataset (Auvinet, Rougier, Meunier, St-Arnaud, Rousseau 2010, Univ. Montreal, TR 1350):
              STAGED falls + ADL (sit, crouch, lying on sofa...), 8 elevated cameras -> per-camera breakdown.
              Kaggle mirror soumicksarker/multiple-cameras-fall-dataset. Licence: research use (verify; Kaggle: not stated).
  (b) CCTVF - Kaggle jonathannield/cctv-action-recognition-dataset: REAL-WORLD CCTV clips (names NTU_fight*/UCFCRIME_*,
              i.e. cut from CCTV-Fights (Perez, Kot, Rocha, ICASSP 2019) and UCF-Crime (Sultani et al. CVPR 2018)).
              Clip-level class labels only (no onset): fall-clip detection = any alarm in clip; FA/h from the 12 non-fall
              classes. Provenance/licence of the Kaggle re-package unverified -> offline evaluation only.
Backtest-style numbers are hypotheses about these specific sets.
Usage: python field_eval.py run
"""
from __future__ import annotations

import json
import logging
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import Parallel, delayed

import evaluate as E
import optimize as O

log = logging.getLogger("field")
X = Path("/Users/chloekang/kcf/data/cache/extra")
FOUT = O.OUT / "field"
MCFD_FPS = 30.0
CUT_AFTER_ONSET_FR = {4: 93, 23: 76}  # chutes with 2 falls: GT 'moving up' starts this many frames after fall onset (tech report 4.3)


def mcfd_falls() -> dict[tuple[int, int], list[tuple[float, float]]]:
    d = pd.read_csv(X / "mcfd/raw/data_tuple3.csv")
    out = {}
    for (c, cam), g in d.groupby(["chute", "cam"]):
        grp: list[list[float]] = []
        for r in g.sort_values("start").itertuples():
            if r.label == 1:
                if grp and r.start <= grp[-1][1] + 2:
                    grp[-1][1] = r.end
                else:
                    grp.append([r.start, r.end])
        out[(int(c), int(cam))] = [(a / MCFD_FPS, b / MCFD_FPS) for a, b in grp]
    return out


def _bank(s: O.Seq) -> O.Seq:
    for W in O.WINDOWS:
        te, B = O.bank_stream(s.t, s.xy, W, s.aspect)
        s.banks[W] = (te, B, np.zeros(len(te), np.int8))  # labels unused (test only)
    return s


def _cut(t: np.ndarray, xy: np.ndarray, a: float, b: float) -> tuple[np.ndarray, np.ndarray]:
    m = (t >= a) & (t < b)
    return t[m] - a, xy[m]


def build_mcfd_one(path: Path, chute: int, cam: int, falls: list) -> list[O.Seq]:
    t, xy, fps, dur = O.load_landmarks(path)
    asp = 720 / 480
    segs = []
    if not falls:
        segs.append((0.0, dur, False, float("nan"), float("nan")))
    elif len(falls) == 1:
        segs.append((0.0, dur, True, falls[0][0], falls[0][1]))
    else:
        cut = falls[0][0] + CUT_AFTER_ONSET_FR[chute] / MCFD_FPS
        segs.append((0.0, cut, True, falls[0][0], falls[0][1]))
        segs.append((cut, dur, True, falls[1][0] - cut, falls[1][1] - cut))
    out = []
    for k, (a, b, isf, on, ly) in enumerate(segs):
        ts, xs = _cut(t, xy, a, b)
        if len(ts) < 10:
            continue
        s = O.Seq("mcfd", f"chute{chute:02d}_cam{cam}_s{k}", isf, on, ly, (b - a), asp, ts, xs)
        out.append(_bank(s))
    return out


def build_mcfd() -> list[O.Seq]:
    falls = mcfd_falls()
    jobs = []
    for p in sorted((X / "mcfd/landmarks").glob("chute*_cam*.npz")):
        c, cam = int(p.stem[5:7]), int(p.stem.split("cam")[1])
        jobs.append((p, c, cam, falls.get((c, cam), [])))
    res = Parallel(n_jobs=8)(delayed(build_mcfd_one)(*j) for j in jobs)
    return [s for r in res for s in r]


def build_cctvf_one(path: Path) -> O.Seq | None:
    z = np.load(path)
    t, xy, fps, dur = O.load_landmarks(path)
    if len(t) < 10:
        return None
    cls = path.stem.split("__")[0]
    s = O.Seq("cctvf", path.stem, cls == "fall", 0.0, dur, dur, float(z["aspect"]), t, xy)
    return _bank(s)


def build_cctvf() -> list[O.Seq]:
    from field_extract import jobs

    ps = [X / "cctvf/landmarks" / f"{n}.npz" for n, _, _ in jobs("cctvf")]
    ps = [p for p in ps if p.exists()]
    return [s for s in Parallel(n_jobs=8)(delayed(build_cctvf_one)(p) for p in ps) if s is not None]


def subset_summary(res: list, S: list, idx: list[int], B: int = 2000) -> dict:
    """Bootstrap over sequences within a subset; robust to subsets with no falls / no negatives."""
    idx = np.array(idx)
    isf = np.array([S[i].is_fall for i in idx])
    rng = np.random.default_rng(O.SEED)
    keys = ("sens", "fa_per_h", "f1", "lat_med")

    def m(ii: np.ndarray) -> dict:
        f = [i for i in ii if S[i].is_fall]
        tp = sum(res[i].detected for i in f)
        fa = sum(res[i].fa for i in ii)
        hrs = sum(res[i].neg_s for i in ii) / 3600
        sens = tp / len(f) if f else np.nan
        prec = tp / (tp + fa) if tp + fa else 0.0
        f1 = 2 * prec * sens / (prec + sens) if f and prec + sens else (0.0 if f else np.nan)
        lat = [res[i].latency for i in f if res[i].detected]
        return {"sens": sens, "fa_per_h": fa / hrs if hrs > 0 else np.nan, "f1": f1,
                "lat_med": float(np.median(lat)) if lat else np.nan}

    pt = m(idx)
    F_, A_ = idx[isf], idx[~isf]
    ds = {k: [] for k in keys}
    for _ in range(B):
        ii = np.concatenate([rng.choice(F_, len(F_)) if len(F_) else F_, rng.choice(A_, len(A_)) if len(A_) else A_])
        mm = m(ii)
        for k in keys:
            ds[k].append(mm[k])
    out = {k: [float(pt[k])] + [float(x) for x in np.nanpercentile(ds[k], [2.5, 97.5])] if not np.all(np.isnan(ds[k])) else [float(pt[k]), np.nan, np.nan] for k in keys}
    out["n_fall"], out["n_neg_seq"] = int(len(F_)), int(len(A_))
    out["neg_hours"] = float(sum(res[i].neg_s for i in idx) / 3600)
    out["fa"] = int(sum(res[i].fa for i in idx))
    out["tp"] = int(sum(res[i].detected for i in F_))
    return out


def breakdowns(name: str, res: list, S: list) -> dict:
    o = {"all": subset_summary(res, S, list(range(len(S))))}
    if S[0].ds == "mcfd":
        for cam in range(1, 9):
            ii = [i for i, s in enumerate(S) if f"_cam{cam}_" in s.name]
            o[f"cam{cam}"] = subset_summary(res, S, ii)
    if S[0].ds == "cctvf":
        classes = sorted({s.name.split("__")[0] for s in S})
        for c in classes:
            ii = [i for i, s in enumerate(S) if s.name.startswith(c + "__")]
            o[f"class:{c}"] = {"n_clips": len(ii), "alarm_clips": int(sum((res[i].detected if S[i].is_fall else res[i].fa > 0) for i in ii)),
                               "fa": int(sum(res[i].fa for i in ii)), "hours": float(sum(S[i].duration for i in ii) / 3600)}
        calm = {"walk", "stand", "sit", "lying_down", "sneak", "run"}
        ii = [i for i, s in enumerate(S) if (not s.is_fall) and s.name.split("__")[0] in calm]
        fall = [i for i, s in enumerate(S) if s.is_fall]
        o["fall_vs_everyday"] = subset_summary(res, S, fall + ii)
    return o


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    O.set_seed()
    FOUT.mkdir(parents=True, exist_ok=True)
    cache = FOUT / "field_banks.pkl"
    if cache.exists():
        MC, CC = pickle.load(open(cache, "rb"))
    else:
        MC, CC = build_mcfd(), build_cctvf()
        pickle.dump((MC, CC), open(cache, "wb"))
    log.info("MCFD seqs=%d falls=%d neg_h=%.3f | CCTVF clips=%d falls=%d hours=%.3f",
             len(MC), sum(s.is_fall for s in MC), sum((s.duration if not s.is_fall else s.onset) for s in MC) / 3600,
             len(CC), sum(s.is_fall for s in CC), sum(s.duration for s in CC) / 3600)
    UR, LE = O.load_ds("ur"), O.load_ds("le2i")
    tests = {"mcfd": MC, "cctvf": CC}
    allt = MC + CC
    cut = len(MC)
    out: dict = {"meta": {"mcfd_seqs": len(MC), "cctvf_clips": len(CC)}}
    for tr_name, train in (("ur", UR), ("ur+le2i", UR + LE)):
        for mode, cfgs in (("v1", [O.BASELINE]), ("best", O.CONFIGS)):
            res, info = O.run_split(train, allt, "orig" if mode == "v1" else "search", cfgs)
            tag = f"train={tr_name}:{mode}"
            out[tag] = {"chosen": {k: v for k, v in info.items() if k != "table"}}
            out[tag]["mcfd"] = breakdowns(tag, res[:cut], MC)
            out[tag]["cctvf"] = breakdowns(tag, res[cut:], CC)
            log.info("%s chosen=%s", tag, out[tag]["chosen"])
            for k in ("mcfd", "cctvf"):
                a = out[tag][k]["all"]
                log.info("  %s sens %.3f [%.3f,%.3f] FA/h %.1f [%.1f,%.1f] F1 %.3f lat %.2f (tp %d/%d fa %d/%.3fh)", k, *a["sens"], *a["fa_per_h"], a["f1"][0], a["lat_med"][0], a["tp"], a["n_fall"], a["fa"], a["neg_hours"])
            json.dump(out, open(FOUT / "field_summary.json", "w"), indent=1, default=float)
    rr = O.rule_events(allt)
    out["rule"] = {"mcfd": breakdowns("rule", rr[:cut], MC), "cctvf": breakdowns("rule", rr[cut:], CC)}
    for k in ("mcfd", "cctvf"):
        a = out["rule"][k]["all"]
        log.info("rule %s sens %.3f [%.3f,%.3f] FA/h %.1f [%.1f,%.1f] F1 %.3f lat %.2f", k, *a["sens"], *a["fa_per_h"], a["f1"][0], a["lat_med"][0])
    json.dump(out, open(FOUT / "field_summary.json", "w"), indent=1, default=float)


if __name__ == "__main__":
    run() if len(sys.argv) < 2 or sys.argv[1] == "run" else sys.exit("usage: field_eval.py run")
