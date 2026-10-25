import { useEffect, useId, useState } from "react";
import { describeHeatError, heatApi } from "../../api/heat";
import type { HeatPlan, HeatPlanEval, HeatVariant } from "../../api/heatTypes";
import { useApi } from "../../api/useApi";
import { PLAN_DEBOUNCE_MS, PLAN_SHELTERS, PLAN_TEAMS_DEFAULT, PLAN_TEAMS_MAX, PLAN_TEAMS_MIN } from "../../config";
import { formatDecimal, formatInt } from "../../lib/format";
import { dayLabel } from "../../lib/heatDays";
import { AsyncBoundary } from "../AsyncBoundary";
import { InfoTip } from "../InfoTip";
import { EmptyState } from "../Notice";
import { SkeletonText } from "../Skeleton";

function SeasonLine({ ev }: { ev: HeatPlanEval }) {
  return (
    <p className="num text-small text-ink-muted">
      {ev.year}년 시즌 {formatInt(ev.n_days)}일 합계: 특보 기준 {formatInt(ev.b1_reached)}명 → 우리 {formatInt(ev.ours_reached)}명
      <span className="text-ink-faint"> (95% 신뢰구간 {formatInt(ev.ci_lo)} ~ {formatInt(ev.ci_hi)}명 차이)</span>
    </p>
  );
}

function PlanBody({ plan, season }: { plan: HeatPlan; season: HeatPlanEval | null }) {
  const { ours_reached: ours, b1_reached: b1 } = plan.compare;
  return (
    <div className="space-y-8">
      <section aria-label="같은 인력으로 만난 환자" className="space-y-2">
        <p className="text-small text-ink-muted">
          같은 인력으로 만난 환자
          <InfoTip label="같은 인력으로 만난 환자">두 방법이 같은 수의 팀을 배치했을 때, 팀이 배치된 지역에서 나온 환자 수입니다.</InfoTip>
        </p>
        <p className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <span className="num text-h2 font-semibold text-ink-muted">특보 기준 {formatInt(b1)}명</span>
          <span aria-hidden="true" className="text-h2 text-ink-faint">→</span>
          <span className="num text-h1 font-semibold text-accent">우리 {formatInt(ours)}명</span>
        </p>
        <p className="num text-small text-ink-muted">{dayLabel(plan.date)} · 대응 팀 {formatInt(plan.teams)}개</p>
        {season !== null && <SeasonLine ev={season} />}
      </section>

      <section aria-labelledby="plan-alloc" className="space-y-3">
        <h3 id="plan-alloc" className="text-h3">팀 배치</h3>
        {plan.allocations.length === 0 ? (
          <EmptyState title="배치할 지역이 없습니다">이날은 배치 기준을 넘는 시군구가 없습니다.</EmptyState>
        ) : (
          <ol className="m-0 list-none divide-y divide-line border border-line p-0">
            {plan.allocations.map((a) => (
              <li key={a.code} className="flex items-start gap-4 px-4 py-3">
                <span className="num flex h-11 min-w-11 shrink-0 items-center justify-center border border-accent bg-accent-wash text-body font-semibold text-accent-strong">
                  {a.teams}팀
                </span>
                <span className="min-w-0">
                  <span className="block text-body font-medium">{a.name}</span>
                  <span className="block text-small text-ink-muted">{a.reason}</span>
                  <span className="num block text-caption text-ink-faint">예상 {formatDecimal(a.expected_cases, 1)}명 → 실제 {formatInt(a.actual_cases)}명</span>
                </span>
              </li>
            ))}
          </ol>
        )}
      </section>

      <section aria-labelledby="plan-shelter" className="space-y-3">
        <h3 id="plan-shelter" className="text-h3">운영 연장을 권하는 쉼터</h3>
        {plan.shelter_extensions.length === 0 ? (
          <p className="text-small text-ink-muted">이날은 연장을 권할 쉼터가 없습니다.</p>
        ) : (
          <ul className="m-0 list-none divide-y divide-line border border-line p-0">
            {plan.shelter_extensions.map((s) => (
              <li key={s.shelter_id} className="px-4 py-3">
                <p className="text-body font-medium">{s.name}</p>
                <p className="text-small text-ink-muted">{s.reason}</p>
              </li>
            ))}
          </ul>
        )}
        {plan.unknown_hours_count > 0 && (
          <p className="text-caption text-ink-faint">운영 시간 자료가 없는 쉼터 {formatInt(plan.unknown_hours_count)}곳은 제안에서 뺐습니다.</p>
        )}
      </section>
    </div>
  );
}

interface PlanStepProps {
  date: string;
  variant: HeatVariant;
}

/** Step 4: pick a team count, see the allocation, the shelters to extend, and the two-number comparison. */
export function PlanStep({ date, variant }: PlanStepProps) {
  const sliderId = useId();
  const [draft, setDraft] = useState(PLAN_TEAMS_DEFAULT);
  const [teams, setTeams] = useState(PLAN_TEAMS_DEFAULT);
  // Debounce: dragging the slider should not fire one request per tick.
  useEffect(() => {
    const t = window.setTimeout(() => setTeams(draft), PLAN_DEBOUNCE_MS);
    return () => window.clearTimeout(t);
  }, [draft]);
  const plan = useApi(() => heatApi.plan(date, variant, teams, PLAN_SHELTERS), { key: `${date}|${variant}|${teams}`, describe: describeHeatError });
  const year = Number(date.slice(0, 4));
  const seasonEval = useApi(() => heatApi.planEval(year, teams), { key: `${year}|${teams}`, describe: describeHeatError });
  return (
    <div className="space-y-8">
      <div className="max-w-md space-y-2">
        <label htmlFor={sliderId} className="flex items-baseline justify-between text-small font-medium">
          <span>대응 팀 수</span>
          <output htmlFor={sliderId} className="num text-h3 font-semibold">{draft}팀</output>
        </label>
        <input
          id={sliderId}
          type="range"
          min={PLAN_TEAMS_MIN}
          max={PLAN_TEAMS_MAX}
          step={1}
          value={draft}
          onChange={(e) => setDraft(Number(e.target.value))}
          className="h-11 w-full accent-[#0072B2]"
        />
        <p className="flex justify-between text-caption text-ink-faint"><span>{PLAN_TEAMS_MIN}팀</span><span>{PLAN_TEAMS_MAX}팀</span></p>
      </div>
      <AsyncBoundary state={plan.state} onRetry={plan.reload} errorTitle="대응안을 불러오지 못했습니다" skeleton={<SkeletonText lines={8} label="대응안을 불러오는 중" />}>
        {(p) => <PlanBody plan={p} season={seasonEval.state.status === "ready" ? seasonEval.state.data : null} />}
      </AsyncBoundary>
    </div>
  );
}
