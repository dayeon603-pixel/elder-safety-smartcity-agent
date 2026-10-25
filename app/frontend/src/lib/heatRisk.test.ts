import { describe, expect, it } from "vitest";
import { CITIZEN_WATCH_MULTIPLE } from "../config";
import { RISK_STYLE, riskLevel } from "./heatRisk";

describe("riskLevel", () => {
  it("is high inside the alert budget, watch up to the multiple, low beyond", () => {
    expect(riskLevel(1, 10)).toBe("high");
    expect(riskLevel(10, 10)).toBe("high");
    expect(riskLevel(11, 10)).toBe("watch");
    expect(riskLevel(10 * CITIZEN_WATCH_MULTIPLE, 10)).toBe("watch");
    expect(riskLevel(10 * CITIZEN_WATCH_MULTIPLE + 1, 10)).toBe("low");
  });
  it("is low when nothing is alerted or the rank is not a number", () => {
    expect(riskLevel(1, 0)).toBe("low");
    expect(riskLevel(Number.NaN, 10)).toBe("low");
  });
  it("gives every level its own word and glyph, so colour is never the only cue", () => {
    const words = Object.values(RISK_STYLE).map((s) => s.word);
    const glyphs = Object.values(RISK_STYLE).map((s) => s.glyph);
    expect(new Set(words).size).toBe(3);
    expect(new Set(glyphs).size).toBe(3);
  });
});
