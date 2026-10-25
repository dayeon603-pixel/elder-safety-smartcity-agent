import { Bar, BarChart, CartesianGrid, ErrorBar, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Interval } from "../api/types";
import { formatInterval } from "../lib/format";

/** Figure palette shared with the paper figures: baseline = gray, ours = #0072B2. */
export const COLOR_BASELINE = "#858c98";
export const COLOR_OURS = "#0072B2";
const COLOR_AXIS = "#d9dde3";
const COLOR_TEXT = "#4a515c";
const COLOR_WHISKER = "#14171c";

export interface ComparisonRow {
  key: string;
  label: string;
  baseline: Interval;
  ours: Interval;
}

interface ComparisonChartProps {
  title: string;
  baselineLabel: string;
  oursLabel: string;
  rows: readonly ComparisonRow[];
  /** Value axis domain. Zero-based by default: truncating a bar axis exaggerates differences. */
  domain?: readonly [number, number];
  height?: number;
  /** Axis name, e.g. "점수 (높을수록 좋음)". */
  valueLabel?: string;
  /** Hide the legend when a parent renders one shared legend. */
  hideLegend?: boolean;
}

const DEFAULT_HEIGHT = 288;

interface Datum {
  label: string;
  baseline: number;
  ours: number;
  baselineErr: [number, number];
  oursErr: [number, number];
  baselineText: string;
  oursText: string;
}

function toDatum(r: ComparisonRow): Datum {
  return {
    label: r.label,
    baseline: r.baseline.point,
    ours: r.ours.point,
    baselineErr: [r.baseline.point - r.baseline.lo, r.baseline.hi - r.baseline.point],
    oursErr: [r.ours.point - r.ours.lo, r.ours.hi - r.ours.point],
    baselineText: formatInterval(r.baseline),
    oursText: formatInterval(r.ours),
  };
}

function Swatch({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-small text-ink">
      <span aria-hidden="true" className="inline-block h-3 w-3" style={{ backgroundColor: color }} />
      {label}
    </span>
  );
}

export function ChartLegend({ baselineLabel, oursLabel }: { baselineLabel: string; oursLabel: string }) {
  return (
    <span className="flex flex-wrap gap-x-4 gap-y-1">
      <Swatch color={COLOR_BASELINE} label={`기준선 · ${baselineLabel}`} />
      <Swatch color={COLOR_OURS} label={`비교 · ${oursLabel}`} />
    </span>
  );
}

/** Evenly spaced round ticks (at most 5 intervals) so axes never show 0.08 / 0.04 style artefacts. */
export function niceTicks(max: number): number[] {
  const step = [0.01, 0.02, 0.05, 0.1, 0.25].find((s) => max / s <= 5) ?? max / 5;
  return Array.from({ length: Math.round(max / step) + 1 }, (_, i) => Number((i * step).toFixed(4)));
}

interface TipProps {
  active?: boolean;
  payload?: { payload: Datum }[];
}

function Tip({ active, payload }: TipProps) {
  const d = payload?.[0]?.payload;
  if (active !== true || d === undefined) return null;
  return (
    <div className="border border-line-strong bg-surface px-3 py-2 text-small shadow-[0_2px_8px_rgba(20,23,28,0.12)]">
      <p className="font-medium">{d.label}</p>
      <p className="num text-ink-muted">기준선 {d.baselineText}</p>
      <p className="num text-accent-strong">비교 {d.oursText}</p>
    </div>
  );
}

/**
 * Baseline (gray) vs ours (accent) grouped bars with 95% CI whiskers.
 * Baseline is always the first series. A visually hidden table carries the same numbers
 * for assistive tech, since SVG charts are not navigable.
 */
export function ComparisonChart({
  title, baselineLabel, oursLabel, rows, domain = [0, 1], height = DEFAULT_HEIGHT, valueLabel, hideLegend = false,
}: ComparisonChartProps) {
  const data = rows.map(toDatum);
  return (
    <figure className="m-0">
      <figcaption className="mb-3 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
        <span className="text-small font-medium text-ink">{title}</span>
        {!hideLegend && <ChartLegend baselineLabel={baselineLabel} oursLabel={oursLabel} />}
      </figcaption>
      <div style={{ height }} aria-hidden="true">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }} barCategoryGap="28%" barGap={4}>
            <CartesianGrid stroke={COLOR_AXIS} vertical={false} />
            <XAxis dataKey="label" tickLine={false} axisLine={{ stroke: COLOR_AXIS }} tick={{ fill: COLOR_TEXT, fontSize: 12 }} />
            <YAxis
              domain={[domain[0], domain[1]]}
              ticks={niceTicks(domain[1])}
              tickLine={false}
              axisLine={false}
              width={36}
              tick={{ fill: COLOR_TEXT, fontSize: 12 }}
              tickFormatter={(v: number) => v.toLocaleString("ko-KR")}
            />
            <Tooltip content={<Tip />} cursor={{ fill: "#f4f5f7" }} />
            <Bar dataKey="baseline" fill={COLOR_BASELINE} isAnimationActive={false}>
              <ErrorBar dataKey="baselineErr" stroke={COLOR_WHISKER} strokeWidth={1.25} width={5} />
            </Bar>
            <Bar dataKey="ours" fill={COLOR_OURS} isAnimationActive={false}>
              <ErrorBar dataKey="oursErr" stroke={COLOR_WHISKER} strokeWidth={1.25} width={5} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-2 text-caption text-ink-faint">
        막대 = 점추정, 수염 = 95% 신뢰구간(부트스트랩).{valueLabel !== undefined ? ` ${valueLabel}.` : ""}
      </p>
      <div className="sr-only">
      <table>
        <caption>{title}</caption>
        <thead>
          <tr>
            <th scope="col">지표</th>
            <th scope="col">기준선 {baselineLabel} (95% CI)</th>
            <th scope="col">비교 {oursLabel} (95% CI)</th>
          </tr>
        </thead>
        <tbody>
          {data.map((d) => (
            <tr key={d.label}>
              <th scope="row">{d.label}</th>
              <td>{d.baselineText}</td>
              <td>{d.oursText}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </figure>
  );
}
