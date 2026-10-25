import { describe, expect, it } from "vitest";
import type { HeatEvalRow } from "../api/heatTypes";
import { forestAxis } from "../components/heat/DeltaForest";
import { boundaryToPath, MAP_HEIGHT, MAP_WIDTH, project } from "./geo";
import { dayLabel, gapOf, sharePercent, sortByGap } from "./heatDays";
import { comparisonRows, decomposition, forestRows, formatPoints, heroPair, pick, POOLED_YEAR, summarizeGains, toInterval, yearLabel } from "./heatEval";

const row = (year: number, method: HeatEvalRow["method"], value: number, lo: number | null, hi: number | null, delta: HeatEvalRow["delta_vs_b1"] = null): HeatEvalRow => ({
  year, method, metric: "capture", value, ci_lo: lo, ci_hi: hi, delta_vs_b1: delta,
});

describe("eval joins", () => {
  const rows = [
    row(2024, "B1", 0.3, 0.25, 0.35),
    row(2024, "OURS", 0.4, 0.33, 0.47, { value: 0.1, ci_lo: 0.04, ci_hi: 0.16 }),
    row(2025, "B1", 0.3, null, null),
    row(2025, "OURS", 0.31, 0.2, 0.4, { value: 0.01, ci_lo: -0.05, ci_hi: 0.07 }),
    row(2023, "OURS", 0.5, 0.4, 0.6),
  ];
  it("pairs B1 (baseline) with ours per year and skips years missing a side", () => {
    const r = comparisonRows(rows, [2023, 2024, 2025]);
    expect(r.map((x) => x.key)).toEqual(["2024", "2025"]);
    expect(r[0]?.baseline.point).toBe(0.3);
  });
  it("collapses a missing CI to the point so charts still draw", () => {
    expect(toInterval({ value: 0.3, ci_lo: null, ci_hi: null })).toEqual({ point: 0.3, lo: 0.3, hi: 0.3 });
  });
  it("finds a row by year, method and metric", () => {
    expect(pick(rows, 2024, "OURS")?.value).toBe(0.4);
    expect(pick(rows, 2024, "OURS", "auc")).toBeUndefined();
  });
  it("marks significance only when the whole interval is above 0, and reports it plainly", () => {
    const f = forestRows({ F: rows, O: [row(2025, "OURS", 0.2, 0.1, 0.3, { value: -0.1, ci_lo: -0.2, ci_hi: -0.02 })] });
    expect(f.map((x) => [x.key, x.significant])).toEqual([["F-2024", true], ["F-2025", false], ["O-2025", false]]);
    const text = summarizeGains(f);
    expect(text[0]).toContain("F: 시험 연도 2개 중 1개");
    expect(text[0]).toContain("2024");
    expect(text[1]).toContain("2025년은 B1보다 낮습니다");
  });
  it("formats percentage points with a real minus sign", () => {
    expect(formatPoints(0.042)).toBe("+4.2%p");
    expect(formatPoints(-0.015)).toBe("−1.5%p");
    expect(formatPoints(0)).toBe("0.0%p");
    expect(formatPoints(Number.NaN)).toBe("–");
  });
});

describe("forestAxis", () => {
  it("always contains 0 and every interval, with ticks inside the range", () => {
    const a = forestAxis([{ lo: 0.02, hi: 0.1 }, { lo: -0.03, hi: 0.05 }]);
    expect(a.min).toBeLessThan(-0.03);
    expect(a.max).toBeGreaterThan(0.1);
    expect(a.ticks).toContain(0);
    for (const t of a.ticks) expect(t >= a.min && t <= a.max).toBe(true);
  });
  it("does not collapse for a single narrow interval", () => {
    const a = forestAxis([{ lo: 0.01, hi: 0.011 }]);
    expect(a.max - a.min).toBeGreaterThan(0.03);
  });
});

describe("days", () => {
  const d = (date: string, n: number, b: number, o: number) => ({ date, n_cases: n, k_budget: 3, b1_captured: b, ours_captured: o });
  it("sorts by gap, then patients, then date, without mutating", () => {
    const input = [d("2025-07-02", 10, 5, 5), d("2025-07-01", 20, 5, 15), d("2025-07-03", 30, 5, 15), d("2025-07-04", 10, 6, 5)];
    const out = sortByGap(input);
    expect(out.map((x) => x.date)).toEqual(["2025-07-03", "2025-07-01", "2025-07-02", "2025-07-04"]);
    expect(input[0]?.date).toBe("2025-07-02");
    expect(gapOf(input[3] ?? d("", 0, 0, 0))).toBe(-1);
  });
  it("labels weekdays from the calendar date, not the viewer's timezone", () => {
    expect(dayLabel("2025-07-23")).toBe("07-23 (수)");
    expect(dayLabel("garbage")).toBe("garbage");
  });
  it("returns null share when nobody was ill", () => {
    expect(sharePercent(0, 0)).toBeNull();
    expect(sharePercent(1, 3)).toBe(33);
  });
});

describe("geo", () => {
  it("projects Korea's corners inside the viewBox", () => {
    for (const [lon, lat] of [[126.1, 33.2], [129.6, 38.5], [124.7, 37.7]] as const) {
      const p = project(lon, lat);
      expect(p.x).toBeGreaterThan(0);
      expect(p.x).toBeLessThan(MAP_WIDTH);
      expect(p.y).toBeGreaterThan(0);
      expect(p.y).toBeLessThan(MAP_HEIGHT);
    }
    expect(project(126, 37).y).toBeLessThan(project(126, 34).y);
  });
  it("builds closed paths for Polygon and MultiPolygon and skips other geometry", () => {
    const ring = [[127, 37], [128, 37], [128, 36], [127, 37]];
    const d = boundaryToPath({
      features: [
        { geometry: { type: "Polygon", coordinates: [ring] } },
        { geometry: { type: "MultiPolygon", coordinates: [[ring], [ring]] } },
        { geometry: { type: "Point", coordinates: [127, 37] } },
        { geometry: { type: "Polygon", coordinates: "bad" } },
      ],
    });
    expect(d.match(/M/g)).toHaveLength(3);
    expect(d.match(/Z/g)).toHaveLength(3);
  });
});

describe("hero and decomposition", () => {
  const mk = (year: number, b1: number, b2: number, heur: number, ours: number): HeatEvalRow[] => [
    row(year, "B1", b1, b1 - 0.05, b1 + 0.05),
    row(year, "B2", b2, b2 - 0.05, b2 + 0.05, { value: b2 - b1, ci_lo: 0.09, ci_hi: 0.14 }),
    row(year, "HEUR", heur, heur - 0.05, heur + 0.05, { value: heur - b1, ci_lo: 0.1, ci_hi: 0.15 }),
    row(year, "OURS", ours, ours - 0.05, ours + 0.05, { value: ours - b1, ci_lo: 0.11, ci_hi: 0.16 }),
  ];
  const rows = [...mk(2025, 0.3, 0.4, 0.41, 0.42), ...mk(POOLED_YEAR, 0.4222, 0.5396, 0.5475, 0.5603)];

  it("prefers the pooled pair, falls back to the latest year, and is null without both sides", () => {
    expect(heroPair(rows)?.year).toBe(POOLED_YEAR);
    expect(heroPair(rows)?.delta?.point).toBeCloseTo(0.1381, 4);
    expect(heroPair(mk(2025, 0.3, 0.4, 0.41, 0.42))?.year).toBe(2025);
    expect(heroPair([row(2025, "B1", 0.3, 0.2, 0.4)])).toBeNull();
  });
  it("splits the total gain across B1 -> B2 -> HEUR -> OURS; shares add up to 100%", () => {
    const d = decomposition(rows, POOLED_YEAR);
    expect(d?.map((s) => s.method)).toEqual(["B1", "B2", "HEUR", "OURS"]);
    const shares = (d ?? []).flatMap((s) => (s.share === null ? [] : [s.share]));
    expect(shares.reduce((a, b) => a + b, 0)).toBeCloseTo(1, 10);
    expect(d?.[1]?.share).toBeGreaterThan(0.8);
  });
  it("returns null when a step is missing and no share when there is no gain", () => {
    expect(decomposition(rows.filter((r) => r.method !== "HEUR"), 2025)).toBeNull();
    expect(decomposition(mk(2025, 0.5, 0.5, 0.5, 0.4), 2025)?.[1]?.share).toBeNull();
  });
  it("labels the pooled year and keeps it out of the per-year summary", () => {
    expect(yearLabel(POOLED_YEAR)).toBe("전체 합산");
    expect(yearLabel(2024)).toBe("2024");
    const f = forestRows({ F: rows });
    expect(f.map((r) => r.key)).toEqual(["F-0", "F-2025"]);
    expect(summarizeGains(f)[0]).toContain("시험 연도 1개 중 1개");
  });
});
