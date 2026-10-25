import type * as HeatMocks from "./heatMocks";
import { ApiError, describeError, request } from "./client";
import type {
  HeatAlerts, HeatCurve, HeatDay, HeatEvalRow, HeatMeta, HeatPlan, HeatPlanEval, HeatRegion, HeatRegionDetail, HeatSort, HeatVariant,
} from "./heatTypes";

/** Build-time switch for the typed mock responses in heatMocks.ts. Off in production builds. */
export const HEAT_MOCK: boolean = import.meta.env.VITE_HEAT_MOCK === "1";

const HTTP_NOT_FOUND = 404;
const HTTP_CONFLICT = 409;
const HTTP_UNPROCESSABLE = 422;
const NO_RUN_CODE = "no_completed_run";
/** FastAPI's detail for an unknown route; a missing resource carries its own message instead. */
const ROUTE_MISSING_DETAIL = "Not Found";
export const HEAT_EVAL_COMMAND = "make heat-eval";

/** Query string without undefined entries; values are percent-encoded. */
export function heatQuery(params: Record<string, string | number | undefined>): string {
  const parts = Object.entries(params)
    .filter((e): e is [string, string | number] => e[1] !== undefined)
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  return parts.length === 0 ? "" : `?${parts.join("&")}`;
}

/** Map heat API failures to one Korean sentence that says what to do next. */
export function describeHeatError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === HTTP_CONFLICT || err.message.includes(NO_RUN_CODE)) {
      return `완료된 평가 실행이 없습니다. 터미널에서 ${HEAT_EVAL_COMMAND}를 실행한 뒤 다시 시도해 주세요.`;
    }
    if (err.status === HTTP_NOT_FOUND) {
      return err.message === ROUTE_MISSING_DETAIL
        ? "경보 API(/heat)를 서버에서 찾을 수 없습니다. 백엔드를 최신 버전으로 실행했는지 확인해 주세요."
        : "해당 시군구나 날짜의 자료를 찾을 수 없습니다.";
    }
    if (err.status === HTTP_UNPROCESSABLE) {
      return "선택한 날짜나 연도가 평가 대상이 아닙니다. 시험 시즌(5–9월)의 시험 연도에서 골라 주세요.";
    }
  }
  return describeError(err);
}

type Mocks = typeof HeatMocks;
const mocks = (): Promise<Mocks> => import("./heatMocks");

function route<T>(real: () => Promise<T>, mock: (m: Mocks) => T | Promise<T>): Promise<T> {
  return HEAT_MOCK ? mocks().then(mock) : real();
}

export const heatApi = {
  meta: () => route(() => request<HeatMeta>("/heat/meta"), (m) => m.mockMeta()),
  eval: (variant: HeatVariant) =>
    route(() => request<HeatEvalRow[]>(`/heat/eval${heatQuery({ variant })}`), (m) => m.mockEval(variant)),
  curve: (year: number, variant: HeatVariant) =>
    route(() => request<HeatCurve>(`/heat/curve${heatQuery({ year, variant })}`), (m) => m.mockCurve(year, variant)),
  days: (year: number, variant: HeatVariant, sort: HeatSort = "gap") =>
    route(
      () => request<HeatDay[]>(`/heat/days${heatQuery({ year, variant, sort })}`),
      (m) => m.mockDays(year, variant, sort),
    ),
  regions: () => route(() => request<HeatRegion[]>("/heat/regions"), (m) => m.mockRegions()),
  alerts: (date: string, variant: HeatVariant) =>
    route(() => request<HeatAlerts>(`/heat/alerts${heatQuery({ date, variant })}`), (m) => m.mockAlerts(date, variant)),
  plan: (date: string, variant: HeatVariant, teams: number, shelters: number) =>
    route(
      () => request<HeatPlan>(`/heat/plan${heatQuery({ date, variant, teams, shelters })}`),
      (m) => m.mockPlan(date, variant, teams, shelters),
    ),
  planEval: (year: number, teams: number) =>
    route(() => request<HeatPlanEval>(`/heat/plan/eval${heatQuery({ year, teams })}`), (m) => m.mockPlanEval(year, teams)),
  region: (code: string, date: string, variant: HeatVariant) =>
    route(
      () => request<HeatRegionDetail>(`/heat/region/${encodeURIComponent(code)}${heatQuery({ date, variant })}`),
      (m) => m.mockRegion(code, date, variant),
    ),
};
