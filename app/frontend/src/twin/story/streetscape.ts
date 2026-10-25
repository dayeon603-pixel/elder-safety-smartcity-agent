/** Street-level legibility: light walk network, footprint edge lines near the walk, zebra crossing, bus shelter, and a building occlusion test for the chase camera. */
import { COORDINATE_SYSTEM } from "@deck.gl/core";
import type { Color, Layer } from "@deck.gl/core";
import { PathLayer, SolidPolygonLayer } from "@deck.gl/layers";
import type { SceneBuffers } from "../types";
import { metreFrame } from "./layers";
import type { XY } from "./geo";

const CELL_M = 48;
const RAY_STEP_M = 1.5;
const ROAD_LIGHT: Color = [196, 206, 218, 235];
const EDGE_COLOR: Color = [92, 100, 112, 190];
const FLOOR_COLOR: Color = [92, 100, 112, 60];
const FLOOR_H_M = 3.2;
const ZEBRA_COLOR: Color = [250, 250, 250, 255];
const SHELTER_ROOF: Color = [120, 190, 235, 210];
const SHELTER_PAD: Color = [88, 100, 114, 255];

export const ZEBRA_BARS = 9;
export const ZEBRA_BAR_LEN_M = 3.4;
export const ZEBRA_BAR_W_M = 0.5;
export const ZEBRA_PITCH_M = 0.95;

interface Box { minX: number; maxX: number; minY: number; maxY: number; h: number }

/** Grid index of building bounding boxes + heights (conservative: a bbox hit counts as blocked). */
export class BuildingIndex {
  private cells = new Map<number, Box[]>();
  constructor(sc: Pick<SceneBuffers, "positions" | "startIndices" | "buildingCount" | "elevations">) {
    const totalV = sc.positions.length / 2;
    for (let b = 0; b < sc.buildingCount; b++) {
      const v0 = sc.startIndices[b] as number;
      const v1 = b + 1 < sc.startIndices.length ? (sc.startIndices[b + 1] as number) : totalV;
      let minX = Infinity; let maxX = -Infinity; let minY = Infinity; let maxY = -Infinity;
      for (let v = v0; v < v1; v++) {
        const x = sc.positions[2 * v] as number; const y = sc.positions[2 * v + 1] as number;
        if (x < minX) minX = x; if (x > maxX) maxX = x; if (y < minY) minY = y; if (y > maxY) maxY = y;
      }
      const box: Box = { minX, maxX, minY, maxY, h: sc.elevations[v0] as number };
      for (let cx = Math.floor(minX / CELL_M); cx <= Math.floor(maxX / CELL_M); cx++) for (let cy = Math.floor(minY / CELL_M); cy <= Math.floor(maxY / CELL_M); cy++) {
        const k = cx * 100_003 + cy;
        const arr = this.cells.get(k);
        if (arr === undefined) this.cells.set(k, [box]); else arr.push(box);
      }
    }
  }
  /** True if a building box taller than the ray's height at that point lies on the segment a -> b (x, y, z metres). */
  blocked(a: readonly [number, number, number], b: readonly [number, number, number]): boolean {
    const len = Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]);
    const n = Math.max(1, Math.ceil(len / RAY_STEP_M));
    for (let i = 1; i < n; i++) {
      const t = i / n;
      const x = a[0] + (b[0] - a[0]) * t; const y = a[1] + (b[1] - a[1]) * t; const z = a[2] + (b[2] - a[2]) * t;
      const arr = this.cells.get(Math.floor(x / CELL_M) * 100_003 + Math.floor(y / CELL_M));
      if (arr === undefined) continue;
      for (const bx of arr) if (x >= bx.minX && x <= bx.maxX && y >= bx.minY && y <= bx.maxY && z < bx.h) return true;
    }
    return false;
  }
}

export interface EdgeBuffers { count: number; positions: Float32Array; startIndices: Uint32Array; floorCount: number; floorPositions: Float32Array; floorStartIndices: Uint32Array }

/** Roof + base outlines (3D) of the buildings within `radiusM` of any anchor point. */
export function buildEdges(sc: Pick<SceneBuffers, "positions" | "startIndices" | "buildingCount" | "elevations">, anchors: readonly XY[], radiusM: number): EdgeBuffers {
  const totalV = sc.positions.length / 2;
  const pos: number[] = [];
  const starts: number[] = [];
  const fpos: number[] = [];
  const fstarts: number[] = [];
  for (let b = 0; b < sc.buildingCount; b++) {
    const v0 = sc.startIndices[b] as number;
    const v1 = b + 1 < sc.startIndices.length ? (sc.startIndices[b + 1] as number) : totalV;
    const x0 = sc.positions[2 * v0] as number; const y0 = sc.positions[2 * v0 + 1] as number;
    if (!anchors.some((a) => Math.abs(a[0] - x0) < radiusM && Math.abs(a[1] - y0) < radiusM)) continue;
    const h = sc.elevations[v0] as number;
    for (const z of [h, 0.06]) {
      starts.push(pos.length / 3);
      for (let v = v0; v < v1; v++) pos.push(sc.positions[2 * v] as number, sc.positions[2 * v + 1] as number, z);
    }
    // floor lines: one ring per storey (facade structure)
    for (let z = FLOOR_H_M; z < h - 0.5; z += FLOOR_H_M) {
      fstarts.push(fpos.length / 3);
      for (let v = v0; v < v1; v++) fpos.push(sc.positions[2 * v] as number, sc.positions[2 * v + 1] as number, z);
    }
  }
  return {
    count: starts.length, positions: Float32Array.from(pos), startIndices: Uint32Array.from([...starts, pos.length / 3]),
    floorCount: fstarts.length, floorPositions: Float32Array.from(fpos), floorStartIndices: Uint32Array.from([...fstarts, fpos.length / 3]),
  };
}

export function streetLayers(sc: SceneBuffers, edges: EdgeBuffers | null, opacity: number): Layer[] {
  const out: Layer[] = [
    new PathLayer({
      id: "story-roads-light", ...metreFrame(sc.center),
      data: { length: sc.roadCount, startIndices: sc.roadStartIndices, attributes: { getPath: { value: sc.roadPositions, size: 2 } } } as never,
      getColor: ROAD_LIGHT, widthUnits: "meters", getWidth: 3.2, widthMinPixels: 1, opacity, pickable: false, parameters: { depthCompare: "less-equal", depthBias: -2 } as never,
    } as never),
  ];
  if (edges !== null && edges.count > 0) {
    out.push(new PathLayer({
      id: "story-edges", ...metreFrame(sc.center),
      data: { length: edges.count, startIndices: edges.startIndices, attributes: { getPath: { value: edges.positions, size: 3 } } } as never,
      getColor: EDGE_COLOR, widthUnits: "pixels", getWidth: 1.8, opacity, pickable: false,
    } as never));
    out.push(new PathLayer({
      id: "story-floors", ...metreFrame(sc.center),
      data: { length: edges.floorCount, startIndices: edges.floorStartIndices, attributes: { getPath: { value: edges.floorPositions, size: 3 } } } as never,
      getColor: FLOOR_COLOR, widthUnits: "pixels", getWidth: 1, opacity, pickable: false,
    } as never));
  }
  return out;
}

/** White bars lying along the road, stacked across it (real crossing length when known). */
export function zebraLayer(center: readonly [number, number], at: XY, roadHeading: number, crossLenM: number): Layer {
  const ux = Math.cos(roadHeading); const uy = Math.sin(roadHeading);
  const nx = -uy; const ny = ux;
  const bars: XY[][] = [];
  const bn = Math.max(5, Math.min(ZEBRA_BARS, Math.round(crossLenM / ZEBRA_PITCH_M)));
  for (let i = 0; i < bn; i++) {
    const off = (i - (bn - 1) / 2) * ZEBRA_PITCH_M;
    const cx = at[0] + nx * off; const cy = at[1] + ny * off;
    const hx = (ux * ZEBRA_BAR_LEN_M) / 2; const hy = (uy * ZEBRA_BAR_LEN_M) / 2;
    const wx = (nx * ZEBRA_BAR_W_M) / 2; const wy = (ny * ZEBRA_BAR_W_M) / 2;
    bars.push([[cx - hx - wx, cy - hy - wy], [cx + hx - wx, cy + hy - wy], [cx + hx + wx, cy + hy + wy], [cx - hx + wx, cy - hy + wy]]);
  }
  return new SolidPolygonLayer<XY[]>({ id: "story-zebra", coordinateSystem: COORDINATE_SYSTEM.METER_OFFSETS, coordinateOrigin: [center[0], center[1], 0], data: bars, getPolygon: (d) => d.map((p) => [p[0], p[1], 0.2]) as never, getFillColor: ZEBRA_COLOR, extruded: false, pickable: false });
}

/** Bus shelter: pad + roof on the stop, aligned with the walking direction. */
export function shelterLayers(center: readonly [number, number], at: XY, heading: number, roofH: number): Layer[] {
  const ux = Math.cos(heading); const uy = Math.sin(heading);
  const nx = -uy; const ny = ux;
  const rect = (len: number, wid: number, z: number): [number, number, number][] => [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([a, b]) => [at[0] + ux * len * (a as number) + nx * wid * (b as number), at[1] + uy * len * (a as number) + ny * wid * (b as number), z]);
  const frame = { coordinateSystem: COORDINATE_SYSTEM.METER_OFFSETS, coordinateOrigin: [center[0], center[1], 0] as [number, number, number] };
  return [
    new SolidPolygonLayer({ id: "story-shelter-pad", ...frame, data: [rect(2.2, 1.0, 0.1)], getPolygon: (d: number[][]) => d as never, getFillColor: SHELTER_PAD, extruded: false, pickable: false }),
    new SolidPolygonLayer({ id: "story-shelter-roof", ...frame, data: [rect(2.2, 1.0, roofH)], getPolygon: (d: number[][]) => d as never, getFillColor: SHELTER_ROOF, extruded: true, getElevation: 0.25, pickable: false }),
  ];
}
