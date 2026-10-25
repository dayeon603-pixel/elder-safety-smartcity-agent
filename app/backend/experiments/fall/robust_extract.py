"""Degraded-video landmark extraction for the robustness study (run with the backend .venv python).
Degradations are applied to each 320-wide RGB frame BEFORE pose estimation (seeded per video+level -> reproducible).
  lowlight : out = 255*(x/255)^gamma + N(0,sigma)            (sensor noise added after darkening, no gain)
  rain     : n semi-transparent bright streaks (len 10-22 px, ~80 deg) + haze (contrast*0.9+offset) + Gaussian blur
  lowres   : bicubic downscale to s*size + JPEG(quality q) + upscale back
Pre-filters (cheap, CPU, <15 ms/frame at 320x240):
  light  : adaptive gamma (mean luma -> 110) -> CLAHE(L, clip 2) -> bilateral(5,30,30)
  rain   : median blur 5 + CLAHE(L, clip 2)
  lowres : bilateral(5, 40, 40) + unsharp mask (1.0)
These are synthetic proxies, NOT real night/rain footage (no real rain/night fall video was available).
Usage: python robust_extract.py <shard> <nshards>
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path("/Users/chloekang/kcf/data/cache/extra")
OUT = ROOT / "robust"
LEVELS = {  # name -> (family, params)
    "light_g2": ("light", dict(gamma=2.0, sigma=6.0)),
    "light_g3": ("light", dict(gamma=3.0, sigma=10.0)),
    "rain_1": ("rain", dict(n=150, alpha=0.35, blur=1.0, haze=0.0)),
    "rain_2": ("rain", dict(n=500, alpha=0.5, blur=2.0, haze=1.0)),
    "lowres_1": ("lowres", dict(scale=0.5, q=30)),
    "lowres_2": ("lowres", dict(scale=0.25, q=12)),
}
log = logging.getLogger("robust")


def degrade(fr: np.ndarray, fam: str, p: dict, rng: np.random.Generator) -> np.ndarray:
    h, w = fr.shape[:2]
    if fam == "light":
        x = 255.0 * (fr.astype(np.float32) / 255.0) ** p["gamma"] + rng.normal(0, p["sigma"], fr.shape)
        return np.clip(x, 0, 255).astype(np.uint8)
    if fam == "rain":
        x = fr.astype(np.float32)
        if p["haze"]:
            x = x * 0.9 + 25
        layer = np.zeros((h, w), np.float32)
        for _ in range(p["n"]):
            x0, y0 = rng.integers(0, w), rng.integers(0, h)
            L = rng.integers(10, 23)
            cv2.line(layer, (int(x0), int(y0)), (int(x0 - 0.17 * L), int(y0 + L)), 1.0, 1)
        x = x * (1 - p["alpha"] * layer[..., None]) + 255 * p["alpha"] * layer[..., None]
        x = cv2.GaussianBlur(x, (0, 0), p["blur"])
        return np.clip(x, 0, 255).astype(np.uint8)
    sm = cv2.resize(fr, (max(8, int(w * p["scale"])), max(8, int(h * p["scale"]))), interpolation=cv2.INTER_CUBIC)
    ok, buf = cv2.imencode(".jpg", sm, [cv2.IMWRITE_JPEG_QUALITY, p["q"]])
    return cv2.resize(cv2.imdecode(buf, 1), (w, h), interpolation=cv2.INTER_CUBIC)


def clahe_l(fr: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(fr, cv2.COLOR_BGR2LAB)
    lab[..., 0] = cv2.createCLAHE(2.0, (8, 8)).apply(lab[..., 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def prefilter(fr: np.ndarray, fam: str) -> np.ndarray:
    if fam == "light":
        m = max(float(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY).mean()), 1.0)
        g = np.clip(np.log(110 / 255) / np.log(m / 255), 0.2, 1.0)  # <1 brightens
        lut = (255 * (np.arange(256) / 255.0) ** g).astype(np.uint8)
        return cv2.bilateralFilter(cv2.medianBlur(clahe_l(cv2.LUT(fr, lut)), 3), 5, 30, 30)
    if fam == "rain":
        return clahe_l(cv2.medianBlur(fr, 5))
    b = cv2.bilateralFilter(fr, 5, 40, 40)
    return cv2.addWeighted(b, 2.0, cv2.GaussianBlur(b, (0, 0), 1.5), -1.0, 0)


def sources() -> list[tuple[str, str, Path, int]]:
    """(dataset, name, path, x_offset) - UR cam0 mp4 is depth|rgb side-by-side (rgb = right 320 px)."""
    ur = [("ur", p.name.replace("-cam0.mp4", ""), p, 320) for p in sorted((ROOT / "fall/raw").glob("*-cam0.mp4"))]
    import json

    meta = json.load(open(ROOT / "le2i/meta.json"))
    rng = np.random.default_rng(42)
    falls = sorted(n for n, m in meta.items() if m[0])
    adl = sorted(n for n, m in meta.items() if not m[0])
    keep = set(rng.choice(falls, 20, replace=False)) | set(rng.choice(adl, 20, replace=False))
    le = []
    for p in sorted((ROOT / "le2i/raw").rglob("*.avi")):
        scene = p.parent.parent.name if p.parent.name.lower() in ("videos", "video") else p.parent.name
        import re

        n = re.sub(r"[^A-Za-z0-9_]+", "_", f"{scene}_{p.stem}")
        if n in keep:
            le.append(("le2i", n, p, 0))
    return ur  # Le2i subset dropped for compute (shared CPU); `le` kept above for reference


def extract(path: Path, x0: int, fam: str | None, p: dict | None, filt: bool, seed: int):
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision

    opts = vision.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path="/Users/chloekang/kcf/app/frontend/public/models/pose_landmarker_lite.task",
                                 delegate=BaseOptions.Delegate.CPU),
        running_mode=vision.RunningMode.VIDEO, num_poses=1)
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    rng = np.random.default_rng(seed)
    lms, i = [], 0
    with vision.PoseLandmarker.create_from_options(opts) as lm:
        while True:
            ok, fr = cap.read()
            if not ok:
                break
            fr = fr[:, x0 : x0 + 320]
            if fam:
                fr = degrade(fr, fam, p, rng)
                if filt:
                    fr = prefilter(fr, fam)
            rgb = np.ascontiguousarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))
            res = lm.detect_for_video(mp.Image(mp.ImageFormat.SRGB, rgb), int(i * 1000 / fps))
            a = np.full((33, 4), np.nan, np.float32)
            if res.pose_landmarks:
                for k, q in enumerate(res.pose_landmarks[0]):
                    a[k] = (q.x, q.y, q.z, q.visibility)
            lms.append(a)
            i += 1
    return np.arange(len(lms)) / fps, np.stack(lms), fps


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    shard, n = int(sys.argv[1]), int(sys.argv[2])
    tasks = [(ds, nm, p, x0, lv, filt) for lv in LEVELS for filt in (False, True) for ds, nm, p, x0 in sources()]
    for ds, nm, p, x0, lv, filt in tasks[shard::n]:
        tag = lv + ("_f" if filt else "")
        o = OUT / ds / tag / f"{nm}.npz"
        if o.exists():
            continue
        o.parent.mkdir(parents=True, exist_ok=True)
        fam, prm = LEVELS[lv]
        seed = abs(hash((nm, lv))) % (2**31) if False else int(np.frombuffer((nm + lv).encode(), np.uint8).sum()) * 7919
        t, lm, fps = extract(p, x0, fam, prm, filt, seed)
        np.savez_compressed(o, t=t, lm=lm, fps=fps)
        log.info("done %s %s %s det=%.2f", ds, tag, nm, float(np.mean(~np.isnan(lm[:, 0, 0]))))


if __name__ == "__main__":
    main()
