import { buildingRgb } from "./basemap/palette";
import { describe, expect, it } from "vitest";
import { daytypeOf, effectFor, haversineM, parseTwinState } from "./api";
import { pickCameraPlace, scoreboard } from "./place";
import { clock } from "./protocol";
import { buildScene } from "./sceneBuild";
import { dayOfYear, lightingFor, sunPosition } from "./sun";
import type { SceneWire, TwinState } from "./types";

const STYLE = { low: [0, 0, 0], high: [200, 100, 50], heightPercentile: 1 } as const;

/** closed ring (first == last), counter-clockwise */
const square = (x: number, y: number, s: number): number[] => [x, y, x + s, y, x + s, y + s, x, y + s, x, y];

describe("buildScene", () => {
  const wire: SceneWire = {
    center: [127, 37], bbox: [126, 36, 128, 38],
    buildings: {
      count: 2,
      positions: [...square(0, 0, 10), ...square(20, 0, 4)],
      start_indices: [0, 5, 10],
      heights: [9.6, 19.2],
      holes: { count: 1, positions: square(3, 3, 2), start_indices: [0, 5], parent: [0] },
    },
    boundary: { rings: 1, positions: [0, 0, 1, 0, 1, 1, 0, 0], start_indices: [0, 4] },
  };
  const s = buildScene(wire, STYLE);

  it("keeps every footprint, rings stay closed, hole appended to its parent", () => {
    expect(s.buildingCount).toBe(2);
    expect(s.vertexCount).toBe(15);
    expect(s.startIndices[1]).toBe(10);
    expect(s.startIndices[2]).toBe(s.vertexCount);
    expect(s.vertexValid.filter((v) => v === 0).length).toBe(3);
  });

  it("honours holes: triangulated area equals outer minus hole plus second building", () => {
    let area = 0;
    for (let i = 0; i < s.indices.length; i += 3) {
      const [a, b, c] = [s.indices[i], s.indices[i + 1], s.indices[i + 2]].map((j) => [s.positions[(j as number) * 2], s.positions[(j as number) * 2 + 1]] as [number, number]);
      area += Math.abs(((b![0] - a![0]) * (c![1] - a![1]) - (c![0] - a![0]) * (b![1] - a![1])) / 2);
    }
    expect(area).toBeCloseTo(100 - 4 + 16, 4);
  });

  it("normalises winding: outer ring clockwise", () => {
    let a = 0;
    for (let i = 0, j = 4; i < 5; j = i++) a += (s.positions[j * 2] as number) * (s.positions[i * 2 + 1] as number) - (s.positions[i * 2] as number) * (s.positions[j * 2 + 1] as number);
    expect(a).toBeLessThan(0);
  });

  it("per-vertex elevation equals the polygon height and colours span the ramp", () => {
    expect(s.elevations[0]).toBeCloseTo(9.6, 5);
    expect(s.elevations[s.vertexCount - 1]).toBeCloseTo(19.2, 5);
    expect(s.colors[(s.vertexCount - 1) * 4]).toBe(buildingRgb(null, 1)[0]); // colours now come from the basemap palette (use-based, neutral ramp without use)
  });

  it("drops degenerate polygons only", () => {
    const w: SceneWire = { ...wire, buildings: { count: 2, positions: [0, 0, 1, 1, 0, 0, ...square(5, 5, 2)], start_indices: [0, 3, 8], heights: [3, 3] } };
    expect(buildScene(w, STYLE).buildingCount).toBe(1);
  });

  it("rejects heights shorter than polygons", () => {
    expect(() => buildScene({ ...wire, buildings: { ...wire.buildings, heights: [1] } }, STYLE)).toThrow(RangeError);
  });
});

describe("buildScene scale", () => {
  it("handles 40k footprints and keeps all of them", () => {
    const n = 40_000;
    const positions: number[] = [];
    const starts: number[] = [0];
    for (let i = 0; i < n; i++) {
      positions.push(...square((i % 200) * 30, Math.floor(i / 200) * 30, 10));
      starts.push(positions.length / 2);
    }
    const wire: SceneWire = { center: [127, 37], bbox: [126, 36, 128, 38], buildings: { count: n, positions, start_indices: starts, heights: new Array<number>(n).fill(12.8) }, boundary: { rings: 1, positions: [0, 0, 1, 0, 1, 1, 0, 0], start_indices: [0, 4] } };
    const out = buildScene(wire, STYLE);
    expect(out.buildingCount).toBe(n);
    expect(out.vertexCount).toBe(n * 5);
    expect(out.indices.length).toBe(n * 6);
    expect(out.parseMs).toBeLessThan(5000);
  });
});

describe("sun", () => {
  const LAT = 37.47;
  const LON = 126.94;
  it("day of year", () => { expect(dayOfYear("2025-01-01")).toBe(1); expect(dayOfYear("2025-07-22")).toBe(203); });
  it("noon sun is south and high in July; midnight is below the horizon", () => {
    const noon = sunPosition("2025-07-22", 12.5, LAT, LON);
    expect(noon.azimuthDeg).toBeGreaterThan(150);
    expect(noon.azimuthDeg).toBeLessThan(210);
    expect(noon.elevationDeg).toBeGreaterThan(65);
    expect(sunPosition("2025-07-22", 0, LAT, LON).elevationDeg).toBeLessThan(0);
  });
  it("night is darker than day and rain greys the sky", () => {
    const day = lightingFor(sunPosition("2025-07-22", 13, LAT, LON), 0);
    const night = lightingFor(sunPosition("2025-07-22", 23, LAT, LON), 0);
    expect(night.day).toBeLessThan(0.05);
    expect(day.day).toBe(1);
    expect(night.ambientIntensity).toBeLessThan(day.ambientIntensity);
    expect(lightingFor(sunPosition("2025-07-22", 13, LAT, LON), 1).skyTop).not.toBe(day.skyTop);
  });
});

describe("api adapters", () => {
  it("daytype", () => { expect(daytypeOf("2025-07-22")).toBe("weekday"); expect(daytypeOf("2025-07-26")).toBe("sat"); expect(daytypeOf("2025-07-27")).toBe("sun"); expect(daytypeOf("2025-07-22", true)).toBe("holiday"); });
  it("haversine ~111 km per degree of latitude", () => { expect(haversineM([127, 37], [127, 38])).toBeGreaterThan(111_000); expect(haversineM([127, 37], [127, 38])).toBeLessThan(111_400); });
  it("parseTwinState reads contract fields and derives open count", () => {
    const st = parseTwinState({ aed: [{ id: "a", lat: 37, lon: 127, name: "A", open: true, open_after: true, recommended_24h: false }, { id: "b", lat: 37, lon: 127, name: "B", open: false, open_after: true, recommended_24h: true }], heat: { b1_alert: false, ours_alert: true }, weather: { rain_mm: 12 }, silver_hotspots: [{ lat: 37, lon: 127, inside_current: false, inside_ours: true }], silver_eval_seoul: { year: 2024, n_hotspots: 70, covered_current: 10, covered_ours: 60 } }, "2025-07-22", 23, "weekday");
    expect(st?.aedOpenCount).toBe(1);
    expect(st?.aedTotal).toBe(2);
    expect(st?.heat.oursAlert).toBe(true);
    expect(st?.weather?.rainMm).toBe(12);
    expect(st?.accidents[0]?.inOurs).toBe(true);
    expect(st?.silverEval?.coveredOurs).toBe(60);
    expect(st?.aedCandidates.length).toBe(1);
  });
  it("parseTwinState rejects non-objects", () => { expect(parseTwinState(null, "d", 1, "weekday")).toBeNull(); });
});

const base = (): TwinState => ({
  date: "d", hour: 1, daytype: "weekday", aed: [], aedCandidates: [], shelters: [], crosswalks: [], silverCurrent: [], silverOurs: [], silverRadiusM: 300, accidents: [], stops: [],
  heat: { apparentMaxC: null, tmaxC: null, b1Alert: null, oursAlert: null, rank: null, nCells: null, expectedCases: null, rankText: null, expectedCasesText: null },
  weather: null, aedOpenPct: null, aedOpenCount: null, aedTotal: null, sources: [], silverEval: null, aedOpenPctAfter: null, seoulZones: null,
});

describe("place and scoreboard", () => {
  it("camera sits at a crosswalk inside a silver zone", () => {
    const st = { ...base(), silverOurs: [{ lat: 37.5, lon: 127, name: null }], crosswalks: [
      { id: "far", lat: 37.6, lon: 127, address: "서울특별시 멀리", greenS: 1, needS: 1, short: false },
      { id: "near", lat: 37.5005, lon: 127, address: "서울특별시 가까이", greenS: 1, needS: 1, short: false },
    ] };
    const p = pickCameraPlace(st);
    expect(p?.crosswalkId).toBe("near");
    expect(p?.insideSilverZone).toBe(true);
    expect(p?.label).toBe("가까이");
  });
  it("falls back to the first crosswalk, and null when none", () => {
    expect(pickCameraPlace(base())).toBeNull();
    const st = { ...base(), crosswalks: [{ id: "x", lat: 37, lon: 127, address: "", greenS: null, needS: null, short: false }] };
    expect(pickCameraPlace(st)?.insideSilverZone).toBe(false);
  });
  it("scoreboard is hidden without the Seoul-wide result and shows it when given", () => {
    expect(scoreboard(base())).toBeNull();
    expect(scoreboard({ ...base(), silverEval: { year: 2024, n: 0, coveredCurrent: 0, coveredOurs: 0, source: null } })).toBeNull();
    expect(scoreboard({ ...base(), silverEval: { year: 2024, n: 5, coveredCurrent: 1, coveredOurs: 4, source: "x" } })).toMatchObject({ total: 5, current: 1, ours: 4, year: 2024 });
  });
});

describe("clock", () => { it("formats KST HH:MM:SS.mmm", () => { expect(clock(new Date("2025-07-22T05:03:09.007Z"))).toBe("14:03:09.007"); }); });

describe("effect chip lookup", () => {
  const eff = { "2": { ours: "60%", baseline: "49%", scope: "같은 경보 수", source: "2022-25" }, silver: { ours: "58곳", baseline: "9곳", scope: null, source: null } };
  it("matches by step number or alias, never invents a chip", () => {
    expect(effectFor(eff, 2, ["heat"])?.ours).toBe("60%");
    expect(effectFor(eff, 4, ["silver"])?.baseline).toBe("9곳");
    expect(effectFor(eff, 5, ["fall"])).toBeNull();
    expect(effectFor(null, 2, ["heat"])).toBeNull();
  });
});

describe("seoul zone caption fields", () => {
  it("parses seoul_zones_* only when both are present", () => {
    expect(parseTwinState({ silver_eval_seoul: { seoul_zones_current: 194, seoul_zones_ours: 1408 } }, "d", 1, "weekday")?.seoulZones).toEqual({ current: 194, ours: 1408 });
    expect(parseTwinState({ seoul_zones_current: 194 }, "d", 1, "weekday")?.seoulZones).toBeNull();
  });
});
