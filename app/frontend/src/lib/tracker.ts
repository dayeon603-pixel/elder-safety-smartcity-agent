/**
 * Minimal multi-person tracker: greedy IoU matching, then centroid distance as a fallback, stable integer IDs.
 * Limits: people who cross paths or leave and re-enter get new IDs; a person whose box changes abruptly
 * (e.g. a fall) is kept by the centroid fallback only if the centre moves less than TRACK_CENTROID_MAX of the frame.
 */
import { TRACK_CENTROID_MAX, TRACK_IOU_MIN, TRACK_LOST_MS } from "../config";

export interface Box {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface Track {
  id: number;
  box: Box;
  lastSeenMs: number;
}

export interface TrackerState {
  nextId: number;
  tracks: Track[];
}

export interface TrackerStep {
  state: TrackerState;
  /** id per detection, same order as the input */
  ids: number[];
  /** ids dropped on this step */
  lost: number[];
}

export const initialTracker = (): TrackerState => ({ nextId: 1, tracks: [] });

export function iou(a: Box, b: Box): number {
  const w = Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0);
  const h = Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0);
  if (w <= 0 || h <= 0) return 0;
  const inter = w * h;
  const union = (a.x1 - a.x0) * (a.y1 - a.y0) + (b.x1 - b.x0) * (b.y1 - b.y0) - inter;
  return union <= 0 ? 0 : inter / union;
}

const centre = (b: Box): [number, number] => [(b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2];

export function trackStep(prev: TrackerState, dets: readonly Box[], tMs: number): TrackerStep {
  const ids: (number | null)[] = dets.map(() => null);
  const used = new Set<number>();
  const pairs = (score: (t: Track, d: Box) => number, ok: (s: number) => boolean, better: (a: number, b: number) => boolean): void => {
    const cand: { ti: number; di: number; s: number }[] = [];
    prev.tracks.forEach((t, ti) => dets.forEach((d, di) => {
      const s = score(t, d);
      if (ok(s)) cand.push({ ti, di, s });
    }));
    cand.sort((a, b) => (better(a.s, b.s) ? -1 : better(b.s, a.s) ? 1 : 0));
    for (const c of cand) {
      if (ids[c.di] !== null || used.has(c.ti)) continue;
      ids[c.di] = (prev.tracks[c.ti] as Track).id;
      used.add(c.ti);
    }
  };
  pairs((t, d) => iou(t.box, d), (s) => s >= TRACK_IOU_MIN, (a, b) => a > b);
  pairs(
    (t, d) => {
      const [tx, ty] = centre(t.box);
      const [dx, dy] = centre(d);
      return Math.hypot(tx - dx, ty - dy);
    },
    (s) => s <= TRACK_CENTROID_MAX,
    (a, b) => a < b,
  );
  let nextId = prev.nextId;
  const final = ids.map((id) => id ?? nextId++);
  const byId = new Map(prev.tracks.map((t) => [t.id, t]));
  final.forEach((id, i) => byId.set(id, { id, box: dets[i] as Box, lastSeenMs: tMs }));
  const lost: number[] = [];
  const tracks: Track[] = [];
  for (const t of byId.values()) {
    if (tMs - t.lastSeenMs > TRACK_LOST_MS) lost.push(t.id);
    else tracks.push(t);
  }
  return { state: { nextId, tracks }, ids: final, lost };
}
