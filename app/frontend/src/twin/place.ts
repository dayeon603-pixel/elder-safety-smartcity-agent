import { haversineM } from "./api";
import type { CrosswalkPoint, StopPoint, TwinState } from "./types";

export interface CameraPlace { lat: number; lon: number; label: string; crosswalkId: string; insideSilverZone: boolean }

/**
 * The demo camera sits at a real crosswalk of the state: the one nearest to the centre of a silver zone and inside its radius
 * (our selected sites first, then the current ones); if none is inside a zone, the first crosswalk of the API list.
 */
export function pickCameraPlace(st: TwinState): CameraPlace | null {
  if (st.crosswalks.length === 0) return null;
  const zones = [...st.silverOurs, ...st.silverCurrent];
  let best: { cw: CrosswalkPoint; d: number } | null = null;
  for (const cw of st.crosswalks) {
    for (const z of zones) {
      const d = haversineM([cw.lon, cw.lat], [z.lon, z.lat]);
      if (d <= st.silverRadiusM && (best === null || d < best.d)) best = { cw, d };
    }
  }
  const cw = best?.cw ?? (st.crosswalks[0] as CrosswalkPoint);
  return { lat: cw.lat, lon: cw.lon, label: cw.address === "" ? "횡단보도" : cw.address.replace(/^서울특별시\s*/, ""), crosswalkId: cw.id, insideSilverZone: best !== null };
}

export function nearestStop(st: TwinState, lat: number, lon: number): StopPoint | null {
  let best: { s: StopPoint; d: number } | null = null;
  for (const s of st.stops) {
    const d = haversineM([lon, lat], [s.lon, s.lat]);
    if (best === null || d < best.d) best = { s, d };
  }
  return best?.s ?? null;
}

export interface Scoreboard { total: number; current: number; ours: number; year: number | null; source: string | null }
/** Seoul-wide silver result from the API (silver_eval_seoul). Null (scoreboard hidden) until the API exposes it. */
export function scoreboard(st: TwinState): Scoreboard | null {
  const e = st.silverEval;
  return e !== null && e.n > 0 ? { total: e.n, current: e.coveredCurrent, ours: e.coveredOurs, year: e.year, source: e.source } : null;
}
