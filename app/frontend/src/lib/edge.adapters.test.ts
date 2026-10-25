/**
 * Edge audit: adapters and formatters must never render "NaN", "undefined", "null" or an invented 0 when the API
 * sends nulls, empty lists, percent-vs-fraction ambiguity, or out-of-range numbers.
 */
import { describe, expect, it } from "vitest";
import type { BriefingResponse, RawAedCandidate, RawAedGu, RawAedHour, RawAedSummary, RawSilverSummary } from "../api/zpTypes";
import { assessAll, clampSpeed, shortestFirst } from "./app/crossing";
import { formatDecimal, formatInt, formatPercent, formatSigned } from "./format";
import { estimateGroundSpeed, estimateSpeed, personStatus } from "./gait";
import { sharePercent } from "./heatDays";
import { riskLevel } from "./heatRisk";
import { adaptAedCandidates, adaptAedGu, adaptAedHourly, adaptAedSummary, adaptSilverSummary } from "./zpAdapt";
import { formatHours, hourData, rankGu } from "./zpAed";
import { normalizeBriefing, toFraction } from "./zpBriefing";
import { evidenceFor } from "./zpEvidence";
import { silverHeadline } from "./zpSilver";

const BAD = /\b(NaN|undefined|null|Infinity)\b/;

function strings(v: unknown, out: string[] = []): string[] {
  if (typeof v === "string") out.push(v);
  else if (Array.isArray(v)) v.forEach((x) => strings(x, out));
  else if (typeof v === "object" && v !== null) Object.values(v).forEach((x) => strings(x, out));
  return out;
}

const nullBriefing: BriefingResponse = {
  date: "2024-07-04",
  teams: 10,
  region: { code: "47111", name: "포항시", sido: "경상북도", pop65: 200 },
  heat: {
    rank: null, evidence: {}, alerted_b1: null, alerted_ours: null, cases_actual: 0,
    plan_allocation_for_region: { teams: 0, teams_total: 10, expected_cases: null, reason: null },
    nearby_open_shelters: { coverage: "none", items: [] },
  },
  silver: {
    current_zones_in_region: null, our_picks_in_region: null, hotspots_2024_in_region: null, hotspots_covered_current: null,
    hotspots_covered_ours: null, crosswalk_green_deficit_count: null, note: "서울 지도만 분석",
  },
  aed: null,
  aed_note: "서울만 분석",
  actions: [{ module: "heat", text: "포항시는 오늘 경보 대상이 아닙니다.", reason: "이 날 평가 대상 아님." }],
} as unknown as BriefingResponse;

describe("briefing with every nullable field null", () => {
  it("normalizes to 'unknown' and 'not available', never to zeros that read as facts", () => {
    const m = normalizeBriefing(nullBriefing);
    expect(m.heat.alertClass).toBe("unknown");
    expect(m.heat.rank).toBeNull();
    expect(m.heat.shelterData).toBe(false); // "자료 없음", not "0곳"
    expect(m.silver.available).toBe(false);
    expect(m.aed).toBeNull();
    const ev = evidenceFor("heat", m, null);
    expect(ev.rows.map((r) => r.label)).not.toContain("위험 순위(우리 모형)");
    expect(strings(ev).some((s) => BAD.test(s))).toBe(false);
    expect(strings(evidenceFor("silver", m, null)).some((s) => BAD.test(s))).toBe(false);
    expect(evidenceFor("aed", m, null).rows).toEqual([]);
  });
  it("toFraction clamps and rejects non-finite input", () => {
    expect(toFraction(Number.NaN)).toBeNull();
    expect(toFraction(Number.POSITIVE_INFINITY)).toBeNull();
    expect(toFraction(-0.2)).toBe(0);
    expect(toFraction(150)).toBe(1);
    expect(toFraction(49)).toBeCloseTo(0.49);
    // ambiguity: a true fraction of exactly 1.0 (100 %) is kept as 1, but 1.5 is read as 1.5 % -> 0.015 (documented)
    expect(toFraction(1)).toBe(1);
    expect(toFraction(1.5)).toBeCloseTo(0.015);
  });
});

describe("AED adapters", () => {
  it("empty hourly list and gu list give empty outputs, not a crash", () => {
    expect(adaptAedHourly([])).toEqual([]);
    expect(adaptAedGu([])).toEqual([]);
    expect(adaptAedCandidates([])).toEqual([]);
    expect(rankGu([])).toEqual([]);
    expect(hourData([], 22, 6)).toEqual([]);
  });
  it("hours outside 0-23 are dropped from the chart data", () => {
    const rows = [{ hour: 24, public_arrests: 1, pct_open: 50 }, { hour: -1, public_arrests: 1, pct_open: 50 }, { hour: 0, public_arrests: 2, pct_open: 49 }];
    expect(hourData(rows, 22, 6).map((d) => d.hour)).toEqual([0]);
  });
  it("a summary whose CI is null falls back to the point value, not NaN", () => {
    const raw: RawAedSummary = {
      scope: "s", definitions: {}, weekday: { day: 0.78, night: 0.49, day_arrest_weighted: 0.8, night_arrest_weighted: 0.49 },
      weekend: { day: 0.6, night: 0.5, day_arrest_weighted: 0.6, night_arrest_weighted: 0.5 }, public_arrests_total: 2534,
      night_share_of_public_arrests: { value: 0.176, ci_lo: null, ci_hi: null, n: null },
      share_arrests_open_lt_50pct: { value: 0.136, ci_lo: 0.12, ci_hi: 0.15, n: 344 }, worst_gu: [], sources: [],
    } as unknown as RawAedSummary;
    const a = adaptAedSummary(raw);
    expect(a.night_arrest_share).toEqual({ value: 0.176, lo: 0.176, hi: 0.176 });
    expect(Number.isFinite(a.day_open_share)).toBe(true);
  });
  it("percent-vs-fraction heuristic: an all-small percent series (e.g. 0.8 %) would be misread as fractions (documented risk)", () => {
    const hourly: RawAedHour[] = [{ hour: 1, public_arrests: 1, open_share_weekday: 0.8, open_share_weekend: 0.8 }];
    // 0.8 is treated as a fraction and scaled to 80 %: correct for the real data (fractions), wrong if a future file sends percents < 1.
    expect(adaptAedHourly(hourly)[0]?.pct_open).toBe(80);
    const gus: RawAedGu[] = [{ gu: "A", sgg_code: "11110", night_open_share: 0.4, day_open_share: 0.7, gap_score: 1, public_arrests_night: 1, n_aed: 1 }];
    expect(adaptAedGu(gus)[0]?.night_open_share).toBe(0.4);
  });
  it("formatHours never prints the raw 'nan-nan' sentinel", () => {
    expect(formatHours("nan-nan")).toBe("시간 정보 없음");
    expect(formatHours(null)).toBe("시간 정보 없음");
    expect(formatHours("0000-2400")).toBe("00:00–24:00");
  });
  it("candidates with an empty place become null (rendered as an en dash), not an empty cell", () => {
    const c: RawAedCandidate[] = [{ gu: "A", rank: 1, org: "o", address: "a", place: "", weekday_hours: null, night_open_weekday_share: 0, gain_score: 1 }];
    expect(adaptAedCandidates(c)[0]?.place).toBeNull();
  });
});

describe("silver adapter with missing strata", () => {
  it("no strata -> empty methods and no headline (the page shows a Notice instead of 0 %)", () => {
    const raw = { k: 3918, variant: "harm", pool: "OPT", ours_method: "ours_opt", comparison_pool: "C", source: "promoted", definitions: {}, strata: [], sources: [] } as unknown as RawSilverSummary;
    const s = adaptSilverSummary(raw);
    expect(s.all.methods).toEqual([]);
    expect(s.all.n_hotspots).toBe(0);
    expect(silverHeadline(s.all)).toBeNull();
    expect(strings(s.notes).some((x) => BAD.test(x))).toBe(false);
  });
  it("the NEW stratum note carries n so a reader sees the small sample", () => {
    const raw = {
      k: 3918, variant: "harm", pool: "OPT", ours_method: "ours_opt", comparison_pool: "C", source: "promoted", definitions: {},
      strata: [
        { stratum: "all", n_hot: 735, casualties: 4248, methods: [{ method: "current", label: "", coverage: 0.154, ci_lo: 0.128, ci_hi: 0.18 }, { method: "ours_opt", label: "", coverage: 0.933, ci_lo: 0.915, ci_hi: 0.95 }], ours_minus: [] },
        { stratum: "new", n_hot: 73, casualties: 375, methods: [{ method: "current", label: "", coverage: 0.125, ci_lo: 0.055, ci_hi: 0.21 }, { method: "ours_opt", label: "", coverage: 0.389, ci_lo: 0.282, ci_hi: 0.507 }], ours_minus: [] },
      ], sources: [],
    } as unknown as RawSilverSummary;
    const s = adaptSilverSummary(raw);
    expect(s.new_sites.n_hotspots).toBe(73);
    expect(s.notes.some((n) => n.text.includes("73곳") && n.text.includes("신뢰구간이 넓습니다"))).toBe(true);
  });
});

describe("crosswalk verdicts with corrupt rows", () => {
  it("skips NaN / zero / negative / infinite rows instead of counting them as OK or short", () => {
    const rows = [
      { id: "a", lat: 0, lon: 0, lengthM: Number.NaN, greenSec: 20, address: "a" },
      { id: "b", lat: 0, lon: 0, lengthM: 0, greenSec: 20, address: "b" },
      { id: "c", lat: 0, lon: 0, lengthM: 20, greenSec: -1, address: "c" },
      { id: "d", lat: 0, lon: 0, lengthM: Number.POSITIVE_INFINITY, greenSec: 20, address: "d" },
      { id: "e", lat: 0, lon: 0, lengthM: 20, greenSec: 0, address: "e" }, // green 0: everyone is short, +29 s
      { id: "f", lat: 0, lon: 0, lengthM: 302, greenSec: 30, address: "f" }, // the 302 m input error from the standard data
    ];
    const v = assessAll(rows, 0.7);
    expect(v.map((x) => x.id)).toEqual(["e", "f"]);
    expect(v.find((x) => x.id === "f")?.dataCheck).toBe(true); // 302 m is a data error: flagged "자료 확인 필요", like the backend's 100 m cap
    expect(shortestFirst(v).map((x) => x.id)).toEqual(["e"]);
    expect(shortestFirst(v)[0]?.extensionSec).toBe(29);
  });
  it("clampSpeed keeps the slider inside its bounds for garbage input", () => {
    expect(clampSpeed(Number.NaN)).toBe(0.5);
    expect(clampSpeed(-3)).toBe(0.5);
    expect(clampSpeed(99)).toBe(1.2);
  });
});

describe("gait estimates", () => {
  it("throws on a non-positive field width and labels implausible / still / warming-up states", () => {
    expect(() => estimateSpeed([], 0)).toThrow(RangeError);
    expect(estimateSpeed([{ tMs: 0, x: 0.1 }], 3)).toEqual({ status: "warming_up", speedMps: null });
    expect(estimateSpeed([{ tMs: 0, x: 0 }, { tMs: 600, x: 0.9 }], 3).status).toBe("implausible"); // 4.5 m/s jitter
    expect(estimateSpeed([{ tMs: 0, x: 0.5 }, { tMs: 600, x: 0.5 }], 3).status).toBe("still");
    expect(estimateGroundSpeed([{ tMs: 0, X: 0, Y: 0 }]).status).toBe("warming_up");
    expect(estimateGroundSpeed([{ tMs: 0, X: 0, Y: 0 }, { tMs: 1000, X: 10, Y: 0 }]).status).toBe("implausible");
  });
  it("personStatus: a wheelchair/walker user with no speed estimate is 정상, a slow walker is 주의, fallen beats both", () => {
    expect(personStatus(false, null, 0.5)).toBe("normal");
    expect(personStatus(false, 0.3, 0.5)).toBe("caution");
    expect(personStatus(true, 1.0, 0.5)).toBe("danger");
    expect(personStatus(false, 0.3, null)).toBe("normal"); // no crosswalk requirement -> cannot judge
  });
});

describe("formatters never print NaN", () => {
  it("non-finite numbers render as an en dash", () => {
    for (const x of [Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY]) {
      expect(formatInt(x)).toBe("–");
      expect(formatDecimal(x)).toBe("–");
      expect(formatSigned(x)).toBe("–");
      expect(formatPercent(x)).toBe("–");
    }
    expect(sharePercent(0, 0)).toBeNull();
    expect(sharePercent(3, 0)).toBeNull();
  });
  it("riskLevel with K=0 is always 낮음 and with a NaN rank is 낮음", () => {
    expect(riskLevel(1, 0)).toBe("low");
    expect(riskLevel(Number.NaN, 10)).toBe("low");
    expect(riskLevel(10, 10)).toBe("high");
    expect(riskLevel(11, 10)).toBe("watch");
    expect(riskLevel(21, 10)).toBe("low");
  });
});
