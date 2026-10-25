import { describe, expect, it } from "vitest";
import type { HeatAlerts, HeatRegion } from "../api/heatTypes";
import {
  DOT_R_MAX, DOT_R_MIN, buildDots, classify, dotLabel, dotRadius, flagState, neighborInDirection, regionTitle, shortSido, summarizeDay,
} from "./heatMap";

const region = (code: string, name: string, sido = "서울특별시"): HeatRegion => ({ code, sido, name, lat: 37, lon: 127, pop65: 1000 });
const REGIONS = [region("11010", "종로구"), region("11020", "중구"), region("34010", "천안시", "충청남도"), region("39010", "제주시", "제주특별자치도")];

function alerts(over: Partial<HeatAlerts>): HeatAlerts {
  return { date: "2025-07-23", k: 2, total_cases: 0, b1: [], ours: [], cases: [], ...over };
}

describe("classify", () => {
  it("covers all four legend classes", () => {
    expect([classify(true, false), classify(false, true), classify(true, true), classify(false, false)]).toEqual(["b1_only", "ours_only", "both", "none"]);
  });
});

describe("buildDots", () => {
  const a = alerts({
    b1: [{ code: "11010", score: 34, cases: 0 }, { code: "11020", score: 33.5, cases: 2 }],
    ours: [{ code: "11020", score: 0.4, cases: 2 }, { code: "34010", score: 0.3, cases: 5 }],
    cases: [{ code: "11020", n: 2 }, { code: "34010", n: 5 }, { code: "39010", n: 1 }, { code: "11010", n: 0 }],
  });
  const { dots, unknownCodes } = buildDots(REGIONS, a);
  const by = (c: string) => dots.find((d) => d.code === c);

  it("joins alerts and patient counts by code and classifies each district", () => {
    expect(by("11010")?.cls).toBe("b1_only");
    expect(by("11020")?.cls).toBe("both");
    expect(by("34010")?.cls).toBe("ours_only");
    expect(by("39010")?.cls).toBe("none");
  });
  it("draws alerted districts with zero patients and skips untouched ones", () => {
    expect(by("11010")?.cases).toBe(0);
    expect(dots).toHaveLength(4);
  });
  it("draws biggest dots first so small ones stay on top", () => {
    expect(dots.map((d) => d.cases)).toEqual([5, 2, 1, 0]);
  });
  it("reports codes missing from the district list instead of drawing them", () => {
    const r = buildDots(REGIONS, alerts({ b1: [{ code: "99999", score: 1, cases: 1 }] }));
    expect(r.unknownCodes).toEqual(["99999"]);
    expect(r.dots).toHaveLength(0);
    expect(unknownCodes).toEqual([]);
  });
  it("falls back to the alert entry's cases when the cases list lacks the district", () => {
    const r = buildDots(REGIONS, alerts({ ours: [{ code: "34010", score: 1, cases: 3 }] }));
    expect(r.dots[0]?.cases).toBe(3);
  });
  it("summarises captured patients, blind-spot districts and patients missed by both", () => {
    const s = summarizeDay(dots);
    expect(s.b1Caught).toBe(2);
    expect(s.oursCaught).toBe(7);
    expect(s.missedByBoth).toBe(1);
    expect(s.onlyOurs.map((d) => d.code)).toEqual(["34010"]);
  });
  it("excludes ours-only districts with no patients from the blind-spot list", () => {
    const r = buildDots(REGIONS, alerts({ ours: [{ code: "34010", score: 1, cases: 0 }] }));
    expect(summarizeDay(r.dots).onlyOurs).toEqual([]);
  });
});

describe("dotRadius", () => {
  it("has a floor, grows with patients and is capped", () => {
    expect(dotRadius(0)).toBe(DOT_R_MIN);
    expect(dotRadius(-3)).toBe(DOT_R_MIN);
    expect(dotRadius(5)).toBeGreaterThan(dotRadius(1));
    expect(dotRadius(10_000)).toBe(DOT_R_MAX);
  });
});

describe("labels", () => {
  it("shortens province names and builds the accessible name", () => {
    expect(shortSido("서울특별시")).toBe("서울");
    expect(shortSido("충청남도")).toBe("충남");
    expect(shortSido("경기도")).toBe("경기");
    expect(shortSido("제주특별자치도")).toBe("제주");
    expect(regionTitle(region("1", "종로구"))).toBe("서울 종로구");
    const d = buildDots(REGIONS, alerts({ ours: [{ code: "11010", score: 1, cases: 3 }] })).dots[0];
    expect(d === undefined ? "" : dotLabel(d)).toBe("서울 종로구, 환자 3명, 우리만 경보");
  });
});

describe("neighborInDirection", () => {
  const pts = [
    { code: "c", x: 100, y: 100 },
    { code: "r", x: 140, y: 104 },
    { code: "far", x: 300, y: 100 },
    { code: "d", x: 100, y: 150 },
  ];
  it("picks the nearest dot in the pressed direction, or null at the edge", () => {
    expect(neighborInDirection(pts, "c", "ArrowRight")).toBe("r");
    expect(neighborInDirection(pts, "c", "ArrowDown")).toBe("d");
    expect(neighborInDirection(pts, "c", "ArrowLeft")).toBeNull();
    expect(neighborInDirection(pts, "c", "ArrowUp")).toBeNull();
    expect(neighborInDirection(pts, "nope", "ArrowUp")).toBeNull();
  });
});

describe("flagState", () => {
  it("keeps unknown distinct from closed", () => {
    expect([flagState(true), flagState(1), flagState(false), flagState(0), flagState(null), flagState(undefined)]).toEqual([
      "open", "open", "closed", "closed", "unknown", "unknown",
    ]);
  });
});
