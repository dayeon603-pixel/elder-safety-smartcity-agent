/**
 * Pedestrian dot sampling. Counts per 행정동 come from the real 서울 생활인구; only the path of each dot is
 * synthetic (non-backtracking random walk on the real walk network). Deterministic: dot i of a dong is the same
 * trip for every hour, so changing the hour only adds or removes dots.
 */
import { MAX_DOTS, PEOPLE_PER_DOT, SAMPLE_SEED, TRIP_LEN_MAX_M, TRIP_LEN_MIN_M, TRIP_LOOP_S, TRIP_VIS_MPS } from "./constants";
import { pointInRing } from "./geo";
import type { XY } from "./geo";
import { edgePolyline } from "./graph";
import type { Graph } from "./graph";

export interface DongRow { adm_cd: string; total: number; age65p: number }
export interface DongGeom { adm_cd: string; adm_nm: string; rings: XY[][] }

export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Graph nodes inside each dong (bbox prefilter + point in polygon). */
export function nodesByDong(g: Graph, dongs: readonly DongGeom[]): Map<string, number[]> {
  const boxes = dongs.map((d) => {
    const all = d.rings.flat();
    return { minX: Math.min(...all.map((p) => p[0])), maxX: Math.max(...all.map((p) => p[0])), minY: Math.min(...all.map((p) => p[1])), maxY: Math.max(...all.map((p) => p[1])) };
  });
  const out = new Map<string, number[]>(dongs.map((d) => [d.adm_cd, []]));
  for (let n = 0; n < g.nodeCount; n++) {
    const x = g.nodeX[n] as number;
    const y = g.nodeY[n] as number;
    for (let i = 0; i < dongs.length; i++) {
      const b = boxes[i] as (typeof boxes)[number];
      if (x < b.minX || x > b.maxX || y < b.minY || y > b.maxY) continue;
      if ((dongs[i] as DongGeom).rings.some((r) => pointInRing(x, y, r))) { (out.get((dongs[i] as DongGeom).adm_cd) as number[]).push(n); break; }
    }
  }
  return out;
}

export interface DotPlan { adm_cd: string; dots: number; dots65: number }

/** People per dot: the nominal scale, raised (never lowered) if the total would exceed the dot budget. */
export function peoplePerDot(rows: readonly DongRow[]): number {
  const total = rows.reduce((s, r) => s + Math.max(0, r.total), 0);
  return Math.max(PEOPLE_PER_DOT, Math.ceil(total / MAX_DOTS / 10) * 10);
}

export function planDots(rows: readonly DongRow[], perDot: number): DotPlan[] {
  return rows.map((r) => {
    const dots = Math.round(Math.max(0, r.total) / perDot);
    const share = r.total > 0 ? Math.min(1, Math.max(0, r.age65p / r.total)) : 0;
    return { adm_cd: r.adm_cd, dots, dots65: Math.round(dots * share) };
  });
}

export interface TripBuffers {
  count: number;
  startIndices: Uint32Array;
  positions: Float32Array;
  timestamps: Float32Array;
  /** per vertex RGBA */
  colors: Uint8Array;
  is65: Uint8Array;
}

function walkTrip(g: Graph, start: number, rnd: () => number, targetLen: number): XY[] {
  const path: XY[] = [[g.nodeX[start] as number, g.nodeY[start] as number]];
  let cur = start;
  let prev = -1;
  let len = 0;
  while (len < targetLen) {
    const a = g.adjStart[cur] as number;
    const b = g.adjStart[cur + 1] as number;
    if (b === a) break;
    const opts: number[] = [];
    for (let k = a; k < b; k++) if ((g.adjTo[k] as number) !== prev) opts.push(k);
    const k = opts.length > 0 ? (opts[Math.floor(rnd() * opts.length)] as number) : a;
    const edge = g.adjEdge[k] as number;
    const seg = edgePolyline(g, edge, cur);
    path.push(...seg.slice(1));
    len += g.edgeLen[edge] as number;
    prev = cur;
    cur = g.adjTo[k] as number;
  }
  return path;
}

/** One dot's trip is a pure function of (dong seed, index): stable across hours. */
export function sampleTrips(g: Graph, nodes: ReadonlyMap<string, number[]>, plans: readonly DotPlan[], colorNormal: ArrayLike<number>, color65: ArrayLike<number>): TripBuffers {
  const trips: { path: XY[]; t0: number; old: boolean }[] = [];
  for (const p of plans) {
    const pool = nodes.get(p.adm_cd) ?? [];
    if (pool.length === 0) continue;
    const seed = SAMPLE_SEED ^ Number(p.adm_cd.slice(-5));
    const rnd = mulberry32(seed);
    for (let i = 0; i < p.dots; i++) {
      const start = pool[Math.floor(rnd() * pool.length)] as number;
      const len = TRIP_LEN_MIN_M + rnd() * (TRIP_LEN_MAX_M - TRIP_LEN_MIN_M);
      const path = walkTrip(g, start, rnd, len);
      const dur = tripLen(path) / TRIP_VIS_MPS;
      const t0 = rnd() * Math.max(1, TRIP_LOOP_S - dur);
      trips.push({ path, t0, old: i < p.dots65 });
    }
  }
  const nV = trips.reduce((s, t) => s + t.path.length, 0);
  const out: TripBuffers = { count: trips.length, startIndices: new Uint32Array(trips.length + 1), positions: new Float32Array(nV * 2), timestamps: new Float32Array(nV), colors: new Uint8Array(nV * 4), is65: new Uint8Array(trips.length) };
  let v = 0;
  trips.forEach((t, ti) => {
    out.startIndices[ti] = v;
    out.is65[ti] = t.old ? 1 : 0;
    const col = t.old ? color65 : colorNormal;
    let acc = 0;
    t.path.forEach((pt, k) => {
      if (k > 0) acc += Math.hypot(pt[0] - (t.path[k - 1] as XY)[0], pt[1] - (t.path[k - 1] as XY)[1]);
      out.positions[2 * v] = pt[0];
      out.positions[2 * v + 1] = pt[1];
      out.timestamps[v] = t.t0 + acc / TRIP_VIS_MPS;
      for (let c = 0; c < 4; c++) out.colors[4 * v + c] = col[c] as number;
      v++;
    });
  });
  out.startIndices[trips.length] = v;
  return out;
}

function tripLen(path: readonly XY[]): number {
  let s = 0;
  for (let i = 1; i < path.length; i++) s += Math.hypot((path[i] as XY)[0] - (path[i - 1] as XY)[0], (path[i] as XY)[1] - (path[i - 1] as XY)[1]);
  return s;
}
