"""Fit final logistic model on all 70 sequences, pick threshold from out-of-fold predictions, export JSON + test vectors."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

import dataset as D
import evaluate as E
import features as F

PUB = Path("/Users/chloekang/kcf/app/frontend/public/models")


def predict_json(spec: dict, f: np.ndarray) -> float:
    z = (f - np.array(spec["mean"])) / np.array(spec["scale"])
    return 1.0 / (1.0 + math.exp(-(float(z @ np.array(spec["coef"])) + spec["intercept"])))


def main() -> None:
    S = D.load_all()
    m = E.fit("logreg", S)
    thr = E.choose_thr(S, E.oof_probs_strat("logreg", S))
    sc, lr = m.steps[0][1], m.steps[1][1]
    cv = json.load(open(E.OUT / "cv_summary.json"))
    spec = {
        "version": 1, "model": "logistic_regression", "source": "UR Fall Detection Dataset (Kwolek & Kepski 2014, CC BY-NC-SA 4.0), cam0 RGB, MediaPipe pose_landmarker_lite",
        "features": F.FEATURES, "window_s": F.WINDOW_S, "min_frames": F.MIN_FRAMES, "min_span_s": F.MIN_SPAN_S,
        "half_window_s": F.HALF_S, "landmark_ids": {"nose": 0, "shoulders": [11, 12], "hips": [23, 24]},
        "input": "normalized x,y from PoseLandmarker; x multiplied by image width/height (aspect) before geometry; NaN frames forward-filled (causal)",
        "mean": sc.mean_.tolist(), "scale": sc.scale_.tolist(), "coef": lr.coef_[0].tolist(), "intercept": float(lr.intercept_[0]),
        "threshold": thr, "refractory_s": E.REFRACTORY_S,
        "threshold_rule": f"OOF over 70 sequences: among thresholds with false alarms/h <= {E.TARGET_FA_PER_H} pick max sensitivity; if none, best event F1",
        "cv_logreg": {k: cv["logreg"][k] for k in ("sens", "fa_per_h", "f1", "lat_med")},
        "caveat": "lab dataset, side camera ~ waist height, 70 sequences; FA/h estimated from 0.093 h of negatives",
    }
    json.dump(spec, open(PUB / "fall_model.json", "w"), indent=1)
    # test vectors: real windows
    vecs = []
    byname = {s.name: s for s in S}
    for name, tq in (("fall-01", None), ("adl-01", 3.0), ("adl-35", None), ("fall-10", None)):
        s = byname[name]
        if tq is None:
            tq = (s.onset + 0.6) if s.is_fall else float(s.t_end[np.argmax(s.X[:, 0])])
        te = s.t_end[np.argmin(np.abs(s.t_end - tq))]
        msk = (s.t >= te - F.WINDOW_S - F.EPS_T) & (s.t <= te + 1e-12)
        ts, xy = s.t[msk], s.xy[msk]
        f = F.window_features(ts, xy, D.ASPECT)
        p = predict_json(spec, f)
        vecs.append({"name": name, "aspect": D.ASPECT, "ts": ts.tolist(), "xy": xy.astype(np.float64).tolist(),
                     "expected_features": f.tolist(), "expected_prob": p})
    # rule vector too
    s = byname["fall-10"]
    json.dump({"spec_ref": "fall_model.json", "tolerance": 1e-6, "feature_order": F.FEATURES, "windows": vecs,
               "rule": {"seq": "fall-10", "aspect": D.ASPECT, "ts": s.t.tolist(), "xy": s.xy.astype(np.float64).tolist(),
                        "expected_alarm_times": F.rule_alarm_times(s.t, s.xy, D.ASPECT)}},
              open(PUB / "fall_model_testvec.json", "w"))
    # independent check: json-weights prediction == sklearn
    for v in vecs:
        sk = m.predict_proba(np.array(v["expected_features"])[None])[0, 1]
        assert abs(sk - v["expected_prob"]) < 1e-9, (sk, v["expected_prob"])
    print("thr", thr, [(v["name"], len(v["ts"]), round(v["expected_prob"], 4)) for v in vecs])
    print(dict(zip(F.FEATURES, np.round(lr.coef_[0], 2))))


if __name__ == "__main__":
    main()
