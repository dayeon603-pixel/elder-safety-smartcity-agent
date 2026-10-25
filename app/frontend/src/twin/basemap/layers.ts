/** deck.gl layers of the offline basemap. Drawn with depth test off (and no depth write) right after the ground polygon, before the buildings. */
import { COORDINATE_SYSTEM } from "@deck.gl/core";
import type { Color, Layer } from "@deck.gl/core";
import { PathLayer, PolygonLayer, TextLayer } from "@deck.gl/layers";
import { ROAD_CLASSES, ROAD_WIDTH_M } from "./data";
import type { Basemap, Label, Ring } from "./data";
import { GROUND, LABEL, LABEL_HALO, LAND_COLOR, PATH, RAIL, ROAD_CASING, ROAD_CASING_HIGHWAY, ROAD_CASING_MAJOR, ROAD_FILL, ROAD_FILL_HIGHWAY, ROAD_FILL_MAJOR, WATER, shade } from "./palette";
import type { Rgb } from "./palette";

const FLAT = { depthCompare: "always", depthWriteEnabled: false } as never;
const FONT = "Pretendard Variable, Pretendard, system-ui, sans-serif";
const EXTENT_M = 14_000;
const CASING_EXTRA_M = 2.6;
const HEAT_TINT: Color = [200, 70, 50, 54];
const HEAT_OVERLAY_ALPHA_NIGHT = 0.7;
const PHOTO_ROAD_SCALE = 0.35;
const PHOTO_ROAD_ALPHA = 120;

export interface BaseCtx {
  center: readonly [number, number];
  /** 0 night .. 1 day */
  day: number;
  /** heat alert of the shown mode: tint the 구 so the old ground colour cue stays */
  heat: boolean;
  boundary: Float32Array;
  /** street-level story: no text labels */
  street: boolean;
  /** satellite photo is the ground: polygons off, roads thin and translucent */
  photo: boolean;
}

const frame = (c: readonly [number, number]) => ({ coordinateSystem: COORDINATE_SYSTEM.METER_OFFSETS, coordinateOrigin: [c[0], c[1], 0] as [number, number, number] });
const roadFill = (cls: number): Rgb => { const n = ROAD_CLASSES[cls]; return n === "highway" ? ROAD_FILL_HIGHWAY : n === "primary" || n === "secondary" ? ROAD_FILL_MAJOR : n === "path" ? PATH : ROAD_FILL; };
const roadCasing = (cls: number): Rgb => { const n = ROAD_CLASSES[cls]; return n === "highway" ? ROAD_CASING_HIGHWAY : n === "primary" || n === "secondary" ? ROAD_CASING_MAJOR : ROAD_CASING; };

export function basemapLayers(b: Basemap, c: BaseCtx): Layer[] {
  const f = frame(c.center);
  const q = Math.round(c.day * 20);
  const d = q / 20;
  const earth: Ring[] = [[[-EXTENT_M, -EXTENT_M], [EXTENT_M, -EXTENT_M], [EXTENT_M, EXTENT_M], [-EXTENT_M, EXTENT_M], [-EXTENT_M, -EXTENT_M]]];
  if (c.photo) return photoOverlays(b, c, f, d, q);
  const out: Layer[] = [
    new PolygonLayer<Ring[]>({ id: "bm-earth", ...f, data: [earth], getPolygon: (r: Ring[]) => r as never, getFillColor: shade(GROUND, d), stroked: false, parameters: FLAT, updateTriggers: { getFillColor: q }, pickable: false }),
    new PolygonLayer({ id: "bm-land", ...f, data: b.land, getPolygon: (l: { poly: Ring[] }) => l.poly as never, getFillColor: (l: { k: string }) => shade(LAND_COLOR[l.k] ?? GROUND, d), stroked: false, parameters: FLAT, updateTriggers: { getFillColor: q }, pickable: false }),
    new PolygonLayer({ id: "bm-water", ...f, data: b.water, getPolygon: (l: { poly: Ring[] }) => l.poly as never, getFillColor: shade(WATER, d), stroked: false, parameters: FLAT, updateTriggers: { getFillColor: q }, pickable: false }),
    new PathLayer({ id: "bm-streams", ...f, data: b.streams, positionFormat: "XY", getPath: (s: { p: number[] }) => s.p as never, getWidth: (s: { w: number }) => s.w, widthUnits: "meters", widthMinPixels: 1.5, getColor: shade(WATER, d), capRounded: true, jointRounded: true, parameters: FLAT, updateTriggers: { getColor: q }, pickable: false }),
    new PathLayer({ id: "bm-road-casing", ...f, data: b.roads.filter((r) => ROAD_CLASSES[r.cls] !== "path"), positionFormat: "XY", getPath: (r: { p: number[] }) => r.p as never, getWidth: (r: { cls: number }) => ROAD_WIDTH_M[ROAD_CLASSES[r.cls] ?? "minor"] + CASING_EXTRA_M, widthUnits: "meters", widthMinPixels: 1.4, getColor: (r: { cls: number }) => shade(roadCasing(r.cls), d), capRounded: true, jointRounded: true, parameters: FLAT, updateTriggers: { getColor: q }, pickable: false }),
    new PathLayer({ id: "bm-road-fill", ...f, data: b.roads, positionFormat: "XY", getPath: (r: { p: number[] }) => r.p as never, getWidth: (r: { cls: number }) => ROAD_WIDTH_M[ROAD_CLASSES[r.cls] ?? "minor"], widthUnits: "meters", widthMinPixels: 0.8, getColor: (r: { cls: number }) => shade(roadFill(r.cls), d), capRounded: true, jointRounded: true, parameters: FLAT, updateTriggers: { getColor: q }, pickable: false }),
    new PathLayer({ id: "bm-rail", ...f, data: b.rail, positionFormat: "XY", getPath: (p: number[]) => p as never, getWidth: 2.6, widthUnits: "meters", widthMinPixels: 1.3, getColor: shade(RAIL, d), parameters: FLAT, updateTriggers: { getColor: q }, pickable: false }),
  ];
  if (c.heat && c.boundary.length > 0) {
    out.push(new PolygonLayer({ id: "bm-heat", ...f, data: [c.boundary], positionFormat: "XY", getPolygon: (r: Float32Array) => r as never, getFillColor: [HEAT_TINT[0] as number, HEAT_TINT[1] as number, HEAT_TINT[2] as number, Math.round((HEAT_TINT[3] as number) * (1 - (1 - HEAT_OVERLAY_ALPHA_NIGHT) * (1 - d)))] as Color, stroked: false, parameters: FLAT, updateTriggers: { getFillColor: q }, pickable: false }));
  }
  return out;
}

/** Thin roads, rail and (night-darkened) streams over the photo; the heat tint stays. */
function photoOverlays(b: Basemap, c: BaseCtx, f: ReturnType<typeof frame>, d: number, q: number): Layer[] {
  const out: Layer[] = [
    new PathLayer({ id: "bm-road-photo", ...f, data: b.roads.filter((r) => ROAD_CLASSES[r.cls] !== "path" && ROAD_CLASSES[r.cls] !== "service"), positionFormat: "XY", getPath: (r: { p: number[] }) => r.p as never, getWidth: (r: { cls: number }) => ROAD_WIDTH_M[ROAD_CLASSES[r.cls] ?? "minor"] * PHOTO_ROAD_SCALE, widthUnits: "meters", widthMinPixels: 0.6, getColor: [255, 255, 255, PHOTO_ROAD_ALPHA], parameters: FLAT, pickable: false }),
  ];
  if (c.heat && c.boundary.length > 0) {
    out.push(new PolygonLayer({ id: "bm-heat", ...f, data: [c.boundary], positionFormat: "XY", getPolygon: (r: Float32Array) => r as never, getFillColor: [HEAT_TINT[0] as number, HEAT_TINT[1] as number, HEAT_TINT[2] as number, Math.round((HEAT_TINT[3] as number) * 0.8)] as Color, stroked: false, parameters: FLAT, updateTriggers: { getFillColor: q }, pickable: false }));
  }
  void d;
  return out;
}

const labelColor = (d: number, kind: Label["kind"], photo: boolean): Color => {
  const c: Rgb = kind === "water" ? [40, 98, 150] : kind === "peak" ? [52, 96, 52] : LABEL;
  return photo || d <= 0.5 ? [248, 250, 253, 255] : [c[0], c[1], c[2], 255];
};

/** Labels are drawn after the buildings so they stay readable; ~20 landmarks only. */
export function labelLayers(b: Basemap, c: BaseCtx): Layer[] {
  if (c.street || b.labels.length === 0) return [];
  const d = Math.round(c.day * 20) / 20;
  return [new TextLayer<Label>({
    id: "bm-labels", ...frame(c.center), data: b.labels, getPosition: (l: Label) => [l.x, l.y, 8] as never, getText: (l: Label) => l.text, getSize: (l: Label) => l.size, getColor: (l: Label) => labelColor(d, l.kind, c.photo),
    fontFamily: FONT, fontWeight: 600, characterSet: "auto", fontSettings: { sdf: true } as never, outlineWidth: 3, outlineColor: d > 0.5 && !c.photo ? [LABEL_HALO[0], LABEL_HALO[1], LABEL_HALO[2], 235] : [10, 14, 22, 235],
    billboard: true, background: false, parameters: { depthCompare: "always" } as never, updateTriggers: { getColor: [d, c.photo] }, pickable: false,
  } as never)];
}
