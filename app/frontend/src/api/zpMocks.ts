/**
 * Typed stand-ins for /silver/*, /aed/* and /briefing/*, selected by VITE_ZP_MOCK=1 or VITE_HEAT_MOCK=1 (see zp.ts).
 * Silver and AED numbers are snapshots of the real experiment outputs (Seoul). The heat panel of the briefing is
 * built from the heat mocks, so it is the seeded simulation for every day except the one real map day.
 * Pages show a "모의 응답" notice whenever mocks are on.
 */
import { ApiError } from "./client";
import { mockAlerts, mockPlan, mockRegion, mockRegions } from "./heatMocks";
import {
  MOCK_AED_CANDIDATES, MOCK_AED_GU, MOCK_AED_HOURS, MOCK_GU_SILVER, MOCK_SILVER_ALL, MOCK_SILVER_K, MOCK_SILVER_NEW, MOCK_SILVER_NOTES,
  MOCK_SILVER_POINTS,
} from "./zpMockData";
import type {
  ActionsResponse, AedCandidate, AedGu, AedHour, AedSummary, BriefingAction, BriefingResponse, SilverMap, SilverSummary,
} from "./zpTypes";

const MOCK_LATENCY_MS = 60;
const SEOUL = "서울특별시";
const MOCK_VARIANT = "F";
const MOCK_SHELTER_COUNT = 5;
const MOCK_AED_SCOPE = "서울 25개 구, 소방청 구급활동정보 2024-01~11, 개방시간표 기준";
const SEOUL_PUBLIC_ARRESTS = 2534;
const NIGHT_ARREST_SHARE = { value: 0.1764, lo: 0.162, hi: 0.1917 };
const MEAN_DAY = 0.7834;
const MEAN_NIGHT = 0.4932;

const delay = (): Promise<void> => new Promise((r) => setTimeout(r, MOCK_LATENCY_MS));

export async function mockSilverSummary(): Promise<SilverSummary> {
  await delay();
  return { k: MOCK_SILVER_K, radius_m: 300, test_years: [2022, 2023, 2024], all: MOCK_SILVER_ALL, new_sites: MOCK_SILVER_NEW, notes: MOCK_SILVER_NOTES };
}

export async function mockSilverMap(city: string): Promise<SilverMap> {
  await delay();
  return {
    city,
    radius_m: 300,
    points: MOCK_SILVER_POINTS.map(([lat, lon, kind, name, cas, cur, ours]) => ({ lat, lon, kind, name, cas, cur_within: cur, ours_within: ours })),
  };
}

export async function mockAedSummary(): Promise<AedSummary> {
  await delay();
  return {
    day_open_share: MEAN_DAY,
    night_open_share: MEAN_NIGHT,
    night_arrest_share: NIGHT_ARREST_SHARE,
    n_public_arrests: SEOUL_PUBLIC_ARRESTS,
    scope: MOCK_AED_SCOPE,
  };
}

export async function mockAedHourly(): Promise<AedHour[]> {
  await delay();
  return MOCK_AED_HOURS;
}

export async function mockAedGu(): Promise<AedGu[]> {
  await delay();
  return MOCK_AED_GU;
}

export async function mockAedCandidates(gu: string): Promise<AedCandidate[]> {
  await delay();
  return MOCK_AED_CANDIDATES.filter((c) => gu === "" || c.gu === gu);
}

function mockActions(b: Omit<BriefingResponse, "actions">): BriefingAction[] {
  const out: BriefingAction[] = [];
  const h = b.heat;
  const name = b.region.name;
  const rank = h.rank === null ? "이 날 평가 대상 아님" : `에이전트 순위 ${h.rank}위`;
  if (h.alerted_ours && !h.alerted_b1) out.push({ module: "heat", text: `${name}은 오늘 폭염 경보 대상입니다. 현행 기준은 빠뜨린 곳입니다.`, reason: `${rank}.` });
  else if (h.alerted_ours) out.push({ module: "heat", text: `${name}은 현행 기준과 에이전트 모두 경보 대상입니다.`, reason: `${rank}.` });
  else out.push({ module: "heat", text: `${name}은 오늘 경보 대상이 아닙니다.`, reason: `${rank}.` });
  if (h.plan_allocation_for_region.teams > 0) out.push({ module: "heat", text: `방문팀 ${h.plan_allocation_for_region.teams}팀 배치.`, reason: "예상 환자 수 기준." });
  if (b.silver.our_picks_in_region !== null) out.push({ module: "silver", text: `실버존 후보 ${b.silver.our_picks_in_region}곳을 현장 확인하세요.`, reason: `현행 노인보호구역은 ${b.silver.current_zones_in_region ?? 0}곳입니다.` });
  if (b.aed !== null) out.push({ module: "aed", text: `밤 AED 열림 ${Math.round(b.aed.night_open_share * 100)}% (낮 ${Math.round(b.aed.day_open_share * 100)}%).`, reason: `서울 25개 구 중 밤 공백 ${b.aed.gap_rank ?? "–"}위.` });
  return out;
}

/** GET /briefing/{code}: heat from the heat mocks, silver and AED from the real Seoul snapshots. */
export async function mockBriefing(code: string, date: string, teams: number): Promise<BriefingResponse> {
  await delay();
  const regions = await mockRegions();
  const region = regions.find((r) => r.code === code);
  if (region === undefined) throw new ApiError(404, "region not found");
  const [alerts, plan, detail] = await Promise.all([
    mockAlerts(date, MOCK_VARIANT),
    mockPlan(date, MOCK_VARIANT, teams, MOCK_SHELTER_COUNT),
    mockRegion(code, date, MOCK_VARIANT),
  ]);
  const inSeoul = region.sido === SEOUL;
  const gu = MOCK_AED_GU.find((g) => g.gu === region.name);
  const sil = MOCK_GU_SILVER[region.name];
  const ranked = [...MOCK_AED_GU].sort((a, b) => a.night_open_share - b.night_open_share);
  const base: Omit<BriefingResponse, "actions"> = {
    date,
    teams,
    region: { code: region.code, sido: region.sido, name: region.name, pop65: region.pop65 },
    heat: {
      rank: detail.scores.rank,
      n_regions: regions.length,
      alerted_b1: alerts.b1.some((a) => a.code === code),
      alerted_ours: alerts.ours.some((a) => a.code === code),
      cases_actual: alerts.cases.find((c) => c.code === code)?.n ?? 0,
      plan_allocation_for_region: { teams: plan.allocations.find((a) => a.code === code)?.teams ?? 0, teams_total: teams },
      nearby_open_shelters: {
        coverage: detail.shelters.coverage,
        items: detail.shelters.nearby.filter((s) => s.night_open === true || s.night_open === 1).map((s) => ({ name: s.name, dist_km: s.dist_km })),
      },
    },
    silver:
      inSeoul && sil !== undefined
        ? { ...sil, crosswalk_green_deficit_count: null, note: null }
        : { current_zones_in_region: null, our_picks_in_region: null, hotspots_2024_in_region: null, crosswalk_green_deficit_count: null, note: "서울 지도만 분석" },
    aed:
      inSeoul && gu !== undefined
        ? {
            night_open_share: gu.night_open_share,
            day_open_share: gu.day_open_share,
            gap_rank: ranked.findIndex((g) => g.gu === gu.gu) + 1,
            candidates_top5: MOCK_AED_CANDIDATES.filter((c) => c.gu === gu.gu).slice(0, 5).map((c) => ({ org: c.org, address: c.address, place: c.place })),
          }
        : null,
    aed_note: inSeoul ? null : "서울만 분석",
  };
  return { ...base, actions: mockActions(base) };
}

/** GET /actions/{code}: a short stand-in so the briefing renders in mock mode; the page labels mocks. */
export async function mockChannels(code: string, date: string): Promise<ActionsResponse> {
  await delay();
  const regions = await mockRegions();
  const region = regions.find((r) => r.code === code);
  if (region === undefined) throw new ApiError(404, "region not found");
  const text = `[${region.name}] ${date} 폭염 모의 문안입니다. 실제 API 결과가 아닙니다.`;
  return {
    sgg_code: code,
    region_name: `${region.sido} ${region.name}`,
    cbs: { text, easy_text: text, length: text.length, easy_length: text.length, limit: 157, shelter_note: null, status: "draft" },
    bit: [],
    signals: [],
    signals_coverage: { n_crosswalks_in_region: 0, n_evaluable: 0, n_short: 0, note: "모의 응답에는 횡단보도 자료가 없습니다." },
    route: { stops: [], km: 0, distance_basis: "직선거리 기준", note: "모의 응답에는 동선이 없습니다." },
    disclaimer: "모든 출력은 담당자가 검토할 권고 초안이며 발송·시행된 것이 아니다.",
  };
}
