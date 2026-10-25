import { describe, expect, it } from "vitest";
import { contrastRatio, KIND_STYLE } from "./present";

const AAA = 7;

describe("palette", () => {
  it("computes known ratios", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 0);
  });
  it.each(Object.entries(KIND_STYLE))("%s meets WCAG AAA for text and accent", (_k, s) => {
    expect(contrastRatio(s.fg, s.bg)).toBeGreaterThanOrEqual(AAA);
    expect(contrastRatio(s.accent, s.bg)).toBeGreaterThanOrEqual(AAA);
  });
});
