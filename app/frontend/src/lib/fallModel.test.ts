import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { gateOpen, initialAi, initialRule, parseFallModel, predictProb, stepAi, stepRule, torsoAngle, windowFeatures } from "./fallModel";
import type { Frame } from "./fallModel";

const model = parseFallModel(JSON.parse(readFileSync("public/models/fall_model.json", "utf8")));
const vec = JSON.parse(readFileSync("public/models/fall_model_testvec.json", "utf8")) as {
  tolerance: number;
  windows: { name: string; aspect: number; ts: number[]; xy: Frame[]; expected_features: number[]; expected_prob: number }[];
  rule: { seq: string; aspect: number; ts: number[]; xy: Frame[]; expected_alarm_times: number[] };
  stream_cases: { name: string; aspect: number; ts: number[]; xy: Frame[]; last_window_features: number[]; last_window_prob: number; last_window_gate_open: boolean; expected_alarm_times_gated: number[]; expected_alarm_times_ungated: number[] }[];
};

describe("fall model port reproduces the Python reference", () => {
  for (const w of vec.windows) {
    it(`${w.name}: features and probability within ${vec.tolerance}`, () => {
      const f = windowFeatures(model, w.ts, w.xy, w.aspect);
      expect(f).not.toBeNull();
      (f as number[]).forEach((v, i) => expect(Math.abs(v - (w.expected_features[i] as number)), model.features[i]).toBeLessThanOrEqual(vec.tolerance));
      expect(Math.abs(predictProb(model, f as number[]) - w.expected_prob)).toBeLessThanOrEqual(vec.tolerance);
    });
    it(`${w.name}: the streaming detector reaches the same probability on its last frame`, () => {
      let s = initialAi();
      let prob: number | null = null;
      w.ts.forEach((t, i) => {
        const r = stepAi(model, s, t, w.xy[i] as Frame, w.aspect);
        s = r.state;
        prob = r.prob;
      });
      expect(Math.abs((prob as unknown as number) - w.expected_prob)).toBeLessThanOrEqual(vec.tolerance);
    });
  }
  it("rule baseline reproduces the reference alarm time", () => {
    let s = initialRule();
    const alarms: number[] = [];
    vec.rule.ts.forEach((t, i) => {
      const r = stepRule(s, t, torsoAngle(vec.rule.xy[i] as Frame, vec.rule.aspect));
      s = r.state;
      if (r.fired) alarms.push(t);
    });
    expect(alarms).toHaveLength(vec.rule.expected_alarm_times.length);
    alarms.forEach((a, i) => expect(a).toBeCloseTo(vec.rule.expected_alarm_times[i] as number, 9));
  });
  it("the model alarms on the same fall sequence no later than the rule", () => {
    let s = initialAi();
    let first: number | null = null;
    vec.rule.ts.forEach((t, i) => {
      const r = stepAi(model, s, t, vec.rule.xy[i] as Frame, vec.rule.aspect);
      s = r.state;
      if (r.fired && first === null) first = t;
    });
    expect(first).not.toBeNull();
    expect(first as unknown as number).toBeLessThanOrEqual((vec.rule.expected_alarm_times[0] as number));
  });
});

describe("descent gate (lying still must not alarm)", () => {
  const ungated = { ...model, descent_gate: null };
  const run = (m: typeof model, c: (typeof vec.stream_cases)[number]): number[] => {
    let s = initialAi();
    const out: number[] = [];
    c.ts.forEach((t, i) => {
      const r = stepAi(m, s, t, c.xy[i] as Frame, c.aspect);
      s = r.state;
      if (r.fired) out.push(t);
    });
    return out;
  };
  it("model file carries the gate", () => expect(model.descent_gate).toEqual({ feature: "hip_drop_L", min: 0.1 }));
  for (const c of vec.stream_cases) {
    it(`${c.name}: gated and ungated alarm times match the Python reference`, () => {
      const g = run(model, c);
      const u = run(ungated, c);
      expect(g).toHaveLength(c.expected_alarm_times_gated.length);
      g.forEach((a, i) => expect(a).toBeCloseTo(c.expected_alarm_times_gated[i] as number, 9));
      expect(u).toHaveLength(c.expected_alarm_times_ungated.length);
      u.forEach((a, i) => expect(a).toBeCloseTo(c.expected_alarm_times_ungated[i] as number, 9));
    });
    it(`${c.name}: last-window probability and gate state within tolerance`, () => {
      const ts = c.ts.filter((t) => t >= (c.ts[c.ts.length - 1] as number) - model.window_s - 1e-9);
      const xy = c.xy.slice(c.ts.length - ts.length);
      const f = windowFeatures(model, ts, xy, c.aspect) as number[];
      expect(Math.abs(predictProb(model, f) - c.last_window_prob)).toBeLessThanOrEqual(vec.tolerance);
      expect(gateOpen(model, f)).toBe(c.last_window_gate_open);
    });
  }
  it("the lying-still case alarms without the gate and not with it", () => {
    const c = vec.stream_cases[0] as (typeof vec.stream_cases)[number];
    expect(run(ungated, c).length).toBeGreaterThan(0);
    expect(run(model, c)).toEqual([]);
  });
  it("rejects a malformed gate", () => {
    expect(() => parseFallModel({ ...JSON.parse(readFileSync("public/models/fall_model.json", "utf8")), descent_gate: { feature: "nope", min: 1 } })).toThrow();
  });
});

describe("fall model edge cases", () => {
  it("rejects malformed model files", () => {
    expect(() => parseFallModel(null)).toThrow();
    expect(() => parseFallModel({ features: [1, 2], mean: [1] })).toThrow();
  });
  it("short windows give null; missing detections carry the last frame forward; nothing seen yet gives null", () => {
    const f = vec.windows[0]?.xy[0] as Frame;
    expect(windowFeatures(model, [0, 0.1], [f, f], 1)).toBeNull();
    expect(stepAi(model, initialAi(), 0, null, 1).prob).toBeNull();
    const a = stepAi(model, initialAi(), 0, f, 1);
    const b = stepAi(model, a.state, 0.1, null, 1);
    expect(b.state.frames).toHaveLength(2);
    expect(b.state.frames[1]).toBe(f);
  });
  it("refractory: a second alarm inside refractory_s is suppressed", () => {
    const w = vec.rule;
    let s = initialAi();
    const times: number[] = [];
    for (let rep = 0; rep < 2; rep++) w.ts.forEach((t, i) => {
      const r = stepAi(model, s, t + rep * (w.ts[w.ts.length - 1] as number + 0.05), w.xy[i] as Frame, w.aspect);
      s = r.state;
      if (r.fired) times.push(t + rep * (w.ts[w.ts.length - 1] as number + 0.05));
    });
    for (let i = 1; i < times.length; i++) expect((times[i] as number) - (times[i - 1] as number)).toBeGreaterThanOrEqual(model.refractory_s);
  });
});
