import type { Run } from "../api/types";
import { formatInt, formatInterval } from "../lib/format";
import { higherIsBetter, isSignificantGain, metricLabel, orderMetrics } from "../lib/metrics";
import { Badge } from "./Badge";
import type { ComparisonRow } from "./ComparisonChart";
import { ChartLegend, ComparisonChart } from "./ComparisonChart";
import { ScrollRegion } from "./ScrollRegion";
import { SourceNote } from "./SourceNote";

/** Latest {baseline, ours} pair from /runs/comparison. Baseline is always first. */
export function pairOf(runs: readonly Run[]): { baseline: Run; ours: Run } | null {
  const baseline = runs.find((r) => r.role === "baseline");
  const ours = runs.find((r) => r.role === "ours");
  return baseline !== undefined && ours !== undefined ? { baseline, ours } : null;
}

function rowsFor(pair: { baseline: Run; ours: Run }, keys: readonly string[]): ComparisonRow[] {
  return keys.flatMap((k) => {
    const b = pair.baseline.metrics.point_ci[k];
    const o = pair.ours.metrics.point_ci[k];
    return b === undefined || o === undefined ? [] : [{ key: k, label: metricLabel(k), baseline: b, ours: o }];
  });
}

/** Round the CI upper bound up to a tidy axis maximum for error-type metrics. */
function errorDomain(rows: readonly ComparisonRow[]): [number, number] {
  const max = Math.max(...rows.flatMap((r) => [r.baseline.hi, r.ours.hi]));
  const step = 0.05;
  return [0, Math.max(step, Math.ceil(max / step) * step)];
}

export function ComparisonResult({ pair, showSource = true }: { pair: { baseline: Run; ours: Run }; showSource?: boolean }) {
  const { baseline, ours } = pair;
  const shared = orderMetrics(Object.keys(ours.metrics.point_ci).filter((m) => m in baseline.metrics.point_ci));
  const upRows = rowsFor(pair, shared.filter(higherIsBetter));
  const downRows = rowsFor(pair, shared.filter((k) => !higherIsBetter(k)));
  const delta = ours.metrics.delta_vs_baseline ?? {};

  return (
    <div className="space-y-8">
      <ChartLegend baselineLabel={baseline.method} oursLabel={ours.method} />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-8 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        {upRows.length > 0 && (
          <ComparisonChart
            title="높을수록 좋은 지표"
            baselineLabel={baseline.method}
            oursLabel={ours.method}
            rows={upRows}
            hideLegend
            domain={[0, 1]}
          />
        )}
        {downRows.length > 0 && (
          <ComparisonChart
            title="낮을수록 좋은 지표"
            baselineLabel={baseline.method}
            oursLabel={ours.method}
            rows={downRows}
            hideLegend
            domain={errorDomain(downRows)}
          />
        )}
      </div>

      <ScrollRegion label="지표별 비교표">
        <table className="w-full min-w-[40rem] border-collapse text-left text-small">
          <caption className="sr-only">지표별 기준선과 비교 방법의 점추정과 95% 신뢰구간</caption>
          <thead>
            <tr className="border-b border-line-strong bg-surface-sunken text-caption text-ink-muted">
              <th scope="col" className="px-4 py-2 font-medium">지표</th>
              <th scope="col" className="px-4 py-2 text-right font-medium">기준선 (95% CI)</th>
              <th scope="col" className="px-4 py-2 text-right font-medium">비교 (95% CI)</th>
              <th scope="col" className="px-4 py-2 text-right font-medium">차이 = 비교 − 기준선 (95% CI)</th>
              <th scope="col" className="px-4 py-2 font-medium">판정</th>
            </tr>
          </thead>
          <tbody>
            {shared.map((k) => {
              const b = baseline.metrics.point_ci[k];
              const o = ours.metrics.point_ci[k];
              const d = delta[k];
              if (b === undefined || o === undefined) return null;
              const better = isSignificantGain(d, k);
              return (
                <tr key={k} className="border-b border-line last:border-b-0">
                  <th scope="row" className="px-4 py-3 font-medium">
                    {metricLabel(k)}
                    {!higherIsBetter(k) && <span className="ml-2 text-caption font-normal text-ink-faint">낮을수록 좋음</span>}
                  </th>
                  <td className="num whitespace-nowrap px-4 py-3 text-right font-mono text-caption">{formatInterval(b)}</td>
                  <td className="num whitespace-nowrap px-4 py-3 text-right font-mono text-caption">{formatInterval(o)}</td>
                  <td className={`num whitespace-nowrap px-4 py-3 text-right font-mono text-caption ${better ? "font-semibold text-accent-strong" : ""}`}>
                    {d === undefined ? "–" : formatInterval(d, true)}
                  </td>
                  <td className="px-4 py-3">
                    {d === undefined ? <Badge>비교 불가</Badge> : better ? <Badge tone="accent">유의한 개선</Badge> : <Badge>차이 불확실</Badge>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </ScrollRegion>

      <p className="max-w-prose text-caption text-ink-faint">
        {ours.split_kind === "temporal" ? "시간순" : "무작위"} 분할 · 시드 {ours.seed} · 학습 {formatInt(ours.n_train)}건 · 시험{" "}
        {formatInt(ours.n_test)}건 · 커밋 <code>{ours.git_hash}</code>. 유의한 개선 = 차이의 95% 신뢰구간이 0을 포함하지 않으며 유리한 방향. 시험 집합
        부트스트랩만 반영하며 학습 변동과 분포 이동은 반영하지 않습니다.
      </p>
      {showSource && <SourceNote datasetId={ours.dataset_id} />}
    </div>
  );
}
