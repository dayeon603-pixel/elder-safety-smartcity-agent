/** Local metric frame around the scene centre (same frame as deck.gl METER_OFFSETS in the scene payload). */
export type XY = readonly [number, number];
const EARTH_R = 6_371_008.8;
const DEG = Math.PI / 180;

export interface Frame { lon0: number; lat0: number; kx: number; ky: number }

export function makeFrame(lon0: number, lat0: number): Frame {
  return { lon0, lat0, kx: EARTH_R * DEG * Math.cos(lat0 * DEG), ky: EARTH_R * DEG };
}
export const toXY = (f: Frame, lon: number, lat: number): XY => [(lon - f.lon0) * f.kx, (lat - f.lat0) * f.ky];
export const toLonLat = (f: Frame, x: number, y: number): [number, number] => [f.lon0 + x / f.kx, f.lat0 + y / f.ky];

export const dist = (a: XY, b: XY): number => Math.hypot(b[0] - a[0], b[1] - a[1]);

export function cumulative(path: readonly XY[]): number[] {
  const out = [0];
  for (let i = 1; i < path.length; i++) out.push((out[i - 1] as number) + dist(path[i - 1] as XY, path[i] as XY));
  return out;
}

/** Point and heading (radians, atan2(dy,dx)) at distance `d` along a polyline, clamped to its ends. */
export function pointAt(path: readonly XY[], cum: readonly number[], d: number): { p: XY; heading: number } {
  const total = cum[cum.length - 1] ?? 0;
  const t = Math.min(total, Math.max(0, d));
  let i = 1;
  while (i < path.length - 1 && (cum[i] as number) < t) i++;
  const a = path[i - 1] as XY;
  const b = path[i] as XY;
  const seg = (cum[i] as number) - (cum[i - 1] as number);
  const f = seg === 0 ? 0 : (t - (cum[i - 1] as number)) / seg;
  return { p: [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f], heading: Math.atan2(b[1] - a[1], b[0] - a[0]) };
}

export function angleDiff(a: number, b: number): number {
  let d = (b - a) % (2 * Math.PI);
  if (d > Math.PI) d -= 2 * Math.PI;
  if (d < -Math.PI) d += 2 * Math.PI;
  return d;
}

export function pointInRing(x: number, y: number, ring: readonly XY[]): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i] as XY;
    const [xj, yj] = ring[j] as XY;
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
