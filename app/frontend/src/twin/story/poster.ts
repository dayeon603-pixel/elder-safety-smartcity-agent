/** Poster mode layers: the AI-run infrastructure in one frame (arcs from 관제 to the BIT stops, glowing stop beacons, AED rings, control-centre halo). Real positions only. */
import { COORDINATE_SYSTEM } from "@deck.gl/core";
import type { Color, Layer } from "@deck.gl/core";
import { ArcLayer, ScatterplotLayer } from "@deck.gl/layers";
import type { Frame, XY } from "./geo";
import { toXY } from "./geo";

export interface PosterInfra { control: { lon: number; lat: number } | null; stops: { lon: number; lat: number }[]; aeds: { lon: number; lat: number }[] }
export interface PosterCfg { x: number; y: number; zoom: number; pitch: number; bearing: number; /** scale of the figure, posts and anchors */ k: number; /** width of the people-dot trails in metres */ tw: number; /** follow the story's stop: centre on it, look along the street the person walked (x, y are then ignored) */ follow?: boolean }

const ALWAYS = { depthCompare: "always" } as never;
const ARC_FROM: Color = [90, 200, 255, 230];
const ARC_TO: Color = [255, 214, 130, 235];
const AMBER: Color = [255, 200, 87, 255];
const GREEN: Color = [90, 255, 175, 255];
const CONTROL_HALO_M = 160;
const STOP_RING_M = 40;
const AED_RING_M = 90;
const MAX_AEDS = 36;
const ARC_WIDTH_PX = 2;
/** poster: draw every 2nd stop's arc (~50% fewer) so the people read */
const ARC_KEEP_EVERY = 2;
const ARC_HEIGHT = 0.55;

const frame = (c: readonly [number, number]) => ({ coordinateSystem: COORDINATE_SYSTEM.METER_OFFSETS, coordinateOrigin: [c[0], c[1], 0] as [number, number, number] });

export function posterLayers(center: readonly [number, number], f: Frame, infra: PosterInfra, t: number): Layer[] {
  const m = frame(center);
  const ctl: XY | null = infra.control === null ? null : toXY(f, infra.control.lon, infra.control.lat);
  const arcStops = infra.stops.filter((_, i) => i % ARC_KEEP_EVERY === 0).map((s) => toXY(f, s.lon, s.lat));
  const stops = infra.stops.map((s) => toXY(f, s.lon, s.lat));
  const step = Math.max(1, Math.floor(infra.aeds.length / MAX_AEDS));
  const aeds = infra.aeds.filter((_, i) => i % step === 0).map((a) => toXY(f, a.lon, a.lat));
  const pulse = 0.5 + 0.5 * Math.sin(t / 700);
  const out: Layer[] = [];
  if (ctl !== null) {
    out.push(
      new ArcLayer<XY>({ id: "poster-arcs", ...m, data: arcStops, getSourcePosition: () => [ctl[0], ctl[1], 20], getTargetPosition: (d) => [d[0], d[1], 20], getSourceColor: ARC_FROM, getTargetColor: ARC_TO, getWidth: ARC_WIDTH_PX, widthUnits: "pixels", getHeight: ARC_HEIGHT, parameters: ALWAYS, pickable: false }),
      new ScatterplotLayer<XY>({ id: "poster-control", ...m, data: [ctl], getPosition: (d) => [d[0], d[1], 25], radiusUnits: "meters", getRadius: CONTROL_HALO_M * (0.85 + 0.3 * pulse), getFillColor: [90, 200, 255, 70], stroked: true, getLineColor: [160, 225, 255, 255], lineWidthMinPixels: 3, parameters: ALWAYS, updateTriggers: { getRadius: pulse }, pickable: false }),
    );
  }
  out.push(
    new ScatterplotLayer<XY>({ id: "poster-stops", ...m, data: stops, getPosition: (d) => [d[0], d[1], 25], radiusUnits: "meters", getRadius: STOP_RING_M, getFillColor: [255, 200, 87, 120], stroked: true, getLineColor: AMBER, lineWidthMinPixels: 1.6, parameters: ALWAYS, pickable: false }),
    new ScatterplotLayer<XY>({ id: "poster-aeds", ...m, data: aeds, getPosition: (d) => [d[0], d[1], 12], radiusUnits: "meters", getRadius: AED_RING_M, filled: false, stroked: true, getLineColor: GREEN, lineWidthMinPixels: 2.4, parameters: ALWAYS, pickable: false }),
  );
  return out;
}
