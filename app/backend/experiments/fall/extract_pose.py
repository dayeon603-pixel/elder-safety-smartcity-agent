"""Extract MediaPipe pose_landmarker_lite landmarks from UR Fall (cam0 RGB half of each mp4).

Source: Kwolek & Kepski 2014, UR Fall Detection Dataset (CC BY-NC-SA 4.0).
The cam0 mp4 is a side-by-side render: left 320 px = depth, right 320 px = RGB. We use RGB only.
Output: data/cache/extra/fall/landmarks/<seq>.npz with
  t (s, from frame idx / fps), lm (T,33,4) = x,y (normalized to the 320x240 RGB frame), z, visibility; NaN if no detection.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions, vision

ROOT = Path("/Users/chloekang/kcf")
RAW = ROOT / "data/cache/extra/fall/raw"
OUT = ROOT / "data/cache/extra/fall/landmarks"
MODEL = ROOT / "app/frontend/public/models/pose_landmarker_lite.task"
log = logging.getLogger("extract")


def extract(path: Path) -> tuple[np.ndarray, np.ndarray, float]:
    opts = vision.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(MODEL)),
        running_mode=vision.RunningMode.VIDEO,
        num_poses=1,
    )
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    lms: list[np.ndarray] = []
    with vision.PoseLandmarker.create_from_options(opts) as lm:
        i = 0
        while True:
            ok, fr = cap.read()
            if not ok:
                break
            rgb = np.ascontiguousarray(cv2.cvtColor(fr[:, 320:], cv2.COLOR_BGR2RGB))
            res = lm.detect_for_video(mp.Image(mp.ImageFormat.SRGB, rgb), int(i * 1000 / fps))
            a = np.full((33, 4), np.nan, np.float32)
            if res.pose_landmarks:
                for k, p in enumerate(res.pose_landmarks[0]):
                    a[k] = (p.x, p.y, p.z, p.visibility)
            lms.append(a)
            i += 1
    return np.arange(len(lms)) / fps, np.stack(lms), fps


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    OUT.mkdir(parents=True, exist_ok=True)
    for p in sorted(RAW.glob("*-cam0.mp4")):
        o = OUT / (p.name.replace("-cam0.mp4", "") + ".npz")
        if o.exists():
            continue
        t, lm, fps = extract(p)
        np.savez_compressed(o, t=t, lm=lm, fps=fps)
        log.info("%s frames=%d detected=%.1f%%", p.name, len(t), 100 * np.mean(~np.isnan(lm[:, 0, 0])))


if __name__ == "__main__":
    sys.exit(main())
