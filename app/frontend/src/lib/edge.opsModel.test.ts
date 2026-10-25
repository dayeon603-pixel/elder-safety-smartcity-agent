/**
 * Edge audit of the 도시 운영 지도 queue: partial data (API down for one module, region with no AED / shelter /
 * crosswalk rows), all-unknown opening hours, and the text it produces. No string may contain NaN/undefined/null.
 */
import { describe, expect, it } from "vitest";
import type { ActionsResponse, BriefingResponse } from "../api/zpTypes";
import { buildQueue, countStates, openShare } from "./app/opsModel";
import type { QueueInput } from "./app/opsModel";
import { normalizeBriefing } from "./zpBriefing";

const BAD = /\b(NaN|undefined|null|Infinity)\b/;
const texts = (q: ReturnType<typeof buildQueue>): string[] => q.flatMap((i) => [i.title, ...i.lines, ...i.evidence.flatMap((e) => [e.label, e.value]), ...i.channels.map((c) => `${c.label} ${c.detail ?? ""}`)]);

const base: QueueInput = { regionName: "강릉시", speedMps: 0.7, crosswalks: null, briefing: null, actions: null, rawActions: [], tmaxC: null, aed: null, capFeedHref: "/api/cap/feed.atom", silverHeadline: null };

const briefingNoModules: BriefingResponse = {
  date: "2025-07-22", teams: 10,
  region: { code: "42150", name: "강릉시", sido: "강원특별자치도", pop65: 50000 },
  heat: {
    rank: 7, evidence: {}, alerted_b1: false, alerted_ours: true, cases_actual: 2,
    plan_allocation_for_region: { teams: 1, teams_total: 10, expected_cases: 1.4, reason: "예상 환자 1.4명" },
    nearby_open_shelters: { coverage: "none", items: [] },
  },
  silver: { current_zones_in_region: null, our_picks_in_region: null, hotspots_2024_in_region: null, hotspots_covered_current: null, hotspots_covered_ours: null, crosswalk_green_deficit_count: null, note: "서울 지도만 분석" },
  aed: null, aed_note: "서울만 분석",
  actions: [{ module: "heat", text: "강릉시는 오늘 폭염 경보 대상입니다. 현행 기준은 빠뜨린 곳입니다.", reason: "에이전트 순위 7위." }],
} as unknown as BriefingResponse;

const actionsEmpty = {
  sgg_code: "42150", region_name: "강원특별자치도 강릉시",
  context: { date: "2025-07-22", variant: "F", alerted: true, score: 1.4, rank: 7, n_cells: 250, tmax_c: null, apparent_max_c: null, tmax_day: "2025-07-22", pop65: 50000, team_allocated: true, teams_total: 10 },
  cbs: { text: "[강릉시] 7/22 폭염.", easy_text: "[강릉시] 오늘은 아주 덥습니다.", length: 12, easy_length: 16, limit: 157, limit_source: "", limit_status: "확인 필요", shelters: [], shelter_note: "자료 없음", status: "draft" },
  bit: [], bit_info: { radius_m: 300, covered_pop65_est: 0, region_pop65_with_stops: 0, n_candidate_stops: 0, method: "", note: "자료 없음" },
  signals: { note: "자료 없음: 이 시군구는 표준데이터에 횡단보도가 없다. 보행신호 판정을 하지 않았다." },
  signals_coverage: { dataset_rows: 50000, dataset_sido: [], n_crosswalks_in_region: 0, n_evaluable: 0, n_short: 0, note: "" },
  route: { stops: [], km: 0, km_pop65_order: 0, distance_basis: "", method: "", allocated: false, teams_in_region: 0, note: "" },
  sources: {}, evidence: {}, action_ids: {}, disclaimer: "초안",
} as unknown as ActionsResponse;

describe("queue with a non-Seoul region and no module data", () => {
  it("names what is missing, invents nothing, and keeps the heat item first when alerted", () => {
    const model = normalizeBriefing(briefingNoModules);
    const q = buildQueue({ ...base, briefing: model, actions: actionsEmpty, rawActions: briefingNoModules.actions });
    const ids = q.map((i) => i.id);
    expect(ids[0]).toBe("heat.alert");
    expect(ids).toContain("signal.extend");
    expect(ids).toContain("silver.check");
    expect(ids).not.toContain("aed.convert"); // AED block is null outside Seoul
    const signal = q.find((i) => i.id === "signal.extend");
    expect(signal?.decidable).toBe(false);
    expect(signal?.title).toContain("자료가 없어 판정하지 않았습니다");
    const silver = q.find((i) => i.id === "silver.check");
    expect(silver?.decidable).toBe(false);
    const bit = q.find((i) => i.id === "bit");
    expect(bit?.decidable).toBe(false);
    expect(bit?.title).toContain("고른 정류장이 없습니다");
    expect(texts(q).some((t) => BAD.test(t))).toBe(false);
    // no temperature arrives (tmax null): the heat evidence has no 최고기온 row rather than "null℃"
    const heat = q.find((i) => i.id === "heat.alert");
    expect(heat?.evidence.map((e) => e.label)).not.toContain("오늘 최고기온");
  });
  it("API down for /actions but /briefing up: the queue still builds from the briefing alone", () => {
    const model = normalizeBriefing(briefingNoModules);
    const q = buildQueue({ ...base, briefing: model, actions: null, rawActions: briefingNoModules.actions });
    expect(q.map((i) => i.id)).toEqual(["heat.alert", "signal.extend", "silver.check", "cap.feed"]);
    const kinds = q[0]?.channels.map((c) => c.kind) ?? [];
    expect(kinds).not.toContain("cbs"); // no CBS draft can be offered without /actions
    expect(kinds).toEqual(expect.arrayContaining(["audio", "webhook"]));
  });
  it("API down for /briefing but /actions up: only the channel items appear, nothing about heat is claimed", () => {
    const q = buildQueue({ ...base, actions: actionsEmpty });
    expect(q.map((i) => i.id)).toEqual(["signal.extend", "bit", "cbs", "cap.feed"]);
    expect(texts(q).some((t) => BAD.test(t))).toBe(false);
  });
});

describe("all-unknown opening hours", () => {
  it("an AED set with no usable hours produces a row that says 0 open of 0 known (documented wording risk)", () => {
    const counts = countStates(["unknown", "unknown", "unknown"]);
    expect(openShare(counts)).toBeNull();
    const model = normalizeBriefing({
      ...briefingNoModules,
      region: { code: "11620", name: "관악구", sido: "서울특별시", pop65: 70000 },
      aed: { night_open_share: 0.33, day_open_share: 0.7, gap_rank: 1, gap_score: 17, candidates_top5: [{ gu: "관악구", rank: 1, org: "A", address: "a", place: "", weekday_hours: null, night_open_weekday_share: 0, gain_score: 1, lat: 37.5, lon: 126.9 }] },
      aed_note: null,
    } as unknown as BriefingResponse);
    const q = buildQueue({ ...base, regionName: "관악구", briefing: model, aed: { counts, hourLabel: "평일 23시" } });
    const aed = q.find((i) => i.id === "aed.convert");
    const row = aed?.evidence.find((e) => e.label === "평일 23시 열린 AED");
    // the value reads "0대 / 시간 자료 있는 0대": true, but a reader may read it as "none open". Recorded in EDGE_CASES.md.
    expect(row?.value).toBe("0대 / 시간 자료 있는 0대");
  });
});

describe("crosswalk item at the edges", () => {
  it("evaluated rows but none short: a positive sentence and a focus of null", () => {
    const q = buildQueue({ ...base, crosswalks: { evaluated: 12, short: [], shortAtBaseline: 0 } });
    const s = q[0];
    expect(s?.title).toContain("신호가 모자란 횡단보도가 없습니다");
    expect(s?.focus).toBeNull();
    expect(s?.decidable).toBe(true);
  });
  it("speed label strips a trailing zero only once (0.70 -> 0.7, 1.00 -> 1.0)", () => {
    const q1 = buildQueue({ ...base, speedMps: 0.7, crosswalks: { evaluated: 1, short: [], shortAtBaseline: 0 } });
    const q2 = buildQueue({ ...base, speedMps: 1.0, crosswalks: { evaluated: 1, short: [], shortAtBaseline: 0 } });
    expect(q1[0]?.title).toContain("0.7 m/s");
    expect(q2[0]?.title).toContain("1.0 m/s");
  });
});
