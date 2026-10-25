import { useState } from "react";
import { HEAT_MOCK, describeHeatError, heatApi } from "../api/heat";
import type { HeatEvalRow, HeatMeta, HeatVariant } from "../api/heatTypes";
import { useApi } from "../api/useApi";
import { PageHeader } from "../components/AppShell";
import { AsyncBoundary } from "../components/AsyncBoundary";
import { Card } from "../components/Card";
import { ComparisonChart } from "../components/ComparisonChart";
import { SelectField } from "../components/Field";
import { GainDecomposition } from "../components/heat/GainDecomposition";
import { HeroCompare } from "../components/heat/HeroCompare";
import { CurveChart } from "../components/heat/CurveChart";
import { DeltaForest } from "../components/heat/DeltaForest";
import { VariantToggle } from "../components/heat/VariantToggle";
import { EmptyState, Notice } from "../components/Notice";
import { ScrollRegion } from "../components/ScrollRegion";
import { SkeletonText } from "../components/Skeleton";
import { HEAT_DEFAULT_VARIANT } from "../config";
import { formatPercent } from "../lib/format";
import { METHOD_LABEL, METHOD_NOTE, POOLED_YEAR, comparisonRows, forestRows, heroPair, pick, summarizeGains, yearLabel } from "../lib/heatEval";
import type { HeatMethod } from "../api/heatTypes";

const TABLE_METHODS: readonly HeatMethod[] = ["B1", "B1S", "B2", "HEUR", "OURS"];

function MethodTable({ rows, years }: { rows: readonly HeatEvalRow[]; years: readonly number[] }) {
  return (
    <ScrollRegion label="방법별 포착률 표">
      <table className="w-full min-w-[30rem] border-collapse text-small">
        <caption className="sr-only">방법별, 연도별 환자 포착률과 95% 신뢰구간</caption>
        <thead className="bg-surface-sunken text-left text-ink-muted">
          <tr>
            <th scope="col" className="px-3 py-2 font-medium">방법</th>
            {years.map((y) => <th key={y} scope="col" className="px-3 py-2 text-right font-medium">{yearLabel(y)}</th>)}
          </tr>
        </thead>
        <tbody>
          {TABLE_METHODS.map((m) => (
            <tr key={m} className="border-t border-line">
              <th scope="row" className="px-3 py-2 text-left font-normal">
                <span className={m === "OURS" ? "font-semibold text-accent-strong" : "font-medium"}>{METHOD_LABEL[m]}</span>
                <span className="block text-caption text-ink-faint">{METHOD_NOTE[m]}</span>
              </th>
              {years.map((y) => {
                const r = pick(rows, y, m);
                return (
                  <td key={y} className="num px-3 py-2 text-right">
                    {r === undefined ? "–" : (
                      <>
                        <span className="font-medium">{formatPercent(r.value)}</span>
                        {r.ci_lo !== null && r.ci_hi !== null && (
                          <span className="block text-caption text-ink-faint">{formatPercent(r.ci_lo)} ~ {formatPercent(r.ci_hi)}</span>
                        )}
                      </>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </ScrollRegion>
  );
}

function Results({ meta }: { meta: HeatMeta }) {
  const [variant, setVariant] = useState<HeatVariant>(meta.variants.includes(HEAT_DEFAULT_VARIANT) ? HEAT_DEFAULT_VARIANT : (meta.variants[0] ?? "F"));
  const [curveYear, setCurveYear] = useState(meta.years[meta.years.length - 1] ?? 0);
  const evalF = useApi(() => heatApi.eval("F"), { describe: describeHeatError });
  const evalO = useApi(() => heatApi.eval("O"), { describe: describeHeatError });
  const curve = useApi(() => heatApi.curve(curveYear, variant), { key: `${curveYear}|${variant}`, describe: describeHeatError });
  const current = variant === "F" ? evalF : evalO;
  const skeleton = <SkeletonText lines={6} label="결과를 불러오는 중" />;

  return (
    <div className="space-y-8">
      <VariantToggle value={variant} onChange={setVariant} variants={meta.variants} />

      <AsyncBoundary state={current.state} onRetry={current.reload} skeleton={skeleton} errorTitle="포착률을 불러오지 못했습니다">
        {(rows) => <HeroCompare rows={rows} variant={variant} />}
      </AsyncBoundary>

      <Card title="연도별 환자 포착률" caption="포착률 = 경보를 낸 지역에서 나온 환자 ÷ 전체 환자. 그날 특보 규칙과 같은 수만 경보합니다. 회색이 특보 규칙, 파랑이 우리 모형입니다.">
        <AsyncBoundary state={current.state} onRetry={current.reload} skeleton={skeleton} errorTitle="포착률을 불러오지 못했습니다">
          {(rows) => {
            const chartRows = comparisonRows(rows, meta.years);
            return chartRows.length === 0 ? (
              <EmptyState title="이 변형의 평가 결과가 없습니다">`make heat-eval`로 평가를 실행하면 연도별 막대가 나타납니다.</EmptyState>
            ) : (
              <div className="space-y-8">
                <ComparisonChart
                  title={`변형 ${variant}: 시험 연도별 포착률`}
                  baselineLabel="특보 규칙"
                  oursLabel="우리 모형"
                  rows={chartRows}
                  valueLabel="높을수록 좋음"
                />
                <MethodTable rows={rows} years={rows.some((r) => r.year === POOLED_YEAR) ? [...meta.years, POOLED_YEAR] : meta.years} />
              </div>
            );
          }}
        </AsyncBoundary>
      </Card>

      <Card title="엔진 구성 요소별 기여" caption="특보 규칙에서 시작해 요소를 하나씩 더하며, 같은 경보 수에서 포착률이 얼마나 오르는지 봅니다.">
        <AsyncBoundary state={current.state} onRetry={current.reload} skeleton={skeleton} errorTitle="단계별 비교를 불러오지 못했습니다">
          {(rows) => {
            const year = heroPair(rows)?.year ?? POOLED_YEAR;
            return <GainDecomposition rows={rows} year={year} />;
          }}
        </AsyncBoundary>
      </Card>

      <Card title="차이 (우리 − B1)" caption="특보 규칙 → 우리 모형으로 바꿨을 때 포착률이 몇 %p 오르는지, 95% 신뢰구간과 함께 보여줍니다. 입력 시점 F와 O 모두입니다.">
        <AsyncBoundary state={evalF.state} onRetry={evalF.reload} skeleton={skeleton} errorTitle="차이를 불러오지 못했습니다">
          {(f) => (
            <AsyncBoundary state={evalO.state} onRetry={evalO.reload} skeleton={skeleton} errorTitle="차이를 불러오지 못했습니다">
              {(o) => {
                const rows = forestRows({ F: f, O: o });
                if (rows.length === 0) return <EmptyState title="차이 자료가 없습니다">B1 대비 쌍 차이가 저장된 연도가 없습니다.</EmptyState>;
                return (
                  <div className="space-y-6">
                    <DeltaForest rows={rows} />
                    <ul className="m-0 list-disc space-y-1 pl-5 text-body">
                      {summarizeGains(rows).map((t) => <li key={t}>{t}</li>)}
                    </ul>
                  </div>
                );
              }}
            </AsyncBoundary>
          )}
        </AsyncBoundary>
      </Card>

      <Card
        title="경보 수를 바꾸면"
        caption="특보 규칙의 경보 수를 0.25배에서 2배까지 바꿔도 우리 모형이 앞서는지 봅니다."
        actions={
          <div className="w-32">
            <SelectField
              label="연도"
              value={String(curveYear)}
              onChange={(e) => setCurveYear(Number(e.target.value))}
              options={meta.years.map((y) => ({ value: String(y), label: `${y}년` }))}
            />
          </div>
        }
      >
        <AsyncBoundary state={curve.state} onRetry={curve.reload} skeleton={skeleton} errorTitle="예산 곡선을 불러오지 못했습니다">
          {(c) => <CurveChart curve={c} title={`${curveYear}년, 변형 ${variant}: 경보 수에 따른 포착률`} />}
        </AsyncBoundary>
      </Card>

      <details className="border border-line bg-surface px-4 py-3 sm:px-6">
        <summary className="cursor-pointer text-body font-medium">방법과 한계</summary>
        <ul className="m-0 mt-3 max-w-prose list-disc space-y-1 pl-5 text-small text-ink-muted">
          <li>경보 수는 날마다 특보 규칙이 낸 수 K와 같습니다. 달라지는 것은 어느 시군구에 쓰느냐입니다.</li>
          <li>시험 연도는 {meta.years.join(", ")}이며 연도별 신뢰구간을 그대로 보여줍니다. 신뢰구간은 7일 블록 부트스트랩(1,000회)으로 구했습니다.</li>
          <li>특보 규칙은 특보 문구를 기온으로 재현한 규칙이며 실제 특보 발령 기록이 아닙니다.</li>
          <li>입력 시점 F는 당일·다음날 기온을 예보 대용으로 씁니다. O는 전날까지의 정보만 씁니다.</li>
          <li>보조 지표(AUC, PR-AUC, 정밀도)는 API에 저장되어 있으며 이 화면의 주 지표는 포착률입니다.</li>
          <li>시간대 예측, 인과, 쉼터의 효과는 주장하지 않습니다. 쉼터는 참고 정보입니다.</li>
        </ul>
      </details>
    </div>
  );
}

export function ResultsPage() {
  const meta = useApi(heatApi.meta, { describe: describeHeatError });
  return (
    <>
      <PageHeader
        eyebrow="결과"
        title="기준선 대비 포착률"
        lead="같은 경보 수에서 우리 모형이 특보 규칙보다 더 많은 환자를 잡는지, 시험 연도마다 95% 신뢰구간과 함께 확인합니다."
      />
      {HEAT_MOCK && <div className="mb-6"><Notice>모의 응답으로 표시 중입니다. 합성 데이터이며 실제 평가 결과가 아닙니다.</Notice></div>}
      <AsyncBoundary state={meta.state} onRetry={meta.reload} skeleton={<Card><SkeletonText lines={6} /></Card>} errorTitle="평가 정보를 불러오지 못했습니다">
        {(m) => <Results meta={m} />}
      </AsyncBoundary>
    </>
  );
}
