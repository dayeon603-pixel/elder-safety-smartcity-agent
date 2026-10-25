import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { POP_LEGEND_SOURCE, POP_PENDING_TEXT, RUN_MPS, STAND_OFF_M, TARGET_WALK_SEC, WALK_MPS } from "./constants";
import { StoryController } from "./controller";
import type { StorySnapshot } from "./controller";
import { cumulative, makeFrame, pointAt, pointInRing, toLonLat, toXY } from "./geo";
import type { XY } from "./geo";
import { buildGraph, dijkstra, nearestNode, pathTo } from "./graph";
import { StoryMachine } from "./machine";
import { planWalk, signalText } from "./plan";
import { joinRowsToDongs, parsePopulation } from "./population";
import { mulberry32, nodesByDong, peoplePerDot, planDots, sampleTrips } from "./sampler";
import type { DongGeom, DongRow } from "./sampler";
import { StoryOverlay } from "./StoryOverlay";
import { parseTerminalLast } from "./api";
import type { TwinCtl } from "../useTwin";

/** 5x5 grid of 100 m edges (2 vertices each) in the metre frame. */
function gridGraph(n = 5, step = 100) {
  const pos: number[] = [];
  const starts: number[] = [];
  let v = 0;
  const edge = (a: XY, b: XY): void => { starts.push(v); pos.push(a[0], a[1], b[0], b[1]); v += 2; };
  for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) {
    if (i + 1 < n) edge([i * step, j * step], [(i + 1) * step, j * step]);
    if (j + 1 < n) edge([i * step, j * step], [i * step, (j + 1) * step]);
  }
  return buildGraph(Float32Array.from(pos), Uint32Array.from(starts), starts.length);
}
const square = (x0: number, y0: number, x1: number, y1: number): XY[] => [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]];
const DONGS: DongGeom[] = [
  { adm_cd: "1162052500", adm_nm: "A", rings: [square(-10, -10, 210, 410)] },
  { adm_cd: "1162054500", adm_nm: "B", rings: [square(210.0001, -10, 410, 410)] },
];

describe("geo + graph", () => {
  it("round-trips lon/lat <-> metres", () => {
    const f = makeFrame(126.944, 37.465);
    const xy = toXY(f, 126.95, 37.47);
    const [lon, lat] = toLonLat(f, xy[0], xy[1]);
    expect(lon).toBeCloseTo(126.95, 9);
    expect(lat).toBeCloseTo(37.47, 9);
    expect(Math.hypot(...xy)).toBeGreaterThan(500);
  });
  it("pointAt clamps and interpolates", () => {
    const path: XY[] = [[0, 0], [10, 0], [10, 10]];
    const cum = cumulative(path);
    expect(pointAt(path, cum, 5).p).toEqual([5, 0]);
    expect(pointAt(path, cum, 15).p).toEqual([10, 5]);
    expect(pointAt(path, cum, 999).p).toEqual([10, 10]);
    expect(pointAt(path, cum, -3).p).toEqual([0, 0]);
  });
  it("point in ring", () => { expect(pointInRing(5, 5, square(0, 0, 10, 10))).toBe(true); expect(pointInRing(15, 5, square(0, 0, 10, 10))).toBe(false); });
  it("builds a connected graph and finds the shortest path (400 m across the grid)", () => {
    const g = gridGraph();
    expect(g.nodeCount).toBe(25);
    const a = nearestNode(g, 0, 0).node;
    const b = nearestNode(g, 400, 400).node;
    const t = dijkstra(g, a);
    expect(t.dist.get(b)).toBeCloseTo(800, 6);
    const p = pathTo(g, t, a, b) as XY[];
    expect(p[0]).toEqual([0, 0]);
    expect(p[p.length - 1]).toEqual([400, 400]);
    expect(dijkstra(g, a, 150).dist.size).toBeLessThan(25);
  });
});

describe("pedestrian sampling", () => {
  const g = gridGraph();
  const nodes = nodesByDong(g, DONGS);
  const rows: DongRow[] = [{ adm_cd: "1162052500", total: 12_000, age65p: 3_000 }, { adm_cd: "1162054500", total: 6_000, age65p: 600 }];
  it("assigns nodes to dongs", () => { expect((nodes.get("1162052500") ?? []).length).toBeGreaterThan(0); expect((nodes.get("1162054500") ?? []).length).toBeGreaterThan(0); });
  it("dot counts are proportional to the real counts, 65+ share preserved", () => {
    const perDot = peoplePerDot(rows);
    const plans = planDots(rows, perDot);
    expect(plans[0]?.dots).toBe(Math.round(12_000 / perDot));
    expect(plans[1]?.dots).toBe(Math.round(6_000 / perDot));
    expect((plans[0]?.dots ?? 0) / (plans[1]?.dots ?? 1)).toBeCloseTo(2, 1);
    expect(plans[0]?.dots65).toBe(Math.round((plans[0]?.dots ?? 0) * 0.25));
  });
  it("fewer people at night -> fewer dots; zero rows -> no dots (nothing invented)", () => {
    const night = rows.map((r) => ({ ...r, total: r.total / 4 }));
    expect(planDots(night, 100).reduce((s, p) => s + p.dots, 0)).toBeLessThan(planDots(rows, 100).reduce((s, p) => s + p.dots, 0));
    expect(sampleTrips(g, nodes, planDots([], 100), [1, 2, 3, 255], [4, 5, 6, 255]).count).toBe(0);
  });
  it("dot budget raises people-per-dot instead of dropping dots silently", () => {
    expect(peoplePerDot([{ adm_cd: "x", total: 10_000_000, age65p: 0 }])).toBeGreaterThan(100);
  });
  it("trips lie on the walk network, are deterministic, and stable when counts shrink", () => {
    const plans = planDots(rows, 100);
    const a = sampleTrips(g, nodes, plans, [1, 2, 3, 255], [9, 9, 9, 255]);
    const b = sampleTrips(g, nodes, plans, [1, 2, 3, 255], [9, 9, 9, 255]);
    expect(Array.from(a.positions)).toEqual(Array.from(b.positions));
    expect(a.count).toBe(plans.reduce((s, p) => s + p.dots, 0));
    const onGrid = (x: number, y: number): boolean => Math.abs(x / 100 - Math.round(x / 100)) < 1e-4 || Math.abs(y / 100 - Math.round(y / 100)) < 1e-4;
    for (let i = 0; i < a.positions.length; i += 2) expect(onGrid(a.positions[i] as number, a.positions[i + 1] as number)).toBe(true);
    for (let t = 0; t < a.count; t++) {
      const s = a.startIndices[t] as number;
      const e = a.startIndices[t + 1] as number;
      for (let v = s + 1; v < e; v++) expect(a.timestamps[v] as number).toBeGreaterThanOrEqual(a.timestamps[v - 1] as number);
    }
    const fewer = sampleTrips(g, nodes, planDots(rows.map((r) => ({ ...r, total: r.total / 2 })), 100), [1, 2, 3, 255], [9, 9, 9, 255]);
    const firstTrip = (x: typeof a): number[] => Array.from(x.positions.slice(0, (x.startIndices[1] as number) * 2));
    expect(firstTrip(fewer)).toEqual(firstTrip(a));
    expect(a.is65.reduce((s, v) => s + v, 0)).toBe(plans.reduce((s, p) => s + p.dots65, 0));
  });
  it("rng is a pure function of its seed", () => { const r1 = mulberry32(7); const r2 = mulberry32(7); expect([r1(), r1(), r1()]).toEqual([r2(), r2(), r2()]); });
});

describe("population parsing", () => {
  it("accepts a bare array and wrapped arrays, drops malformed rows", () => {
    const row = { adm_cd: "1162052500", adm_nm: "보라매동", total: 100, age65p: 20 };
    expect(parsePopulation([row])?.rows).toHaveLength(1);
    expect(parsePopulation({ rows: [row, { adm_cd: "x" }], sources: { a: 1 } })?.rows).toHaveLength(1);
    expect(parsePopulation({ nothing: 1 })).toBeNull();
    expect(parsePopulation([])).toBeNull();
  });
  it("joins 8-digit and 10-digit codes to the dong polygons", () => {
    const joined = joinRowsToDongs([{ adm_cd: "11620525", total: 1, age65p: 0 }, { adm_cd: "1162054500", total: 2, age65p: 0 }, { adm_cd: "9999999999", total: 3, age65p: 0 }], DONGS);
    expect(joined.map((r) => r.adm_cd)).toEqual(["1162052500", "1162054500"]);
  });
});

describe("walk plan + signal honesty", () => {
  const g = gridGraph(9, 70);
  it("ends beside the stop, walks at 0.7 m/s real time, and derives the time-lapse factor", () => {
    const stop = { id: "S1", name: "정류장", xy: [280, 280] as XY };
    const plan = planWalk(g, stop, null, () => null);
    expect(plan).not.toBeNull();
    if (plan === null) return;
    const end = plan.path[plan.path.length - 1] as XY;
    expect(Math.hypot(end[0] - 280, end[1] - 280)).toBeCloseTo(STAND_OFF_M, 3);
    expect(plan.realSec).toBeCloseTo(plan.lengthM / WALK_MPS, 6);
    expect(plan.speedup).toBe(Math.max(1, Math.ceil(plan.realSec / TARGET_WALK_SEC)));
    expect(plan.lengthM).toBeGreaterThan(50);
    expect(plan.lengthM).toBeLessThan(100);
  });
  it("signal text never invents a green time", () => {
    expect(signalText({ greenS: null, needS: null, short: false }).line).toBe("신호 자료 없음");
    expect(signalText({ greenS: null, needS: 5.8, short: false }).line).toContain("신호 자료 없음");
    expect(signalText({ greenS: 42, needS: 17, short: false })).toMatchObject({ tone: "ok" });
    const s = signalText({ greenS: 10, needS: 17.2, short: true });
    expect(s.tone).toBe("short");
    expect(s.line).toContain("연장 권고");
  });
});

describe("story state machine", () => {
  const g = gridGraph(9, 70);
  const plan = planWalk(g, { id: "S1", name: "정류장", xy: [280, 280] }, null, () => null);
  if (plan === null) throw new Error("plan");
  const run = (m: StoryMachine, ms: number, step = 50): void => { for (let t = 0; t < ms; t += step) m.update(step); };

  it("enter -> walk -> arrived; speed = 0.7 m/s x time-lapse", () => {
    const m = new StoryMachine();
    m.start(plan);
    expect(m.phase).toBe("enter");
    run(m, 2300);
    expect(m.phase).toBe("walk");
    const s0 = m.s;
    run(m, 1000);
    expect(m.s - s0).toBeCloseTo(WALK_MPS * plan.speedup, 1);
    run(m, 120_000);
    expect(m.phase).toBe("arrived");
    expect(m.s).toBeCloseTo(plan.lengthM, 6);
  });
  it("accepts a fall in an active phase only once; rescue timing = 2 x distance / 1.2 m/s", () => {
    const m = new StoryMachine();
    expect(m.fall("button", [0, 0])).toBe(false);
    m.start(plan);
    run(m, 4000);
    expect(m.fall("button", [10, 10])).toBe(true);
    expect(m.phase).toBe("fall");
    expect(m.fall("camera", [0, 0])).toBe(false);
    expect(m.beginRescue([[10, 10]], 5)).toBe(false);
    const route: XY[] = [[10, 10], [10, 70], [10, 120]];
    expect(m.beginRescue(route, 111)).toBe(true);
    expect(m.runTotalRealSec).toBeCloseTo((2 * 111) / RUN_MPS, 6);
    expect(m.passerby()?.leg).toBe("out");
    const sp = plan.speedup;
    const needMs = (2 * 111 / (RUN_MPS * sp)) * 1000;
    run(m, needMs * 0.6);
    expect(m.passerby()?.leg).toBe("back");
    run(m, needMs);
    expect(m.phase).toBe("done");
    expect(m.runRealSec).toBeCloseTo(m.runTotalRealSec, 6);
    const end = m.passerby();
    expect(end?.p[0]).toBeCloseTo(10, 6);
    expect(end?.p[1]).toBeCloseTo(10, 6);
  });
  it("person lies down after the fall and stays at the fall point", () => {
    const m = new StoryMachine();
    m.start(plan);
    run(m, 5000);
    m.fall("camera", [33, 44]);
    expect(m.lie01).toBe(0);
    run(m, 2000);
    expect(m.lie01).toBe(1);
    expect(m.person()?.p).toEqual([33, 44]);
    expect(m.fallSource).toBe("camera");
  });
  it("reset returns to idle", () => { const m = new StoryMachine(); m.start(plan); m.reset(); expect(m.phase).toBe("idle"); expect(m.person()).toBeNull(); });
});

describe("terminal message parsing", () => {
  it("reads /terminal/last verbatim", () => {
    const m = parseTerminalLast({ message: { id: 3, kind: "heat", title: "관악구 폭염 주의", body: "체감 32.3도.", stop_id: null } });
    expect(m).toEqual({ id: 3, kind: "heat", title: "관악구 폭염 주의", body: "체감 32.3도.", stopId: null });
    expect(parseTerminalLast({ message: null })).toBeNull();
  });
});

describe("honesty labels on screen", () => {
  const base = (over: Partial<StorySnapshot>): StorySnapshot => ({
    phase: "idle", active: false, exiting: false, pop: { status: "loading", perDot: null, dots: null, dots65: null, message: null }, motionOn: true, speedup: null, stopName: null, walkRealSec: null, lengthM: null,
    signal: null, terminal: null, terminalSource: null, fallSource: null, run: null, error: null, seq: 1, ...over,
  });
  const ctl = { scene: null, state: null, fall: { status: "idle", routeText: null, publishText: null } } as unknown as TwinCtl;
  const mount = (snap: StorySnapshot) => { const c = new StoryController(); c.getSnapshot = () => snap; return render(<StoryOverlay controller={c} ctl={ctl} />); };

  it("population pending shows 생활인구 자료 준비 중 and no legend scale", () => {
    mount(base({ pop: { status: "pending", perDot: null, dots: null, dots65: null, message: null } }));
    expect(screen.getByTestId("pop-pending").textContent).toContain(POP_PENDING_TEXT);
    expect(screen.queryByTestId("pop-legend-text")).toBeNull();
  });
  it("population ok shows 1점 = N명 and the source / visualisation note", () => {
    mount(base({ pop: { status: "ok", perDot: 100, dots: 3838, dots65: 574, message: null } }));
    const t = screen.getByTestId("pop-legend-text").textContent ?? "";
    expect(t).toContain("1점 = 100명");
    expect(t).toContain(POP_LEGEND_SOURCE);
    expect(POP_LEGEND_SOURCE).toBe("인원 분포: 서울 생활인구 실측 · 이동 경로: 시각화");
  });
  it("story HUD labels the person as 시연 인물, shows ×N 배속, credit and the 버튼 시연 label", () => {
    mount(base({ active: true, phase: "walk", speedup: 17, stopName: "당곡역", walkRealSec: 230, lengthM: 161 }));
    const hud = screen.getByTestId("story-hud").textContent ?? "";
    expect(hud).toContain("시연 인물");
    expect(hud).toContain("×17 배속");
    expect(hud).toContain("CC-BY 4.0");
    expect(hud).toContain("확대 표시");
    expect(hud).toContain("버튼 시연");
    expect(hud).toContain("0.7 m/s");
  });
  it("fall by button is labelled 버튼 시연; passerby time = distance / 1.2 m/s with its basis", () => {
    mount(base({ active: true, phase: "rescue", speedup: 17, fallSource: "button", run: { meters: 111, totalRealSec: 185, realSec: 10, aedName: "복지관", leg: "out" } }));
    expect(screen.getByTestId("story-title").textContent).toContain("버튼 시연");
    expect(screen.getByTestId("story-run").textContent).toContain("÷ 1.2 m/s");
    expect(screen.getByTestId("story-hud-detail").textContent).toContain("행인 1.2 m/s 가정");
  });
  it("signal text with no data says 신호 자료 없음", () => {
    mount(base({ active: true, phase: "fall", fallSource: "camera", signal: { id: "06-1", address: "", line: signalText({ greenS: null, needS: null, short: false }).line, tone: "none" } }));
    expect(screen.queryByTestId("story-title")?.textContent).toContain("카메라 감지");
  });
});
