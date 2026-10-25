/** Face-region geometry for the pixelation mask. Pure; the drawing lives in FieldCamera. */
import { FACE_LANDMARK_COUNT, FACE_MARGIN, FACE_MIN_HALF, POSE_MIN_VISIBILITY } from "../config";
import type { Box } from "./tracker";

export interface PointLike {
  x: number;
  y: number;
  visibility?: number;
}

/** Bounding box of the nose/eyes/mouth/ears landmarks (indices 0-10), expanded by FACE_MARGIN, clamped to [0,1]. null if none visible. */
export function faceBox(landmarks: readonly PointLike[]): Box | null {
  const pts = landmarks.slice(0, FACE_LANDMARK_COUNT).filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y) && (p.visibility ?? 1) >= POSE_MIN_VISIBILITY);
  if (pts.length === 0) return null;
  const xs = pts.map((p) => p.x);
  const ys = pts.map((p) => p.y);
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2;
  const cy = (Math.min(...ys) + Math.max(...ys)) / 2;
  const half = Math.max(FACE_MIN_HALF, ((Math.max(...xs) - Math.min(...xs)) / 2) * (1 + FACE_MARGIN), ((Math.max(...ys) - Math.min(...ys)) / 2) * (1 + FACE_MARGIN));
  const c = (v: number): number => Math.min(1, Math.max(0, v));
  return { x0: c(cx - half), y0: c(cy - half), x1: c(cx + half), y1: c(cy + half) };
}

/** Box around the visible landmarks of one person, for tracking. null if fewer than 2 are visible. */
export function personBox(landmarks: readonly PointLike[]): Box | null {
  const pts = landmarks.filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y) && (p.visibility ?? 1) >= POSE_MIN_VISIBILITY);
  if (pts.length < 2) return null;
  const xs = pts.map((p) => p.x);
  const ys = pts.map((p) => p.y);
  return { x0: Math.min(...xs), y0: Math.min(...ys), x1: Math.max(...xs), y1: Math.max(...ys) };
}
