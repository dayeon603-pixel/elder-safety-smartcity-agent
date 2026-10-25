/**
 * Scene parsing and triangulation, pure functions (run inside sceneWorker.ts, unit-tested on the main thread).
 * Input rings are already closed (first == last) and in metres from the scene centre. Output is deck.gl binary: outer ring CW and holes CCW, earcut triangle
 * indices with holes honoured, per-vertex elevation and colour. Every footprint is kept: nothing is simplified or skipped
 * except degenerate polygons (< 3 distinct vertices), which have no area.
 */
import earcut from "earcut";
import { FORBIDDEN_SOURCE_RE } from "./constants";
import { buildingRgb } from "./basemap/palette";
import type { SceneBuffers, SceneWire } from "./types";

export interface BuildStyle {
  low: readonly [number, number, number];
  high: readonly [number, number, number];
  heightPercentile: number;
}

const POS_DIM = 2;
const MIN_RING_VERTICES = 4; // closed triangle = 4 vertices
const COLOR_CHANNELS = 4;
const ALPHA_OPAQUE = 255;

function signedArea(p: Float32Array, start: number, end: number): number {
  let a = 0;
  for (let i = start, j = end - 1; i < end; j = i++) a += (p[j * POS_DIM] as number) * (p[i * POS_DIM + 1] as number) - (p[i * POS_DIM] as number) * (p[j * POS_DIM + 1] as number);
  return a / 2;
}

function reverseRing(p: Float32Array, start: number, end: number): void {
  for (let a = start, b = end - 1; a < b; a++, b--) {
    for (let d = 0; d < POS_DIM; d++) {
      const tmp = p[a * POS_DIM + d] as number;
      p[a * POS_DIM + d] = p[b * POS_DIM + d] as number;
      p[b * POS_DIM + d] = tmp;
    }
  }
}

export function percentile(sorted: Float32Array | number[], q: number): number {
  if (sorted.length === 0) return 0;
  return sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))] as number;
}

function creditOf(w: SceneWire): string | null {
  const entry = w.sources?.find((x) => x.sources_json?.dataset !== undefined);
  const j = entry?.sources_json;
  if (entry === undefined || j === undefined) return null;
  if (entry.display_source !== undefined && !FORBIDDEN_SOURCE_RE.test(entry.display_source)) return entry.display_source;
  const text = [j.dataset, j.license, j.data_reference_date === undefined ? null : `${j.data_reference_date.slice(0, 10)} 기준`].filter((x): x is string => x !== undefined && x !== null).join(" · ");
  return FORBIDDEN_SOURCE_RE.test(text) ? null : text;
}

interface Ring { src: ArrayLike<number>; s: number; e: number }

export function buildScene(wire: SceneWire, style: BuildStyle, now: () => number = () => performance.now()): SceneBuffers {
  const t0 = now();
  const B = wire.buildings;
  const n = B.start_indices.length - 1;
  if (n < 0 || B.heights.length < n) throw new RangeError("scene: heights shorter than polygons");

  const holesByParent = new Map<number, Ring[]>();
  const H = B.holes;
  if (H !== undefined) {
    for (let i = 0; i < H.count; i++) {
      const parent = H.parent[i] as number;
      const ring: Ring = { src: H.positions, s: H.start_indices[i] as number, e: H.start_indices[i + 1] as number };
      const list = holesByParent.get(parent);
      if (list === undefined) holesByParent.set(parent, [ring]);
      else list.push(ring);
    }
  }

  const kept: number[] = [];
  const rings: Ring[][] = [];
  let outVerts = 0;
  for (let k = 0; k < n; k++) {
    const s = B.start_indices[k] as number;
    const e = B.start_indices[k + 1] as number;
    if (e - s < MIN_RING_VERTICES) continue;
    const list: Ring[] = [{ src: B.positions, s, e }, ...(holesByParent.get(k) ?? []).filter((r) => r.e - r.s >= MIN_RING_VERTICES)];
    kept.push(k);
    rings.push(list);
    for (const r of list) outVerts += r.e - r.s;
  }

  const positions = new Float32Array(outVerts * POS_DIM);
  const startIndices = new Uint32Array(kept.length + 1);
  const vertexValid = new Uint16Array(outVerts);
  const elevations = new Float32Array(outVerts);
  const colors = new Uint8Array(outVerts * COLOR_CHANNELS);
  const idx: number[] = [];

  const heightsKept = Float32Array.from(kept, (k) => B.heights[k] as number);
  const sorted = Float32Array.from(heightsKept).sort();
  const hRef = Math.max(percentile(sorted, style.heightPercentile), Number.EPSILON);

  let v = 0;
  kept.forEach((_, bi) => {
    startIndices[bi] = v;
    const polyStart = v;
    const holeLocal: number[] = [];
    (rings[bi] as Ring[]).forEach((r, ri) => {
      const ringStart = v;
      for (let i = r.s; i < r.e; i++) {
        positions[v * POS_DIM] = r.src[i * POS_DIM] as number;
        positions[v * POS_DIM + 1] = r.src[i * POS_DIM + 1] as number;
        vertexValid[v] = 1;
        v++;
      }
      vertexValid[v - 1] = 0; // last vertex of a ring does not connect onward
      const wantCw = ri === 0;
      if ((signedArea(positions, ringStart, v) > 0) === wantCw) reverseRing(positions, ringStart, v);
      if (ri > 0) holeLocal.push(ringStart - polyStart);
    });
    const local = positions.subarray(polyStart * POS_DIM, v * POS_DIM);
    for (const t of earcut(local as unknown as number[], holeLocal, POS_DIM)) idx.push(t + polyStart);
    const h = heightsKept[bi] as number;
    const f = Math.min(1, h / hRef);
    const [r8, g8, b8] = buildingRgb(B.use === undefined ? null : (B.use[kept[bi] as number] ?? null), f); // BASEMAP hook: colour by use (neutral ramp without use)
    for (let i = polyStart; i < v; i++) {
      elevations[i] = h;
      colors[i * COLOR_CHANNELS] = r8;
      colors[i * COLOR_CHANNELS + 1] = g8;
      colors[i * COLOR_CHANNELS + 2] = b8;
      colors[i * COLOR_CHANNELS + 3] = ALPHA_OPAQUE;
    }
  });
  startIndices[kept.length] = v;

  const R = wire.roads;
  const boundaryEnd = wire.boundary.start_indices[1] ?? wire.boundary.positions.length / POS_DIM;
  return {
    buildingCount: kept.length, vertexCount: v, triangleIndexCount: idx.length, positions, startIndices, indices: Uint32Array.from(idx),
    vertexValid, elevations, colors,
    roadCount: R?.count ?? 0, roadPositions: Float32Array.from(R?.positions ?? []), roadStartIndices: Uint32Array.from(R?.start_indices ?? [0]),
    boundaryPositions: Float32Array.from(wire.boundary.positions.slice(0, (boundaryEnd as number) * POS_DIM)),
    center: wire.center, bbox: wire.bbox, sources: (wire.sources ?? []).map((x) => x.file),
    heightSource: typeof B.height_source === "string" ? B.height_source : B.level_height_m === undefined ? null : `층수 × ${B.level_height_m} m`,
    buildingUse: B.use === undefined ? null : Uint8Array.from(kept, (k) => B.use?.[k] ?? 5), useNames: B.use_names ?? null,
    measuredPct: B.measured_pct ?? null, credit: creditOf(wire), fetchedAt: wire.fetched_at ?? null, parseMs: now() - t0,
  };
}
