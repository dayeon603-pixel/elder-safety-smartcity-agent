import { HEAT_STAGE_LABEL } from "../../lib/field";
import type { HeatStage } from "../../lib/field";
import { Button } from "../Button";
import { SelectField } from "../Field";
import { Notice } from "../Notice";
import type { FieldHeat } from "./useFieldHeat";

const STAGES: readonly HeatStage[] = ["none", "advisory", "warning"];

/** Day + heat-stage input. API-backed unless the API is unavailable or the user switches to manual. */
export function HeatPicker({ heat }: { heat: FieldHeat }) {
  const { info } = heat;
  const manualShown = heat.manual || heat.problem !== null;
  return (
    <div className="space-y-3">
      {heat.meta.status === "ready" && heat.days.status === "ready" && !manualShown && (
        <div className="grid gap-3 sm:grid-cols-2">
          <SelectField
            label="연도"
            value={String(heat.year ?? "")}
            onChange={(e) => heat.setYear(Number(e.target.value))}
            options={heat.meta.data.years.map((y) => ({ value: String(y), label: `${y}년` }))}
          />
          <SelectField
            label="날짜 (온열질환 환자가 많았던 날 순)"
            value={heat.date ?? ""}
            onChange={(e) => heat.setDate(e.target.value)}
            options={heat.days.data.map((d) => ({ value: d.date, label: `${d.date} · 환자 ${d.n_cases}명` }))}
          />
        </div>
      )}
      {manualShown && (
        <div className="space-y-2">
          {heat.problem !== null && (
            <Notice tone="danger">
              경보 API 값을 쓸 수 없어 폭염 단계를 직접 선택합니다. {heat.problem}
            </Notice>
          )}
          <SelectField
            label="폭염 단계 (직접 선택)"
            value={heat.manualStage}
            onChange={(e) => heat.setManualStage(e.target.value as HeatStage)}
            options={STAGES.map((s) => ({ value: s, label: HEAT_STAGE_LABEL[s] }))}
            hint="공공 API 값이 아니라 사용자가 고른 값입니다."
          />
        </div>
      )}
      <p className="text-small" aria-live="polite">
        {info.source === "api" && info.tmaxC !== null ? (
          <>
            <strong className="num font-semibold">{info.regionName}</strong> 일 최고기온 <strong className="num font-semibold">{info.tmaxC.toFixed(1)}℃</strong> → {HEAT_STAGE_LABEL[info.stage]}
            <span className="dev-only block text-caption text-ink-faint">
              횡단보도에서 가장 가까운 시군구 중심 {info.regionKm?.toFixed(1)} km (근사, 경계 판정 아님). 33℃ 이상 주의보, 35℃ 이상 경보 수준: 기상청 특보는 체감온도 기준이라 최고기온으로 근사했습니다.
            </span>
          </>
        ) : (
          <>폭염 단계: <strong className="font-semibold">{HEAT_STAGE_LABEL[info.stage]}</strong> (직접 선택)</>
        )}
      </p>
      <div className="dev-only">
        <Button variant="ghost" onClick={() => heat.setManual(!heat.manual)} aria-pressed={heat.manual}>
          {heat.manual ? "API 값 사용" : "폭염 단계 직접 선택"}
        </Button>
        {heat.problem !== null && <Button variant="ghost" onClick={heat.reloadAll}>API 다시 시도</Button>}
      </div>
    </div>
  );
}
