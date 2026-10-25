import { describe, expect, it } from "vitest";
import type { BriefingResponse, SilverMapPoint, SilverMethodRow } from "../api/zpTypes";
import { candidatesFor, formatHours, hourData, isNightHour, rankGu } from "./zpAed";
import { clampTeams, isIsoDate, normalizeBriefing, toFraction } from "./zpBriefing";
import { boundsOf, hotspotRadius, makeFrame, mapCounts, HOT_R_MAX, HOT_R_MIN } from "./zpMap";
import { adaptAedGu, adaptAedHourly, adaptAedSummary, adaptSilverMap, adaptSilverSummary } from "./zpAdapt";
import { regionOptions, resolvePick } from "./zpRegions";
import { silverBars, silverHeadline } from "./zpSilver";

const region = (code: string, sido: string, name: string) => ({ code, sido, name, lat: 0, lon: 0, pop65: 1 });

const BASE: BriefingResponse = {
  region: { code: "1", sido: "서울특별시", name: "관악구" },
  heat: {
    rank: 3, alerted_b1: false, alerted_ours: true, cases_actual: 4,
    plan_allocation_for_region: { teams: 2, teams_total: 10 },
    nearby_open_shelters: { coverage: "data", items: [{ name: "A", dist_km: 1 }] },
  },
  silver: { current_zones_in_region: 10, our_picks_in_region: 32, hotspots_2024_in_region: 2, crosswalk_green_deficit_count: null },
  aed: { night_open_share: 33, day_open_share: 0.7, candidates_top5: [] },
  aed_note: null,
  actions: [{ module: "heat", text: "t", reason: "r" }],
};

describe("normalizeBriefing", () => {
  it("reads the backend shape and accepts percent-or-fraction shares", () => {
    const m = normalizeBriefing(BASE);
    expect(m.heat.teams).toBe(2);
    expect(m.heat.shelterCount).toBe(1);
    expect(m.heat.shelterData).toBe(true);
    expect(m.heat.alertClass).toBe("ours_only");
    expect(m.silver.hotspots).toBe(2);
    expect(m.silver.greenDeficit).toBeNull();
    expect(m.aed?.nightOpen).toBeCloseTo(0.33);
    expect(m.aed?.dayOpen).toBeCloseTo(0.7);
  });
  it("outside Seoul: aed null with its note, silver unavailable", () => {
    const m = normalizeBriefing({
      ...BASE, aed: null, aed_note: "서울만 분석",
      silver: { current_zones_in_region: null, our_picks_in_region: null, hotspots_2024_in_region: null, crosswalk_green_deficit_count: null, note: "서울 지도만 분석" },
    });
    expect(m.aed).toBeNull();
    expect(m.aedNote).toBe("서울만 분석");
    expect(m.silver.available).toBe(false);
  });
  it("unevaluated day: null rank and alerts are not read as 'no alert'", () => {
    const m = normalizeBriefing({ ...BASE, heat: { ...BASE.heat, rank: null, alerted_b1: null, alerted_ours: null, nearby_open_shelters: { coverage: "none", items: [] } } });
    expect(m.heat.rank).toBeNull();
    expect(m.heat.alertClass).toBe("unknown");
    expect(m.heat.shelterData).toBe(false);
  });
});

describe("inputs", () => {
  it("toFraction handles NaN, null, percent and clamps", () => {
    expect(toFraction(null)).toBeNull();
    expect(toFraction(Number.NaN)).toBeNull();
    expect(toFraction(49)).toBeCloseTo(0.49);
    expect(toFraction(1)).toBe(1);
    expect(toFraction(-0.2)).toBe(0);
  });
  it("isIsoDate rejects impossible dates", () => {
    expect(isIsoDate("2025-07-22")).toBe(true);
    expect(isIsoDate("2025-02-30")).toBe(false);
    expect(isIsoDate("2025-13-01")).toBe(false);
    expect(isIsoDate("")).toBe(false);
    expect(isIsoDate("22/07/2025")).toBe(false);
  });
  it("clampTeams", () => {
    expect(clampTeams(null, 1, 30, 10)).toBe(10);
    expect(clampTeams("abc", 1, 30, 10)).toBe(10);
    expect(clampTeams("0", 1, 30, 10)).toBe(1);
    expect(clampTeams("99", 1, 30, 10)).toBe(30);
    expect(clampTeams("12.9", 1, 30, 10)).toBe(12);
  });
});

describe("regions", () => {
  const rs = [region("2", "경상북도", "포항시남구"), region("1", "서울특별시", "관악구"), region("3", "서울특별시", "강북구")];
  it("puts Seoul first and sorts 시군구", () => {
    const o = regionOptions(rs, "서울특별시");
    expect(o.sidos[0]).toBe("서울특별시");
    expect(o.sigungu.map((r) => r.name)).toEqual(["강북구", "관악구"]);
  });
  it("defaults to 관악구, honours a valid code, ignores a bad one", () => {
    expect(resolvePick(rs, null)?.code).toBe("1");
    expect(resolvePick(rs, "2")?.sido).toBe("경상북도");
    expect(resolvePick(rs, "zzz")?.code).toBe("1");
    expect(resolvePick([], null)).toBeNull();
  });
});

describe("silver", () => {
  const rows: SilverMethodRow[] = [
    { method: "ours_full", coverage: 0.89, lo: 0.87, hi: 0.91 },
    { method: "current", coverage: 0.15, lo: 0.13, hi: 0.18 },
    { method: "random_k", coverage: 0.74, lo: 0.72, hi: 0.76 },
  ];
  it("orders baselines first and ours last, skipping absent methods", () => {
    expect(silverBars(rows).map((b) => b.method)).toEqual(["current", "random_k", "ours_full"]);
  });
  it("headline gain is in percentage points", () => {
    const h = silverHeadline({ n_hotspots: 1, casualties: 1, methods: rows });
    expect(h?.gainPp).toBeCloseTo(74);
    expect(silverHeadline({ n_hotspots: 0, casualties: 0, methods: [] })).toBeNull();
  });
});

describe("aed", () => {
  it("night window wraps midnight", () => {
    expect(isNightHour(23, 22, 6)).toBe(true);
    expect(isNightHour(5, 22, 6)).toBe(true);
    expect(isNightHour(6, 22, 6)).toBe(false);
    expect(isNightHour(12, 22, 6)).toBe(false);
  });
  it("hourData sorts, drops invalid hours and flags night", () => {
    const d = hourData([{ hour: 23, public_arrests: 1, pct_open: 50 }, { hour: 25, public_arrests: 1, pct_open: 1 }, { hour: 0, public_arrests: 2, pct_open: 49 }], 22, 6);
    expect(d.map((x) => x.hour)).toEqual([0, 23]);
    expect(d.every((x) => x.night)).toBe(true);
  });
  it("rankGu: worst night share first, stable ties", () => {
    const g = (gu: string, n: number, a: number) => ({ code: gu, gu, n_aed: 1, night_open_share: n, day_open_share: 1, night_arrests_per_year: a });
    expect(rankGu([g("나", 0.4, 1), g("가", 0.4, 5), g("다", 0.2, 0)]).map((x) => x.gu)).toEqual(["다", "가", "나"]);
  });
  it("candidatesFor filters and sorts by gain; formatHours tolerates junk", () => {
    const c = (gu: string, s: number) => ({ gu, org: "o", address: `${s}`, place: null, hours: null, night_open_share: 0, gain_score: s });
    expect(candidatesFor([c("a", 1), c("b", 9), c("a", 3)], "a").map((x) => x.gain_score)).toEqual([3, 1]);
    expect(formatHours("0900-1630")).toBe("09:00–16:30");
    expect(formatHours("nan-nan")).toBe("시간 정보 없음");
    expect(formatHours(null)).toBe("시간 정보 없음");
  });
});

describe("map", () => {
  const pts: SilverMapPoint[] = [
    { lat: 37.4, lon: 126.9, kind: "current", name: null, cas: null, cur_within: 1, ours_within: 0 },
    { lat: 37.5, lon: 127.0, kind: "hotspot_2024", name: "x", cas: 6, cur_within: 0, ours_within: 1 },
    { lat: 37.6, lon: 127.1, kind: "hotspot_2024", name: "y", cas: null, cur_within: 1, ours_within: 1 },
  ];
  it("counts coverage per scenario", () => {
    const c = mapCounts(pts);
    expect(c.hotspots).toBe(2);
    expect(c.covered).toEqual({ current: 1, ours: 2 });
  });
  it("frame keeps north up and all points inside", () => {
    const b = boundsOf(pts);
    expect(b).not.toBeNull();
    const f = makeFrame(b!);
    const a = f.project(126.9, 37.6);
    const z = f.project(126.9, 37.4);
    expect(a.y).toBeLessThan(z.y);
    for (const p of pts) {
      const q = f.project(p.lon, p.lat);
      expect(q.x).toBeGreaterThanOrEqual(0);
      expect(q.x).toBeLessThanOrEqual(f.width);
      expect(q.y).toBeLessThanOrEqual(f.height);
    }
    expect(boundsOf([])).toBeNull();
  });
  it("hotspot radius grows with casualties and is bounded", () => {
    expect(hotspotRadius(null)).toBe(HOT_R_MIN);
    expect(hotspotRadius(4)).toBeGreaterThan(hotspotRadius(1));
    expect(hotspotRadius(10_000)).toBe(HOT_R_MAX);
  });
});

describe("adapters (backend shape -> view model)", () => {
  it("silver summary: strata -> all/new, ci_lo -> lo, unknown methods dropped, note from the deltas", () => {
    const m = (method: string, c: number) => ({ method, label: method, coverage: c, ci_lo: c - 0.02, ci_hi: c + 0.02 });
    const out = adaptSilverSummary({
      k: 3918, variant: "harm", pool: "C", definitions: {},
      strata: [
        { stratum: "all", n_hot: 735, casualties: 4248, methods: [m("current", 0.15), m("oracle", 0.93), m("past_k", 0.926), m("ours_full", 0.89)], ours_minus: [{ vs: "C:past_k", label: "x", delta_pp: -3.46, lo_pp: -4.9, hi_pp: -2.1 }] },
        { stratum: "new", n_hot: 75, casualties: 386, methods: [m("current", 0.12), m("ours_full", 0.21)], ours_minus: [] },
      ],
    });
    expect(out.all.methods.map((x) => x.method)).toEqual(["current", "past_k", "ours_full"]);
    expect(out.all.methods[0]?.lo).toBeCloseTo(0.13);
    expect(out.new_sites.n_hotspots).toBe(75);
    expect(out.notes[0]?.text).toContain("-3.5%p");
  });
  it("silver map: three layers, coverage flags to 0/1, casualties only on hotspots", () => {
    const site = { lat: 37, lon: 127, name: "n", sgg_code: null, near_current: true, near_ours: false };
    const out = adaptSilverMap({ city: "seoul", k: 1, current: [site], ours: [site], hotspots_2024: [{ lat: 37, lon: 127, casualties: 5, sgg_code: null, covered_current: false, covered_ours: true }] });
    expect(out.points.map((p) => p.kind)).toEqual(["current", "ours", "hotspot_2024"]);
    expect(out.points[0]).toMatchObject({ cur_within: 1, ours_within: 0, cas: null });
    expect(out.points[2]).toMatchObject({ cur_within: 0, ours_within: 1, cas: 5 });
  });
  it("aed: fractions pass through, percent series are rescaled, hourly becomes percent", () => {
    const s = adaptAedSummary({ scope: "s", weekday: { day: 0.78, night: 0.49 }, public_arrests_total: 2534, night_share_of_public_arrests: { value: 0.176, ci_lo: 0.16, ci_hi: null, n: 447 } });
    expect(s.night_open_share).toBeCloseTo(0.49);
    expect(s.night_arrest_share.hi).toBeCloseTo(0.176);
    const p = adaptAedSummary({ scope: "s", weekday: { day: 78, night: 49 }, public_arrests_total: 1, night_share_of_public_arrests: { value: 17.6, ci_lo: null, ci_hi: null, n: null } });
    expect(p.night_open_share).toBeCloseTo(0.49);
    expect(adaptAedHourly([{ hour: 3, public_arrests: 4, open_share_weekday: 0.486, open_share_weekend: 0.5 }])[0]?.pct_open).toBeCloseTo(48.6);
    const g = adaptAedGu([{ gu: "관악구", sgg_code: null, night_open_share: 0.33, day_open_share: 0.7, gap_score: 1, public_arrests_night: 25.6, n_aed: 5 }]);
    expect(g[0]).toMatchObject({ code: "관악구", night_arrests_per_year: 25.6 });
  });
});
