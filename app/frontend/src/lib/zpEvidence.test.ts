import { describe, expect, it } from "vitest";
import { normalizeSummary } from "../api/feedback";
import { evidenceFor } from "./zpEvidence";
import type { BriefingModel } from "./zpBriefing";

const MODEL = {
  region: { code: "11620", sido: "서울특별시", name: "관악구", pop65: 94733 },
  date: "2025-07-22",
  heat: { rank: 61, nRegions: 250, alertedB1: false, alertedOurs: true, casesActual: 1, teams: 0, expectedCases: null, shelters: [], shelterCount: 0, shelterData: false, alertClass: "ours_only" },
  silver: { available: true, note: null, currentZones: 13, ourPicks: 41, hotspots: 1, greenDeficit: null },
  aed: { nightOpen: 0.328, dayOpen: 0.705, gapRank: 1, candidates: [] },
  aedNote: null,
  actions: [],
} as unknown as BriefingModel;

describe("evidenceFor", () => {
  it("lists only values the API returned", () => {
    const heat = evidenceFor("heat", MODEL, 30.5);
    const labels = heat.rows.map((r) => r.label);
    expect(labels).toContain("오늘 최고기온");
    expect(labels).not.toContain("방문계획상 예상 환자");
    expect(heat.rows.find((r) => r.label === "위험 순위(우리 모형)")?.value).toBe("상위 25% 안 (전국 250곳 중)");
    expect(evidenceFor("heat", MODEL, null).rows.map((r) => r.label)).not.toContain("오늘 최고기온");
  });
  it("omits the green deficit when unknown and reports AED shares", () => {
    expect(evidenceFor("silver", MODEL, null).rows.map((r) => r.label)).not.toContain("녹색시간이 모자라는 횡단보도");
    expect(evidenceFor("aed", MODEL, null).rows[0]?.value).toBe("33%");
    expect(evidenceFor("unknown", MODEL, null).rows).toEqual([]);
  });
});

describe("normalizeSummary", () => {
  it("reads by_decision and rejects anything else", () => {
    expect(normalizeSummary({ sgg_code: "11620", by_decision: { 채택: 2, 보류: 1, 부적합: 0 } })).toEqual({ 채택: 2, 보류: 1, 부적합: 0 });
    expect(normalizeSummary({ detail: "Not Found" })).toBeNull();
    expect(normalizeSummary("x")).toBeNull();
  });
});

describe("rank bands and lower-bound labels", () => {
  it("states set membership, never an exact rank", async () => {
    const { heatRankBand, guRankBand } = await import("./rankBand");
    expect(heatRankBand(61, 250)).toBe("상위 25% 안");
    expect(heatRankBand(10, 250)).toBe("상위 10% 안");
    expect(heatRankBand(200, 250)).toBe("하위 절반");
    expect(guRankBand(1)).toBe("상위 5구 안");
    expect(guRankBand(9)).toBe("상위 10구 안");
    expect(guRankBand(20)).toBe("상위 10구 밖");
  });
  it("labels expected cases as a minimum at 체감 36℃ and above", async () => {
    const { apiEvidence } = await import("./zpEvidence");
    const mk = (apparent: number) => apiEvidence({ module: "heat", text: "", reason: "", evidence: { expected_cases_lambda: 1.5, apparent_max_c: apparent } });
    expect(mk(36)?.rows.find((r) => r.label.startsWith("예상 환자"))?.value).toBe("최소 1.50명 이상");
    expect(mk(33)?.rows.find((r) => r.label.startsWith("예상 환자"))?.value).toBe("1.50명");
  });
});
