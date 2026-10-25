"""Add the descent gate to the shipped v1 logistic model (same weights; threshold re-selected on gated OOF) and extend the
test vectors with gate cases + streaming cases (incl. 'lying still from start': real landmark frame held still).
Writes public/models/fall_model.json and fall_model_testvec.json (originals backed up in results/opt/backup_v1/)."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

import evaluate as E
import export_model as X
import features as F
import optimize as O

PUB = Path("/Users/chloekang/kcf/app/frontend/public/models")
GATE_MIN = 0.1  # hip_drop_L > 0.1 torso lengths; chosen: the smallest grid value that does not worsen sens or FA/h in any of 4 splits
COL = F.FEATURES.index("hip_drop_L")


def ref_stream(spec: dict, ts: np.ndarray, xy: np.ndarray, aspect: float, gate: bool) -> list[float]:
    """Python reference of the TS stepAi (causal windows, optional gate, refractory)."""
    alarms: list[float] = []
    last = None
    lo = 0
    for i in range(len(ts)):
        while ts[lo] < ts[i] - spec["window_s"] - F.EPS_T:
            lo += 1
        f = F.window_features(ts[lo : i + 1], xy[lo : i + 1], aspect)
        if f is None:
            continue
        p = X.predict_json(spec, f)
        ok = (f[COL] > spec["descent_gate"]["min"]) if gate else True
        if p >= spec["threshold"] and ok and (last is None or ts[i] - last >= spec["refractory_s"]):
            alarms.append(float(ts[i]))
            last = ts[i]
    return alarms


def main() -> None:
    spec = json.load(open(PUB / "fall_model.json"))
    assert "descent_gate" not in spec, "already gated; restore from results/opt/backup_v1 first"
    vec = json.load(open(PUB / "fall_model_testvec.json"))
    UR = O.load_ds("ur")
    oof = O.inner_oof(UR, [O.BASELINE], n_jobs=6)[O.BASELINE]
    Pg = [np.where(s.banks[1.0][1][:, COL] > GATE_MIN, p, 0.0) for s, p in zip(UR, oof)]
    (_, pp), _ = O.select(UR, {O.BASELINE: Pg}, "orig")
    old_thr = spec["threshold"]
    # weights unchanged: verify against a fresh fit
    m = E.fit("logreg", __import__("dataset").load_all())
    sc, lr = m.steps[0][1], m.steps[1][1]
    assert np.allclose(sc.mean_, spec["mean"]) and np.allclose(lr.coef_[0], spec["coef"])
    spec["version"] = 2
    spec["threshold"] = float(pp["thr"])
    spec["descent_gate"] = {"feature": "hip_drop_L", "min": GATE_MIN,
                            "rule": "alarm only if hip_drop_L (hip y at window end minus start, torso lengths, +y = down) > min; blocks a person already lying still (no descent in the window)"}
    spec["threshold_rule"] += f"; threshold re-selected on gated OOF (ungated v1 threshold was {old_thr})"
    json.dump(spec, open(PUB / "fall_model.json", "w"), indent=1)
    by = {s.name: s for s in UR}
    cases = []
    # real pose frame held still (lying from frame 0): fall-26 frame 43 (static-window prob 0.96 with the v1 weights)
    s = by["fall-26"]
    fr = s.xy[43]
    ts = np.arange(0, 3.0, 1 / 30)
    cases.append(("lying_still_from_start (real landmark frame fall-26#43 held 3 s, synthetic timing)", ts, np.repeat(fr[None], len(ts), 0), O.ASPECT))
    s = by["fall-01"]
    msk = s.t <= s.onset + 4.0
    cases.append(("fall-01 (real, up to onset+4 s)", s.t[msk], s.xy[msk], O.ASPECT))
    s = by["adl-35"]
    cases.append(("adl-35 (real ADL)", s.t, s.xy, O.ASPECT))
    vec["stream_cases"] = []
    for name, t, xy, asp in cases:
        f = F.window_features(t[t >= t[-1] - 1.0 - F.EPS_T], xy[t >= t[-1] - 1.0 - F.EPS_T], asp)
        vec["stream_cases"].append({
            "name": name, "aspect": asp, "ts": np.asarray(t).tolist(), "xy": xy.astype(np.float64).tolist(),
            "last_window_features": f.tolist(), "last_window_prob": X.predict_json(spec, f), "last_window_gate_open": bool(f[COL] > GATE_MIN),
            "expected_alarm_times_gated": ref_stream(spec, t, xy, asp, True),
            "expected_alarm_times_ungated": ref_stream(spec, t, xy, asp, False)})
        c = vec["stream_cases"][-1]
        print(name, "p_last=%.3f" % c["last_window_prob"], "gated:", c["expected_alarm_times_gated"], "ungated:", c["expected_alarm_times_ungated"])
    json.dump(vec, open(PUB / "fall_model_testvec.json", "w"))
    print("thr", old_thr, "->", spec["threshold"])


if __name__ == "__main__":
    main()
