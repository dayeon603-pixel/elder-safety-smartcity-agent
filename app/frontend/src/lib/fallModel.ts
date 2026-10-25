/**
 * Fall detection from pose landmarks: 9 window features (1.0 s, torso-length normalised, causal forward-fill) + logistic regression.
 * TypeScript port of backend/experiments/fall/features.py; reproduced against its test vectors to 1e-6 in fallModel.test.ts.
 * Trained on the public UR Fall Detection Dataset (lab, side camera, 70 sequences). Not validated on street footage.
 *
 * v2 file adds a descent gate (hip_drop_L > min) so a person lying still (no descent in the window) cannot alarm.
 *
 * Also contains the rule baseline (torso angle > 60 deg for >= 1.5 s) used for the side-by-side comparison.
 */
import { FALL_HOLD_MS, FALL_HORIZONTAL_DEG } from "../config";

const EPS_T = 1e-9;
const NOSE = 0;
const SHOULDERS = [11, 12] as const;
const HIPS = [23, 24] as const;
const MIN_L = 1e-6;
const SLOPE_EPS = 1e-12;

export interface FallModel {
  features: string[];
  window_s: number;
  min_frames: number;
  min_span_s: number;
  half_window_s: number;
  mean: number[];
  scale: number[];
  coef: number[];
  intercept: number;
  threshold: number;
  refractory_s: number;
  /** Alarm also requires features[feature] > min (e.g. hip_drop_L: the hips went down inside the window). Absent in v1 files = no gate. */
  descent_gate: { feature: string; min: number } | null;
  source: string;
  caveat: string;
}

/** [x, y] per landmark (33), normalised image coordinates. */
export type Frame = readonly (readonly [number, number])[];

function numArray(v: unknown, n: number, name: string): number[] {
  if (!Array.isArray(v) || v.length !== n || v.some((x) => typeof x !== "number" || !Number.isFinite(x))) throw new Error(`fall model: ${name} must be ${n} finite numbers`);
  return v as number[];
}

export function parseFallModel(raw: unknown): FallModel {
  if (typeof raw !== "object" || raw === null) throw new Error("fall model: not an object");
  const r = raw as Record<string, unknown>;
  const features = r.features;
  if (!Array.isArray(features)) throw new Error("fall model: features missing");
  const n = features.length;
  const num = (k: string): number => {
    const v = r[k];
    if (typeof v !== "number" || !Number.isFinite(v)) throw new Error(`fall model: ${k} must be a number`);
    return v;
  };
  const dg = r.descent_gate;
  let descent_gate: FallModel["descent_gate"] = null;
  if (dg !== undefined && dg !== null) {
    const g = dg as Record<string, unknown>;
    if (typeof dg !== "object" || typeof g.feature !== "string" || !features.includes(g.feature) || typeof g.min !== "number" || !Number.isFinite(g.min)) throw new Error("fall model: descent_gate malformed");
    descent_gate = { feature: g.feature, min: g.min };
  }
  return {
    features: features as string[], window_s: num("window_s"), min_frames: num("min_frames"), min_span_s: num("min_span_s"), half_window_s: num("half_window_s"),
    mean: numArray(r.mean, n, "mean"), scale: numArray(r.scale, n, "scale"), coef: numArray(r.coef, n, "coef"),
    intercept: num("intercept"), threshold: num("threshold"), refractory_s: num("refractory_s"),
    descent_gate, source: typeof r.source === "string" ? r.source : "", caveat: typeof r.caveat === "string" ? r.caveat : "",
  };
}

function slope(t: readonly number[], y: readonly number[]): number {
  const n = t.length;
  const tm = t.reduce((a, b) => a + b, 0) / n;
  const ym = y.reduce((a, b) => a + b, 0) / n;
  let d = 0;
  let num = 0;
  for (let i = 0; i < n; i++) {
    const tc = (t[i] as number) - tm;
    d += tc * tc;
    num += tc * ((y[i] as number) - ym);
  }
  return d < SLOPE_EPS ? 0 : num / d;
}

interface Signals {
  ang: number;
  tl: number;
  hipX: number;
  hipY: number;
  noseY: number;
  bw: number;
  bh: number;
}

function at(f: Frame, i: number): readonly [number, number] {
  return f[i] ?? [Number.NaN, Number.NaN];
}

function signals(f: Frame, aspect: number): Signals {
  const mid = (ids: readonly [number, number]): [number, number] => {
    const a = at(f, ids[0]);
    const b = at(f, ids[1]);
    return [((a[0] + b[0]) / 2) * aspect, (a[1] + b[1]) / 2];
  };
  const s = mid(SHOULDERS);
  const h = mid(HIPS);
  const dx = h[0] - s[0];
  const dy = h[1] - s[1];
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const [x, y] of f) {
    minX = Math.min(minX, x * aspect);
    maxX = Math.max(maxX, x * aspect);
    minY = Math.min(minY, y);
    maxY = Math.max(maxY, y);
  }
  return { ang: (Math.atan2(Math.abs(dx), dy) * 180) / Math.PI, tl: Math.hypot(dx, dy), hipX: h[0], hipY: h[1], noseY: at(f, NOSE)[1], bw: maxX - minX, bh: maxY - minY };
}

/** Torso angle from vertical in degrees (0 upright, 90 horizontal), aspect-corrected. */
export function torsoAngle(f: Frame, aspect: number): number {
  return signals(f, aspect).ang;
}

/** Features of the window ending at the last frame. `ts` in seconds, already restricted to the window. null if the window is too short. */
export function windowFeatures(m: Pick<FallModel, "min_frames" | "min_span_s" | "half_window_s">, ts: readonly number[], frames: readonly Frame[], aspect: number): number[] | null {
  const n = ts.length;
  const tEnd = ts[n - 1];
  const t0 = ts[0];
  if (n < m.min_frames || tEnd === undefined || t0 === undefined || tEnd - t0 < m.min_span_s) return null;
  const g = frames.map((f) => signals(f, aspect));
  const L = Math.max(...g.map((s) => s.tl));
  if (!(L >= MIN_L)) return null;
  const t = ts.map((x) => x - tEnd);
  const hy = g.map((s) => s.hipY / L);
  const hx = g.map((s) => s.hipX / L);
  const ang = g.map((s) => s.ang);
  const pick = (pred: (x: number) => boolean): number[] => t.map((x, i) => (pred(x) ? i : -1)).filter((i) => i >= 0);
  const firstIdx = pick((x) => x < -m.half_window_s);
  const lastIdx = pick((x) => !(x < -m.half_window_s));
  const sl = (idx: number[]): number => (idx.length >= 2 ? slope(idx.map((i) => t[i] as number), idx.map((i) => hy[i] as number)) : 0);
  const v1 = sl(firstIdx);
  const v2 = sl(lastIdx);
  const sp: number[] = [];
  for (let i = 1; i < n; i++) {
    if ((ts[i] as number) - tEnd >= -m.half_window_s) {
      const dt = Math.max((ts[i] as number) - (ts[i - 1] as number), 1e-6);
      sp.push(Math.hypot((hx[i] as number) - (hx[i - 1] as number), (hy[i] as number) - (hy[i - 1] as number)) / dt);
    }
  }
  const last = g[n - 1] as Signals;
  const first = hy[0] as number;
  return [
    last.ang,
    ang.reduce((a, b) => a + b, 0) / n,
    slope(t, ang),
    slope(t, hy),
    (v2 - v1) / m.half_window_s,
    (hy[n - 1] as number) - first,
    last.bw / Math.max(last.bh, 1e-6),
    (last.hipY - last.noseY) / L,
    sp.length > 0 ? sp.reduce((a, b) => a + b, 0) / sp.length : 0,
  ];
}

/** True when the descent gate (if any) lets this window raise an alarm. Mirrors export_gate.py / gate_eval.py. */
export function gateOpen(model: Pick<FallModel, "features" | "descent_gate">, feats: readonly number[]): boolean {
  if (model.descent_gate === null) return true;
  const v = feats[model.features.indexOf(model.descent_gate.feature)];
  return typeof v === "number" && v > model.descent_gate.min;
}

export function predictProb(model: FallModel, feats: readonly number[]): number {
  let z = model.intercept;
  for (let i = 0; i < model.coef.length; i++) z += (((feats[i] as number) - (model.mean[i] as number)) / (model.scale[i] as number)) * (model.coef[i] as number);
  return 1 / (1 + Math.exp(-z));
}

export interface AiState {
  ts: number[];
  frames: Frame[];
  lastGood: Frame | null;
  lastAlarmT: number | null;
}

export const initialAi = (): AiState => ({ ts: [], frames: [], lastGood: null, lastAlarmT: null });

export interface AiStep {
  state: AiState;
  /** null while the window is too short. Raw model probability (the gate only suppresses alarms). */
  prob: number | null;
  fired: boolean;
  /** false when the descent gate blocked a window that would otherwise have crossed the threshold */
  gated?: boolean;
}

/** One frame in, causal. `frame === null` = person not detected: the last good frame is carried forward. tSec in seconds. */
export function stepAi(model: FallModel, prev: AiState, tSec: number, frame: Frame | null, aspect: number): AiStep {
  const used = frame ?? prev.lastGood;
  if (used === null) return { state: prev, prob: null, fired: false };
  const ts = [...prev.ts, tSec];
  const frames = [...prev.frames, used];
  while (ts.length > 0 && (ts[0] as number) < tSec - model.window_s - EPS_T) {
    ts.shift();
    frames.shift();
  }
  const f = windowFeatures(model, ts, frames, aspect);
  const state: AiState = { ts, frames, lastGood: used, lastAlarmT: prev.lastAlarmT };
  if (f === null) return { state, prob: null, fired: false };
  const prob = predictProb(model, f);
  const armed = prev.lastAlarmT === null || tSec - prev.lastAlarmT >= model.refractory_s;
  const open = gateOpen(model, f);
  const fired = prob >= model.threshold && open && armed;
  return { state: fired ? { ...state, lastAlarmT: tSec } : state, prob, fired, gated: prob >= model.threshold && !open };
}

export interface RuleState {
  startSec: number | null;
  fired: boolean;
}
export const initialRule = (): RuleState => ({ startSec: null, fired: false });

/** Baseline: torso angle > FALL_HORIZONTAL_DEG continuously for >= FALL_HOLD_MS; re-arms when the angle drops. Mirrors rule_alarm_times in features.py. */
export function stepRule(prev: RuleState, tSec: number, angleDeg: number, thrDeg: number = FALL_HORIZONTAL_DEG, holdSec: number = FALL_HOLD_MS / 1000): { state: RuleState; fired: boolean } {
  if (!(angleDeg > thrDeg)) return { state: initialRule(), fired: false };
  const start = prev.startSec ?? tSec;
  if (!prev.fired && tSec - start >= holdSec - EPS_T) return { state: { startSec: start, fired: true }, fired: true };
  return { state: { startSec: start, fired: prev.fired }, fired: false };
}
