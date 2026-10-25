import { describe, expect, it } from "vitest";
import { fpsFrom, RollingWindow } from "./perfStats";

describe("perf stats", () => {
  it("p50/p95 over the last N samples only", () => {
    const w = new RollingWindow(100);
    for (let i = 1; i <= 150; i++) w.push(i);
    expect(w.count).toBe(100);
    expect(w.percentile(50)).toBeCloseTo(100.5);
    expect(w.percentile(95)).toBeCloseTo(145.05);
  });
  it("empty window gives null; bad p throws", () => {
    const w = new RollingWindow(5);
    expect(w.percentile(50)).toBeNull();
    w.push(1);
    expect(() => w.percentile(101)).toThrow(RangeError);
    expect(() => new RollingWindow(0)).toThrow(RangeError);
  });
  it("fps counts completions in the last second", () => {
    const stamps = [0, 100, 200, 1100, 1200];
    expect(fpsFrom(stamps, 1250)).toBe(2);
    expect(stamps).toHaveLength(2);
  });
});
