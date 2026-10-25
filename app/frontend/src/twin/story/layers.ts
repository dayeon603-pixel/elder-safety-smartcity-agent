/** deck.gl layers of the 시민 시점 and of the ambient 생활인구 movement. All in the scene's metre frame. */
import { COORDINATE_SYSTEM } from "@deck.gl/core";
import type { Color, Layer } from "@deck.gl/core";
import { TripsLayer } from "@deck.gl/geo-layers";
import { ScenegraphLayer } from "@deck.gl/mesh-layers";
import { ColumnLayer, PathLayer, ScatterplotLayer } from "@deck.gl/layers";
import { MODEL_URL, PASSERBY_TINT, PERSON, PULSE_COLOR, PULSE_MAX_M, PULSE_RINGS, ROUTE_COLOR } from "./constants";
import type { XY } from "./geo";
import type { TripBuffers } from "./sampler";

export const metreFrame = (center: readonly [number, number]) => ({ coordinateSystem: COORDINATE_SYSTEM.METER_OFFSETS, coordinateOrigin: [center[0], center[1], 0] as [number, number, number] });

export function tripsLayer(center: readonly [number, number], b: TripBuffers, currentTime: number, opacity: number, trail: number, widthM: number): Layer {
  return new TripsLayer({
    id: "story-trips",
    ...metreFrame(center),
    data: { length: b.count, startIndices: b.startIndices, attributes: { getPath: { value: b.positions, size: 2 }, getTimestamps: { value: b.timestamps, size: 1 }, getColor: { value: b.colors, size: 4, normalized: true } } } as never,
    _pathType: "open",
    currentTime,
    trailLength: trail,
    fadeTrail: true,
    widthUnits: "meters",
    getWidth: widthM,
    widthMinPixels: 1.6,
    capRounded: true,
    jointRounded: true,
    opacity,
    pickable: false,
    parameters: { depthCompare: "always", blend: true } as never,
  } as never);
}

export interface Actor { id: string; xy: XY; yawDeg: number; lieDeg: number; z: number; anim: number; tint: Color }

/** CesiumMan from glTF-Sample-Assets (CC-BY 4.0). Orientation: [pitch, yaw, roll] degrees. */
export function actorLayer(center: readonly [number, number], a: Actor, sizeScale: number): Layer {
  return new ScenegraphLayer<Actor>({
    id: `story-actor-${a.id}`,
    ...metreFrame(center),
    data: [a],
    scenegraph: MODEL_URL,
    getPosition: (d: Actor) => [d.xy[0], d.xy[1], d.z],
    getOrientation: (d: Actor) => [d.lieDeg, d.yawDeg, 90],
    getColor: (d: Actor) => d.tint,
    sizeScale,
    _animations: { "*": { speed: a.anim } },
    _lighting: "pbr",
    updateTriggers: { getPosition: [a.xy[0], a.xy[1], a.z], getOrientation: [a.yawDeg, a.lieDeg], getColor: [a.tint[0], a.tint[1], a.tint[2]] },
    pickable: false,
  } as never);
}

/** the route and pulse float this far above the ground so they depth-test against buildings without z-fighting the ground */
const ROUTE_LIFT_M = 0.3;
export const PERSON_SIZE = PERSON.sizeM;
export const PASSERBY_COLOR: Color = PASSERBY_TINT;

export function routeLayer(center: readonly [number, number], path: readonly XY[], visibleM: number): Layer[] {
  const part: XY[] = [];
  let acc = 0;
  for (let i = 0; i < path.length; i++) {
    const p = path[i] as XY;
    if (i > 0) {
      const q = path[i - 1] as XY;
      const seg = Math.hypot(p[0] - q[0], p[1] - q[1]);
      if (acc + seg > visibleM) { const f = seg === 0 ? 0 : (visibleM - acc) / seg; part.push([q[0] + (p[0] - q[0]) * f, q[1] + (p[1] - q[1]) * f]); break; }
      acc += seg;
    }
    part.push(p);
  }
  return [new PathLayer<XY[]>({ id: "story-route", ...metreFrame(center), data: [part.map((p): [number, number, number] => [p[0], p[1], ROUTE_LIFT_M]) as never], getPath: (d: unknown) => d as never, getColor: ROUTE_COLOR, getWidth: 0.45, widthUnits: "meters", widthMinPixels: 3, capRounded: true, jointRounded: true, pickable: false, updateTriggers: { getPath: visibleM } })];
}

export function pulseLayer(center: readonly [number, number], xy: XY, t01: number): Layer {
  const rings = Array.from({ length: PULSE_RINGS }, (_, i) => { const ph = (t01 + i / PULSE_RINGS) % 1; return { r: 0.6 + ph * PULSE_MAX_M, a: Math.round(255 * (1 - ph)) }; });
  return new ScatterplotLayer({ id: "story-pulse", ...metreFrame(center), data: rings, getPosition: [xy[0], xy[1], ROUTE_LIFT_M + 0.1], getRadius: (d: { r: number }) => d.r, radiusUnits: "meters", filled: false, stroked: true, getLineColor: (d: { a: number }) => [PULSE_COLOR[0] as number, PULSE_COLOR[1] as number, PULSE_COLOR[2] as number, d.a] as Color, lineWidthMinPixels: 2, updateTriggers: { getRadius: t01, getLineColor: t01, getPosition: [xy[0], xy[1]] }, pickable: false });
}

export interface Pole { id: string; xy: XY; heightM: number; color: Color; radiusM: number }
/** Posts for the signal head and the stop terminal (the panels themselves are HTML anchored to the post top). */
export function poleLayers(center: readonly [number, number], poles: readonly Pole[]): Layer[] {
  if (poles.length === 0) return [];
  return [
    new ColumnLayer<Pole>({ id: "story-poles", ...metreFrame(center), data: poles as Pole[], getPosition: (d) => d.xy as never, diskResolution: 8, radius: 0.18, extruded: true, getElevation: (d) => d.heightM, getFillColor: [150, 160, 172, 255], material: { ambient: 0.8, diffuse: 0.4, shininess: 8, specularColor: [30, 30, 30] } as never, pickable: false }),
    new ScatterplotLayer<Pole>({ id: "story-pole-lamp", ...metreFrame(center), data: poles as Pole[], getPosition: (d) => [d.xy[0], d.xy[1], d.heightM] as never, getRadius: (d) => d.radiusM, radiusUnits: "meters", getFillColor: (d) => d.color, stroked: false, pickable: false, updateTriggers: { getFillColor: poles.map((p) => p.color.join(",")).join("|") } }),
  ];
}

export function ringLayer(center: readonly [number, number], id: string, xy: XY, radiusM: number, color: Color): Layer {
  return new ScatterplotLayer({ id, ...metreFrame(center), data: [xy], getPosition: (d: XY) => [d[0], d[1], 0.25], getRadius: radiusM, radiusUnits: "meters", filled: false, stroked: true, getLineColor: color, lineWidthMinPixels: 2.5, pickable: false });
}
