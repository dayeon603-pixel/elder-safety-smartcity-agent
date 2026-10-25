import type { ForestRow } from "../../lib/heatEval";
import { formatPoints } from "../../lib/heatEval";
import { COLOR_OURS } from "../ComparisonChart";

const PAD_FRAC = 0.08;
const MIN_HALF_SPAN = 0.02;
const TICK_STEPS: readonly number[] = [0.01, 0.02, 0.05, 0.1, 0.2];
const MAX_TICKS = 6;

export interface Axis {
  min: number;
  max: number;
  ticks: number[];
}

/** Axis that always contains 0 and every interval, with round tick values. Pure, so it is unit-tested. */
export function forestAxis(rows: readonly Pick<ForestRow, "lo" | "hi">[]): Axis {
  const lo = Math.min(0, ...rows.map((r) => r.lo));
  const top = Math.max(0, ...rows.map((r) => r.hi));
  const span = Math.max(top - lo, MIN_HALF_SPAN * 2);
  const step = TICK_STEPS.find((s) => span / s <= MAX_TICKS) ?? TICK_STEPS[TICK_STEPS.length - 1] ?? 0.2;
  const min = lo - span * PAD_FRAC;
  const max = lo + span + span * PAD_FRAC;
  const ticks: number[] = [];
  for (let t = Math.ceil(min / step) * step; t <= max + 1e-9; t += step) ticks.push(Number(t.toFixed(4)));
  return { min, max, ticks };
}

interface DeltaForestProps {
  rows: readonly ForestRow[];
}

/**
 * Forest plot of the paired difference (ours - B1) with its 95% CI, one row per year and variant.
 * A filled marker means the interval excludes 0; a hollow one means it includes 0. The numbers are printed on
 * every row so nothing depends on reading the drawing.
 */
export function DeltaForest({ rows }: DeltaForestProps) {
  const axis = forestAxis(rows);
  const pos = (v: number): string => `${((v - axis.min) / (axis.max - axis.min)) * 100}%`;
  return (
    <figure className="m-0">
      <figcaption className="mb-3 text-small font-medium text-ink">포착률 차이 (우리 − B1), 95% 신뢰구간</figcaption>
      <ul className="m-0 list-none space-y-3 p-0">
        {rows.map((r) => (
          <li key={r.key}>
            <p className="flex flex-wrap items-baseline justify-between gap-x-4 text-small">
              <span className="font-medium">{r.label}</span>
              <span className="num">
                <span className="font-semibold">{formatPoints(r.value)}</span>{" "}
                <span className="text-ink-muted">[{formatPoints(r.lo)}, {formatPoints(r.hi)}]</span>{" "}
                <span className={r.significant ? "text-accent-strong" : "text-ink-muted"}>{r.significant ? "0 제외" : "0 포함"}</span>
              </span>
            </p>
            <div className="relative mt-1 h-5" aria-hidden="true">
              <div className="absolute inset-y-0 w-px bg-ink" style={{ left: pos(0) }} />
              <div className="absolute top-1/2 h-0.5 -translate-y-1/2" style={{ left: pos(r.lo), width: `calc(${pos(r.hi)} - ${pos(r.lo)})`, backgroundColor: COLOR_OURS }} />
              <div
                className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 border-2"
                style={{ left: pos(r.value), borderColor: COLOR_OURS, backgroundColor: r.significant ? COLOR_OURS : "#ffffff" }}
              />
            </div>
          </li>
        ))}
      </ul>
      <div className="relative mt-1 h-5 border-t border-line text-caption text-ink-faint" aria-hidden="true">
        {axis.ticks.map((t) => (
          <span key={t} className="num absolute top-1 -translate-x-1/2" style={{ left: pos(t) }}>
            {t === 0 ? "0" : formatPoints(t, 0)}
          </span>
        ))}
      </div>
      <p className="mt-4 text-caption text-ink-faint">세로선 = 차이 0. 채운 네모 = 구간이 0을 제외, 빈 네모 = 구간이 0을 포함(우연과 구분되지 않음).</p>
    </figure>
  );
}
