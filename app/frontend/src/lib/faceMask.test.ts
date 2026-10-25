import { describe, expect, it } from "vitest";
import { faceBox, personBox } from "./faceMask";
import type { PointLike } from "./faceMask";

const lm = (n: number, f: (i: number) => PointLike): PointLike[] => Array.from({ length: n }, (_, i) => f(i));

describe("face box", () => {
  it("surrounds face landmarks (0-10) with margin and ignores body landmarks", () => {
    const pts = lm(33, (i) => (i <= 10 ? { x: 0.5 + (i % 3) * 0.01, y: 0.2 + (i % 4) * 0.01, visibility: 0.9 } : { x: 0.9, y: 0.9, visibility: 0.9 }));
    const b = faceBox(pts);
    expect(b).not.toBeNull();
    expect(b!.x0).toBeLessThan(0.5);
    expect(b!.x1).toBeGreaterThan(0.52);
    expect(b!.x1).toBeLessThan(0.7);
    expect(b!.y1 - b!.y0).toBeGreaterThanOrEqual(0.08 - 1e-9);
  });
  it("is null when no face landmark is visible, and clamps to the frame", () => {
    expect(faceBox(lm(33, () => ({ x: 0.5, y: 0.5, visibility: 0.1 })))).toBeNull();
    const edge = faceBox(lm(11, () => ({ x: 0.01, y: 0.01, visibility: 1 })));
    expect(edge!.x0).toBe(0);
    expect(edge!.y0).toBe(0);
  });
  it("personBox needs two visible points", () => {
    expect(personBox([{ x: 0.1, y: 0.1 }])).toBeNull();
    expect(personBox([{ x: 0.1, y: 0.2 }, { x: 0.4, y: 0.9 }])).toEqual({ x0: 0.1, y0: 0.2, x1: 0.4, y1: 0.9 });
  });
});
