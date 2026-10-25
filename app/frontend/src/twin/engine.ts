/**
 * Imperative 3D stage around a deck.gl Deck instance. React drives it through a handful of setters; all per-frame
 * animation (mode morph, hour lighting, fall pulse, route walk, rain, fly-to) lives here so React never re-renders
 * at frame rate. Static building buffers are uploaded once per scene.
 */
import { AmbientLight, Deck, DirectionalLight, FlyToInterpolator, LightingEffect, MapView, WebMercatorViewport } from "@deck.gl/core";
import type { Viewport } from "@deck.gl/core";
import type { Layer } from "@deck.gl/core";
import { CAMERA, RENDER, PALETTE } from "./constants";
import { aedIsOpen, boundaryLayers, buildingLayer, dynamicLayers, roadLayer } from "./layers";
import type { DynCtx } from "./layers";
import { PLAYBACK_MS, infraLayers, playbackLayers } from "./infra";
import type { Infra, PlaybackEvent, PlaybackKind } from "./infra";
import { BasemapController } from "./basemap/controller"; // BASEMAP hook (offline map under the buildings)
import { lightingFor, sunPosition } from "./sun";
import type { Lighting } from "./sun";
import type { LonLat, Mode, SceneBuffers, TwinState } from "./types";

const MODE_MORPH_MS = 900;
const FRAME_WINDOW = 180;
const PULSE_PERIOD_MS = 2400;
/** Whole flip animation: stagger (up to FLIP_STAGGER_MS) + one ring of FLIP_RING_MS. */
const STREET_WARM: [number, number, number] = [255, 238, 214];
const STREET_AMBIENT_GAIN = 0.8;
/** AED columns reach full height once the camera is this many zoom levels closer than the whole-구 fit. */
const TALL_ZOOM_OVER_FIT = 1.4;
const FLIP_STAGGER_MS = 900;
const FLIP_RING_MS = 1500;
const FLIP_MS = FLIP_STAGGER_MS + FLIP_RING_MS + 100;
const MS_PER_S = 1000;
const RAIN_MAX_STREAKS = 700;
const RAIN_LEN_PX = 22;
const RAIN_SPEED_PX = 18;
const RAIN_SLANT = 0.22;
const ORBIT_DEG_PER_S = 12;

export type CameraPreset = "overview" | "night" | "rain" | "fall";
export interface CamPlace { lat: number; lon: number; label: string }
export interface StageStats { fps: number; frameMsP50: number; frameMsP95: number; frames: number; dpr: number; buildings: number; vertices: number }

export interface ViewState { longitude: number; latitude: number; zoom: number; pitch: number; bearing: number }

/** Which theme-layer checkbox a deck layer id belongs to. */
function groupOf(id: string): string {
  if (id.startsWith("aed")) return "aed";
  if (id === "stops" || id === "infra-stop") return "stop";
  if (id === "crosswalks" || id === "infra-signal") return "signal";
  if (id.startsWith("silver")) return "silver";
  if (id === "accidents") return "accident";
  if (id === "infra-cctv") return "cctv";
  return id;
}

export class TwinEngine {
  private deck: Deck<MapView>;
  private rain: HTMLCanvasElement;
  private parent: HTMLDivElement;
  private scene: SceneBuffers | null = null;
  private staticLayers: Layer[] = [];
  private state: TwinState | null = null;
  private mode: Mode = "asis";
  private modeT = 0;
  private modeFrom = 0;
  private modeStart = 0;
  private hour = 12;
  private date = "";
  private light: Lighting | null = null;
  private rain01 = 0;
  private cam: CamPlace | null = null;
  private highlight: { lat: number; lon: number } | null = null;
  private pulseStart: number | null = null;
  private route: { path: LonLat[]; start: number; ms: number; done?: () => void; aed: { lat: number; lon: number } | null } | null = null;
  private dirty = true;
  private raf = 0;
  private alive = true;
  private ambient = new AmbientLight({ color: [255, 255, 255], intensity: 1 });
  private sun = new DirectionalLight({ color: [255, 255, 255], intensity: 1, direction: [-1, -1, -1] });
  private effect = new LightingEffect({ ambient: this.ambient, sun: this.sun });
  private frames: number[] = [];
  private streaks: { x: number; y: number; s: number }[] = [];
  private fitZoom = 0;
  private openById = new Map<string, boolean>();
  private flips: { start: number; items: { lon: number; lat: number; closing: boolean; delayMs: number }[] } | null = null;
  private lastVs: ViewState | null = null;
  private extra: Layer[] = [];
  private extraAnimating = false;
  private storyLock = false;
  private basemap = new BasemapController();
  /** street-level story: control-room overlays (silver discs, AED beacons ...) are hidden */
  private storyStreet = false;
  private lastTall = false;
  private infra: Infra | null = null;
  private hiddenGroups: ReadonlySet<string> = new Set();
  private events: PlaybackEvent[] = [];
  private lockReleasedAt = 0;
  private lastFlyAt = 0;
  private pendingPreset: { preset: CameraPreset; ms: number } | null = null;
  private lastLat = 0;
  private lastLon = 0;
  private orbitUntil = 0;
  private orbitFrom = 0;
  private dpr: number;

  constructor(parent: HTMLDivElement, rainCanvas: HTMLCanvasElement) {
    this.parent = parent;
    this.rain = rainCanvas;
    this.dpr = Math.min(window.devicePixelRatio || 1, RENDER.dprCap);
    this.deck = new Deck({
      parent,
      views: new MapView({ repeat: false }),
      initialViewState: { longitude: 126.94, latitude: 37.47, zoom: 11, pitch: CAMERA.overview.pitch, bearing: CAMERA.overview.bearing },
      controller: true,
      layers: [],
      effects: [this.effect],
      useDevicePixels: this.dpr,
      parameters: { clearColor: [0, 0, 0, 0] } as never,
      style: { position: "absolute", inset: "0" },
      onAfterRender: () => this.onFrame(),
    });
    this.raf = requestAnimationFrame(this.tick);
    this.basemap.load(null, () => this.rebuildStatic()); // BASEMAP hook
  }

  private onFrame(): void {
    const t = performance.now();
    this.frames.push(t);
    if (this.frames.length > FRAME_WINDOW) this.frames.shift();
  }

  stats(): StageStats {
    const f = this.frames;
    const dts = f.slice(1).map((t, i) => t - (f[i] as number)).sort((a, b) => a - b);
    const mean = dts.length === 0 ? 0 : dts.reduce((a, b) => a + b, 0) / dts.length;
    const q = (p: number): number => (dts.length === 0 ? 0 : (dts[Math.min(dts.length - 1, Math.floor(p * dts.length))] as number));
    return { fps: mean === 0 ? 0 : MS_PER_S / mean, frameMsP50: q(0.5), frameMsP95: q(0.95), frames: f.length, dpr: this.dpr, buildings: this.scene?.buildingCount ?? 0, vertices: this.scene?.vertexCount ?? 0 };
  }

  setScene(s: SceneBuffers): void {
    this.scene = s;
    this.rebuildStatic();
    this.basemap.probe(s.center); // BASEMAP hook: satellite or OSM ground
    const w = this.parent.clientWidth;
    const h = this.parent.clientHeight;
    const vp = new WebMercatorViewport({ width: w, height: h });
    const [minX, minY, maxX, maxY] = s.bbox;
    this.fitZoom = vp.fitBounds([[minX, minY], [maxX, maxY]], { padding: CAMERA.fitPadding }).zoom;
    this.flyTo("overview", 0);
  }

  private rebuildStatic(): void {
    const s = this.scene;
    if (s === null) return;
    this.staticLayers = [...boundaryLayers(s, this.groundColor()), ...(this.basemap.ready ? [] : [roadLayer(s)]), buildingLayer(s, this.storyStreet)];
    this.dirty = true;
  }

  /** BASEMAP hook: offline map layers (under the buildings) and landmark labels (over them). */
  private basemapLayers(): ReturnType<BasemapController["layers"]> {
    const s = this.scene;
    if (s === null) return { under: [], over: [] };
    const st = this.state;
    const heat = (st === null ? null : this.mode === "ours" ? st.heat.oursAlert : st.heat.b1Alert) === true;
    return this.basemap.layers({ center: s.center, day: this.light?.day ?? 1, heat, boundary: s.boundaryPositions, street: this.storyStreet });
  }

  private groundColor(): [number, number, number, number] {
    const st = this.state;
    const alert = st === null ? null : this.mode === "ours" ? st.heat.oursAlert : st.heat.b1Alert;
    const base = (alert === true ? PALETTE.groundHeat : PALETTE.groundCalm) as [number, number, number, number];
    const k = this.light === null ? 1 : 0.55 + 0.45 * this.light.day;
    return [Math.round(base[0] * k), Math.round(base[1] * k), Math.round(base[2] * k), 255];
  }

  setState(st: TwinState): void {
    // rings on AEDs whose open state flipped since the previous state (hour crossed a closing/opening time)
    const next = new Map<string, boolean>();
    const items: { lon: number; lat: number; closing: boolean; delayMs: number }[] = [];
    for (const a of st.aed) {
      const o = aedIsOpen(a, this.mode);
      next.set(a.id, o);
      const prev = this.openById.get(a.id);
      if (prev !== undefined && prev !== o) items.push({ lon: a.lon, lat: a.lat, closing: !o, delayMs: 0 });
    }
    // stagger by distance from the centroid so the change reads as a wave across the city
    if (items.length > 1) {
      const cx = items.reduce((m, i) => m + i.lon, 0) / items.length;
      const cy = items.reduce((m, i) => m + i.lat, 0) / items.length;
      const dist = items.map((i) => Math.hypot(i.lon - cx, (i.lat - cy) * 1.2));
      const dmax = Math.max(...dist, Number.EPSILON);
      items.forEach((i, k) => { i.delayMs = ((dist[k] as number) / dmax) * FLIP_STAGGER_MS; });
    }
    this.openById = next;
    this.flips = items.length > 0 ? { start: performance.now(), items } : null;
    this.state = st;
    this.rebuildStatic();
  }

  setMode(m: Mode): void {
    if (m === this.mode) return;
    this.mode = m;
    this.openById = new Map((this.state?.aed ?? []).map((a) => [a.id, aedIsOpen(a, m)]));
    this.modeFrom = this.modeT;
    this.modeStart = performance.now();
    this.rebuildStatic();
  }

  setTime(date: string, hour: number, lat: number, lon: number): void {
    this.date = date;
    this.hour = hour;
    this.lastLat = lat;
    this.lastLon = lon;
    const l = lightingFor(sunPosition(date, hour, lat, lon), this.rain01);
    this.light = l;
    this.sun.direction = l.sunDir;
    const warm: [number, number, number] | null = this.storyStreet ? STREET_WARM : null;
    this.sun.color = warm ?? l.sunColor;
    this.sun.intensity = l.sunIntensity;
    this.ambient.color = warm ?? l.ambientColor;
    this.ambient.intensity = l.ambientIntensity * (this.storyStreet ? STREET_AMBIENT_GAIN : 1);
    this.parent.style.setProperty("--sky-top", l.skyTop);
    this.parent.style.setProperty("--sky-bottom", l.skyBottom);
    this.dirty = true;
  }

  getLighting(): Lighting | null { return this.light; }
  getDate(): string { return this.date; }
  getHour(): number { return this.hour; }

  setRain(r01: number): void { this.rain01 = Math.max(0, Math.min(1, r01)); this.dirty = true; }
  setCamera(c: CamPlace | null): void { this.cam = c; this.dirty = true; }
  /** Theme-layer checkboxes: hide whole groups of layers (aed, stop, signal, silver, accident, cctv). */
  setHiddenGroups(h: ReadonlySet<string>): void { this.hiddenGroups = h; this.dirty = true; }

  zoomBy(dz: number): void {
    const v = this.lastVs;
    if (v === null || this.storyLock) return;
    this.setViewState({ ...v, zoom: v.zoom + dz }, 250);
  }

  setInfra(i: Infra | null): void { this.infra = i; this.dirty = true; }

  /** Start a 상황 playback event (arcs, ripples, labels) at the current time. */
  playback(kind: PlaybackKind, points?: { lon: number; lat: number }[], at?: { lon: number; lat: number }): void {
    const now = performance.now();
    this.events = [...this.events.filter((e) => now - e.start < e.ms && e.kind !== kind), { kind, start: now, ms: PLAYBACK_MS[kind], points, at }];
    this.dirty = true;
  }

  setHighlight(h: { lat: number; lon: number } | null): void { this.highlight = h; this.dirty = true; }
  startPulse(): void { this.pulseStart = performance.now(); this.dirty = true; }
  stopPulse(): void { this.pulseStart = null; this.dirty = true; }
  startRoute(path: LonLat[], ms: number, aed: { lat: number; lon: number } | null, done?: () => void): void { if (this.storyLock) return; this.route = { path, start: performance.now(), ms, aed, done }; this.dirty = true; }
  clearRoute(): void { this.route = null; this.dirty = true; }

  /** Median AED position: the dense core of the cluster (robust to outliers), used to frame the night view. */
  private aedCore(): { lon: number; lat: number } | null {
    const a = this.state?.aed ?? [];
    if (a.length === 0) return null;
    const med = (v: number[]): number => { const s2 = [...v].sort((x, y) => x - y); return s2[Math.floor(s2.length / 2)] as number; };
    return { lon: med(a.map((x) => x.lon)), lat: med(a.map((x) => x.lat)) };
  }

  flyTo(preset: CameraPreset, ms: number = CAMERA.flyMs): void {
    const s = this.scene;
    if (s === null) return;
    if (this.storyLock) { this.pendingPreset = { preset, ms }; return; } // applied when the story view releases the camera
    this.pendingPreset = null;
    this.lastFlyAt = performance.now();
    let vs: ViewState;
    if (preset === "fall" && this.cam !== null) {
      vs = { longitude: this.cam.lon, latitude: this.cam.lat, zoom: CAMERA.fall.zoom, pitch: CAMERA.fall.pitch, bearing: CAMERA.fall.bearing };
    } else {
      const p = preset === "night" ? CAMERA.night : preset === "rain" ? CAMERA.rain : CAMERA.overview;
      const focus = preset === "night" ? this.aedCore() : null;
      vs = { longitude: focus?.lon ?? s.center[0], latitude: focus?.lat ?? s.center[1], zoom: this.fitZoom + p.zoomOffset, pitch: p.pitch, bearing: p.bearing };
    }
    this.lastVs = vs;
    this.deck.setProps({ initialViewState: { ...vs, ...(ms > 0 ? { transitionDuration: ms, transitionInterpolator: new FlyToInterpolator({ speed: 1.4 }) } : {}) } as never });
  }

  /**
   * Extension point (시민 시점 / story layers): extra deck.gl layers drawn after ours. Pass `animating = true` while the
   * layers change every frame (they are then rebuilt each tick via `onTick`).
   */
  setExtraLayers(layers: Layer[], animating = false): void { this.extra = layers; this.extraAnimating = animating; this.dirty = true; }

  /** Per-frame hook for extension layers: called each animation frame while `extraAnimating`; return the new layer list. */
  onTick: ((nowMs: number) => Layer[] | null) | null = null;

  /** Camera override: jump or fly to an arbitrary view state (e.g. a street-level follow camera). */
  setViewState(vs: ViewState, transitionMs = 0): void {
    // a story view that has released the camera must not overwrite a preset chosen after the release (stale 'saved view' pushes)
    if (!this.storyLock && this.lastFlyAt > this.lockReleasedAt) return;
    this.lastVs = vs;
    this.deck.setProps({ initialViewState: { ...vs, ...(transitionMs > 0 ? { transitionDuration: transitionMs, transitionInterpolator: new FlyToInterpolator({ speed: 1.4 }) } : {}) } as never });
  }

  /** Diagnostics: the last view the engine itself set and whether a story view holds the camera. */
  debugView(): { view: ViewState | null; storyLock: boolean; pending: string | null } { return { view: this.lastVs, storyLock: this.storyLock, pending: this.pendingPreset?.preset ?? null }; }

  getParent(): HTMLDivElement { return this.parent; }

  /** Story camera ownership: while locked, flyTo/startRoute are ignored and the mouse controller is off. */
  setStoryLock(on: boolean, street: boolean = on): void {
    const changed = street !== this.storyStreet;
    this.storyStreet = street;
    if (changed) {
      if (this.date !== "") this.setTime(this.date, this.hour, this.lastLat, this.lastLon);
      this.rebuildStatic();
    }
    if (on !== this.storyLock) {
      this.storyLock = on;
      if (!on) this.lockReleasedAt = performance.now();
      this.deck.setProps({ controller: !on });
      if (!on && this.pendingPreset !== null) { const p = this.pendingPreset; this.flyTo(p.preset, p.ms); }
    }
  }

  /** First viewport of the deck (screen projection of 3D anchors, current camera). */
  getViewport(): Viewport | undefined { return this.deck.getViewports()[0]; }

  /** Measurement aid: slowly rotates the camera for `ms` so every frame re-draws the whole scene. */
  orbit(ms: number): void {
    this.orbitFrom = performance.now();
    this.orbitUntil = this.orbitFrom + ms;
    this.frames = [];
  }

  private tick = (): void => {
    if (!this.alive) return;
    this.raf = requestAnimationFrame(this.tick);
    const now = performance.now();
    let animating = false;
    if (now < this.orbitUntil && this.lastVs !== null) {
      this.deck.setProps({ initialViewState: { ...this.lastVs, bearing: this.lastVs.bearing + ((now - this.orbitFrom) / MS_PER_S) * ORBIT_DEG_PER_S } as never });
      animating = true;
    }
    if (this.modeT !== (this.mode === "ours" ? 1 : 0)) {
      const target = this.mode === "ours" ? 1 : 0;
      const f = Math.min(1, (now - this.modeStart) / MODE_MORPH_MS);
      const e = f * f * (3 - 2 * f);
      this.modeT = this.modeFrom + (target - this.modeFrom) * e;
      animating = true;
    }
    if (this.pulseStart !== null) animating = true;
    let flipCtx: DynCtx["flips"] = null;
    if (this.flips !== null) {
      const ms = now - this.flips.start;
      if (ms >= FLIP_MS) this.flips = null;
      else { flipCtx = { t: ms, items: this.flips.items, ringMs: FLIP_RING_MS }; animating = true; }
    }
    let routeCtx: DynCtx["route"] = null;
    if (this.route !== null) {
      const p = (now - this.route.start) / this.route.ms;
      routeCtx = { path: this.route.path, progress: Math.min(1, p) };
      animating = true;
      if (p >= 1 && this.route.done !== undefined) {
        const d = this.route.done;
        this.route.done = undefined;
        d();
      }
    }
    if (this.rain01 > 0) this.drawRain(); else if (this.streaks.length > 0) { this.streaks = []; this.rain.getContext("2d")?.clearRect(0, 0, this.rain.width, this.rain.height); }
    if (this.onTick !== null && this.extraAnimating) {
      const next = this.onTick(now);
      if (next !== null) this.extra = next;
      animating = true;
    }
    if (this.events.length > 0) {
      this.events = this.events.filter((e) => now - e.start < e.ms);
      animating = true;
      this.dirty = true;
    }
    const tall = (this.light?.nightness ?? 0) > 0.5 || ((this.frames.length > 0 ? this.getViewport()?.zoom : undefined) ?? 0) >= this.fitZoom + TALL_ZOOM_OVER_FIT;
    if (tall !== this.lastTall) { this.lastTall = tall; this.dirty = true; }
    if (!animating && !this.dirty) return;
    this.dirty = false;
    const ctx: DynCtx = {
      state: this.state, mode: this.mode, modeT: this.modeT, hour: this.hour, camera: this.cam, highlight: this.highlight,
      pulseT: this.pulseStart === null ? null : ((now - this.pulseStart) % PULSE_PERIOD_MS) / PULSE_PERIOD_MS,
      flips: flipCtx, route: routeCtx, routeAed: this.route?.aed ?? null, nightness: this.light?.nightness ?? 0,
      tall,
    };
    const bm = this.basemapLayers(); // BASEMAP hook
    const vis = (l: Layer): boolean => this.hiddenGroups.size === 0 || !this.hiddenGroups.has(groupOf(l.id));
    this.deck.setProps({ layers: [...this.staticLayers.slice(0, 1), ...bm.under, ...this.staticLayers.slice(1), ...bm.over, ...(this.storyStreet ? [] : dynamicLayers(ctx).filter(vis)), ...(this.infra === null ? [] : infraLayers({ infra: this.infra, hidden: this.storyStreet }).filter(vis)), ...(this.storyStreet || this.infra === null ? [] : playbackLayers(this.events, this.infra.control, now)), ...this.extra] });
  };

  private drawRain(): void {
    const c = this.rain;
    const w = c.clientWidth;
    const h = c.clientHeight;
    if (c.width !== w) c.width = w;
    if (c.height !== h) c.height = h;
    const g = c.getContext("2d");
    if (g === null) return;
    const want = Math.round(RAIN_MAX_STREAKS * this.rain01);
    while (this.streaks.length < want) this.streaks.push({ x: Math.random() * w, y: Math.random() * h, s: 0.6 + Math.random() * 0.8 });
    if (this.streaks.length > want) this.streaks.length = want;
    g.clearRect(0, 0, w, h);
    g.strokeStyle = "rgba(180,200,225,0.45)";
    g.lineWidth = 1;
    g.beginPath();
    for (const s of this.streaks) {
      g.moveTo(s.x, s.y);
      g.lineTo(s.x - RAIN_LEN_PX * RAIN_SLANT * s.s, s.y + RAIN_LEN_PX * s.s);
      s.y += RAIN_SPEED_PX * s.s;
      s.x -= RAIN_SPEED_PX * RAIN_SLANT * s.s;
      if (s.y > h) { s.y = -RAIN_LEN_PX; s.x = Math.random() * w; }
      if (s.x < 0) s.x = w;
    }
    g.stroke();
  }

  destroy(): void {
    this.alive = false;
    cancelAnimationFrame(this.raf);
    this.deck.finalize();
  }
}
