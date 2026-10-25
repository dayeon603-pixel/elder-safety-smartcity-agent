/**
 * Edge audit of the field camera pipeline with the SHIPPED fall model (public/models/fall_model.json):
 * synthetic pose streams for the human situations the brief lists (person lying down on purpose, sitting on a
 * bench, wheelchair user, child, occlusion then re-entry, tilted camera, nobody in frame, 4+ people) plus the
 * tracker and the emergency workflow without data. Scenario expectations marked "documents" record current
 * behaviour so a change is noticed; they are not claims that the behaviour is right (see EDGE_CASES.md).
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { FIELD_MAX_PERSONS, TRACK_LOST_MS } from "../config";
import { initialAi, initialRule, parseFallModel, stepAi, stepRule, torsoAngle, windowFeatures } from "./fallModel";
import type { AiState, Frame } from "./fallModel";
import { runEmergency } from "./fieldEmergency";
import type { Crosswalk } from "./field";
import { initialTracker, trackStep } from "./tracker";
import type { Box, TrackerState } from "./tracker";

const model = parseFallModel(JSON.parse(readFileSync("public/models/fall_model.json", "utf8")));
const ASPECT = 4 / 3;
const FPS = 30;
/** Measured values go to the test log (audit evidence), without tripping the no-console rule. */
const note = (text: string): void => {
  process.stdout.write(`${text}\n`);
};

interface Body {
  /** hip midpoint, normalised image coordinates */
  hipX: number;
  hipY: number;
  /** torso angle from vertical in degrees (0 upright, 90 horizontal to the right) */
  angleDeg: number;
  /** torso length as a fraction of image height */
  torso: number;
  /** leg length below the hips (fraction of image height); 0 for a seated/wheelchair silhouette */
  legs: number;
  scale?: number;
}

/** 33 landmarks from a few body parameters. Points not used by the features are placed along the torso. */
function frame(b: Body): Frame {
  const s = b.scale ?? 1;
  const t = b.torso * s;
  const rad = (b.angleDeg * Math.PI) / 180;
  // shoulder is `torso` away from the hip, rotated by angle; in image coords y points down and x is divided by aspect
  const sx = b.hipX + (t * Math.sin(rad)) / ASPECT;
  const sy = b.hipY - t * Math.cos(rad);
  const nose: [number, number] = [sx + (0.25 * t * Math.sin(rad)) / ASPECT, sy - 0.25 * t * Math.cos(rad)];
  const legEnd: [number, number] = [b.hipX - (b.legs * s * Math.sin(rad)) / ASPECT, b.hipY + b.legs * s * Math.cos(rad)];
  const pts: [number, number][] = [];
  for (let i = 0; i < 33; i++) {
    const u = i / 32;
    pts.push([sx + (b.hipX - sx) * u, sy + (b.hipY - sy) * u]);
  }
  pts[0] = nose;
  pts[11] = [sx - (0.06 * s) / ASPECT, sy];
  pts[12] = [sx + (0.06 * s) / ASPECT, sy];
  pts[23] = [b.hipX - (0.05 * s) / ASPECT, b.hipY];
  pts[24] = [b.hipX + (0.05 * s) / ASPECT, b.hipY];
  pts[27] = legEnd;
  pts[28] = [legEnd[0] + (0.04 * s) / ASPECT, legEnd[1]];
  return pts;
}

function run(bodies: (Body | null)[], dt = 1 / FPS): { probs: (number | null)[]; fired: number[]; ruleFired: number[]; state: AiState } {
  let s = initialAi();
  let r = initialRule();
  const probs: (number | null)[] = [];
  const fired: number[] = [];
  const ruleFired: number[] = [];
  bodies.forEach((b, i) => {
    const t = i * dt;
    const f = b === null ? null : frame(b);
    const step = stepAi(model, s, t, f, ASPECT);
    s = step.state;
    probs.push(step.prob);
    if (step.fired) fired.push(t);
    if (f !== null) {
      const rr = stepRule(r, t, torsoAngle(f, ASPECT));
      r = rr.state;
      if (rr.fired) ruleFired.push(t);
    }
  });
  return { probs, fired, ruleFired, state: s };
}

const STAND: Body = { hipX: 0.5, hipY: 0.55, angleDeg: 0, torso: 0.25, legs: 0.3 };
const seconds = (n: number): number => Math.round(n * FPS);

describe("fall model on synthetic situations", () => {
  it("standing still for 3 s never fires and the probability stays below the threshold", () => {
    const { probs, fired, ruleFired } = run(Array(seconds(3)).fill(STAND));
    expect(fired).toEqual([]);
    expect(ruleFired).toEqual([]);
    const p = probs.filter((x): x is number => x !== null);
    expect(p.length).toBeGreaterThan(0);
    expect(Math.max(...p)).toBeLessThan(model.threshold);
  });

  it("a real fall (upright -> horizontal within 0.6 s, hips drop) fires the model before the 1.5 s rule", () => {
    const seq: Body[] = [];
    for (let i = 0; i < seconds(1.5); i++) seq.push(STAND);
    const n = seconds(0.6);
    for (let i = 0; i < n; i++) {
      const u = (i + 1) / n;
      seq.push({ hipX: 0.5 + 0.1 * u, hipY: 0.55 + 0.3 * u, angleDeg: 85 * u, torso: 0.25, legs: 0.3 });
    }
    for (let i = 0; i < seconds(2.5); i++) seq.push({ hipX: 0.6, hipY: 0.85, angleDeg: 85, torso: 0.25, legs: 0.3 });
    const { fired, ruleFired } = run(seq);
    expect(fired.length).toBe(1);
    expect(ruleFired.length).toBe(1);
    expect(fired[0] as number).toBeLessThan(ruleFired[0] as number);
  });

  it("someone lying down on purpose from the first frame (static, horizontal): documents whether the model fires", () => {
    const lying: Body = { hipX: 0.5, hipY: 0.8, angleDeg: 90, torso: 0.25, legs: 0.3 };
    const { probs, fired, ruleFired } = run(Array(seconds(4)).fill(lying));
    const p = probs.filter((x): x is number => x !== null);
    // The rule baseline fires on any horizontal torso held 1.5 s: lying on a bench is indistinguishable from a fall.
    expect(ruleFired.length).toBe(1);
    // The model sees no descent (hip_vy, hip_drop, ang_vel all 0) but a wide silhouette; record the outcome.
    const maxP = Math.max(...p);
    note(`[edge] static lying from frame 0: max prob ${maxP.toFixed(3)} (threshold ${model.threshold}), fired=${fired.length}`);
    expect(Number.isFinite(maxP)).toBe(true);
    expect(fired.length).toBeLessThanOrEqual(1); // never more than one alarm per refractory window
  });

  it("sitting down on a bench (slow, 2 s, torso stays near upright) does not fire", () => {
    const seq: Body[] = [];
    for (let i = 0; i < seconds(1.5); i++) seq.push(STAND);
    const n = seconds(2);
    for (let i = 0; i < n; i++) {
      const u = (i + 1) / n;
      seq.push({ hipX: 0.5, hipY: 0.55 + 0.15 * u, angleDeg: 15 * u, torso: 0.25, legs: 0.3 - 0.15 * u });
    }
    for (let i = 0; i < seconds(2); i++) seq.push({ hipX: 0.5, hipY: 0.7, angleDeg: 15, torso: 0.25, legs: 0.15 });
    const { fired, ruleFired, probs } = run(seq);
    expect(ruleFired).toEqual([]);
    const p = probs.filter((x): x is number => x !== null);
    note(`[edge] sitting down: max prob ${Math.max(...p).toFixed(3)}, fired=${fired.length}`);
    expect(fired).toEqual([]);
  });

  it("a wheelchair user rolling across (upright torso, no legs visible, 1 m/s sideways) does not fire", () => {
    const seq: Body[] = [];
    for (let i = 0; i < seconds(3); i++) seq.push({ hipX: 0.2 + 0.2 * (i / FPS), hipY: 0.65, angleDeg: 5, torso: 0.22, legs: 0 });
    const { fired, ruleFired, probs } = run(seq);
    expect(ruleFired).toEqual([]);
    const p = probs.filter((x): x is number => x !== null);
    note(`[edge] wheelchair: max prob ${Math.max(...p).toFixed(3)}`);
    expect(fired).toEqual([]);
  });

  it("a walker/cane user leaning 35 degrees forward while walking slowly does not fire", () => {
    const seq: Body[] = [];
    for (let i = 0; i < seconds(3); i++) seq.push({ hipX: 0.3 + 0.08 * (i / FPS), hipY: 0.6, angleDeg: 35, torso: 0.22, legs: 0.25 });
    const { fired, ruleFired, probs } = run(seq);
    expect(ruleFired).toEqual([]);
    const p = probs.filter((x): x is number => x !== null);
    note(`[edge] walker lean 35°: max prob ${Math.max(...p).toFixed(3)}`);
    expect(fired).toEqual([]);
  });

  it("a child (half scale, same proportions) gives the same features as an adult: torso-length normalisation", () => {
    const adult = Array(seconds(1.2)).fill(STAND) as Body[];
    const child = adult.map((b) => ({ ...b, scale: 0.5 }));
    const ts = adult.map((_, i) => i / FPS);
    const fa = windowFeatures(model, ts, adult.map(frame), ASPECT) as number[];
    const fc = windowFeatures(model, ts, child.map(frame), ASPECT) as number[];
    fa.forEach((v, i) => expect(Math.abs(v - (fc[i] as number)), model.features[i]).toBeLessThan(1e-9));
  });

  it("a tilted camera (person appears 30 degrees off vertical) stays well below both triggers while standing", () => {
    const tilted: Body = { ...STAND, angleDeg: 30 };
    const { fired, ruleFired, probs } = run(Array(seconds(3)).fill(tilted));
    expect(fired).toEqual([]);
    expect(ruleFired).toEqual([]);
    const p = probs.filter((x): x is number => x !== null);
    expect(Math.max(...p)).toBeLessThan(model.threshold);
  });

  it("nobody in frame: no probability, no alarm, state stays empty", () => {
    const { probs, fired, state } = run(Array(seconds(2)).fill(null));
    expect(probs.every((p) => p === null)).toBe(true);
    expect(fired).toEqual([]);
    expect(state.frames).toHaveLength(0);
  });

  it("occlusion for 1.5 s then re-entry lying down: documents whether the carried-forward frames create a false fall", () => {
    const seq: (Body | null)[] = [];
    for (let i = 0; i < seconds(1.5); i++) seq.push(STAND);
    for (let i = 0; i < seconds(1.5); i++) seq.push(null); // detector loses the person; last good frame is carried forward
    const lying: Body = { hipX: 0.5, hipY: 0.85, angleDeg: 88, torso: 0.25, legs: 0.3 };
    for (let i = 0; i < seconds(2); i++) seq.push(lying);
    const { fired, probs } = run(seq);
    const p = probs.filter((x): x is number => x !== null);
    note(`[edge] occlusion then lying: max prob ${Math.max(...p).toFixed(3)}, fired at ${JSON.stringify(fired)}`);
    // Either outcome is recorded; the invariant is that at most one alarm fires and it fires only after re-entry.
    expect(fired.length).toBeLessThanOrEqual(1);
    if (fired.length === 1) expect(fired[0] as number).toBeGreaterThanOrEqual(3.0 - 1e-9);
  });

  it("a partial detection (only a few landmarks, the rest NaN) yields no feature and no alarm", () => {
    const f = frame(STAND).map((pt, i) => (i === 0 || i === 11 || i === 12 ? pt : ([Number.NaN, Number.NaN] as const)));
    const ts = Array.from({ length: seconds(1.2) }, (_, i) => i / FPS);
    expect(windowFeatures(model, ts, ts.map(() => f), ASPECT)).toBeNull();
  });

  it("fewer than 33 landmarks does not crash: missing indices read as NaN and the window is rejected", () => {
    const short: Frame = frame(STAND).slice(0, 20);
    const ts = Array.from({ length: seconds(1.2) }, (_, i) => i / FPS);
    expect(windowFeatures(model, ts, ts.map(() => short), ASPECT)).toBeNull();
  });

  it("a frozen camera (identical timestamps) does not divide by zero", () => {
    const ts = Array(10).fill(0.5) as number[];
    expect(windowFeatures(model, ts, ts.map(() => frame(STAND)), ASPECT)).toBeNull(); // span 0 < min_span
  });
});

describe("tracker", () => {
  const box = (x: number, y: number, w = 0.1, h = 0.3): Box => ({ x0: x, y0: y, x1: x + w, y1: y + h });

  it("the pose model is capped at FIELD_MAX_PERSONS; the tracker itself accepts more detections without a cap", () => {
    expect(FIELD_MAX_PERSONS).toBe(4);
    const dets = [0.05, 0.25, 0.45, 0.65, 0.85, 0.95].map((x) => box(x, 0.3, 0.04));
    const s = trackStep(initialTracker(), dets, 0);
    expect(new Set(s.ids).size).toBe(6); // the 5th and 6th person would simply never be detected upstream (documented)
  });

  it("a person occluded for less than TRACK_LOST_MS keeps the id; longer than that gets a new id on re-entry", () => {
    let s: TrackerState = initialTracker();
    s = trackStep(s, [box(0.4, 0.3)], 0).state;
    s = trackStep(s, [], TRACK_LOST_MS - 100).state;
    const back = trackStep(s, [box(0.41, 0.3)], TRACK_LOST_MS - 50);
    expect(back.ids).toEqual([1]);
    s = trackStep(back.state, [], TRACK_LOST_MS * 2).state;
    expect(s.tracks).toHaveLength(0);
    const later = trackStep(s, [box(0.41, 0.3)], TRACK_LOST_MS * 2 + 10);
    expect(later.ids).toEqual([2]); // new id: a second alert for the same person is possible after a long occlusion
  });

  it("two people crossing paths: ids may swap when boxes overlap (documented limitation)", () => {
    let s: TrackerState = initialTracker();
    const first = trackStep(s, [box(0.2, 0.3), box(0.7, 0.3)], 0);
    s = first.state;
    expect(first.ids).toEqual([1, 2]);
    // both move to the middle and overlap heavily
    const mid = trackStep(s, [box(0.45, 0.3), box(0.46, 0.3)], 33);
    s = mid.state;
    // then separate on opposite sides
    const after = trackStep(s, [box(0.7, 0.3), box(0.2, 0.3)], 66);
    expect(new Set(after.ids).size).toBe(2);
    note(`[edge] crossing ids after swap step: ${JSON.stringify(after.ids)} (1->right, 2->left means a swap)`);
  });

  it("an abrupt box change (a fall) keeps the id through the centroid fallback", () => {
    let s: TrackerState = initialTracker();
    s = trackStep(s, [box(0.4, 0.2, 0.1, 0.5)], 0).state;
    const fallen = trackStep(s, [box(0.3, 0.6, 0.4, 0.1)], 33); // wide, low: IoU ~ 0 but centre moved < 0.3
    expect(fallen.ids).toEqual([1]);
  });
});

describe("emergency workflow without data", () => {
  const cw: Crosswalk = {
    id: "cw01", sido: "서울특별시", sigungu: "동작구", address: "서울특별시 동작구 상도동 320-21", lat: 37.4997, lon: 126.9319, greenSec: 27, redSec: 30,
    lengthM: 10.6, widthM: 5.9, lanes: 2, hasIsland: false,
    hotspot: { id: "h", name: "n", lat: 37.5, lon: 126.93, occurrences: 1, casualties: 1, deaths: 0, distanceM: 25 },
  };
  const base = {
    trigger: "button" as const, t0: 0, now: () => 5, at: new Date("2026-07-22T03:00:00Z"), crosswalk: cw,
    heat: { stage: "none" as const, tmaxC: null, date: null, source: "manual" as const, regionName: null, regionKm: null },
    trackId: null, uuid: () => "123e4567-e89b-12d3-a456-426614174000", torsoAngleDeg: null, estSpeedMps: null, muted: true,
    speakFn: () => "unsupported" as const,
  };
  it("AED file failed to load vs loaded but empty are worded differently; speech unsupported is stated", () => {
    const noFile = runEmergency({ ...base, aeds: null });
    expect(noFile.aedStatus).toBe("unavailable");
    expect(noFile.steps.find((s) => s.id === "aed")?.lines[0]).toContain("불러오지 못함");
    const empty = runEmergency({ ...base, aeds: [] });
    expect(empty.aedStatus).toBe("none_nearby");
    expect(empty.steps.find((s) => s.id === "aed")?.lines[0]).toContain("이내 기록 없음");
    expect(empty.speechText).toBe("근처에 쓰러진 분이 있습니다. 119에 신고해 주세요.");
    expect(empty.steps.find((s) => s.id === "speech")?.lines[1]).toContain("미지원");
    expect(empty.payload.nearest_aed).toBeNull();
    expect(empty.payload.sent).toBe(false);
  });
  it("manual heat input is labelled as such in every artefact and no temperature is invented", () => {
    const ev = runEmergency({ ...base, aeds: [] });
    expect(ev.steps.find((s) => s.id === "heat")?.lines[0]).toContain("직접 선택");
    expect(ev.incident.environment).toMatchObject({ tmax_c: null, source: "manual" });
    expect(ev.capXml).not.toMatch(/일 최고기온/);
    expect(JSON.stringify(ev.fhir)).not.toContain("NaN");
  });
  it("an AED 3.5 km away is reported as none nearby, not as the nearest", () => {
    const far = { org: "far", place: "", sido: "", gugun: "", address: "", lat: cw.lat + 0.032, lon: cw.lon };
    expect(runEmergency({ ...base, aeds: [far] }).aedStatus).toBe("none_nearby");
  });
});
