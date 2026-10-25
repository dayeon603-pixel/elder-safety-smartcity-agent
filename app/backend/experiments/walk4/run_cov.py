"""Coverage by 구 x hour x daytype: share of 65+ / total population within R (one-way walk) of >=1 OPEN AED.

Hypothesis-generating model output, not a measurement: residence-based exposure (where people sleep), one-way radius,
AED 'open' = open at h:30 per NMC-registered hours (self-reported by managers), 2026 AED snapshot.
"""
from __future__ import annotations

import json
import logging
import pickle
import time

import numpy as np

import engine as E
import w4lib as L

log = logging.getLogger("walk4.cov")
SCHEMES = ["w_main", "w_resonly", "w_foot", "w_uni"]
RKEYS = list(L.RADII)  # v0.8_2min, v0.8_4min, v1.2_2min, v1.2_4min
DT = ["wd", "sat", "sun", "hol"]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    I = E.load_inputs(); eng = E.Engine(I["A"], I["sites"]); dem = I["dem"]
    gus = sorted(dem.gu.unique()); assert len(gus) == 25
    gi = dem.gu.map({g: i for i, g in enumerate(gus)}).to_numpy()
    W = np.stack([dem[f"{k}_{s}"].to_numpy() for s in SCHEMES for k in ("p65", "ptot")], axis=1)  # npts x 8
    names = [(s, k) for s in SCHEMES for k in ("p65", "ptot")]
    denom = np.stack([np.bincount(gi, weights=W[:, c], minlength=25) for c in range(W.shape[1])])  # 8 x 25
    dnode = dem.node.to_numpy(); dsnap = dem.snap.to_numpy()
    cache: dict[bytes, np.ndarray] = {}

    def covered(mask: np.ndarray) -> np.ndarray:
        """-> [radius4, col8, gu25] covered population."""
        k = np.packbits(mask).tobytes()
        if k in cache:
            return cache[k]
        d = eng.dist(mask)[dnode] + dsnap
        out = np.zeros((len(RKEYS), W.shape[1], 25))
        for r, rk in enumerate(RKEYS):
            c = d <= L.RADII[rk]
            for j in range(W.shape[1]):
                out[r, j] = np.bincount(gi, weights=W[:, j] * c, minlength=25)
        cache[k] = out
        return out

    t0 = time.time()
    res: dict = {"gus": gus, "names": names, "radii": RKEYS, "denom": denom,
                 "cov": {}, "static": covered(np.ones(len(I["sites"]), bool))}
    for sc in ("primary", "upper", "asreport"):
        arr = np.zeros((len(DT), 24, len(RKEYS), W.shape[1], 25))
        for a, dt in enumerate(DT):
            for h in range(24):
                arr[a, h] = covered(I["sched"][sc][dt][:, h])
        res["cov"][sc] = arr
        log.info("%s done, unique dijkstras so far %d, %.0fs", sc, len(cache), time.time() - t0)
    res["n_sites"] = int(len(I["sites"])); res["n_aed"] = int(len(I["aed"]))
    with open(L.OUT / "cov.pkl", "wb") as f:
        pickle.dump(res, f)
    log.info("saved")


if __name__ == "__main__":
    main()
