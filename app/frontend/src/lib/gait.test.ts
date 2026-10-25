import { describe, expect, it } from "vitest";
import { estimateSpeed, personStatus, pushSample } from "./gait";
import type { HipSample } from "./gait";

/** Synthetic hip track: constant speed in frame-widths per second, 15 fps. */
function track(fracPerSec: number, seconds: number): HipSample[] {
  let w: HipSample[] = [];
  for (let t = 0; t <= seconds * 1000; t += 66) w = pushSample(w, { tMs: t, x: 0.2 + (fracPerSec * t) / 1000 });
  return w;
}

describe("gait speed estimate", () => {
  it("scales with the calibrated field width", () => {
    expect(estimateSpeed(track(0.2, 2), 3).speedMps).toBeCloseTo(0.6, 1);
    expect(estimateSpeed(track(0.2, 2), 6).speedMps).toBeCloseTo(1.2, 1);
  });
  it("keeps only a 1 s window", () => {
    const w = track(0.1, 3);
    expect((w[w.length - 1]?.tMs ?? 0) - (w[0]?.tMs ?? 0)).toBeLessThanOrEqual(1000);
  });
  it("warms up, detects standing, and rejects jitter", () => {
    expect(estimateSpeed([{ tMs: 0, x: 0.5 }], 3).status).toBe("warming_up");
    expect(estimateSpeed(track(0.2, 0.3), 3).status).toBe("warming_up");
    expect(estimateSpeed(track(0.005, 2), 3).status).toBe("still");
    expect(estimateSpeed(track(2, 0.9), 3).status).toBe("implausible");
    expect(() => estimateSpeed(track(0.2, 2), 0)).toThrow(RangeError);
  });
  it("status chip: fall beats slow speed, slow speed beats normal", () => {
    expect(personStatus(true, 1, 0.5)).toBe("danger");
    expect(personStatus(false, 0.4, 0.5)).toBe("caution");
    expect(personStatus(false, 0.6, 0.5)).toBe("normal");
    expect(personStatus(false, null, 0.5)).toBe("normal");
  });
});

import { estimateGroundSpeed, pushGround } from "./gait";
import type { GroundSample } from "./gait";

describe("ground-plane speed", () => {
  it("uses metric floor distance in any direction", () => {
    let w: GroundSample[] = [];
    for (let t = 0; t <= 1000; t += 100) w = pushGround(w, { tMs: t, X: 0.6 * (t / 1000), Y: 0.8 * (t / 1000) });
    expect(estimateGroundSpeed(w).speedMps).toBeCloseTo(1.0, 6);
    expect(estimateGroundSpeed([]).status).toBe("warming_up");
  });
});
