import { describe, expect, it } from "vitest";
import { AlertTone, TONE_PATTERNS, patternDurationMs } from "./tone";

describe("tone", () => {
  it("every class has a short, non-empty pattern", () => {
    for (const [cls, p] of Object.entries(TONE_PATTERNS)) {
      expect(p.length).toBeGreaterThan(0);
      expect(patternDurationMs(cls as keyof typeof TONE_PATTERNS)).toBeLessThan(4000);
    }
  });
  it("resolves immediately when not unlocked (no autoplay)", async () => {
    await expect(new AlertTone().play("safety")).resolves.toBeUndefined();
  });
});
