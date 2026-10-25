import { describe, expect, it } from "vitest";
import { isSignificantGain, orderMetrics } from "./metrics";

describe("metrics", () => {
  it("orders primary first, then known order, unknown last", () => {
    expect(orderMetrics(["f1", "zzz", "auroc", "accuracy"])).toEqual(["auroc", "accuracy", "f1", "zzz"]);
  });
  it("direction-aware significance", () => {
    expect(isSignificantGain({ lo: 0.01, hi: 0.1 }, "auroc")).toBe(true);
    expect(isSignificantGain({ lo: -0.01, hi: 0.1 }, "auroc")).toBe(false);
    expect(isSignificantGain({ lo: -0.09, hi: -0.01 }, "brier")).toBe(true);
    expect(isSignificantGain({ lo: -0.09, hi: 0.01 }, "brier")).toBe(false);
    expect(isSignificantGain(undefined, "auroc")).toBe(false);
  });
});
