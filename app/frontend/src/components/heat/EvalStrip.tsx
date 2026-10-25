import type { HeatEvalRow, HeatMethod, HeatVariant } from "../../api/heatTypes";
import { formatPercent } from "../../lib/format";
import { CAPTURE, METHOD_LABEL, formatPoints, pick, toInterval } from "../../lib/heatEval";
import { EmptyState } from "../Notice";
import { StatTile } from "../StatTile";

const SECONDARY: readonly HeatMethod[] = ["B1S", "B2", "HEUR"];

function ciText(r: HeatEvalRow): string {
  const { lo, hi } = toInterval(r);
  return `95% 신뢰구간 ${formatPercent(lo)} ~ ${formatPercent(hi)}`;
}

interface EvalStripProps {
  rows: readonly HeatEvalRow[];
  year: number;
  variant: HeatVariant;
  /** One line instead of tiles, for when the map and panel need the space (step 3). */
  compact?: boolean;
}

/** Season-level capture rate for the selected year: B1 vs ours as tiles, the other baselines as one line. */
export function EvalStrip({ rows, year, variant, compact = false }: EvalStripProps) {
  const b1 = pick(rows, year, "B1");
  const ours = pick(rows, year, "OURS");
  if (b1 === undefined || ours === undefined) {
    return (
      <EmptyState title={`${year}년 포착률이 아직 없습니다`}>
        이 연도와 변형({variant})의 평가 결과가 저장되어 있지 않습니다.
      </EmptyState>
    );
  }
  const delta = ours.delta_vs_b1;
  const dIv = delta === null ? null : toInterval(delta);
  if (compact) {
    return (
      <p className="text-small text-ink-muted">
        {year}년 시즌 포착률(변형 {variant}): B1 <span className="num font-medium text-ink">{formatPercent(b1.value)}</span> → 우리{" "}
        <span className="num font-medium text-accent-strong">{formatPercent(ours.value)}</span>
        {dIv !== null && <span className="num"> ({formatPoints(dIv.point)}, 95% 신뢰구간 {dIv.lo > 0 ? "0 제외" : "0 포함"})</span>}
      </p>
    );
  }
  const others = SECONDARY.flatMap((m) => {
    const r = pick(rows, year, m, CAPTURE);
    return r === undefined ? [] : [`${METHOD_LABEL[m].split(" ")[0]} ${formatPercent(r.value)}`];
  });
  return (
    <section aria-label={`${year}년 시즌 포착률, 변형 ${variant}`} className="space-y-3">
      <p className="text-small text-ink-muted">
        <span className="font-medium text-ink">{year}년 시즌 전체</span> · 변형 {variant} · 환자 포착률 (경보 지역에서 나온 환자 ÷ 전체 환자)
      </p>
      <dl className="m-0 grid grid-cols-2 gap-3 sm:gap-4">
        <StatTile label="B1 특보식 규칙" value={formatPercent(b1.value)} note={ciText(b1)} />
        <StatTile label="우리 모형" value={formatPercent(ours.value)} note={ciText(ours)} tone="accent" />
      </dl>
      {dIv !== null && (
        <p className="text-small">
          <span className="text-ink-muted">차이(우리−B1) </span>
          <span className="num font-semibold">{formatPoints(dIv.point)}</span>
          <span className="num text-ink-muted"> [{formatPoints(dIv.lo)}, {formatPoints(dIv.hi)}]</span>
          <span className="text-ink-muted"> · {dIv.lo > 0 ? "95% 신뢰구간이 0을 제외합니다" : "95% 신뢰구간이 0을 포함합니다"}</span>
        </p>
      )}
      {others.length > 0 && <p className="text-caption text-ink-faint">참고 기준선: {others.join(" · ")}</p>}
    </section>
  );
}
