import { describe, expect, it } from "vitest";
import { initialTracker, iou, trackStep } from "./tracker";
import type { Box } from "./tracker";

const box = (x: number, y = 0.2, w = 0.15, h = 0.6): Box => ({ x0: x, y0: y, x1: x + w, y1: y + h });

describe("tracker", () => {
  it("keeps IDs stable while two people move slightly", () => {
    let s = initialTracker();
    let r = trackStep(s, [box(0.1), box(0.7)], 0);
    expect(r.ids).toEqual([1, 2]);
    s = r.state;
    r = trackStep(s, [box(0.72), box(0.12)], 100);
    expect(r.ids).toEqual([2, 1]);
  });
  it("gives new IDs to newcomers and drops tracks after the lost time", () => {
    let r = trackStep(initialTracker(), [box(0.1)], 0);
    r = trackStep(r.state, [box(0.1), box(0.6)], 100);
    expect(r.ids).toEqual([1, 2]);
    r = trackStep(r.state, [box(0.1)], 200);
    r = trackStep(r.state, [box(0.1)], 2000);
    expect(r.lost).toEqual([2]);
    expect(r.state.tracks.map((t) => t.id)).toEqual([1]);
  });
  it("keeps the ID through a fall via the centroid fallback (box shape changes, IoU small)", () => {
    let r = trackStep(initialTracker(), [{ x0: 0.4, y0: 0.2, x1: 0.55, y1: 0.8 }], 0);
    r = trackStep(r.state, [{ x0: 0.3, y0: 0.65, x1: 0.8, y1: 0.85 }], 100);
    expect(r.ids).toEqual([1]);
  });
  it("does not merge far-apart people and iou is symmetric", () => {
    expect(iou(box(0), box(0.8))).toBe(0);
    expect(iou(box(0), box(0.05))).toBeCloseTo(iou(box(0.05), box(0)));
    const r = trackStep(trackStep(initialTracker(), [box(0.05)], 0).state, [box(0.8)], 100);
    expect(r.ids).toEqual([2]);
  });
});
