/**
 * deck.gl layer factories. Static layers (buildings, roads, boundary) are built once per scene from prebuilt typed
 * arrays and never re-created; dynamic layers are small and rebuilt per animation frame with stable accessors +
 * updateTriggers so GPU buffers are only re-uploaded when their inputs change.
 */
import { PathLayer, ScatterplotLayer, ColumnLayer, SolidPolygonLayer, PolygonLayer, TextLayer } from "@deck.gl/layers";
import { COORDINATE_SYSTEM } from "@deck.gl/core";
import type { Color, Layer } from "@deck.gl/core";
import { PALETTE, RENDER } from "./constants";
import type { AedPoint, LonLat, Mode, SceneBuffers, TwinState } from "./types";

const AED_RADIUS_M = 18;
const AED_OPEN_H = 110;
const AED_CLOSED_H = 6;
const AED_GLOW_OPEN_M = 48;
const AED_GLOW_CLOSED_M = 26;
const AED_MIN_PX = 6;
const AED_MAX_PX = 34;
/** Brighter building material while the street-level 시민 시점 is active, so facades read as buildings. */
const STREET_AMBIENT = 0.8;
const FLIP_MIN_PX = 24;
const FLIP_MAX_PX = 78;
const STOP_RADIUS_M = 11;
const STOP_H = 46;
const CROSSWALK_RADIUS_M = 16;
const ACCIDENT_RADIUS_M = 34;
const CAMERA_RADIUS_M = 20;
const TRANSITION_MS = 700;
const PULSE_RINGS = 3;
const PULSE_MAX_M = 260;
const ALPHA_MAX = 255;
const LAYER_Z_LIFT = 0.4;

const meterFrame = (s: SceneBuffers) => ({ coordinateSystem: COORDINATE_SYSTEM.METER_OFFSETS, coordinateOrigin: [s.center[0], s.center[1], 0] as [number, number, number] });

/** Binary building data per scene: the same object is reused so toggling the street-view material never re-uploads geometry. */
const buildingData = new WeakMap<SceneBuffers, unknown>();

export function buildingLayer(s: SceneBuffers, street: boolean = false): Layer {
  let data = buildingData.get(s);
  if (data === undefined) {
    data = {
      length: s.buildingCount,
      startIndices: s.startIndices,
      attributes: {
        getPolygon: { value: s.positions, size: 2 },
        indices: { value: s.indices, size: 1 },
        instanceVertexValid: { value: s.vertexValid, size: 1 },
        getElevation: { value: s.elevations, size: 1 },
        getFillColor: { value: s.colors, size: 4, normalized: true },
      },
    };
    buildingData.set(s, data);
  }
  return new SolidPolygonLayer({
    id: "buildings",
    ...meterFrame(s),
    data: data as never,
    _normalize: false,
    extruded: true,
    elevationScale: RENDER.elevationScale,
    material: { ambient: street ? STREET_AMBIENT : RENDER.material.ambient, diffuse: RENDER.material.diffuse, shininess: RENDER.material.shininess, specularColor: RENDER.material.specularColor },
    pickable: false,
  });
}

export function roadLayer(s: SceneBuffers): Layer {
  return new PathLayer({
    id: "roads",
    ...meterFrame(s),
    data: { length: s.roadCount, startIndices: s.roadStartIndices, attributes: { getPath: { value: s.roadPositions, size: 2 } } } as never,
    getColor: PALETTE.road as Color,
    widthUnits: "meters",
    getWidth: 3,
    widthMinPixels: 0.6,
    capRounded: false,
    pickable: false,
  });
}

export function boundaryLayers(s: SceneBuffers, groundColor: Color): Layer[] {
  const ring = s.boundaryPositions;
  return [
    new PolygonLayer<Float32Array>({
      id: "ground", ...meterFrame(s), data: [ring], positionFormat: "XY", getPolygon: (d: Float32Array) => d as never, getFillColor: groundColor, stroked: false, filled: true, pickable: false,
      transitions: { getFillColor: TRANSITION_MS }, updateTriggers: { getFillColor: groundColor },
    }),
    new PathLayer<Float32Array>({ id: "boundary", ...meterFrame(s), data: [ring], positionFormat: "XY", getPath: (d: Float32Array) => d as never, getColor: PALETTE.boundary as Color, getWidth: 2, widthUnits: "pixels", pickable: false }),
  ];
}

export interface DynCtx {
  state: TwinState | null;
  mode: Mode;
  /** 0 = 현행 기준, 1 = 에이전트 (animated) */
  modeT: number;
  hour: number;
  camera: { lat: number; lon: number; label: string } | null;
  highlight: { lat: number; lon: number } | null;
  pulseT: number | null;
  route: { path: LonLat[]; progress: number } | null;
  routeAed: { lat: number; lon: number } | null;
  nightness: number;
  /** full-height columns: zoomed in, or the night step (otherwise short, thin pins keep the overview readable) */
  tall: boolean;
  /** AEDs whose open state just flipped: ring animation, t = 0..1 */
  flips: { t: number; ringMs: number; items: { lon: number; lat: number; closing: boolean; delayMs: number }[] } | null;
}

export const aedIsOpen = (d: AedPoint, mode: Mode): boolean => (mode === "ours" ? (d.openAfter ?? d.open) === true : d.open === true);

const fade = (c: Color, k: number): Color => [c[0] as number, c[1] as number, c[2] as number, Math.round((c[3] ?? ALPHA_MAX) * k)];

function partialPath(path: LonLat[], progress: number, cum: number[]): LonLat[] {
  const total = cum[cum.length - 1] ?? 0;
  const target = total * Math.min(1, Math.max(0, progress));
  const out: LonLat[] = [path[0] as LonLat];
  for (let i = 1; i < path.length; i++) {
    if ((cum[i] as number) <= target) out.push(path[i] as LonLat);
    else {
      const a = path[i - 1] as LonLat;
      const b = path[i] as LonLat;
      const seg = (cum[i] as number) - (cum[i - 1] as number);
      const f = seg === 0 ? 0 : (target - (cum[i - 1] as number)) / seg;
      out.push([a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]);
      break;
    }
  }
  return out;
}

function cumulative(path: LonLat[]): number[] {
  const cum = [0];
  for (let i = 1; i < path.length; i++) {
    const a = path[i - 1] as LonLat;
    const b = path[i] as LonLat;
    cum.push((cum[i - 1] as number) + Math.hypot(b[0] - a[0], (b[1] - a[1]) * 1.2));
  }
  return cum;
}

export function dynamicLayers(c: DynCtx): Layer[] {
  const out: Layer[] = [];
  const st = c.state;
  const isOpen = (d: AedPoint): boolean => aedIsOpen(d, c.mode);
  if (st !== null) {
    const t = c.modeT;
    out.push(
      new ScatterplotLayer({ id: "silver-current", data: st.silverCurrent, getPosition: (d) => [d.lon, d.lat], getRadius: st.silverRadiusM, radiusUnits: "meters", getFillColor: PALETTE.silverCurrent as Color, stroked: true, getLineColor: [170, 180, 190, 200], lineWidthMinPixels: 1, opacity: 1 - t, visible: t < 0.999, pickable: false }),
      new ScatterplotLayer({ id: "silver-ours", data: st.silverOurs, getPosition: (d) => [d.lon, d.lat], getRadius: st.silverRadiusM, radiusUnits: "meters", getFillColor: PALETTE.silverOurs as Color, stroked: true, getLineColor: [120, 190, 255, 230], lineWidthMinPixels: 1, opacity: t, visible: t > 0.001, pickable: false }),
      new ScatterplotLayer({
        id: "crosswalks", data: st.crosswalks, getPosition: (d) => [d.lon, d.lat, LAYER_Z_LIFT], getRadius: (d) => (d.short ? CROSSWALK_RADIUS_M * 1.5 : CROSSWALK_RADIUS_M), radiusUnits: "meters",
        getFillColor: (d) => (d.short ? PALETTE.crosswalkShort : PALETTE.crosswalkOk) as Color, stroked: true, getLineColor: [255, 255, 255, 200], lineWidthMinPixels: 1, pickable: false,
      }),
      new ScatterplotLayer({
        id: "accidents", data: st.accidents, getPosition: (d) => [d.lon, d.lat, LAYER_Z_LIFT], getRadius: ACCIDENT_RADIUS_M, radiusUnits: "meters",
        getFillColor: (d) => ((c.mode === "ours" ? d.inOurs : d.inCurrent) ? PALETTE.accidentIn : PALETTE.accidentOut) as Color,
        transitions: { getFillColor: TRANSITION_MS }, updateTriggers: { getFillColor: c.mode }, stroked: true, getLineColor: [255, 255, 255, 230], lineWidthMinPixels: 1.5, pickable: false,
      }),
      // AED: screen-space glow disc (readable at overview zoom) + a tall column; open = bright green, closed = dim gray.
      new ScatterplotLayer({
        id: "aed-glow", data: st.aed, getPosition: (d) => [d.lon, d.lat, LAYER_Z_LIFT], getRadius: (d) => (isOpen(d) ? AED_GLOW_OPEN_M : AED_GLOW_CLOSED_M), radiusUnits: "meters", radiusMinPixels: AED_MIN_PX, radiusMaxPixels: AED_MAX_PX,
        getFillColor: (d) => (isOpen(d) ? [61, 220, 151, 120] : [90, 100, 112, 70]) as Color, stroked: true, getLineColor: (d) => (isOpen(d) ? [140, 255, 200, 255] : [110, 120, 132, 160]) as Color, lineWidthMinPixels: 1.5,
        transitions: { getFillColor: TRANSITION_MS, getLineColor: TRANSITION_MS, getRadius: TRANSITION_MS }, updateTriggers: { getFillColor: [st.hour, c.mode], getLineColor: [st.hour, c.mode], getRadius: [st.hour, c.mode] }, pickable: false,
      }),
      new ColumnLayer({
        id: "aed", data: st.aed, getPosition: (d) => [d.lon, d.lat], diskResolution: 10, radius: c.tall ? AED_RADIUS_M : AED_RADIUS_M * 0.55, extruded: true,
        getElevation: (d) => (isOpen(d) ? (c.tall ? AED_OPEN_H : AED_OPEN_H * 0.3) : AED_CLOSED_H),
        getFillColor: (d) => (isOpen(d) ? [90, 255, 175, 255] : [84, 92, 104, 255]) as Color,
        transitions: { getElevation: TRANSITION_MS, getFillColor: TRANSITION_MS }, updateTriggers: { getElevation: [st.hour, c.mode, c.tall], getFillColor: [st.hour, c.mode] },
        material: { ambient: 1, diffuse: 0.2, shininess: 4, specularColor: [20, 20, 20] }, pickable: false,
      }),
      new ScatterplotLayer({
        id: "aed-new", data: st.aedCandidates, getPosition: (d) => [d.lon, d.lat, LAYER_Z_LIFT * 2], getRadius: AED_GLOW_OPEN_M * 1.4, radiusUnits: "meters", radiusMinPixels: AED_MIN_PX + 4, radiusMaxPixels: AED_MAX_PX + 8,
        getFillColor: [76, 194, 255, 150], stroked: true, getLineColor: [200, 240, 255, 255], lineWidthMinPixels: 2.5, opacity: t, visible: t > 0.001, pickable: false,
      }),
      new ColumnLayer({
        id: "aed-new-col", data: st.aedCandidates, getPosition: (d) => [d.lon, d.lat], diskResolution: 12, radius: AED_RADIUS_M * 1.4, extruded: true, getElevation: AED_OPEN_H * 1.8,
        getFillColor: PALETTE.aedNew as Color, opacity: t, visible: t > 0.001, material: { ambient: 1, diffuse: 0.2, shininess: 4, specularColor: [20, 20, 20] }, pickable: false,
      }),
      new ColumnLayer({
        id: "shelters", data: st.shelters, getPosition: (d) => [d.lon, d.lat], diskResolution: 8, radius: AED_RADIUS_M, extruded: true, getElevation: (d) => (d.open === true ? AED_OPEN_H : AED_CLOSED_H),
        getFillColor: (d) => (d.open === true ? PALETTE.shelter : PALETTE.aedClosed) as Color, transitions: { getElevation: TRANSITION_MS, getFillColor: TRANSITION_MS }, updateTriggers: { getElevation: st.hour, getFillColor: st.hour }, pickable: false,
      }),
      new ColumnLayer({
        id: "stops", data: st.stops, getPosition: (d) => [d.lon, d.lat], diskResolution: 6, radius: STOP_RADIUS_M, extruded: true, getElevation: STOP_H, getFillColor: PALETTE.stop as Color,
        opacity: t, visible: t > 0.001, pickable: false,
      }),
    );
  }
  if (c.flips !== null) {
    // screen-space rings (readable at overview zoom): each ring grows from FLIP_MIN_PX to FLIP_MAX_PX over ringMs after its own delay
    const f = c.flips;
    const local = (d: { delayMs: number }): number => Math.min(1, Math.max(0, (f.t - d.delayMs) / f.ringMs));
    const live = f.items.filter((d) => { const l = local(d); return l > 0 && l < 1; });
    out.push(
      new ScatterplotLayer({
        id: "aed-flip-glow", data: live, getPosition: (d) => [d.lon, d.lat, LAYER_Z_LIFT * 3], radiusUnits: "pixels", getRadius: (d) => FLIP_MIN_PX + (FLIP_MAX_PX - FLIP_MIN_PX) * local(d),
        getFillColor: (d) => (d.closing ? [255, 80, 80, Math.round(110 * (1 - local(d)))] : [90, 255, 175, Math.round(110 * (1 - local(d)))]) as Color, stroked: false, filled: true,
        updateTriggers: { getRadius: f.t, getFillColor: f.t }, pickable: false,
      }),
      new ScatterplotLayer({
        id: "aed-flip", data: live, getPosition: (d) => [d.lon, d.lat, LAYER_Z_LIFT * 3], radiusUnits: "pixels", getRadius: (d) => FLIP_MIN_PX + (FLIP_MAX_PX - FLIP_MIN_PX) * local(d), filled: false, stroked: true,
        getLineColor: (d) => (d.closing ? [255, 110, 110, Math.round(ALPHA_MAX * (1 - 0.7 * local(d)))] : [120, 255, 190, Math.round(ALPHA_MAX * (1 - 0.7 * local(d)))]) as Color, lineWidthUnits: "pixels", getLineWidth: 4,
        updateTriggers: { getRadius: f.t, getLineColor: f.t }, pickable: false,
      }),
    );
  }
  if (c.highlight !== null) {
    out.push(new ScatterplotLayer({ id: "highlight", data: [c.highlight], getPosition: (d) => [d.lon, d.lat, LAYER_Z_LIFT], getRadius: PULSE_MAX_M / 2, radiusUnits: "meters", filled: false, stroked: true, getLineColor: [255, 255, 255, 230], lineWidthMinPixels: 2, pickable: false }));
  }
  if (c.camera !== null) {
    out.push(
      new ScatterplotLayer({ id: "camera", data: [c.camera], getPosition: (d) => [d.lon, d.lat, LAYER_Z_LIFT], getRadius: CAMERA_RADIUS_M, radiusUnits: "meters", getFillColor: [255, 255, 255, 255], stroked: true, getLineColor: [76, 194, 255, 255], lineWidthMinPixels: 3, radiusMinPixels: 6, pickable: false }),
      new TextLayer({ id: "camera-label", data: [c.camera], getPosition: (d) => [d.lon, d.lat, LAYER_Z_LIFT], getText: (d) => `▣ ${d.label}`, getSize: 15, getColor: [230, 240, 250, 255], getPixelOffset: [0, -22], background: true, getBackgroundColor: [10, 16, 24, 220], backgroundPadding: [6, 3], fontFamily: "Pretendard Variable, Pretendard, system-ui, sans-serif", characterSet: "auto", billboard: true, pickable: false }),
    );
  }
  if (c.pulseT !== null && c.camera !== null) {
    const rings = Array.from({ length: PULSE_RINGS }, (_, i) => {
      const ph = (c.pulseT as number + i / PULSE_RINGS) % 1;
      return { lon: c.camera?.lon ?? 0, lat: c.camera?.lat ?? 0, r: ph * PULSE_MAX_M, a: Math.round(ALPHA_MAX * (1 - ph)) };
    });
    out.push(new ScatterplotLayer({ id: "fall-pulse", data: rings, getPosition: (d) => [d.lon, d.lat, LAYER_Z_LIFT], getRadius: (d) => d.r, radiusUnits: "meters", filled: false, stroked: true, getLineColor: (d) => fade(PALETTE.fallPulse as Color, d.a / ALPHA_MAX), lineWidthMinPixels: 3, updateTriggers: { getRadius: c.pulseT, getLineColor: c.pulseT }, pickable: false }));
  }
  if (c.route !== null && c.route.path.length > 1) {
    const cum = cumulative(c.route.path);
    const part = partialPath(c.route.path, c.route.progress, cum);
    const tip = part[part.length - 1] as LonLat;
    out.push(
      new PathLayer({ id: "route", data: [part], getPath: (d: LonLat[]) => d as never, getColor: PALETTE.route as Color, getWidth: 7, widthUnits: "meters", widthMinPixels: 4, capRounded: true, jointRounded: true, pickable: false, updateTriggers: { getPath: c.route.progress } }),
      new ScatterplotLayer({ id: "walker", data: [tip], getPosition: (d: LonLat) => [d[0], d[1], LAYER_Z_LIFT], getRadius: 14, radiusUnits: "meters", radiusMinPixels: 7, getFillColor: [255, 255, 255, 255], stroked: true, getLineColor: PALETTE.route as Color, lineWidthMinPixels: 3, pickable: false, updateTriggers: { getPosition: c.route.progress } }),
    );
    if (c.routeAed !== null) out.push(new ScatterplotLayer({ id: "route-aed", data: [c.routeAed], getPosition: (d) => [d.lon, d.lat, LAYER_Z_LIFT], getRadius: 38, radiusUnits: "meters", filled: false, stroked: true, getLineColor: PALETTE.route as Color, lineWidthMinPixels: 3, pickable: false }));
  }
  return out;
}
