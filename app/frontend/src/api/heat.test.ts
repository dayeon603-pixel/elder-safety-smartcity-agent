import { describe, expect, it } from "vitest";
import { ApiError } from "./client";
import { describeHeatError, HEAT_EVAL_COMMAND, heatQuery } from "./heat";
import { mockAlerts, mockDays, mockEval, mockRegion, mockRegions } from "./heatMocks";

describe("describeHeatError", () => {
  it("tells the user to run make heat-eval on 409 no_completed_run, whichever shape the detail has", () => {
    for (const detail of ["no_completed_run", '{"code":"no_completed_run"}']) {
      const msg = describeHeatError(new ApiError(409, detail));
      expect(msg).toContain(HEAT_EVAL_COMMAND);
      expect(msg).toContain("make heat-eval");
    }
  });
  it("treats any 409 as a missing run", () => {
    expect(describeHeatError(new ApiError(409, "conflict"))).toContain("make heat-eval");
  });
  it("separates a missing route from a missing district", () => {
    expect(describeHeatError(new ApiError(404, "Not Found"))).toContain("/heat");
    expect(describeHeatError(new ApiError(404, "region not found"))).toContain("시군구");
  });
  it("explains 422 as outside the test season", () => {
    expect(describeHeatError(new ApiError(422, "date outside season"))).toContain("시험 시즌");
  });
  it("falls back to the generic mapper (network error, 5xx)", () => {
    expect(describeHeatError(new TypeError("Failed to fetch"))).toContain("서버에 연결할 수 없습니다");
    expect(describeHeatError(new ApiError(500, "boom"))).toContain("서버에서 오류");
  });
});

describe("heatQuery", () => {
  it("encodes values and drops undefined", () => {
    expect(heatQuery({ date: "2025-07-23", variant: "F", year: undefined })).toBe("?date=2025-07-23&variant=F");
    expect(heatQuery({ a: "가 b" })).toBe("?a=%EA%B0%80%20b");
    expect(heatQuery({})).toBe("");
  });
});

describe("mock responses honour the real contract", () => {
  it("days are sorted by gap and never capture more than the day's patients", async () => {
    const days = await mockDays(2025, "F", "gap");
    const gaps = days.map((d) => d.ours_captured - d.b1_captured);
    expect(gaps).toEqual([...gaps].sort((a, b) => b - a));
    for (const d of days) {
      expect(d.b1_captured).toBeLessThanOrEqual(d.n_cases);
      expect(d.ours_captured).toBeLessThanOrEqual(d.n_cases);
    }
  });
  it("alerts use the budget K for both methods and only known region codes", async () => {
    const [days, regions] = await Promise.all([mockDays(2024, "F", "date"), mockRegions()]);
    const day = days.find((d) => d.k_budget > 0) ?? days[0];
    expect(day).toBeDefined();
    const a = await mockAlerts(day?.date ?? "", "F");
    const codes = new Set(regions.map((r) => r.code));
    expect(a.b1).toHaveLength(a.k);
    expect(a.ours).toHaveLength(a.k);
    for (const e of [...a.b1, ...a.ours]) expect(codes.has(e.code)).toBe(true);
  });
  it("eval intervals bracket the point and B1 has no delta", async () => {
    const rows = await mockEval("O");
    for (const r of rows.filter((x) => x.metric === "capture")) {
      expect(r.ci_lo ?? r.value).toBeLessThanOrEqual(r.value);
      expect(r.ci_hi ?? r.value).toBeGreaterThanOrEqual(r.value);
      if (r.method === "B1") expect(r.delta_vs_b1).toBeNull();
    }
  });
  it("eval snapshot carries the published pooled numbers (F: 42.2% -> 56.0%, +13.8%p)", async () => {
    const rows = await mockEval("F");
    const pooled = (m: string) => rows.find((r) => r.year === 0 && r.method === m && r.metric === "capture");
    expect(pooled("B1")?.value).toBeCloseTo(0.4222, 4);
    expect(pooled("OURS")?.value).toBeCloseTo(0.5603, 4);
    expect(pooled("OURS")?.delta_vs_b1?.value).toBeCloseTo(0.1381, 4);
    expect(pooled("OURS")?.delta_vs_b1?.ci_lo).toBeCloseTo(0.1177, 4);
    expect(pooled("B2")?.delta_vs_b1?.value).toBeCloseTo(0.1174, 4);
    const o = await mockEval("O");
    expect(o.find((r) => r.year === 0 && r.method === "OURS")?.delta_vs_b1?.value).toBeCloseTo(0.1463, 4);
  });
  it("representative day 2025-07-08 reproduces the real map: 259 patients, 13 districts only we catch", async () => {
    const a = await mockAlerts("2025-07-08", "F");
    expect(a.total_cases).toBe(259);
    const b1 = new Set(a.b1.map((e) => e.code));
    const both = a.ours.filter((e) => b1.has(e.code)).length;
    expect(a.b1).toHaveLength(a.k);
    expect(both).toBeGreaterThan(100);
    const onlyOurs = a.ours.filter((e) => !b1.has(e.code) && e.cases > 0);
    expect(onlyOurs).toHaveLength(13);
  });
  it("rejects dates outside the test season and unknown districts with ApiError", async () => {
    await expect(mockAlerts("2025-12-25", "F")).rejects.toMatchObject({ status: 422 });
    await expect(mockRegion("99999", "2025-07-23", "F")).rejects.toMatchObject({ status: 404 });
  });
});
