/**
 * Walking-speed estimate from the hip midpoint seen by a single camera.
 * speed = |x_last - x_first| * fieldWidthM / (t_last - t_first) over a 1 s moving window (net sideways displacement).
 * ESTIMATE ONLY: needs the real-world width of the camera field at the walker's distance; ignores motion toward/away from the lens.
 */
import { GAIT_MAX_PLAUSIBLE_MPS, GAIT_MIN_SPAN_MS, GAIT_STILL_MPS, GAIT_WINDOW_MS } from "../config";

export interface HipSample {
  tMs: number;
  /** hip midpoint x, normalized 0..1 of the frame width */
  x: number;
}

export type GaitStatus = "ok" | "warming_up" | "still" | "implausible";

export interface GaitEstimate {
  status: GaitStatus;
  speedMps: number | null;
}

/** Drop samples older than the window ending at `nowMs`. Returns a new array. */
export function pushSample(window: readonly HipSample[], sample: HipSample, windowMs: number = GAIT_WINDOW_MS): HipSample[] {
  return [...window, sample].filter((s) => sample.tMs - s.tMs <= windowMs);
}

export function estimateSpeed(window: readonly HipSample[], fieldWidthM: number): GaitEstimate {
  if (!(fieldWidthM > 0)) throw new RangeError("fieldWidthM must be > 0");
  const first = window[0];
  const last = window[window.length - 1];
  if (first === undefined || last === undefined || window.length < 2) return { status: "warming_up", speedMps: null };
  const span = last.tMs - first.tMs;
  if (span < GAIT_MIN_SPAN_MS) return { status: "warming_up", speedMps: null };
  const speed = (Math.abs(last.x - first.x) * fieldWidthM) / (span / 1000);
  if (speed > GAIT_MAX_PLAUSIBLE_MPS) return { status: "implausible", speedMps: null };
  if (speed < GAIT_STILL_MPS) return { status: "still", speedMps: speed };
  return { status: "ok", speedMps: speed };
}

export type PersonStatus = "normal" | "caution" | "danger";
export const PERSON_STATUS_LABEL: Record<PersonStatus, string> = { normal: "정상", caution: "주의", danger: "위험" };

/**
 * Chip thresholds (documented in the UI):
 *  위험 = fall state machine reached "fallen" (torso > 60 deg from vertical for >= 1.5 s after an upright baseline)
 *  주의 = estimated speed below the speed needed to finish the green (crosswalk length / green seconds)
 *  정상 = otherwise (including: no speed estimate yet)
 */
export function personStatus(fallen: boolean, speedMps: number | null, requiredMps: number | null): PersonStatus {
  if (fallen) return "danger";
  if (speedMps !== null && requiredMps !== null && speedMps < requiredMps) return "caution";
  return "normal";
}

export interface GroundSample {
  tMs: number;
  /** metres on the calibrated floor plane */
  X: number;
  Y: number;
}

export function pushGround(window: readonly GroundSample[], sample: GroundSample, windowMs: number = GAIT_WINDOW_MS): GroundSample[] {
  return [...window, sample].filter((s) => sample.tMs - s.tMs <= windowMs);
}

/** Metric speed: straight-line floor distance between the first and last sample of the 1 s window / elapsed time. */
export function estimateGroundSpeed(window: readonly GroundSample[]): GaitEstimate {
  const first = window[0];
  const last = window[window.length - 1];
  if (first === undefined || last === undefined || window.length < 2) return { status: "warming_up", speedMps: null };
  const span = last.tMs - first.tMs;
  if (span < GAIT_MIN_SPAN_MS) return { status: "warming_up", speedMps: null };
  const speed = Math.hypot(last.X - first.X, last.Y - first.Y) / (span / 1000);
  if (speed > GAIT_MAX_PLAUSIBLE_MPS) return { status: "implausible", speedMps: null };
  if (speed < GAIT_STILL_MPS) return { status: "still", speedMps: speed };
  return { status: "ok", speedMps: speed };
}
