import type { SilverMapPoint } from "../api/zpTypes";
import type { BoundaryCollection } from "./geo";

export const ZP_MAP_WIDTH = 560;
const PAD_PX = 10;
const PAD_FRAC = 0.04;
const RAD_PER_DEG = Math.PI / 180;
const PATH_DECIMALS = 1;

export interface GeoBounds {
  lonMin: number;
  lonMax: number;
  latMin: number;
  latMax: number;
}

export interface MapFrame {
  width: number;
  height: number;
  project: (lon: number, lat: number) => { x: number; y: number };
  bounds: GeoBounds;
}

/** Bounding box of the points, padded. Returns null for an empty list. */
export function boundsOf(points: readonly { lat: number; lon: number }[]): GeoBounds | null {
  if (points.length === 0) return null;
  let lonMin = Infinity;
  let lonMax = -Infinity;
  let latMin = Infinity;
  let latMax = -Infinity;
  for (const p of points) {
    lonMin = Math.min(lonMin, p.lon);
    lonMax = Math.max(lonMax, p.lon);
    latMin = Math.min(latMin, p.lat);
    latMax = Math.max(latMax, p.lat);
  }
  const dLon = Math.max(lonMax - lonMin, 1e-6) * PAD_FRAC;
  const dLat = Math.max(latMax - latMin, 1e-6) * PAD_FRAC;
  return { lonMin: lonMin - dLon, lonMax: lonMax + dLon, latMin: latMin - dLat, latMax: latMax + dLat };
}

/** Equirectangular frame with longitude scaled by cos(center latitude), fitted to `bounds` at `width` px. */
export function makeFrame(bounds: GeoBounds, width = ZP_MAP_WIDTH): MapFrame {
  const kx = Math.cos(((bounds.latMin + bounds.latMax) / 2) * RAD_PER_DEG);
  const scale = (width - 2 * PAD_PX) / ((bounds.lonMax - bounds.lonMin) * kx);
  const height = Math.round((bounds.latMax - bounds.latMin) * scale + 2 * PAD_PX);
  return {
    width,
    height,
    bounds,
    project: (lon, lat) => ({ x: PAD_PX + (lon - bounds.lonMin) * kx * scale, y: PAD_PX + (bounds.latMax - lat) * scale }),
  };
}

type Ring = number[][];

function ringsOf(g: { type: string; coordinates: unknown }): Ring[] {
  const c = g.coordinates;
  if (!Array.isArray(c)) return [];
  if (g.type === "Polygon") return c as Ring[];
  if (g.type === "MultiPolygon") return (c as Ring[][]).flat();
  return [];
}

/** SVG path of every ring whose bounding box touches `bounds`. Rings are not clipped; the SVG viewBox clips them. */
export function outlinePath(fc: BoundaryCollection, frame: MapFrame): string {
  const b = frame.bounds;
  const parts: string[] = [];
  for (const f of fc.features) {
    for (const ring of ringsOf(f.geometry)) {
      if (ring.length < 3) continue;
      const lons = ring.map((p) => p[0] ?? 0);
      const lats = ring.map((p) => p[1] ?? 0);
      if (Math.max(...lons) < b.lonMin || Math.min(...lons) > b.lonMax || Math.max(...lats) < b.latMin || Math.min(...lats) > b.latMax) continue;
      const pts = ring.map((p) => frame.project(p[0] ?? 0, p[1] ?? 0));
      parts.push(`M${pts.map((q) => `${q.x.toFixed(PATH_DECIMALS)} ${q.y.toFixed(PATH_DECIMALS)}`).join("L")}Z`);
    }
  }
  return parts.join("");
}

export type MapScenario = "current" | "ours";

export interface MapCounts {
  currentZones: number;
  ourPicks: number;
  hotspots: number;
  /** Hotspots with a zone within the radius, per scenario. */
  covered: Record<MapScenario, number>;
}

export function mapCounts(points: readonly SilverMapPoint[]): MapCounts {
  const c: MapCounts = { currentZones: 0, ourPicks: 0, hotspots: 0, covered: { current: 0, ours: 0 } };
  for (const p of points) {
    if (p.kind === "current") c.currentZones += 1;
    else if (p.kind === "ours") c.ourPicks += 1;
    else {
      c.hotspots += 1;
      c.covered.current += p.cur_within;
      c.covered.ours += p.ours_within;
    }
  }
  return c;
}

export const HOT_R_MIN = 3.5;
export const HOT_R_PER_ROOT = 1.9;
export const HOT_R_MAX = 12;

/** Hotspot radius: area grows with casualties. Missing casualty counts get the minimum size. */
export function hotspotRadius(cas: number | null): number {
  const n = cas === null || !Number.isFinite(cas) ? 0 : Math.max(0, cas);
  return Math.min(HOT_R_MAX, HOT_R_MIN + HOT_R_PER_ROOT * Math.sqrt(n));
}

let boundaryRequest: Promise<BoundaryCollection> | null = null;

/** Fetch the static boundary asset once per page load (shared with geo.ts via the same URL, cached by the browser). */
export function loadBoundary(url: string): Promise<BoundaryCollection> {
  boundaryRequest ??= fetch(url)
    .then((res) => {
      if (!res.ok) throw new Error(`boundary asset: HTTP ${res.status}`);
      return res.json() as Promise<BoundaryCollection>;
    })
    .then((fc) => {
      if (!Array.isArray(fc.features)) throw new Error("boundary asset: not a FeatureCollection");
      return fc;
    })
    .catch((err: unknown) => {
      boundaryRequest = null;
      throw err;
    });
  return boundaryRequest;
}
