import type { HeatEvalRow, HeatVariant } from "../../api/heatTypes";
import { formatPercent } from "../../lib/format";
import { GLOSS, formatPoints, heroPair, POOLED_YEAR, yearLabel } from "../../lib/heatEval";
import { COLOR_BASELINE } from "../ComparisonChart";
import { InfoTip } from "../InfoTip";

const pct0 = (x: number): string => `${Math.round(x * 100)}%`;

function Big({ label, value, ci, tone }: { label: string; value: number; ci: { lo: number; hi: number }; tone: "base" | "ours" }) {
  return (
    <div className={`border bg-surface px-4 py-5 sm:px-8 sm:py-6 ${tone === "ours" ? "border-accent" : "border-line"}`}>
      <dt className="text-small font-medium text-ink-muted">{label}</dt>
      <dd className="m-0">
        <span
          className={`num block text-5xl font-semibold leading-tight tracking-tight sm:text-7xl ${tone === "ours" ? "text-accent" : "text-ink-muted"}`}
          style={tone === "base" ? { color: "#4a515c" } : undefined}
        >
          {pct0(value)}
        </span>
        <span className="num mt-1 block text-caption text-ink-faint">95% 신뢰구간 {pct0(ci.lo)}–{pct0(ci.hi)}</span>
        <span aria-hidden="true" className="mt-3 block h-1.5 w-full bg-surface-inset">
          <span className="block h-full" style={{ width: pct0(value), backgroundColor: tone === "ours" ? "#0072B2" : COLOR_BASELINE }} />
        </span>
      </dd>
    </div>
  );
}

interface HeroCompareProps {
  rows: readonly HeatEvalRow[];
  variant: HeatVariant;
}

/**
 * The one comparison to remember: capture rate of the rule B1 against ours at the same alert count.
 * Uses the pooled 2023-25 estimate when the API provides it, otherwise the latest year (labelled).
 */
export function HeroCompare({ rows, variant }: HeroCompareProps) {
  const pair = heroPair(rows);
  if (pair === null) return null;
  const perYear = rows
    .filter((r) => r.method === "OURS" && r.metric === "capture" && r.year !== POOLED_YEAR && r.delta_vs_b1 !== null)
    .sort((a, b) => a.year - b.year);
  const years = [...new Set(rows.map((r) => r.year))].filter((y) => y !== POOLED_YEAR).sort((a, b) => a - b);
  const range = years.length > 1 ? `${years[0]}–${years[years.length - 1]}` : `${years[0] ?? ""}`;
  const scope = pair.year === POOLED_YEAR ? `${range} 시험 시즌 합산` : `${yearLabel(pair.year)}년 시험 시즌`;
  return (
    <section aria-label="핵심 비교: 특보 규칙과 우리 모형의 환자 포착률" className="space-y-3">
      {pair.delta !== null && (
        <h2 className="text-h2 sm:text-h1">
          같은 경보 수로 온열질환자를 <span className="num text-accent">{Math.round(pair.delta.point * 100)}%p</span> 더 먼저
        </h2>
      )}
      <p className="text-small text-ink-muted">
        환자 포착률 <InfoTip label="포착률">{GLOSS.capture}</InfoTip> · {scope} · 입력 시점 {variant}
        <InfoTip label="입력 시점">{GLOSS.variant}</InfoTip>
      </p>
      <dl className="m-0 grid grid-cols-2 gap-3 sm:gap-6">
        <Big label="특보 규칙 (B1)" value={pair.b1.point} ci={pair.b1} tone="base" />
        <Big label="우리 모형" value={pair.ours.point} ci={pair.ours} tone="ours" />
      </dl>
      <p className="max-w-prose text-body">
        경보 수는 그대로 두고 고르는 시군구만 바꿨더니, 환자 100명 중 {Math.round(pair.b1.point * 100)}명이 아니라{" "}
        <strong className="font-semibold text-accent-strong">{Math.round(pair.ours.point * 100)}명</strong>이 경보 지역에 있었습니다.
      </p>
      {pair.delta !== null && (
        <p className="num text-small text-ink-muted">
          차이 {formatPoints(pair.delta.point)}
          <InfoTip label="%p">{GLOSS.pp}</InfoTip>
          · 95% 신뢰구간 [{formatPoints(pair.delta.lo)}, {formatPoints(pair.delta.hi)}]
          <InfoTip label="95% 신뢰구간">{GLOSS.ci}</InfoTip>
          {perYear.length > 0 && (
            <>
              {" · 연도별 "}
              {perYear.map((r, i) => (
                <span key={r.year}>
                  {i > 0 && ", "}
                  {r.year} {formatPoints(r.delta_vs_b1?.value ?? Number.NaN)}
                </span>
              ))}
            </>
          )}
        </p>
      )}
      <span className="sr-only">{formatPercent(pair.b1.point)} 대 {formatPercent(pair.ours.point)}</span>
    </section>
  );
}
