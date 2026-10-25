/** Offline basemap (Protomaps/OpenStreetMap extract, baked by scripts/bake_basemap.mjs): types, loader and label picking. */
import { LAND_ORDER } from "./palette";

export const BASEMAP_URL = `${import.meta.env.BASE_URL}basemap/gwanak_basemap.json`;
export const BASEMAP_CREDIT = "© OpenStreetMap contributors, Protomaps (ODbL)";

export interface LandPoly { k: string; r: number[][] }
export type Ring = [number, number][];
export interface PolyItem { k: string; poly: Ring[] }
const pairs = (flat: number[]): Ring => { const o: Ring = []; for (let i = 0; i + 1 < flat.length; i += 2) o.push([flat[i] as number, flat[i + 1] as number]); return o; };
export interface Line { k?: string; d?: string; n?: string; p: number[] }
export interface Poi { k: string; n: string; x: number; y: number; e?: number }
export interface BasemapWire { source: string; center: [number, number]; land: LandPoly[]; water: { r: number[][] }[]; waterLines: Line[]; roads: Line[]; rail: Line[]; pois: Poi[]; places: Poi[] }

export interface RoadItem { p: number[]; cls: number }
export interface Basemap {
  land: PolyItem[];
  water: { poly: Ring[] }[];
  streams: { p: number[]; w: number }[];
  roads: RoadItem[];
  rail: number[][];
  labels: Label[];
}
export interface Label { text: string; x: number; y: number; kind: "station" | "place" | "road" | "water" | "peak" | "campus"; size: number }

/** Road classes, low to high draw order. */
export const ROAD_CLASSES = ["path", "service", "minor", "other", "tertiary", "secondary", "primary", "highway"] as const;
export const ROAD_WIDTH_M: Readonly<Record<(typeof ROAD_CLASSES)[number], number>> = { path: 1.8, service: 3.5, minor: 6, other: 5, tertiary: 9, secondary: 11, primary: 14, highway: 18 };

export function roadClass(k: string | undefined, d: string | undefined): number {
  let c: (typeof ROAD_CLASSES)[number];
  if (k === "highway" || d === "trunk" || d === "trunk_link") c = "highway";
  else if (k === "major_road") c = d === "primary" || d === "primary_link" ? "primary" : d === "secondary" || d === "secondary_link" ? "secondary" : "tertiary";
  else if (k === "minor_road") c = d === "service" ? "service" : "minor";
  else if (k === "path") c = "path";
  else c = "other";
  return ROAD_CLASSES.indexOf(c);
}

/** Landmarks to label (only names present in the extract are drawn). */
const STATIONS = ["신림", "봉천", "서울대입구", "낙성대", "사당", "신대방", "보라매", "서울대벤처타운"];
const ROADS = ["남부순환로", "관악로", "신림로", "시흥대로", "봉천로", "도림로"];
const STREAMS = ["도림천", "봉천천"];
const PLACES = ["신림동", "봉천동", "남현동", "대방동"];
const CAMPUS_RE = /^서울대학교/;
const PEAK = "관악산";

const mid = (p: number[]): [number, number] => { const i = Math.floor(p.length / 4) * 2; return [p[i] as number, p[i + 1] as number]; };
const lenOf = (p: number[]): number => { let s = 0; for (let i = 2; i < p.length; i += 2) s += Math.hypot((p[i] as number) - (p[i - 2] as number), (p[i + 1] as number) - (p[i - 1] as number)); return s; };

export function pickLabels(w: BasemapWire): Label[] {
  const out: Label[] = [];
  for (const n of STATIONS) {
    const p = w.pois.find((q) => q.k === "station" && q.n === n);
    if (p !== undefined) out.push({ text: `${n}역`, x: p.x, y: p.y, kind: "station", size: 15 });
  }
  const campus = w.pois.find((q) => q.k === "university" && CAMPUS_RE.test(q.n));
  if (campus !== undefined) out.push({ text: "서울대학교", x: campus.x, y: campus.y, kind: "campus", size: 17 });
  const peaks = w.pois.filter((q) => q.k === "peak" && q.n === PEAK).sort((a, b) => (b.e ?? 0) - (a.e ?? 0));
  if (peaks[0] !== undefined) out.push({ text: PEAK, x: peaks[0].x, y: peaks[0].y, kind: "peak", size: 19 });
  for (const n of ROADS) {
    const best = w.roads.filter((r) => r.n === n).sort((a, b) => lenOf(b.p) - lenOf(a.p))[0];
    if (best !== undefined) { const [x, y] = mid(best.p); out.push({ text: n, x, y, kind: "road", size: 13 }); }
  }
  for (const n of STREAMS) {
    const best = w.waterLines.filter((r) => r.n === n).sort((a, b) => lenOf(b.p) - lenOf(a.p))[0];
    if (best !== undefined) { const [x, y] = mid(best.p); out.push({ text: n, x, y, kind: "water", size: 13 }); }
  }
  for (const n of PLACES) {
    const p = w.places.find((q) => q.n === n);
    if (p !== undefined) out.push({ text: n, x: p.x, y: p.y, kind: "place", size: 16 });
  }
  return out;
}

export function prepare(w: BasemapWire): Basemap {
  const order = new Map(LAND_ORDER.map((k, i) => [k, i]));
  const land = [...w.land].filter((l) => order.has(l.k)).sort((a, b) => (order.get(a.k) as number) - (order.get(b.k) as number));
  const streams = w.waterLines.filter((l) => l.k !== "ditch" && l.k !== "drain").map((l) => ({ p: l.p, w: l.k === "river" ? 16 : 5 }));
  const roads = w.roads.map((r): RoadItem => ({ p: r.p, cls: roadClass(r.k, r.d) })).sort((a, b) => a.cls - b.cls);
  return { land: land.map((l) => ({ k: l.k, poly: l.r.map(pairs) })), water: w.water.map((x) => ({ poly: x.r.map(pairs) })), streams, roads, rail: w.rail.map((r) => r.p), labels: pickLabels(w) };
}

export async function loadBasemap(url: string = BASEMAP_URL): Promise<Basemap> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`basemap ${res.status}`);
  return prepare((await res.json()) as BasemapWire);
}
