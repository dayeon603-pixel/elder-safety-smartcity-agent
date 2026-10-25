import type { BriefingAction } from "../api/zpTypes";
import type { BriefingModel } from "./zpBriefing";
import { HEAT_LOWER_BOUND_APPARENT_C } from "../config";
import { guRankBand, heatRankBand } from "./rankBand";
import { pct0 } from "./zpSilver";

export interface EvidenceRow {
  label: string;
  value: string;
}

export interface Evidence {
  rows: EvidenceRow[];
  source: string;
}

const SOURCE: Readonly<Record<string, string>> = {
  heat: "질병관리청 온열질환 신고 2011–2025 · 기상청 일 최고기온(ASOS) · 행정안전부 주민등록 인구",
  silver: "도로교통공단 보행노인 사고다발지(TAAS) 2024 · 전국 노인보호구역·횡단보도 표준데이터",
  aed: "소방청 구급활동정보 2024 · 국립중앙의료원 AED 개방시간표",
};
const UNKNOWN_SOURCE = "출처는 /data 화면에서 확인할 수 있습니다";

/**
 * The numbers behind one recommended action, in plain Korean. Only values that the API returned are listed;
 * a missing value is left out rather than shown as 0.
 */
export function evidenceFor(module: string, m: BriefingModel, tmaxC: number | null): Evidence {
  const rows: EvidenceRow[] = [];
  if (module === "heat") {
    if (tmaxC !== null) rows.push({ label: "오늘 최고기온", value: `${tmaxC.toFixed(1)}℃` });
    if (m.heat.rank !== null) rows.push({ label: "위험 순위(우리 모형)", value: `${heatRankBand(m.heat.rank, m.heat.nRegions)}${m.heat.nRegions !== null ? ` (전국 ${m.heat.nRegions}곳 중)` : ""}` });
    if (m.region.pop65 !== undefined) rows.push({ label: "65세 이상 인구", value: `${m.region.pop65.toLocaleString("ko-KR")}명` });
    if (m.heat.expectedCases !== null) rows.push({ label: "방문계획상 예상 환자", value: `${m.heat.expectedCases.toFixed(1)}명` });
    rows.push({ label: "이 날 실제 온열질환자", value: `${m.heat.casesActual}명` });
    if (m.heat.alertedB1 !== null) rows.push({ label: "기상 특보", value: m.heat.alertedB1 ? "있음" : "없음" });
  } else if (module === "silver") {
    if (m.silver.available) {
      rows.push({ label: "실버존 수(현행 → 추천)", value: `${m.silver.currentZones}곳 → ${m.silver.ourPicks}곳` });
      rows.push({ label: "2024 사고다발지", value: `${m.silver.hotspots}곳` });
      if (m.silver.greenDeficit !== null) rows.push({ label: "녹색시간이 모자라는 횡단보도", value: `${m.silver.greenDeficit}곳` });
    }
    if (m.region.pop65 !== undefined) rows.push({ label: "65세 이상 인구", value: `${m.region.pop65.toLocaleString("ko-KR")}명` });
  } else if (module === "aed" && m.aed !== null) {
    if (m.aed.nightOpen !== null) rows.push({ label: "밤(22–06시) 열린 AED", value: pct0(m.aed.nightOpen) });
    if (m.aed.dayOpen !== null) rows.push({ label: "낮 열린 AED", value: pct0(m.aed.dayOpen) });
    if (m.aed.gapRank !== null) rows.push({ label: "서울 25개 구 중 밤 공백", value: guRankBand(m.aed.gapRank) });
  }
  return { rows, source: SOURCE[module] ?? UNKNOWN_SOURCE };
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const rec = (v: unknown): Record<string, unknown> | null => (typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : null);

/**
 * Evidence rows straight from the API's `evidence` object (keys known to /briefing). Unknown keys are ignored, missing
 * ones are left out. Returns null when the action carries no evidence, so the caller falls back to the derived rows.
 */
export function apiEvidence(action: BriefingAction): Evidence | null {
  const e = action.evidence;
  if (e === undefined) return null;
  const rows: EvidenceRow[] = [];
  const add = (label: string, value: string | null): void => {
    if (value !== null) rows.push({ label, value });
  };
  const n = (key: string, label: string, fmt: (x: number) => string): void => add(label, num(e[key]) === null ? null : fmt(num(e[key]) as number));
  n("apparent_max_c", "체감 최고", (x) => `${x.toFixed(1)}℃`);
  n("tmax_c", "최고기온", (x) => `${x.toFixed(1)}℃`);
  const st = rec(e.station);
  if (st !== null && typeof st.name === "string") add("기온 측정소", `${st.name}${num(st.dist_km) !== null ? ` (${(num(st.dist_km) as number).toFixed(1)}km)` : ""}`);
  n("rank", "위험 순위(우리 모형)", (x) => `${heatRankBand(x, num(e.n_cells))}${num(e.n_cells) !== null ? ` (전국 ${e.n_cells as number}곳 중)` : ""}`);
  const apparent = num(e.apparent_max_c);
  const lowerBound = apparent !== null && apparent >= HEAT_LOWER_BOUND_APPARENT_C;
  n("expected_cases_lambda", lowerBound ? "예상 환자(최소, 하루 기대값)" : "예상 환자(하루 기대값)", (x) => `${lowerBound ? "최소 " : ""}${x.toFixed(2)}명${lowerBound ? " 이상" : ""}`);
  if (lowerBound) add("해석 주의", `체감 ${HEAT_LOWER_BOUND_APPARENT_C}℃ 이상은 학습 범위 밖이라 실제 환자가 더 많을 수 있습니다`);
  n("pop65", "65세 이상 인구", (x) => `${x.toLocaleString("ko-KR")}명`);
  n("old_share", "65세 이상 비중", (x) => pct0(x));
  n("hotspots_2024", "2024 사고다발지", (x) => `${x}곳`);
  if (num(e.covered_current) !== null && num(e.covered_ours) !== null) add("다발지를 덮는 구역", `현행 ${e.covered_current as number}곳 → 에이전트 ${e.covered_ours as number}곳`);
  n("our_picks", "에이전트 추천 구역", (x) => `${x}곳`);
  n("night_open_share", "밤 열린 AED", (x) => pct0(x));
  n("day_open_share", "낮 열린 AED", (x) => pct0(x));
  n("gap_rank", "서울 25개 구 중 밤 공백", (x) => guRankBand(x));
  const c = rec(e.candidate);
  if (c !== null && typeof c.org === "string") add("전환 후보", `${c.org}${typeof c.address === "string" ? ` · ${c.address}` : ""}`);
  const model = typeof e.model === "string" ? e.model : null;
  return rows.length === 0 ? null : { rows, source: model ?? SOURCE[action.module] ?? UNKNOWN_SOURCE };
}
