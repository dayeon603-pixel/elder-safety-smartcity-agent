import { ApiError, request } from "./client";
import { HEAT_MOCK, describeHeatError, heatQuery } from "./heat";
import type * as ZpMocks from "./zpMocks";
import { adaptAedCandidates, adaptAedGu, adaptAedHourly, adaptAedSummary, adaptSilverMap, adaptSilverSummary } from "../lib/zpAdapt";
import type { ActionsResponse, BriefingResponse, RawAedCandidate, RawAedGu, RawAedHour, RawAedSummary, RawSilverMap, RawSilverSummary } from "./zpTypes";

/** Mocks are on for VITE_ZP_MOCK=1 and, so one dev server serves every page, whenever the heat mocks are on. */
export const ZP_MOCK: boolean = import.meta.env.VITE_ZP_MOCK === "1" || HEAT_MOCK;

const HTTP_NOT_FOUND = 404;
const HTTP_UNPROCESSABLE = 422;
/** FastAPI's detail for an unknown route; a missing resource carries its own message instead. */
const ROUTE_MISSING_DETAIL = "Not Found";
const NO_MODULE_DATA = "no_module_data";
const MODULES_LOAD_COMMAND = "make modules-load";

/** Map failures of the 에이전트 endpoints to one Korean sentence that says what to do next. */
export function describeZpError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.message.includes(NO_MODULE_DATA)) {
      return `분석 자료가 아직 적재되지 않았습니다. 터미널에서 ${MODULES_LOAD_COMMAND}를 실행한 뒤 다시 시도해 주세요.`;
    }
    if (err.status === HTTP_NOT_FOUND) {
      return err.message === ROUTE_MISSING_DETAIL
        ? "이 화면의 API를 서버에서 찾을 수 없습니다. 백엔드를 최신 버전으로 실행했는지 확인해 주세요."
        : "해당 시군구나 날짜의 자료를 찾을 수 없습니다.";
    }
    if (err.status === HTTP_UNPROCESSABLE) {
      return "선택한 날짜나 입력값이 분석 대상이 아닙니다. 시험 시즌(5–9월, 2023–2025년)의 날짜를 골라 주세요.";
    }
  }
  return describeHeatError(err);
}

type Mocks = typeof ZpMocks;
const mocks = (): Promise<Mocks> => import("./zpMocks");

function route<T>(real: () => Promise<T>, mock: (m: Mocks) => Promise<T>): Promise<T> {
  return ZP_MOCK ? mocks().then(mock) : real();
}

export const zpApi = {
  silverSummary: () =>
    route(() => request<RawSilverSummary>("/silver/summary").then(adaptSilverSummary), (m) => m.mockSilverSummary()),
  silverMap: (city: string) =>
    route(() => request<RawSilverMap>(`/silver/map${heatQuery({ city })}`).then(adaptSilverMap), (m) => m.mockSilverMap(city)),
  aedSummary: () => route(() => request<RawAedSummary>("/aed/summary").then(adaptAedSummary), (m) => m.mockAedSummary()),
  aedHourly: () => route(() => request<RawAedHour[]>("/aed/hourly").then(adaptAedHourly), (m) => m.mockAedHourly()),
  aedGu: () => route(() => request<RawAedGu[]>("/aed/gu").then(adaptAedGu), (m) => m.mockAedGu()),
  aedCandidates: (gu: string) =>
    route(
      () => request<RawAedCandidate[]>(`/aed/candidates${heatQuery({ gu: gu === "" ? undefined : gu })}`).then(adaptAedCandidates),
      (m) => m.mockAedCandidates(gu),
    ),
  briefing: (code: string, date: string, teams: number) =>
    route(
      () => request<BriefingResponse>(`/briefing/${encodeURIComponent(code)}${heatQuery({ date, teams })}`),
      (m) => m.mockBriefing(code, date, teams),
    ),
  actions: (code: string, date: string, teams: number) =>
    route(
      () => request<ActionsResponse>(`/actions/${encodeURIComponent(code)}${heatQuery({ date, teams })}`),
      (m) => m.mockChannels(code, date),
    ),
};
