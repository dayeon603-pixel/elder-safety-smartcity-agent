/**
 * StoryController: owns the 생활인구 dots, the 시연 인물 story, the street camera and the HTML anchors.
 * Frame work (camera, actors, layers) runs from the engine's tick; slow state is pushed to React through snapshot().
 */
import type { Layer } from "@deck.gl/core";
import { loadRoute } from "../api";
import type { CrosswalkPoint, SceneBuffers, StopPoint, TwinState } from "../types";
import { fetchTerminalLast, publishHeat } from "./api";
import type { TerminalMsg } from "./api";
import { CAM, DISPLAY_SCALE, WALK_MPS, ENTER_MS, EXIT_MS, FALL_HOLD_MS, PASSERBY_LABEL, PERSON, PERSON_LABEL, PULSE_PERIOD_MS, TERMINAL_POLL_MAX_MS, TERMINAL_POLL_MS, TRIP_LOOP_S, TRIP_VIEW, WALK_BASIS, POP_DEBOUNCE_MS } from "./constants";
import { angleDiff, cumulative, makeFrame, pointAt, toLonLat, toXY } from "./geo";
import type { Frame, XY } from "./geo";
import { buildGraph, edgePolyline, nearestNode } from "./graph";
import { BuildingIndex, buildEdges, shelterLayers, streetLayers, zebraLayer } from "./streetscape";
import type { EdgeBuffers } from "./streetscape";
import type { Graph } from "./graph";
import { storyBridge } from "./host";
import type { StageApi, StoryFrame, StoryHost, StoryViewState } from "./host";
import { actorLayer, PASSERBY_COLOR, poleLayers, pulseLayer, ringLayer, routeLayer, tripsLayer } from "./layers";
import { StoryMachine } from "./machine";
import type { FallSource, Phase } from "./machine";
import { nearestBuildingCenter, planWalk, signalText } from "./plan";
import type { CrossingIn, StoryPlan } from "./plan";
import { joinRowsToDongs, loadDongs, loadPopulation } from "./population";
import type { PopResult } from "./population";
import type { DongGeom, TripBuffers } from "./sampler";
import type { WorkerIn, WorkerOut } from "./storyWorker";

export interface PopUi { status: "off" | "loading" | "pending" | "ok" | "error"; perDot: number | null; dots: number | null; dots65: number | null; message: string | null }
export interface StorySnapshot {
  phase: Phase;
  active: boolean;
  exiting: boolean;
  pop: PopUi;
  motionOn: boolean;
  speedup: number | null;
  stopName: string | null;
  walkRealSec: number | null;
  lengthM: number | null;
  signal: { id: string; address: string; line: string; tone: "ok" | "short" | "none" } | null;
  terminal: TerminalMsg | null;
  terminalSource: "heat" | "fall" | null;
  fallSource: FallSource | null;
  run: { meters: number; totalRealSec: number; realSec: number; aedName: string; leg: "out" | "back" | "none" } | null;
  error: string | null;
  seq: number;
}

export interface Anchors {
  person: [number, number] | null;
  passerby: [number, number] | null;
  signal: [number, number] | null;
  stop: [number, number] | null;
  aed: [number, number] | null;
}

export interface StoryInputs {
  scene: SceneBuffers;
  state: TwinState;
  date: string;
  hour: number;
  place: { lat: number; lon: number; label: string; crosswalkId: string } | null;
  triggerFall: (src: "camera" | "button", detectMs: number | null) => void;
}

/**
 * CesiumMan.glb heading: yaw = heading(math) + 90. Verified by render: with the chase camera behind the person the back is
 * visible (visor/face only shows with -90). The glTF foot geometry alone suggested the opposite, so the render is the reference.
 */
const MODEL_YAW_OFFSET_DEG = 90;
/** blend above which the control-room overlays are hidden */
const STREET_AT = 0.5;
const STOP_POLE_H = 5.2;
const SIGNAL_POLE_H = 5.4;
const LAMP_R = 0.32;
const AED_POLE_H = 4.2;
const FALLEN_LABEL_H = 4.6;
const SHELTER_ROOF_H = 3.6;
const FRONT_DIST_M = 16;
const FRONT_PITCH_DEG = 68;
const ZEBRA_FALLBACK_M = 8;
const GREEN: [number, number, number, number] = [61, 220, 151, 255];
const AMBER: [number, number, number, number] = [255, 176, 64, 255];
const GRAY: [number, number, number, number] = [130, 140, 150, 255];
const RED: [number, number, number, number] = [255, 82, 82, 255];

const ease = (x: number): number => x * x * (3 - 2 * x);
const clamp01 = (x: number): number => Math.min(1, Math.max(0, x));
const deg = (r: number): number => (r * 180) / Math.PI;

/** Heading of the first road segment leaving `node` (zebra bars lie along it). */
function roadHeadingAt(g: Graph, node: number): number {
  const k = g.adjStart[node] as number;
  if (k >= (g.adjStart[node + 1] as number)) return 0;
  const poly = edgePolyline(g, g.adjEdge[k] as number, node);
  const a = poly[0] as XY;
  const b = poly[1] as XY;
  return Math.atan2(b[1] - a[1], b[0] - a[0]);
}

export class StoryController implements StoryHost {
  private stage: StageApi | null = null;
  private scene: SceneBuffers | null = null;
  private frame0: Frame | null = null;
  private graph: Graph | null = null;
  private worker: Worker | null = null;
  private workerReady = false;
  private dongs: DongGeom[] = [];
  private trips: TripBuffers | null = null;
  private tripsEpoch = 0;
  private sampleId = 0;
  private popKey = "";
  private popTimer: ReturnType<typeof setTimeout> | null = null;
  private motionOn = true;
  private pop: PopUi = { status: "loading", perDot: null, dots: null, dots65: null, message: null };
  private inputs: StoryInputs | null = null;
  private listeners = new Set<() => void>();
  private snap: StorySnapshot | null = null;
  private seq = 0;

  readonly machine = new StoryMachine();
  anchors: Anchors = { person: null, passerby: null, signal: null, stop: null, aed: null };
  private plan: StoryPlan | null = null;
  private crossing: CrossingIn | null = null;
  private crossingPole: XY | null = null;
  private aedName = "";
  private routeXY: XY[] | null = null;
  private routeCum: number[] = [];
  private pendingRescue: { path: XY[]; meters: number } | null = null;
  private camZoom = 0;
  /** test aid: camera ~6 m in front of the person, looking back (heading check) */
  frontCam = false;
  private camEvalAt = 0;
  private camChoice = { dhDeg: 0, dPitch: 0 };
  private occ: BuildingIndex | null = null;
  private edges: EdgeBuffers | null = null;
  private zebra: { at: XY; heading: number; len: number } | null = null;
  private shelterHeading = 0;
  private camPitch = 0;
  private terminal: TerminalMsg | null = null;
  private terminalSource: "heat" | "fall" | null = null;
  private error: string | null = null;

  // camera
  private controlled = false;
  private exiting = false;
  private blend = 0;
  private blendFrom = 0;
  private blendT0 = 0;
  private saved: StoryViewState | null = null;
  private camXY: XY = [0, 0];
  private camHeading = 0;
  private camInit = false;
  private lastNow = 0;
  private vt = 0;
  private phaseSeen: Phase = "idle";
  private pulseT0 = 0;
  private fallToken = 0;

  /** Registers this controller as the receiver of fall events (call from an effect; destroy() unregisters). */
  activate(): void { storyBridge.onFall = (src): void => this.onFallEvent(src); }

  attachStage(s: StageApi): void { this.stage = s; }

  /** A fall (camera or button) while in 관제 시점 opens the street view at the crosswalk (step ④). */
  private onFallEvent(src: FallSource): void {
    if (!this.controlled && !this.begin()) return;
    this.onFall(src);
  }

  // ---------- subscription ----------
  subscribe = (fn: () => void): (() => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; };
  getSnapshot = (): StorySnapshot => {
    if (this.snap === null || this.snap.seq !== this.seq) this.snap = this.buildSnapshot();
    return this.snap;
  };
  private emit(): void { this.seq++; this.listeners.forEach((f) => f()); }

  private buildSnapshot(): StorySnapshot {
    const m = this.machine;
    const sig = this.crossing === null ? null : { id: this.crossing.id, address: this.crossing.address, ...signalText(this.crossing) };
    const r = m.rescue;
    return {
      phase: m.phase, active: this.controlled, exiting: this.exiting, pop: this.pop, motionOn: this.motionOn,
      speedup: this.plan?.speedup ?? null, stopName: this.plan?.stop.name ?? null, walkRealSec: this.plan?.realSec ?? null, lengthM: this.plan?.lengthM ?? null,
      signal: sig, terminal: this.terminal, terminalSource: this.terminalSource, fallSource: m.fallSource,
      run: r === null ? null : { meters: r.meters, totalRealSec: m.runTotalRealSec, realSec: m.runRealSec, aedName: this.aedName, leg: m.passerby()?.leg ?? "none" },
      error: this.error, seq: this.seq,
    };
  }

  // ---------- inputs ----------
  setInputs(inp: StoryInputs): void {
    const first = this.scene !== inp.scene;
    this.inputs = inp;
    if (first) this.initScene(inp.scene);
    this.schedulePopulation();
  }

  private initScene(scene: SceneBuffers): void {
    this.scene = scene;
    this.frame0 = makeFrame(scene.center[0], scene.center[1]);
    this.graph = buildGraph(scene.roadPositions, scene.roadStartIndices, scene.roadCount);
    this.worker?.terminate();
    this.workerReady = false;
    const w = new Worker(new URL("./storyWorker.ts", import.meta.url), { type: "module" });
    this.worker = w;
    w.onmessage = (ev: MessageEvent<WorkerOut>): void => this.onWorker(ev.data);
    w.onerror = (ev: ErrorEvent): void => { this.pop = { status: "error", perDot: null, dots: null, dots65: null, message: ev.message }; this.emit(); };
    const frame = this.frame0;
    loadDongs(frame).then((d) => {
      this.dongs = d;
      const msg: WorkerIn = { kind: "init", roads: scene.roadPositions.slice(), starts: scene.roadStartIndices.slice(), edges: scene.roadCount, dongs: d };
      w.postMessage(msg);
    }).catch((e: unknown) => { this.pop = { status: "error", perDot: null, dots: null, dots65: null, message: e instanceof Error ? e.message : String(e) }; this.emit(); });
  }

  private onWorker(m: WorkerOut): void {
    if (m.kind === "ready") { this.workerReady = true; this.popKey = ""; this.schedulePopulation(); return; }
    if (m.kind === "error") { this.pop = { status: "error", perDot: null, dots: null, dots65: null, message: m.message }; this.emit(); return; }
    if (m.id !== this.sampleId) return;
    this.trips = m.buffers;
    this.tripsEpoch++;
    this.pop = { status: "ok", perDot: m.perDot, dots: m.dots, dots65: m.dots65, message: null };
    this.emit();
  }

  private schedulePopulation(): void {
    const inp = this.inputs;
    if (inp === null || !this.workerReady) return;
    const key = `${inp.date}|${Math.min(23, Math.floor(inp.hour))}`;
    if (key === this.popKey) return;
    this.popKey = key;
    if (this.popTimer !== null) clearTimeout(this.popTimer);
    this.popTimer = setTimeout(() => {
      const id = ++this.sampleId;
      void loadPopulation(inp.date, inp.hour).then((r: PopResult) => {
        if (id !== this.sampleId) return;
        if (r.status === "pending") { this.trips = null; this.pop = { status: "pending", perDot: null, dots: null, dots65: null, message: null }; this.emit(); return; }
        if (r.status === "error") { this.trips = null; this.pop = { status: "error", perDot: null, dots: null, dots65: null, message: r.message }; this.emit(); return; }
        const rows = joinRowsToDongs(r.rows, this.dongs);
        this.worker?.postMessage({ kind: "sample", id, rows } satisfies WorkerIn);
      });
    }, POP_DEBOUNCE_MS);
  }

  setMotion(on: boolean): void { this.motionOn = on; this.emit(); }

  /** Retry loading population (the endpoint may land after the page opened). */
  retryPopulation(): void { this.popKey = ""; this.schedulePopulation(); }

  // ---------- story control ----------
  start(): boolean {
    return this.begin();
  }

  private begin(): boolean {
    const inp = this.inputs;
    const g = this.graph;
    const f = this.frame0;
    const sc = this.scene;
    if (inp === null || g === null || f === null || sc === null || this.controlled) return false;
    const stops = inp.state.stops;
    if (stops.length === 0) { this.error = "추천 정류장 자료 없음"; this.emit(); return false; }
    const place = inp.place;
    const stop = this.pickStop(stops, place, f);
    const cw = place === null ? null : inp.state.crosswalks.find((c: CrosswalkPoint) => c.id === place.crosswalkId) ?? null;
    this.crossing = cw === null ? null : { id: cw.id, address: cw.address, xy: toXY(f, cw.lon, cw.lat), greenS: cw.greenS, needS: cw.needS, short: cw.short };
    const plan = planWalk(g, { id: stop.id, name: stop.name, xy: toXY(f, stop.lon, stop.lat) }, this.crossing, (x, y, d) => nearestBuildingCenter(sc.positions, sc.startIndices, sc.buildingCount, x, y, d));
    if (plan === null) { this.error = "보행망에서 경로를 만들지 못함"; this.emit(); return false; }
    this.plan = plan;
    this.occ ??= new BuildingIndex(sc);
    const anchors: XY[] = [];
    for (let d = 0; d <= plan.lengthM; d += CAM.edgeRadiusM / 3) anchors.push(pointAt(plan.path, plan.cum, d).p);
    if (this.crossing !== null) anchors.push(this.crossing.xy);
    const cwNode = this.graph === null || this.crossing === null ? null : nearestNode(this.graph, this.crossing.xy[0], this.crossing.xy[1]);
    if (cwNode !== null && this.graph !== null) anchors.push([this.graph.nodeX[cwNode.node] as number, this.graph.nodeY[cwNode.node] as number]);
    this.edges = buildEdges(sc, anchors, CAM.edgeRadiusM);
    this.zebra = null;
    this.shelterHeading = pointAt(plan.path, plan.cum, plan.lengthM).heading;
    this.camEvalAt = 0;
    this.error = null;
    this.terminal = null;
    this.terminalSource = null;
    this.routeXY = null;
    this.pendingRescue = null;
    this.crossingPole = null;
    this.machine.start(plan);
    this.saved = this.stage?.currentView() ?? null;
    this.controlled = true;
    this.exiting = false;
    this.blendFrom = 0;
    this.blend = 0;
    this.blendT0 = performance.now();
    this.camInit = false;
    this.phaseSeen = "enter";
    this.emit();
    return true;
  }

  private pickStop(stops: StopPoint[], place: StoryInputs["place"], f: Frame): StopPoint {
    if (place === null) return stops[0] as StopPoint;
    const p = toXY(f, place.lon, place.lat);
    let best = stops[0] as StopPoint;
    let bd = Infinity;
    for (const s of stops) {
      const q = toXY(f, s.lon, s.lat);
      const d = Math.hypot(q[0] - p[0], q[1] - p[1]);
      if (d < bd) { bd = d; best = s; }
    }
    return best;
  }

  stop(): void {
    if (!this.controlled || this.exiting) return;
    this.exiting = true;
    this.blendFrom = this.blend;
    this.blendT0 = performance.now();
    this.fallToken++;
    this.pendingRescue = null;
    this.emit();
  }

  /** Called when the camera pipeline or the button reports a fall (via useTwin.triggerFall). */
  private onFall(src: FallSource): void {
    const inp = this.inputs;
    const f = this.frame0;
    if (inp === null || f === null || inp.place === null || this.exiting) return;
    const raw = toXY(f, inp.place.lon, inp.place.lat);
    /** The crosswalk coordinate can lie inside a building footprint; the person lies on the nearest walk-network point. */
    const g = this.graph;
    const nn = g === null ? null : nearestNode(g, raw[0], raw[1]);
    const at: XY = g === null || nn === null ? raw : [g.nodeX[nn.node] as number, g.nodeY[nn.node] as number];
    const cur = this.machine.person();
    this.machine.fallHeading = cur === null ? 0 : cur.heading;
    if (!this.machine.fall(src, at)) return;
    this.pulseT0 = performance.now();
    this.camInit = false;
    const token = ++this.fallToken;
    this.crossingPole = at;
    this.zebra = { at, heading: g === null || nn === null ? 0 : roadHeadingAt(g, nn.node), len: (this.crossing?.needS ?? null) === null ? ZEBRA_FALLBACK_M : (this.crossing?.needS as number) * WALK_MPS };
    const lastIdBefore = this.terminal?.id ?? null;
    void (async (): Promise<void> => {
      const base = await fetchTerminalLast();
      const baseId = base?.id ?? lastIdBefore;
      const r = await loadRoute(inp.place?.lat ?? 0, inp.place?.lon ?? 0, inp.date, Math.floor(inp.hour), inp.state.daytype);
      if (token !== this.fallToken) return;
      if (r === null) { this.error = "열린 AED 경로를 찾지 못함"; this.emit(); return; }
      const path = r.route.path.map((p): XY => toXY(f, p[0], p[1]));
      this.routeXY = path;
      this.routeCum = cumulative(path);
      this.aedName = r.route.aed.name;
      this.pendingRescue = { path, meters: r.route.meters };
      this.emit();
      await this.pollTerminal(token, baseId, "fall");
    })();
    this.emit();
  }

  private async pollTerminal(token: number, baseId: number | null, kind: "fall" | "heat"): Promise<void> {
    const t0 = performance.now();
    while (token === this.fallToken && performance.now() - t0 < TERMINAL_POLL_MAX_MS) {
      const m = await fetchTerminalLast();
      if (token !== this.fallToken) return;
      if (m !== null && m.id !== baseId && m.kind === kind) { this.terminal = m; this.terminalSource = kind; this.emit(); return; }
      await new Promise((r) => setTimeout(r, TERMINAL_POLL_MS));
    }
  }

  /** Step ③: the person is at the stop -> show the terminal's current message (publish heat first if it is not a heat message). */
  private async showArrivalTerminal(token: number): Promise<void> {
    const inp = this.inputs;
    if (inp === null) return;
    let m = await fetchTerminalLast();
    if (token !== this.fallToken) return;
    if (m === null || m.kind !== "heat") {
      await publishHeat(inp.date, inp.hour);
      m = await fetchTerminalLast();
      if (token !== this.fallToken) return;
    }
    if (m === null) { this.error = "단말 메시지를 읽지 못함"; this.emit(); return; }
    this.terminal = m;
    this.terminalSource = "heat";
    this.emit();
  }

  /** Button path for the fall; goes through the same useTwin flow as the camera (labelled 버튼 시연). */
  fallByButton(): void { this.inputs?.triggerFall("button", null); }

  // ---------- per frame ----------
  frame = (now: number): StoryFrame => {
    const dt = this.lastNow === 0 ? 16 : Math.min(100, now - this.lastNow);
    this.lastNow = now;
    const sc = this.scene;
    const f = this.frame0;
    if (sc === null || f === null) return { layers: [], view: null, animating: false, controlled: false, street: false };
    const m = this.machine;
    if (this.controlled) {
      if (this.pendingRescue !== null && m.phase === "fall" && m.phaseMs >= FALL_HOLD_MS) { m.beginRescue(this.pendingRescue.path, this.pendingRescue.meters); this.pendingRescue = null; }
      m.update(dt);
      if (m.phase !== this.phaseSeen) this.onPhase(m.phase);
    }
    // blend of camera between control view and street view
    if (this.controlled) {
      if (this.exiting) {
        const k = clamp01((now - this.blendT0) / EXIT_MS);
        this.blend = this.blendFrom * (1 - ease(k));
      } else if (m.phase === "enter" || this.blend < 1) {
        this.blend = clamp01((now - this.blendT0) / ENTER_MS);
      }
    }
    const street = this.controlled ? ease(this.blend) : 0;
    const v = TRIP_VIEW;
    this.vt += (dt / 1000) * (v.overview.rate + (v.street.rate - v.overview.rate) * street);

    const layers: Layer[] = [];
    if (this.motionOn && this.trips !== null && this.trips.count > 0) {
      const lerp = (a: number, b: number): number => a + (b - a) * street;
      layers.push(tripsLayer(sc.center, this.trips, this.vt % TRIP_LOOP_S, lerp(v.overview.opacity, v.street.opacity), lerp(v.overview.trail, v.street.trail), lerp(v.overview.widthM, v.street.widthM)));
    }
    let view: StoryViewState | null = null;
    if (this.controlled) {
      this.storyLayers(now, layers, f, sc);
      view = this.cameraView(now, dt, f);
      if (this.exiting && this.blend <= 0.0005) {
        this.finishExit();
        return { layers, view: this.saved, animating: false, controlled: false, street: false };
      }
    }
    this.updateAnchors(f);
    return { layers, view, animating: this.controlled || (this.motionOn && this.trips !== null), controlled: this.controlled, street: this.controlled && street > STREET_AT };
  };

  private onPhase(p: Phase): void {
    this.phaseSeen = p;
    if (p === "arrived") void this.showArrivalTerminal(this.fallToken);
    this.emit();
  }

  private finishExit(): void {
    this.controlled = false;
    this.exiting = false;
    this.machine.reset();
    this.plan = null;
    this.routeXY = null;
    this.crossingPole = null;
    this.terminal = null;
    this.terminalSource = null;
    this.camInit = false;
    this.emit();
  }

  private storyLayers(now: number, layers: Layer[], f: Frame, sc: SceneBuffers): void {
    void f;
    const m = this.machine;
    const plan = this.plan;
    if (plan === null) return;
    const pr = m.person();
    const fallen = m.phase === "fall" || m.phase === "rescue" || m.phase === "done";
    const poles = [] as { id: string; xy: XY; heightM: number; color: [number, number, number, number]; radiusM: number }[];
    if (!fallen) poles.push({ id: "stop", xy: plan.stop.xy, heightM: STOP_POLE_H, color: AMBER, radiusM: LAMP_R });
    if (fallen && this.crossingPole !== null) {
      const c = this.crossing;
      poles.push({ id: "signal", xy: this.crossingPole, heightM: SIGNAL_POLE_H, color: c === null || c.greenS === null ? GRAY : c.short ? RED : GREEN, radiusM: LAMP_R });
    }
    if (fallen && this.routeXY !== null) { const end = this.routeXY[this.routeXY.length - 1] as XY; poles.push({ id: "aed", xy: end, heightM: AED_POLE_H, color: GREEN, radiusM: LAMP_R * 1.6 }); }
    layers.push(...streetLayers(sc, this.edges, clamp01(this.blend)));
    layers.push(...shelterLayers(sc.center, plan.stop.xy, this.shelterHeading, SHELTER_ROOF_H));
    if (fallen && this.zebra !== null) layers.push(zebraLayer(sc.center, this.zebra.at, this.zebra.heading, this.zebra.len));
    layers.push(...poleLayers(sc.center, poles));
    if (fallen && this.crossing !== null) layers.push(ringLayer(sc.center, "story-crosswalk", this.crossingPole ?? this.crossing.xy, 2.6, [255, 255, 255, 230]));
    if (pr !== null) {
      const yaw = deg(pr.heading) + MODEL_YAW_OFFSET_DEG;
      layers.push(actorLayer(sc.center, { id: "person", xy: pr.p, yawDeg: yaw, lieDeg: 90 * ease(m.lie01), z: 0.12 * m.lie01, anim: m.walking ? PERSON.walkAnim : 0.0001, tint: PERSON.tint }, DISPLAY_SCALE));
    }
    if (fallen && pr !== null) layers.push(pulseLayer(sc.center, pr.p, ((now - this.pulseT0) % PULSE_PERIOD_MS) / PULSE_PERIOD_MS));
    if (this.routeXY !== null && fallen && m.rescue !== null) layers.push(...routeLayer(sc.center, this.routeXY, m.rescue.meters));
    const pb = m.passerby();
    if (pb !== null) layers.push(actorLayer(sc.center, { id: "passerby", xy: pb.p, yawDeg: deg(pb.heading) + MODEL_YAW_OFFSET_DEG, lieDeg: 0, z: 0, anim: m.phase === "rescue" ? 2.4 : 0.0001, tint: PASSERBY_COLOR }, DISPLAY_SCALE));
  }

  /** Street follow camera with a smoothed centre/heading; blends from/to the control view. */
  private cameraView(now: number, dt: number, f: Frame): StoryViewState | null {
    const m = this.machine;
    const pr = m.person();
    if (pr === null) return null;
    const tau = (x: number): number => 1 - Math.exp(-dt / 1000 / x);
    let target: XY = pr.p;
    let heading = pr.heading;
    const H = this.stage?.size()?.[1] ?? null;
    /** zoom whose camera distance (1.5 x viewport height x metres per pixel) equals `distM` */
    const zFor = (distM: number, fallback: number): number => (H === null ? fallback : Math.log2((78271.517 * Math.cos((f.lat0 * Math.PI) / 180)) / (distM / (1.5 * H))));
    let zoom: number = zFor(CAM.street.distM, CAM.street.zoom);
    let pitch: number = CAM.street.pitch;
    let h: number = CAM.street.targetH;
    /** Fall scene: camera behind the person, looking along the street towards the AED. */
    const toAed = this.routeXY === null ? null : pointAt(this.routeXY, this.routeCum, CAM.fall.lookAlongM).heading;
    if (m.phase === "arrived") { zoom = zFor(CAM.arrive.distM, CAM.arrive.zoom); pitch = CAM.arrive.pitch; h = CAM.arrive.targetH; }
    if (m.phase === "fall" || m.phase === "done") { zoom = zFor(CAM.fall.distM, CAM.fall.zoom); pitch = CAM.fall.pitch; heading = toAed ?? (this.camInit ? this.camHeading : heading); }
    if (m.phase === "rescue") {
      const pb = m.passerby();
      if (pb !== null) {
        target = [(pr.p[0] + pb.p[0]) / 2, (pr.p[1] + pb.p[1]) / 2];
        const sep = Math.hypot(pb.p[0] - pr.p[0], pb.p[1] - pr.p[1]);
        zoom = Math.min(CAM.rescue.zoomMax, Math.max(CAM.rescue.zoomMin, CAM.rescue.zoomMax - Math.log2(Math.max(1, sep / CAM.rescue.fitM))));
        pitch = CAM.rescue.pitch;
        h = 0;
        heading = toAed ?? (this.camInit ? this.camHeading : heading);
      }
    }
    if (m.walking || m.phase === "enter") {
      const ahead = this.plan === null ? { p: target, heading } : pointAt(this.plan.path, this.plan.cum, m.s + CAM.street.lookAheadM);
      heading = Math.atan2(ahead.p[1] - pr.p[1], ahead.p[0] - pr.p[0]);
      if (Math.hypot(ahead.p[0] - pr.p[0], ahead.p[1] - pr.p[1]) < 0.5) heading = pr.heading;
      target = [pr.p[0] + Math.cos(heading) * CAM.street.leadM, pr.p[1] + Math.sin(heading) * CAM.street.leadM];
    }
    if (this.frontCam) {
      const size = this.stage?.size() ?? null;
      const mpp = FRONT_DIST_M / (1.5 * (size?.[1] ?? 500));
      zoom = Math.log2((78271.517 * Math.cos((f.lat0 * Math.PI) / 180)) / mpp);
      pitch = FRONT_PITCH_DEG;
      heading += Math.PI;
    }
    // occlusion: if a building stands between the chase camera and the person, rotate around / raise the camera
    if (this.frontCam) this.camChoice = { dhDeg: 0, dPitch: 0 };
    else if (now - this.camEvalAt > CAM.evalMs || !this.camInit) {
      this.camEvalAt = now;
      this.camChoice = this.chooseCamera(target, heading, pitch, zoom, h, f);
    }
    heading += (this.camChoice.dhDeg * Math.PI) / 180;
    pitch += this.camChoice.dPitch;
    if (!this.camInit) { this.camXY = target; this.camHeading = heading; this.camZoom = zoom; this.camPitch = pitch; this.camInit = true; }
    this.camZoom += (zoom - this.camZoom) * tau(CAM.street.zoomTau);
    this.camPitch += (pitch - this.camPitch) * tau(CAM.street.zoomTau);
    const k = tau(CAM.street.followTau);
    this.camXY = [this.camXY[0] + (target[0] - this.camXY[0]) * k, this.camXY[1] + (target[1] - this.camXY[1]) * k];
    this.camHeading += angleDiff(this.camHeading, heading) * tau(CAM.street.headingTau);
    const [lon, lat] = toLonLat(f, this.camXY[0], this.camXY[1]);
    const live: StoryViewState = { longitude: lon, latitude: lat, zoom: this.camZoom, pitch: this.camPitch, bearing: 90 - deg(this.camHeading), position: [0, 0, h] };
    const s = this.saved;
    const b = ease(this.blend);
    if (s === null || b >= 1) return live;
    const lerp = (a: number, c: number): number => a + (c - a) * b;
    return {
      longitude: lerp(s.longitude, live.longitude), latitude: lerp(s.latitude, live.latitude), zoom: lerp(s.zoom, live.zoom), pitch: lerp(s.pitch, live.pitch),
      bearing: s.bearing + angleDiff((s.bearing * Math.PI) / 180, (live.bearing * Math.PI) / 180) * (180 / Math.PI) * b, position: [0, 0, h * b],
    };
  }


  /** First (pitch, heading) candidate whose camera position has a clear line to the person. */
  private chooseCamera(target: XY, heading: number, pitch: number, zoom: number, targetH: number, f: Frame): { dhDeg: number; dPitch: number } {
    const occ = this.occ;
    const size = this.stage?.size() ?? null;
    if (occ === null || size === null) return { dhDeg: 0, dPitch: 0 };
    const mpp = (78271.517 * Math.cos((f.lat0 * Math.PI) / 180)) / 2 ** zoom;
    const d = 1.5 * size[1] * mpp;
    const head: [number, number, number] = [target[0], target[1], Math.min(targetH, 1.8) + 0.2];
    for (const dp of CAM.pitchSteps) for (const dh of CAM.headingOffsets) {
      const p = ((pitch + dp) * Math.PI) / 180;
      const hd = heading + (dh * Math.PI) / 180;
      const cam: [number, number, number] = [target[0] - Math.cos(hd) * d * Math.sin(p), target[1] - Math.sin(hd) * d * Math.sin(p), targetH + d * Math.cos(p)];
      if (!occ.blocked(cam, head) && !occ.blocked(cam, [target[0], target[1], 0.6])) return { dhDeg: dh, dPitch: dp };
    }
    return { dhDeg: 0, dPitch: CAM.pitchSteps[CAM.pitchSteps.length - 1] as number };
  }

  private updateAnchors(f: Frame): void {
    const st = this.stage;
    if (st === null || !this.controlled) { this.anchors = { person: null, passerby: null, signal: null, stop: null, aed: null }; return; }
    const m = this.machine;
    const pr = m.person();
    const proj = (xy: XY | null, z: number): [number, number] | null => {
      if (xy === null) return null;
      const [lon, lat] = toLonLat(f, xy[0], xy[1]);
      return st.project(lon, lat, z);
    };
    const fallen = m.phase === "fall" || m.phase === "rescue" || m.phase === "done";
    const pb = m.passerby();
    this.anchors = {
      person: proj(pr?.p ?? null, fallen ? FALLEN_LABEL_H : PERSON.sizeM * DISPLAY_SCALE + 0.5),
      passerby: proj(pb?.p ?? null, PERSON.sizeM * DISPLAY_SCALE + 0.5),
      signal: fallen ? proj(this.crossingPole, SIGNAL_POLE_H + 0.5) : null,
      stop: proj(this.plan?.stop.xy ?? null, STOP_POLE_H + 0.5),
      aed: fallen && this.routeXY !== null ? proj(this.routeXY[this.routeXY.length - 1] as XY, AED_POLE_H + 0.5) : null,
    };
  }

  destroy(): void {
    this.worker?.terminate();
    if (this.popTimer !== null) clearTimeout(this.popTimer);
    storyBridge.onFall = null;
  }
}

export const LABELS = { person: PERSON_LABEL, passerby: PASSERBY_LABEL, walk: WALK_BASIS } as const;
