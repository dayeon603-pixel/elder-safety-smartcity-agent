"""Reference feature extraction for the fall model (port target for the browser TS code).

Input per window: ts (N,) seconds, xy (N,33,2) MediaPipe normalized x,y, aspect = W/H of the image.
All coordinates are aspect-corrected (x*aspect, y) before geometry. Causal only: a window ending at
t_end uses frames with ts <= t_end. Missing detections are forward-filled (causal), never interpolated.
Units: lengths in torso lengths (L = max over window of |shoulder_mid - hip_mid|), time in seconds.
Landmark ids: nose 0, shoulders 11/12, hips 23/24.
"""
from __future__ import annotations

import math

import numpy as np

WINDOW_S = 1.0
MIN_FRAMES = 5
MIN_SPAN_S = 0.8
HALF_S = 0.5
EPS_T = 1e-9
FEATURES = [
    "torso_angle_end_deg",
    "torso_angle_mean_deg",
    "torso_ang_vel_deg_s",
    "hip_vy_L_s",
    "hip_acc_L_s2",
    "hip_drop_L",
    "bbox_aspect_w_over_h",
    "head_hip_dy_L",
    "hip_speed_last_half_L_s",
]


def ffill(lm: np.ndarray) -> np.ndarray:
    """Causal forward-fill of NaN frames (T,33,K). Leading NaN frames stay NaN (caller drops them)."""
    out = lm.copy()
    ok = ~np.isnan(out[:, 0, 0])
    idx = np.where(ok, np.arange(len(out)), -1)
    np.maximum.accumulate(idx, out=idx)
    valid = idx >= 0
    out[valid] = out[idx[valid]]
    return out


def _slope(t: np.ndarray, y: np.ndarray) -> float:
    """Least-squares slope dy/dt (closed form, port-friendly). 0 if degenerate."""
    tc = t - t.mean()
    d = float(np.sum(tc * tc))
    return 0.0 if d < 1e-12 else float(np.sum(tc * (y - y.mean())) / d)


def frame_signals(xy: np.ndarray, aspect: float) -> dict[str, np.ndarray]:
    p = xy.astype(np.float64).copy()
    p[..., 0] *= aspect
    s = 0.5 * (p[:, 11] + p[:, 12])
    h = 0.5 * (p[:, 23] + p[:, 24])
    d = h - s  # image y points down
    ang = np.degrees(np.arctan2(np.abs(d[:, 0]), d[:, 1]))  # 0 = upright, 90 = horizontal
    tl = np.hypot(d[:, 0], d[:, 1])
    w = p[..., 0].max(1) - p[..., 0].min(1)
    ht = p[..., 1].max(1) - p[..., 1].min(1)
    return {"ang": ang, "tl": tl, "hip": h, "nose": p[:, 0], "bw": w, "bh": ht}


def window_features(ts: np.ndarray, xy: np.ndarray, aspect: float) -> np.ndarray | None:
    """Features for the window ending at ts[-1] (ts, xy already restricted to the window)."""
    if len(ts) < MIN_FRAMES or ts[-1] - ts[0] < MIN_SPAN_S:
        return None
    g = frame_signals(xy, aspect)
    L = float(g["tl"].max())
    if L < 1e-6:
        return None
    t = ts - ts[-1]
    hy = g["hip"][:, 1] / L
    hx = g["hip"][:, 0] / L
    first = t < -HALF_S
    last = ~first
    v1 = _slope(t[first], hy[first]) if first.sum() >= 2 else 0.0
    v2 = _slope(t[last], hy[last]) if last.sum() >= 2 else 0.0
    dt = np.diff(ts)
    sp = np.hypot(np.diff(hx), np.diff(hy)) / np.maximum(dt, 1e-6)
    spl = sp[(ts[1:] - ts[-1]) >= -HALF_S]
    return np.array(
        [
            g["ang"][-1],
            g["ang"].mean(),
            _slope(t, g["ang"]),
            _slope(t, hy),
            (v2 - v1) / HALF_S,
            hy[-1] - hy[0],
            g["bw"][-1] / max(g["bh"][-1], 1e-6),
            (g["hip"][-1, 1] - g["nose"][-1, 1]) / L,
            float(spl.mean()) if len(spl) else 0.0,
        ],
        dtype=np.float64,
    )


def stream(ts: np.ndarray, xy: np.ndarray, aspect: float) -> tuple[np.ndarray, np.ndarray]:
    """Features for every window end. Returns (t_end (M,), X (M,F)); windows are [t_end-1.0, t_end]."""
    ends, rows = [], []
    lo = 0
    for i in range(len(ts)):
        while ts[lo] < ts[i] - WINDOW_S - EPS_T:
            lo += 1
        f = window_features(ts[lo : i + 1], xy[lo : i + 1], aspect)
        if f is not None:
            ends.append(ts[i])
            rows.append(f)
    return np.array(ends), np.array(rows).reshape(-1, len(FEATURES))


def rule_alarm_times(ts: np.ndarray, xy: np.ndarray, aspect: float, thr=60.0, hold=1.5) -> list[float]:
    """Baseline: torso angle > thr deg continuously for >= hold s (the browser heuristic).
    Alarm at the moment the hold is reached; re-arms only after angle drops below thr."""
    ang = frame_signals(xy, aspect)["ang"]
    out, start, fired = [], None, False
    for t, a in zip(ts, ang):
        if a > thr:
            start = t if start is None else start
            if not fired and t - start >= hold - EPS_T:
                out.append(float(t))
                fired = True
        else:
            start, fired = None, False
    return out


# ---- v2: descent-profile features (appended to v1 order; v1 code path unchanged) ----
FEATURES_V2 = FEATURES + [
    "peak_hip_vy_L_s",
    "peak_ang_vel_deg_s",
    "torso_ang_acc_deg_s2",
    "hip_descent_time_s",
]
LAG_S = 0.2


def _peak_lag_diff(t: np.ndarray, y: np.ndarray, sign: float) -> float:
    """max over i of sign*(y[j]-y[i])/(t[j]-t[i]) where j = first index with t[j]-t[i] >= LAG_S. 0 if none."""
    best = 0.0
    j = 0
    for i in range(len(t)):
        if j < i:
            j = i
        while j < len(t) and t[j] - t[i] < LAG_S - EPS_T:
            j += 1
        if j >= len(t):
            break
        best = max(best, sign * (y[j] - y[i]) / (t[j] - t[i]))
    return best


def window_features_v2(ts: np.ndarray, xy: np.ndarray, aspect: float) -> np.ndarray | None:
    base = window_features(ts, xy, aspect)
    if base is None:
        return None
    g = frame_signals(xy, aspect)
    L = float(g["tl"].max())
    t = ts - ts[-1]
    hy = g["hip"][:, 1] / L
    ang = g["ang"]
    first = t < -HALF_S
    last = ~first
    a1 = _slope(t[first], ang[first]) if first.sum() >= 2 else 0.0
    a2 = _slope(t[last], ang[last]) if last.sum() >= 2 else 0.0
    rng = hy.max() - hy.min()
    if rng >= 0.3:  # time for hip y to go from 10% to 90% of its window range (downward only)
        lo, hi = hy.min() + 0.1 * rng, hy.min() + 0.9 * rng
        ilo = int(np.argmax(hy >= lo))
        ihi = int(np.argmax(hy >= hi))
        dtime = float(ts[ihi] - ts[ilo]) if hy[-1] > hy[0] and ihi >= ilo else WINDOW_S
    else:
        dtime = WINDOW_S
    extra = np.array(
        [_peak_lag_diff(ts, hy, 1.0), _peak_lag_diff(ts, ang, 1.0), (a2 - a1) / HALF_S, dtime]
    )
    return np.concatenate([base, extra])


def stream_v2(ts: np.ndarray, xy: np.ndarray, aspect: float) -> tuple[np.ndarray, np.ndarray]:
    ends, rows = [], []
    lo = 0
    for i in range(len(ts)):
        while ts[lo] < ts[i] - WINDOW_S - EPS_T:
            lo += 1
        f = window_features_v2(ts[lo : i + 1], xy[lo : i + 1], aspect)
        if f is not None:
            ends.append(ts[i])
            rows.append(f)
    return np.array(ends), np.array(rows).reshape(-1, len(FEATURES_V2))
