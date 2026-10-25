"""Le2i landmark extraction (MediaPipe pose_landmarker_lite, same model/pipeline as extract_pose.py).
Run with the backend venv python (conda's mediapipe 1.0.1 aborts: Metal GPU service unavailable in this session):
  /Users/chloekang/kcf/app/backend/.venv/bin/python le2i_extract.py <shard> <nshards>
Dataset: Le2i / IMVIA Fall (Charfi et al. 2013, J. Electronic Imaging 22(4):041106), Kaggle mirror
tuyenldvn/falldataset-imvia (licence "unknown"; offline evaluation only).
Annotations: line1 = fall start frame, line2 = fall end frame (0,0 for ADL). Lecture_room/Office (60 videos) and
nothing else lack annotation files in the mirror -> excluded.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

import numpy as np

log = logging.getLogger("le2i")
ROOT = Path("/Users/chloekang/kcf")
LE_DIR = ROOT / "data/cache/extra/le2i"

def extract_le2i(shard: int = 0, nshards: int = 1) -> None:
    """Find Le2i videos + annotation files, run pose extraction, write meta.json {name: [is_fall, onset_s, lying_s]}.
    Le2i annotation txt: line1 = fall start frame, line2 = fall end frame (0,0 for ADL), then per-frame boxes."""
    import cv2

    raw = LE_DIR / "raw"
    vids = sorted(p for p in raw.rglob("*") if p.suffix.lower() in (".avi", ".mp4"))
    log.info("found %d videos", len(vids))
    (LE_DIR / "landmarks").mkdir(exist_ok=True)
    meta, todo = {}, []
    for v in vids:
        ann_dir = v.parent.parent / "Annotation_files"
        cand = [ann_dir / (v.stem + ".txt"), v.parent.parent / "Annotations_files" / (v.stem + ".txt")]  # sic: Coffee_room_02
        ann = next((a for a in cand if a.exists()), None)
        scene = v.parent.parent.name if v.parent.name.lower() in ("videos", "video") else v.parent.name
        name = re.sub(r"[^A-Za-z0-9_]+", "_", f"{scene}_{v.stem}")
        if ann is None:
            log.warning("no annotation for %s", v)
            continue
        nums = [int(float(x)) for x in re.findall(r"-?\d+\.?\d*", "\n".join(ann.read_text().splitlines()[:2]))][:2]
        cap = cv2.VideoCapture(str(v))
        fps, nfr = cap.get(cv2.CAP_PROP_FPS) or 25.0, cap.get(cv2.CAP_PROP_FRAME_COUNT)
        w, h = cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        cap.release()
        is_fall = len(nums) == 2 and nums[0] > 0 and nums[1] > nums[0]
        meta[name] = [bool(is_fall), ((nums[0] - 1) / fps) if is_fall else float("nan"), ((nums[1] - 1) / fps) if is_fall else float("nan"), w / h]
        todo.append((name, v, w, h, fps))
    json.dump(meta, open(LE_DIR / "meta.json", "w"))
    log.info("%d annotated; falls=%d; dims=%s", len(meta), sum(m[0] for m in meta.values()),
             sorted({(w, h) for _, _, w, h, _ in todo}))

    def one(name: str, v: Path) -> str:
        o = LE_DIR / "landmarks" / f"{name}.npz"
        if o.exists():
            return name
        t, lm, fps = ex_full(v)
        np.savez_compressed(o, t=t, lm=lm, fps=fps)
        return name

    for n, v, *_ in todo[shard::nshards]:  # serial per process: mediapipe crashes inside loky workers
        one(n, v)
        log.info('done %s', n)


def ex_full(path: Path, fps_override: float | None = None):
    """extract_pose.extract variant for full-frame (not side-by-side depth|rgb) videos."""
    import cv2
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision

    opts = vision.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(ROOT / "app/frontend/public/models/pose_landmarker_lite.task"),
                                 delegate=BaseOptions.Delegate.CPU),
        running_mode=vision.RunningMode.VIDEO, num_poses=1)
    cap = cv2.VideoCapture(str(path))
    fps = fps_override or cap.get(cv2.CAP_PROP_FPS) or 25.0
    lms = []
    with vision.PoseLandmarker.create_from_options(opts) as lm:
        i = 0
        while True:
            ok, fr = cap.read()
            if not ok:
                break
            if fr.shape[1] > 320:  # field sets are larger than UR/Le2i (320 wide); downscale to the training resolution
                fr = cv2.resize(fr, (320, int(round(fr.shape[0] * 320 / fr.shape[1]))), interpolation=cv2.INTER_AREA)
            rgb = np.ascontiguousarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))
            res = lm.detect_for_video(mp.Image(mp.ImageFormat.SRGB, rgb), int(i * 1000 / fps))
            a = np.full((33, 4), np.nan, np.float32)
            if res.pose_landmarks:
                for k, p in enumerate(res.pose_landmarks[0]):
                    a[k] = (p.x, p.y, p.z, p.visibility)
            lms.append(a)
            i += 1
    return np.arange(len(lms)) / fps, np.stack(lms), fps


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    extract_le2i(int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else extract_le2i()
