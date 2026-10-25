/**
 * 실버존 재배치 step: before/after on one frame. 현행 zones stay as thin gray dashed ghosts; agent zones grow in as solid blue
 * rings with translucent fill; accident hotspots are green (newly protected, pulsing), blue (protected before and now) or red
 * (still unprotected). Data: GET /silver/map (Seoul-wide lists, filtered to the scene's surroundings).
 */
import { PathLayer, ScatterplotLayer } from "@deck.gl/layers";
import { PathStyleExtension } from "@deck.gl/extensions";
import type { Color, Layer } from "@deck.gl/core";
import { arr, getJsonPublic, num, rec, str } from "./api";
import { haversineM } from "./api";
import type { LonLat } from "./types";

export interface SilverSiteLL { lon: number; lat: number }
export interface Hotspot { lon: number; lat: number; current: boolean; ours: boolean }
export interface SilverContext { current: SilverSiteLL[]; ours: SilverSiteLL[]; hotspots: Hotspot[]; radiusM: number }

export const COLORS = {
  ghost: [70, 78, 90, 255] as Color,
  agentLine: [30, 123, 214, 255] as Color,
  agentFill: [30, 123, 214, 70] as Color,
  newly: [22, 163, 74, 255] as Color,
  protectedBefore: [30, 123, 214, 255] as Color,
  unprotected: [217, 48, 37, 255] as Color,
};
const RING_POINTS = 48;
const M_PER_DEG_LAT = 111_320;
const PAD_DEG = 0.12;
const FRAME_RADIUS_M = 1500;
const FRAME_MARGIN = 1.35;
const PULSES = 2;
const PULSE_TOTAL_MS = 2600;
const DASH: [number, number] = [4, 3];

export async function loadSilverContext(code: string, bbox: readonly [number, number, number, number], defaultRadiusM: number): Promise<SilverContext | null> {
  const raw = rec(await getJsonPublic(`/silver/map?sgg_code=${code}`));
  if (raw === null) return null;
  const inside = (lon: number, lat: number): boolean => lon >= bbox[0] - PAD_DEG && lon <= bbox[2] + PAD_DEG && lat >= bbox[1] - PAD_DEG && lat <= bbox[3] + PAD_DEG;
  const sites = (v: unknown): SilverSiteLL[] => arr(v).flatMap((x) => { const o = rec(x); const lon = num(o?.lon); const lat = num(o?.lat); return lon === null || lat === null || !inside(lon, lat) ? [] : [{ lon, lat }]; });
  const hotspots = arr(raw.hotspots_2024).flatMap((x): Hotspot[] => {
    const o = rec(x);
    const lon = num(o?.lon);
    const lat = num(o?.lat);
    if (o === null || lon === null || lat === null || !inside(lon, lat) || typeof o.covered_current !== "boolean" || typeof o.covered_ours !== "boolean") return [];
    return [{ lon, lat, current: o.covered_current, ours: o.covered_ours }];
  });
  void str;
  return { current: sites(raw.current), ours: sites(raw.ours), hotspots, radiusM: num(raw.radius_m) ?? defaultRadiusM };
}

/** View that frames every hotspot around the district (so all the green dots are on one frame): centre + zoom for a ~1200 px wide map. */
export function frameHotspots(c: SilverContext, mapWidthPx = 1200): { lon: number; lat: number; zoom: number } | null {
  if (c.hotspots.length === 0) return null;
  const lons = c.hotspots.map((h) => h.lon);
  const lats = c.hotspots.map((h) => h.lat);
  const lon = (Math.min(...lons) + Math.max(...lons)) / 2;
  const lat = (Math.min(...lats) + Math.max(...lats)) / 2;
  const spanM = Math.max(haversineM([Math.min(...lons), lat], [Math.max(...lons), lat]), haversineM([lon, Math.min(...lats)], [lon, Math.max(...lats)]), 800);
  const mpp = (spanM * FRAME_MARGIN) / mapWidthPx;
  const zoom = Math.log2((156543.03 * Math.cos((lat * Math.PI) / 180)) / mpp);
  return { lon, lat, zoom: Math.min(15.2, Math.max(10.8, zoom)) };
}

/** Camera centre for the silver view: where current zones, agent zones and hotspots are densest within FRAME_RADIUS_M. */
export function densestCentre(c: SilverContext): SilverSiteLL | null {
  const cands: SilverSiteLL[] = c.hotspots.length > 0 ? c.hotspots : c.current;
  let best: { p: SilverSiteLL; score: number } | null = null;
  for (const p of cands) {
    const near = (q: SilverSiteLL): boolean => haversineM([p.lon, p.lat], [q.lon, q.lat]) <= FRAME_RADIUS_M;
    const score = c.current.filter(near).length * 2 + c.ours.filter(near).length + c.hotspots.filter(near).length * 6;
    if (best === null || score > best.score) best = { p, score };
  }
  if (best === null) return null;
  const p = best.p;
  const group = c.hotspots.filter((h) => haversineM([p.lon, p.lat], [h.lon, h.lat]) <= FRAME_RADIUS_M * 1.4);
  if (group.length === 0) return p;
  return { lon: group.reduce((m, h) => m + h.lon, 0) / group.length, lat: group.reduce((m, h) => m + h.lat, 0) / group.length };
}

function ring(c: SilverSiteLL, radiusM: number): LonLat[] {
  const out: LonLat[] = [];
  const dLat = radiusM / M_PER_DEG_LAT;
  const dLon = dLat / Math.cos((c.lat * Math.PI) / 180);
  for (let i = 0; i <= RING_POINTS; i++) {
    const a = (i / RING_POINTS) * Math.PI * 2;
    out.push([c.lon + Math.cos(a) * dLon, c.lat + Math.sin(a) * dLat]);
  }
  return out;
}

const ghostCache = new WeakMap<SilverContext, LonLat[][]>();
const dash = new PathStyleExtension({ dash: true });

/** `t`: 0 = 현행 기준 .. 1 = 에이전트 (animated by the engine); `sinceFlipMs`: time since the flip started (drives the pulses). */
export function silverStepLayers(c: SilverContext, t: number, sinceFlipMs: number): Layer[] {
  let ghosts = ghostCache.get(c);
  if (ghosts === undefined) { ghosts = c.current.map((s) => ring(s, c.radiusM)); ghostCache.set(c, ghosts); }
  const grow = Math.max(0.001, t);
  const pulseT = Math.min(1, Math.max(0, (sinceFlipMs - 600) / PULSE_TOTAL_MS));
  const pulse = t >= 0.999 && pulseT < 1 ? Math.max(0, Math.sin(pulseT * Math.PI * 2 * PULSES)) : 0;
  const colorOf = (h: Hotspot): Color => {
    if (h.current) return COLORS.protectedBefore;
    if (!h.ours) return COLORS.unprotected;
    const k = t;
    const a = COLORS.unprotected;
    const b = COLORS.newly;
    return [Math.round((a[0] as number) + ((b[0] as number) - (a[0] as number)) * k), Math.round((a[1] as number) + ((b[1] as number) - (a[1] as number)) * k), Math.round((a[2] as number) + ((b[2] as number) - (a[2] as number)) * k), 255];
  };
  const newly = c.hotspots.filter((h) => !h.current && h.ours);
  return [
    new PathLayer<LonLat[]>({ id: "silver-ghost", data: ghosts, getPath: (d: LonLat[]) => d as never, getColor: COLORS.ghost, getWidth: 3.5, widthUnits: "pixels", getDashArray: DASH, dashJustified: true, extensions: [dash], pickable: false } as never),
    new ScatterplotLayer<SilverSiteLL>({
      id: "silver-agent", data: c.ours, getPosition: (d) => [d.lon, d.lat, 2], radiusUnits: "meters", radiusMinPixels: 6, getRadius: c.radiusM * grow, getFillColor: [COLORS.agentFill[0] as number, COLORS.agentFill[1] as number, COLORS.agentFill[2] as number, Math.round(34 * t)],
      stroked: true, getLineColor: [COLORS.agentLine[0] as number, COLORS.agentLine[1] as number, COLORS.agentLine[2] as number, Math.round(255 * t)], lineWidthUnits: "pixels", getLineWidth: 2, visible: t > 0.002, updateTriggers: { getRadius: t, getFillColor: t, getLineColor: t }, pickable: false,
    }),
    new ScatterplotLayer<Hotspot>({
      id: "silver-hotspot-glow", data: newly, getPosition: (d) => [d.lon, d.lat, 30], radiusUnits: "pixels", getRadius: 34 + 24 * pulse, getFillColor: [22, 163, 74, Math.round((80 + 90 * pulse) * t)], stroked: true,
      getLineColor: [22, 163, 74, Math.round(255 * t)], lineWidthMinPixels: 3, visible: t > 0.02, updateTriggers: { getRadius: pulse, getFillColor: [t, pulse], getLineColor: t }, pickable: false,
    }),
    new ScatterplotLayer<Hotspot>({
      id: "silver-hotspots", data: c.hotspots, getPosition: (d) => [d.lon, d.lat, 32], radiusUnits: "pixels", getRadius: (d) => (!d.current && d.ours ? 20 + 3 * t : 13), getFillColor: colorOf, stroked: true, getLineColor: [255, 255, 255, 255], lineWidthMinPixels: 2.5,
      updateTriggers: { getFillColor: t, getRadius: t }, pickable: false,
    }),
  ];
}
