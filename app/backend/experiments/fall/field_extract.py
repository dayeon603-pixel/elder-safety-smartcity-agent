"""Landmark extraction for field-like test sets (run with the backend .venv python; see le2i_extract.py).
  MCFD   : Multiple Cameras Fall Dataset (Auvinet et al. 2010, Univ. Montreal; staged falls + ADL, 8 elevated cameras,
           720x480; container fps header says 120 but fall durations (25-76 frames) are only plausible at 30 fps -> 30).
  CCTVF  : Kaggle jonathannield/cctv-action-recognition-dataset (clips named NTU_fight####_<class>_k, i.e. cut from the
           real-world CCTV-Fights footage, Perez, Kot, Rocha, ICASSP 2019; clip-level class labels only).
Usage: python field_extract.py <mcfd|cctvf> <shard> <nshards>
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np

from le2i_extract import ex_full

ROOT = Path("/Users/chloekang/kcf/data/cache/extra")
log = logging.getLogger("field")


def jobs(ds: str) -> list[tuple[str, Path, float | None]]:
    if ds == "mcfd":
        return [(f"{p.parent.name}_{p.stem}", p, 30.0) for p in sorted((ROOT / "mcfd/raw/dataset/dataset").glob("chute*/cam*.avi"))]
    root = ROOT / "cctvfights/raw/Videos/Videos"
    rng = np.random.default_rng(42)  # compute-limited: all fall clips + 90 random clips from every other class
    ps: list[Path] = []
    for d in sorted(root.iterdir()):
        v = sorted(d.glob("*.mp4"))
        ps += v if d.name == "fall" else [v[i] for i in sorted(rng.choice(len(v), min(90, len(v)), replace=False))]
    return [(f"{p.parent.name}__{p.stem}", p, None) for p in ps]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ds, shard, n = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    out = ROOT / ds / "landmarks"
    out.mkdir(parents=True, exist_ok=True)
    import cv2

    for name, p, fps in jobs(ds)[shard::n]:
        o = out / f"{name}.npz"
        if o.exists():
            continue
        cap = cv2.VideoCapture(str(p))
        w, h = cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        cap.release()
        t, lm, f = ex_full(p, fps)
        np.savez_compressed(o, t=t, lm=lm, fps=f, aspect=w / h)
        log.info("done %s frames=%d det=%.2f", name, len(t), float(np.mean(~np.isnan(lm[:, 0, 0]))))


if __name__ == "__main__":
    main()
