import { describe, expect, it } from "vitest";
import { applyH, groundRect, homographyFrom4, invertH, isConvexQuad, reprojectionRmsPx } from "./homography";
import type { Mat3, Pt } from "./homography";

/** Unit-test fixture only: a known ground->image projective map (perspective foreshortening). */
const TRUE_G2I: Mat3 = [80, 20, 100, -10, 40, 60, 0.02, 0.15, 1];

const rect = groundRect(2, 1);
const image = rect.map((g) => applyH(TRUE_G2I, g) as Pt);

describe("DLT homography", () => {
  it("recovers a known homography: interior points map to within 1e-6 m", () => {
    const h = homographyFrom4(image, rect) as Mat3;
    expect(h).not.toBeNull();
    for (const g of [{ x: 0.5, y: 0.5 }, { x: 1.7, y: 0.2 }, { x: 1, y: 1.3 }, { x: 3, y: 2 }]) {
      const px = applyH(TRUE_G2I, g) as Pt;
      const back = applyH(h, px) as Pt;
      expect(back.x).toBeCloseTo(g.x, 6);
      expect(back.y).toBeCloseTo(g.y, 6);
    }
  });
  it("measures a known 1.2 m walk as 1.2 m", () => {
    const h = homographyFrom4(image, rect) as Mat3;
    const a = applyH(h, applyH(TRUE_G2I, { x: 0.3, y: 0.4 }) as Pt) as Pt;
    const b = applyH(h, applyH(TRUE_G2I, { x: 1.5, y: 0.4 }) as Pt) as Pt;
    expect(Math.hypot(b.x - a.x, b.y - a.y)).toBeCloseTo(1.2, 6);
  });
  it("reprojection residual is ~0 for exact data and large for a perturbed click", () => {
    const h = homographyFrom4(image, rect) as Mat3;
    expect(reprojectionRmsPx(h, image, rect)).toBeLessThan(1e-6);
    const moved = image.map((p, i) => (i === 2 ? { x: p.x + 5, y: p.y } : p));
    expect(reprojectionRmsPx(h, moved, rect) as number).toBeGreaterThan(1);
  });
  it("inverse round-trips and degenerate input is rejected", () => {
    const h = homographyFrom4(image, rect) as Mat3;
    const inv = invertH(h) as Mat3;
    const p = applyH(inv, { x: 1, y: 0.5 }) as Pt;
    const q = applyH(h, p) as Pt;
    expect(q.x).toBeCloseTo(1, 8);
    const collinear: Pt[] = [{ x: 0, y: 0 }, { x: 1, y: 1 }, { x: 2, y: 2 }, { x: 3, y: 3 }];
    expect(homographyFrom4(collinear, rect)).toBeNull();
    expect(isConvexQuad(collinear)).toBe(false);
    expect(isConvexQuad(image)).toBe(true);
    expect(isConvexQuad([image[0] as Pt, image[2] as Pt, image[1] as Pt, image[3] as Pt])).toBe(false);
    expect(() => homographyFrom4(image.slice(0, 3), rect)).toThrow(RangeError);
    expect(() => groundRect(0, 1)).toThrow(RangeError);
  });
});
