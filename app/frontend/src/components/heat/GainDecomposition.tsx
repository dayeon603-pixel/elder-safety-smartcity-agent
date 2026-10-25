import type { HeatEvalRow } from "../../api/heatTypes";
import { formatPercent } from "../../lib/format";
import { DECOMP_LABEL, DECOMP_NOTE, decomposition, formatPoints, yearLabel } from "../../lib/heatEval";
import { COLOR_BASELINE, COLOR_OURS } from "../ComparisonChart";

const AXIS_STEP = 0.1;

interface GainDecompositionProps {
  rows: readonly HeatEvalRow[];
  year: number;
}

/** Capture rate after each step B1 -> B2 -> HEUR -> OURS, with the point gain of every step. */
export function GainDecomposition({ rows, year }: GainDecompositionProps) {
  const steps = decomposition(rows, year);
  if (steps === null) return null;
  const axisMax = Math.min(1, Math.ceil(Math.max(...steps.map((s) => s.capture.hi)) / AXIS_STEP) * AXIS_STEP);
  const w = (v: number): string => `${(v / axisMax) * 100}%`;
  const total = steps.length > 1 ? (steps[steps.length - 1]?.capture.point ?? 0) - (steps[0]?.capture.point ?? 0) : null;
  return (
    <figure className="m-0">
      <figcaption className="mb-4 text-small font-medium text-ink">엔진 구성 요소별 기여 ({yearLabel(year)}), 같은 경보 수</figcaption>
      <ol className="m-0 list-none space-y-5 p-0">
        {steps.map((s) => {
          const ours = s.method === "OURS";
          return (
            <li key={s.method}>
              <p className="flex flex-wrap items-baseline justify-between gap-x-4 text-small">
                <span className={ours ? "font-semibold text-accent-strong" : "font-medium"}>{DECOMP_LABEL[s.method]}</span>
                <span className="num">
                  <span className="font-semibold">{formatPercent(s.capture.point)}</span>
                  {s.method !== "B1" && (
                    <span className="ml-2 text-ink-muted">
                      {formatPoints(s.gain)}
                      {s.share !== null && ` · 기여 ${Math.round(s.share * 100)}%`}
                    </span>
                  )}
                </span>
              </p>
              <div className="relative mt-1 h-6 bg-surface-inset" aria-hidden="true">
                <div className="absolute inset-y-0 left-0" style={{ width: w(s.capture.point), backgroundColor: ours ? COLOR_OURS : COLOR_BASELINE }} />
                <div className="absolute top-1/2 h-px -translate-y-1/2 bg-ink" style={{ left: w(s.capture.lo), width: `calc(${w(s.capture.hi)} - ${w(s.capture.lo)})` }} />
                <div className="absolute top-1/4 h-1/2 w-px bg-ink" style={{ left: w(s.capture.lo) }} />
                <div className="absolute top-1/4 h-1/2 w-px bg-ink" style={{ left: w(s.capture.hi) }} />
              </div>
              <p className="mt-1 text-caption text-ink-faint">{DECOMP_NOTE[s.method]}</p>
            </li>
          );
        })}
      </ol>
      {total !== null && (
        <p className="mt-5 max-w-prose border-l-4 border-accent bg-accent-wash/40 px-4 py-3 text-body">
          인구·취약성 반영과 시군구 모델을 더한 우리 엔진은 특보 규칙보다 <strong className="font-semibold">{formatPoints(total)}</strong> 더 많은 환자를 먼저 잡습니다.
        </p>
      )}
      <p className="mt-3 text-caption text-ink-faint">막대 = 점추정, 가는 선 = 95% 신뢰구간. 구성 요소 사이의 기여는 점추정의 차이입니다.</p>
    </figure>
  );
}
