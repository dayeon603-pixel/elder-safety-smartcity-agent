import { describe, expect, it } from "vitest";
import { assessAll, clampSpeed, shortestFirst } from "./crossing";
import type { CrosswalkRow } from "./crossing";

const row = (id: string, lengthM: number, greenSec: number, address = id): CrosswalkRow => ({ id, lat: 37, lon: 127, lengthM, greenSec, address });

describe("assessAll", () => {
  it("flags a crossing that needs more green than it has and rounds the extension up", () => {
    const [v] = assessAll([row("a", 20, 25)], 0.7); // 28.57 s needed
    expect(v?.short).toBe(true);
    expect(v?.extensionSec).toBe(4);
    expect(v?.requiredSec).toBeCloseTo(28.571, 2);
  });
  it("exactly enough green is not short", () => expect(assessAll([row("a", 14, 20)], 0.7)[0]).toMatchObject({ short: false, extensionSec: 0 }));
  it("a faster walker needs fewer crossings extended", () => {
    const rows = [row("a", 20, 25), row("b", 30, 30)];
    expect(assessAll(rows, 0.7).filter((v) => v.short)).toHaveLength(2);
    expect(assessAll(rows, 1.2).filter((v) => v.short)).toHaveLength(0);
  });
  it("skips invalid rows instead of counting them as fine", () => {
    expect(assessAll([row("a", 0, 20), row("b", 10, -1), row("c", Number.NaN, 5)], 0.7)).toEqual([]);
  });
});

describe("shortestFirst", () => {
  it("orders by extension then address, short only", () => {
    const v = assessAll([row("a", 20, 25, "나"), row("b", 20, 25, "가"), row("c", 40, 20, "다"), row("d", 5, 30)], 0.7);
    expect(shortestFirst(v).map((x) => x.id)).toEqual(["c", "b", "a"]);
  });
});

describe("clampSpeed", () => {
  it("clamps to the slider range and survives NaN", () => {
    expect(clampSpeed(5)).toBe(1.2);
    expect(clampSpeed(0.1)).toBe(0.5);
    expect(clampSpeed(Number.NaN)).toBe(0.5);
  });
});

describe("implausible crosswalk length", () => {
  it("flags a length above 100 m as a data error, never as +N seconds", async () => {
    const { assessAll } = await import("./crossing");
    const [v] = assessAll([{ id: "x", lat: 0, lon: 0, lengthM: 402, greenSec: 20, address: "a" }], 0.7);
    expect(v?.dataCheck).toBe(true);
    expect(v?.short).toBe(false);
    expect(v?.extensionSec).toBe(0);
  });
});
