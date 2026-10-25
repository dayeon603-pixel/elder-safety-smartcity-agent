/**
 * City infrastructure shown in Zone 1 and the "상황" playback that operates it. Device positions come from
 * GET /twin/{sgg}/infra when it is live and otherwise from the twin state (crosswalks, recommended stops); nothing is invented.
 */
import { ArcLayer, IconLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import type { Color, Layer } from "@deck.gl/core";
import { arr, getJsonPublic, num, rec, str } from "./api";
import { PALETTE } from "./constants";
import { haversineM } from "./api";
import type { TwinState } from "./types";

export type InfraKind = "cctv" | "signal" | "stop" | "aed";
export interface InfraPoint { id: string; lat: number; lon: number; label: string | null; bit?: boolean }
export interface ControlCenter { lat: number; lon: number; label: string }
export interface Infra {
  cctv: InfraPoint[] | null;
  signal: InfraPoint[];
  stop: InfraPoint[];
  aed: InfraPoint[];
  control: ControlCenter | null;
  /** counts as the API states them (null = unknown, omitted from the legend) */
  counts: Record<InfraKind, number | null>;
  /** 정류장 중 안내단말 연계 후보 수 */
  bitCandidates: number | null;
}

const POINT_KEYS: Readonly<Record<InfraKind, readonly string[]>> = { cctv: ["cctv"], signal: ["signal_crosswalk", "signal"], stop: ["bus_stop", "stop"], aed: ["aed"] };

function pts(v: unknown): { items: InfraPoint[]; count: number | null } | null {
  const o = rec(v);
  const list = Array.isArray(v) ? v : arr(o?.items ?? o?.points);
  if (!Array.isArray(v) && o === null) return null;
  const items = list.flatMap((x, i): InfraPoint[] => {
    const p = rec(x);
    const lat = num(p?.lat);
    const lon = num(p?.lon);
    return p === null || lat === null || lon === null ? [] : [{ id: str(p.id) ?? str(p.stop_id) ?? String(i), lat, lon, label: str(p.name) ?? str(p.label), bit: p.recommended_bit === true || p.recommended === true }];
  });
  const count = num(o?.count) ?? (items.length > 0 ? items.length : null);
  return { items, count: count === 0 ? null : count };
}

/** GET /twin/{sgg}/infra: `devices` (typed rows), `counts`, `control_center`. */
export async function loadInfra(code: string): Promise<Infra | null> {
  const raw = rec(await getJsonPublic(`/twin/${code}/infra`));
  if (raw === null) return null;
  const out: Infra = { cctv: null, signal: [], stop: [], aed: [], control: null, counts: { cctv: null, signal: null, stop: null, aed: null }, bitCandidates: null };
  const TYPE_KIND: Readonly<Record<string, InfraKind>> = { cctv: "cctv", signal_crosswalk: "signal", bus_stop: "stop", aed: "aed" };
  const devices = arr(raw.devices);
  for (const x of devices) {
    const d = rec(x);
    const kind = TYPE_KIND[str(d?.type) ?? ""];
    const lat = num(d?.lat);
    const lon = num(d?.lon);
    if (d === null || kind === undefined || lat === null || lon === null) continue;
    const item: InfraPoint = { id: str(d.id) ?? "", lat, lon, label: str(d.name), bit: d.recommended_bit === true };
    if (kind === "cctv") (out.cctv ??= []).push(item); else out[kind].push(item);
  }
  const counts = rec(raw.counts);
  const nz = (k: string): number | null => num(counts?.[k]);
  out.counts = { cctv: nz("cctv"), signal: nz("signal_crosswalk"), stop: nz("bus_stop"), aed: nz("aed") };
  out.bitCandidates = nz("bus_stop_recommended_bit");
  // legacy shape (per-type lists) kept working for older backends
  if (devices.length === 0) {
    for (const kind of Object.keys(POINT_KEYS) as InfraKind[]) {
      const key = POINT_KEYS[kind].find((k) => raw[k] !== undefined);
      const p = key === undefined ? null : pts(raw[key]);
      if (p === null) continue;
      out.counts[kind] = p.count;
      if (kind === "cctv") out.cctv = p.items; else out[kind] = p.items;
    }
  }
  const c = rec(raw.control_center);
  const lat = num(c?.lat);
  const lon = num(c?.lon);
  if (c !== null && lat !== null && lon !== null) out.control = { lat, lon, label: str(c.name) ?? str(c.label) ?? "관제(시연 위치)" };
  return out;
}

/** Infra for display: the API's lists where present, else the twin state's real crosswalks / recommended stops / AEDs. */
export function infraFromState(api: Infra | null, st: TwinState | null): Infra {
  const base: Infra = api ?? { cctv: null, signal: [], stop: [], aed: [], control: null, counts: { cctv: null, signal: null, stop: null, aed: null }, bitCandidates: null };
  const signal = base.signal.length > 0 ? base.signal : (st?.crosswalks ?? []).map((c) => ({ id: c.id, lat: c.lat, lon: c.lon, label: c.address }));
  const stop = base.stop.length > 0 ? base.stop : (st?.stops ?? []).map((s) => ({ id: s.id, lat: s.lat, lon: s.lon, label: s.name, bit: true }));
  const aed = base.aed.length > 0 ? base.aed : (st?.aed ?? []).map((a) => ({ id: a.id, lat: a.lat, lon: a.lon, label: a.name }));
  return {
    ...base, signal, stop, aed,
    counts: { cctv: base.counts.cctv ?? (base.cctv === null ? null : base.cctv.length), signal: base.counts.signal ?? (signal.length > 0 ? signal.length : null), stop: base.counts.stop ?? (stop.length > 0 ? stop.length : null), aed: base.counts.aed ?? (aed.length > 0 ? aed.length : null) },
  };
}

export const INFRA_LABEL: Readonly<Record<InfraKind, string>> = { cctv: "CCTV", signal: "신호 횡단보도", stop: "정류장", aed: "AED" };

// ---------- icon atlas (drawn once on a canvas: camera, traffic light, bus-stop sign, control tower) ----------

const CELL = 64;
const ICON_ORDER = ["cctv", "signal", "stop", "control"] as const;
type IconName = (typeof ICON_ORDER)[number];
let atlas: string | null = null;

function drawAtlas(): string {
  if (atlas !== null) return atlas;
  const c = document.createElement("canvas");
  c.width = CELL * ICON_ORDER.length;
  c.height = CELL;
  const g = c.getContext("2d");
  if (g === null) return (atlas = "");
  const plate = (i: number, color: string): void => { g.fillStyle = "#0b1118"; g.strokeStyle = color; g.lineWidth = 4; g.beginPath(); g.arc(i * CELL + CELL / 2, CELL / 2, CELL / 2 - 3, 0, Math.PI * 2); g.fill(); g.stroke(); };
  // cctv: camera body + lens
  plate(0, "#ffffff"); g.fillStyle = "#ffffff"; g.fillRect(14, 24, 28, 16); g.beginPath(); g.moveTo(42, 28); g.lineTo(52, 22); g.lineTo(52, 42); g.lineTo(42, 36); g.fill(); g.fillStyle = "#0b1118"; g.beginPath(); g.arc(26, 32, 4, 0, Math.PI * 2); g.fill();
  // signal: traffic light
  plate(1, "#ffd479"); const ox = CELL; g.fillStyle = "#ffd479"; g.fillRect(ox + 23, 12, 18, 40); (["#ff5a5a", "#ffd479", "#3ddc97"] as const).forEach((col, k) => { g.fillStyle = col; g.beginPath(); g.arc(ox + 32, 20 + k * 12, 4.5, 0, Math.PI * 2); g.fill(); });
  // bus stop: sign on a pole
  plate(2, "#ffb040"); const sx = CELL * 2; g.fillStyle = "#ffb040"; g.fillRect(sx + 30, 22, 4, 28); g.fillRect(sx + 16, 12, 32, 16); g.fillStyle = "#0b1118"; g.font = "bold 13px sans-serif"; g.textAlign = "center"; g.fillText("BUS", sx + 32, 25);
  // control: tower
  plate(3, "#4cc2ff"); const cx = CELL * 3; g.fillStyle = "#4cc2ff"; g.fillRect(cx + 28, 22, 8, 26); g.beginPath(); g.moveTo(cx + 14, 22); g.lineTo(cx + 50, 22); g.lineTo(cx + 32, 10); g.fill();
  return (atlas = c.toDataURL("image/png"));
}

const iconMapping = Object.fromEntries(ICON_ORDER.map((n, i) => [n, { x: i * CELL, y: 0, width: CELL, height: CELL, mask: false }]));
const ICON_PX = 28;
const CONTROL_PX = 36;
const LABEL_FONT = "Pretendard Variable, Pretendard, system-ui, sans-serif";

export interface InfraCtx {
  infra: Infra;
  /** true while a story street view owns the camera: infra icons stay out of it */
  hidden: boolean;
}

export function infraLayers(c: InfraCtx): Layer[] {
  if (c.hidden) return [];
  const url = drawAtlas();
  if (url === "") return [];
  const items: { lon: number; lat: number; icon: IconName; size: number }[] = [
    ...(c.infra.cctv ?? []).map((p) => ({ lon: p.lon, lat: p.lat, icon: "cctv" as const, size: ICON_PX })),
    ...c.infra.signal.map((p) => ({ lon: p.lon, lat: p.lat, icon: "signal" as const, size: ICON_PX })),
    ...c.infra.stop.filter((p) => p.bit !== false).map((p) => ({ lon: p.lon, lat: p.lat, icon: "stop" as const, size: ICON_PX - 6 })),
    ...(c.infra.control === null ? [] : [{ lon: c.infra.control.lon, lat: c.infra.control.lat, icon: "control" as const, size: CONTROL_PX }]),
  ];
  const out: Layer[] = ICON_ORDER.map((icon) => new IconLayer({
    id: `infra-${icon}`, data: items.filter((d) => d.icon === icon), iconAtlas: url, iconMapping, getIcon: (d) => d.icon, getPosition: (d) => [d.lon, d.lat, 30], getSize: (d) => d.size,
    sizeUnits: "pixels", sizeMinPixels: 14, billboard: true, pickable: false, alphaCutoff: 0.05,
  }));
  if (c.infra.control !== null) {
    out.push(new TextLayer({ id: "infra-control-label", data: [c.infra.control], getPosition: (d) => [d.lon, d.lat, 30], getText: (d) => d.label, getSize: 13, getColor: [230, 240, 250, 255], getPixelOffset: [0, 30], background: true, getBackgroundColor: [10, 16, 24, 230], backgroundPadding: [5, 2], fontFamily: LABEL_FONT, characterSet: "auto", pickable: false }));
  }
  return out;
}

// ---------- 상황 playback ----------

export type PlaybackKind = "broadcast" | "silverInfra" | "fallAlert" | "fallConfirmed" | "nightTargets";
export interface PlaybackEvent { kind: PlaybackKind; start: number; ms: number; points?: { lon: number; lat: number }[]; at?: { lon: number; lat: number } }
export const PLAYBACK_MS: Readonly<Record<PlaybackKind, number>> = { broadcast: 7000, silverInfra: 5000, fallAlert: 5000, fallConfirmed: 7000, nightTargets: 9000 };
const ARC_TRAVEL_MS = 1200;
const RIPPLE_MS = 2600;
const RIPPLE_MAX_M = 7000;

const clamp01 = (x: number): number => Math.max(0, Math.min(1, x));
const mid = (a: { lon: number; lat: number }, b: { lon: number; lat: number }, t: number): [number, number] => [a.lon + (b.lon - a.lon) * t, a.lat + (b.lat - a.lat) * t];

const label = (id: string, data: { lon: number; lat: number; text: string }[], color: Color, dy = -26): Layer =>
  new TextLayer({ id, data, getPosition: (d) => [d.lon, d.lat, 40], getText: (d) => d.text, getSize: 14, getColor: color, getPixelOffset: [0, dy], background: true, getBackgroundColor: [10, 16, 24, 235], backgroundPadding: [6, 3], fontFamily: LABEL_FONT, characterSet: "auto", billboard: true, pickable: false });

/** Layers of the active playback events at time `now`. */
export function playbackLayers(events: PlaybackEvent[], control: ControlCenter | null, now: number): Layer[] {
  const out: Layer[] = [];
  for (const ev of events) {
    const t = now - ev.start;
    if (t < 0 || t > ev.ms) continue;
    if (ev.kind === "broadcast" && control !== null) {
      const stops = ev.points ?? [];
      const f = clamp01(t / ARC_TRAVEL_MS);
      out.push(
        new ArcLayer({ id: "pb-arcs", data: stops, getSourcePosition: () => [control.lon, control.lat], getTargetPosition: (d) => mid(control, d, f), getSourceColor: [76, 194, 255, 255], getTargetColor: [160, 225, 255, 255], getWidth: 3, widthUnits: "pixels", updateTriggers: { getTargetPosition: f }, pickable: false }),
        new ScatterplotLayer({ id: "pb-stop-glow", data: f >= 1 ? stops : [], getPosition: (d) => [d.lon, d.lat, 35], radiusUnits: "pixels", getRadius: 16 + 6 * Math.sin(t / 220), filled: true, getFillColor: [255, 200, 87, 90], stroked: true, getLineColor: [255, 200, 87, 255], lineWidthMinPixels: 2, updateTriggers: { getRadius: t }, pickable: false }),
        label("pb-stop-label", f >= 1 ? stops.slice(0, 6).map((d) => ({ ...d, text: "안내 표출(연계 제안)" })) : [], [255, 214, 130, 255]),
      );
      const rt = t / RIPPLE_MS;
      if (rt <= 1) {
        out.push(
          new ScatterplotLayer({ id: "pb-ripple", data: [control], getPosition: (d) => [d.lon, d.lat, 20], radiusUnits: "meters", getRadius: RIPPLE_MAX_M * rt, filled: false, stroked: true, getLineColor: [76, 194, 255, Math.round(230 * (1 - rt))], lineWidthUnits: "pixels", getLineWidth: 4, updateTriggers: { getRadius: t, getLineColor: t }, pickable: false }),
          label("pb-ripple-label", [{ ...control, text: "재난문자 송출 요청(훈련)" }], [160, 225, 255, 255], 56),
        );
      }
    } else if (ev.kind === "silverInfra") {
      const pts = ev.points ?? [];
      out.push(new ScatterplotLayer({ id: "pb-silver-hl", data: pts, getPosition: (d) => [d.lon, d.lat, 35], radiusUnits: "pixels", getRadius: 20 + 8 * Math.sin(t / 200), filled: false, stroked: true, getLineColor: [76, 160, 255, 255], lineWidthMinPixels: 3, updateTriggers: { getRadius: t }, pickable: false }));
    } else if ((ev.kind === "fallAlert" || ev.kind === "fallConfirmed") && control !== null && ev.at !== undefined) {
      const f = clamp01(t / ARC_TRAVEL_MS);
      const from = ev.kind === "fallAlert" ? ev.at : control;
      const to = ev.kind === "fallAlert" ? control : { lon: control.lon, lat: control.lat + 0.0035 };
      out.push(
        new ArcLayer({ id: `pb-${ev.kind}-arc`, data: [0], getSourcePosition: () => [from.lon, from.lat], getTargetPosition: () => mid(from, to, f), getSourceColor: ev.kind === "fallAlert" ? [255, 90, 90, 255] : [61, 220, 151, 255], getTargetColor: [255, 255, 255, 255], getWidth: 4, widthUnits: "pixels", updateTriggers: { getTargetPosition: f }, pickable: false }),
        label(`pb-${ev.kind}-label`, f >= 1 ? [{ ...(ev.kind === "fallAlert" ? control : to), text: ev.kind === "fallAlert" ? "관제요원 확인" : "119 연계(훈련)" }] : [], ev.kind === "fallAlert" ? [255, 160, 160, 255] : [140, 255, 200, 255], ev.kind === "fallAlert" ? -26 : -10),
      );
    } else if (ev.kind === "nightTargets") {
      const pts = ev.points ?? [];
      out.push(
        new ScatterplotLayer({ id: "pb-night-ring", data: pts, getPosition: (d) => [d.lon, d.lat, 120], radiusUnits: "pixels", getRadius: 22 + 6 * Math.sin(t / 250), filled: false, stroked: true, getLineColor: [76, 194, 255, 255], lineWidthMinPixels: 3, updateTriggers: { getRadius: t }, pickable: false }),
        label("pb-night-label", t > 600 ? pts.map((d) => ({ ...d, text: "개방 요청 대상" })) : [], [160, 225, 255, 255], -34),
      );
    }
  }
  return out;
}

/** Signals / CCTV inside any of the new zones (the "구역 안 인프라 n" of step 4). */
export function infraInsideZones(infra: Infra, zones: { lat: number; lon: number }[], radiusM: number): { lon: number; lat: number }[] {
  const all = [...(infra.cctv ?? []), ...infra.signal];
  return all.filter((p) => zones.some((z) => haversineM([p.lon, p.lat], [z.lon, z.lat]) <= radiusM)).map((p) => ({ lon: p.lon, lat: p.lat }));
}
export const INFRA_COLORS = PALETTE;
