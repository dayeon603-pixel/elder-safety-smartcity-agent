import { useState } from "react";
import { describeHeatError, heatApi } from "../../api/heat";
import type { HeatDay, HeatMeta, HeatRegion, HeatRegionDetail } from "../../api/heatTypes";
import type { AsyncState } from "../../api/useApi";
import { useApi } from "../../api/useApi";
import { FIELD_HEAT_VARIANT, FIELD_REGION_MAX_KM } from "../../config";
import { nearestBy, stageFromTmax } from "../../lib/field";
import type { Crosswalk, HeatInfo, HeatStage } from "../../lib/field";

export interface FieldHeat {
  info: HeatInfo;
  meta: AsyncState<HeatMeta>;
  days: AsyncState<HeatDay[]>;
  regions: AsyncState<HeatRegion[]>;
  detail: AsyncState<HeatRegionDetail | null>;
  year: number | null;
  setYear: (y: number) => void;
  date: string | null;
  setDate: (d: string) => void;
  manual: boolean;
  setManual: (m: boolean) => void;
  manualStage: HeatStage;
  setManualStage: (s: HeatStage) => void;
  nearest: { region: HeatRegion; km: number } | null;
  /** Why the API value is not being used (empty when it is). */
  problem: string | null;
  regionCode: string | null;
  reloadAll: () => void;
}

/** Heat stage for the chosen crosswalk and day: /heat API for the nearest 시군구 centroid, or a clearly labelled manual choice. */
export function useFieldHeat(crosswalk: Crosswalk): FieldHeat {
  const meta = useApi(heatApi.meta, { describe: describeHeatError });
  const [yearState, setYear] = useState<number | null>(null);
  const year = yearState ?? (meta.state.status === "ready" ? (meta.state.data.years[meta.state.data.years.length - 1] ?? null) : null);
  const days = useApi<HeatDay[]>(() => (year === null ? Promise.resolve([]) : heatApi.days(year, FIELD_HEAT_VARIANT, "gap")), { key: String(year), describe: describeHeatError });
  const regions = useApi(heatApi.regions, { describe: describeHeatError });
  const [dateState, setDate] = useState<string | null>(null);
  const firstDate = days.state.status === "ready" ? (days.state.data[0]?.date ?? null) : null;
  const date = dateState !== null && days.state.status === "ready" && days.state.data.some((d) => d.date === dateState) ? dateState : firstDate;

  const near = regions.state.status === "ready" ? nearestBy(regions.state.data, crosswalk.lat, crosswalk.lon) : null;
  const nearest = near === null ? null : { region: near.item, km: near.distanceM / 1000 };
  const code = nearest?.region.code ?? null;
  const detail = useApi<HeatRegionDetail | null>(
    () => (code === null || date === null ? Promise.resolve(null) : heatApi.region(code, date, FIELD_HEAT_VARIANT)),
    { key: `${code}|${date}`, describe: describeHeatError },
  );

  const [manual, setManual] = useState(false);
  const [manualStage, setManualStage] = useState<HeatStage>("none");

  const failed = [meta.state, days.state, regions.state, detail.state].find((s) => s.status === "error");
  let problem: string | null = null;
  if (failed !== undefined && failed.status === "error") problem = failed.message;
  else if (nearest !== null && nearest.km > FIELD_REGION_MAX_KM) problem = `가장 가까운 시군구 중심이 ${nearest.km.toFixed(0)} km 떨어져 있어 맞는 시군구인지 알 수 없습니다.`;
  else if (days.state.status === "ready" && days.state.data.length === 0) problem = "선택한 연도에 날짜 자료가 없습니다.";

  const d = detail.state.status === "ready" ? detail.state.data : null;
  const tmax = d?.drivers.tmax ?? null;
  if (problem === null && d !== null && tmax === null) problem = "이 시군구의 이 날 기온 자료가 없습니다(자료 없음). 폭염 단계를 직접 선택해 주세요.";
  const useApiValue = !manual && problem === null && d !== null && tmax !== null;
  const info: HeatInfo = useApiValue
    ? { stage: stageFromTmax(tmax), tmaxC: tmax, date, source: "api", regionName: `${d.region.sido} ${d.region.name}`, regionKm: nearest?.km ?? null }
    : { stage: manualStage, tmaxC: null, date: null, source: "manual", regionName: null, regionKm: null };

  return {
    info, meta: meta.state, days: days.state, regions: regions.state, detail: detail.state, year, setYear, date, setDate,
    manual, setManual, manualStage, setManualStage, nearest, problem, regionCode: code,
    reloadAll: () => { meta.reload(); days.reload(); regions.reload(); detail.reload(); },
  };
}
