import { Bar, CartesianGrid, ComposedChart, Line, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { HourDatum } from "../../lib/zpAed";
import { COLOR_BASELINE, COLOR_OURS } from "../ComparisonChart";

const COLOR_AXIS = "#d9dde3";
const COLOR_TEXT = "#4a515c";
const COLOR_NIGHT = "#14171c";
const NIGHT_OPACITY = 0.07;
const CHART_HEIGHT = 320;
const TICK_EVERY = 3;

interface Props {
  data: readonly HourDatum[];
  nightStart: number;
  nightEnd: number;
}

/**
 * Hourly public-place cardiac arrests (gray bars, left axis) and the share of AEDs open at that hour (blue line, right axis).
 * The night window is shaded. Hours are categorical, so the shading spans the first and last category of each night block.
 */
export function NightGapChart({ data, nightStart, nightEnd }: Props) {
  const label = (h: number): string => `${String(h).padStart(2, "0")}시`;
  const wraps = nightStart > nightEnd;
  return (
    <figure className="m-0">
      <div role="img" aria-label="시간대별 공공장소 심정지 건수와 열려 있는 AED 비율. 밤 시간대에 열린 AED 비율이 낮아집니다." style={{ height: CHART_HEIGHT }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={[...data]} margin={{ top: 8, right: 8, bottom: 4, left: 0 }}>
            <CartesianGrid stroke={COLOR_AXIS} vertical={false} />
            {wraps ? (
              <>
                <ReferenceArea yAxisId="n" x1={label(0)} x2={label(nightEnd - 1)} fill={COLOR_NIGHT} fillOpacity={NIGHT_OPACITY} ifOverflow="visible" />
                <ReferenceArea yAxisId="n" x1={label(nightStart)} x2={label(23)} fill={COLOR_NIGHT} fillOpacity={NIGHT_OPACITY} ifOverflow="visible" />
              </>
            ) : (
              <ReferenceArea yAxisId="n" x1={label(nightStart)} x2={label(nightEnd - 1)} fill={COLOR_NIGHT} fillOpacity={NIGHT_OPACITY} ifOverflow="visible" />
            )}
            <XAxis dataKey="label" interval={TICK_EVERY - 1} tick={{ fill: COLOR_TEXT, fontSize: 12 }} stroke={COLOR_AXIS} tickLine={false} />
            <YAxis yAxisId="n" tick={{ fill: COLOR_TEXT, fontSize: 12 }} stroke={COLOR_AXIS} tickLine={false} width={40} allowDecimals={false} label={{ value: "심정지(건)", angle: -90, position: "insideLeft", fill: COLOR_TEXT, fontSize: 12, dx: 8 }} />
            <YAxis yAxisId="p" orientation="right" domain={[0, 100]} unit="%" tick={{ fill: COLOR_TEXT, fontSize: 12 }} stroke={COLOR_AXIS} tickLine={false} width={44} />
            <Tooltip
              formatter={(v: number, k: string) => (k === "pctOpen" ? [`${v.toFixed(0)}%`, "열린 AED"] : [`${v}건`, "공공장소 심정지"])}
              labelFormatter={(l: string) => l}
              contentStyle={{ borderRadius: 2, borderColor: COLOR_AXIS, fontSize: 13 }}
            />
            <Bar yAxisId="n" dataKey="arrests" fill={COLOR_BASELINE} radius={0} isAnimationActive={false} />
            <Line yAxisId="p" dataKey="pctOpen" stroke={COLOR_OURS} strokeWidth={2.5} dot={false} isAnimationActive={false} type="monotone" />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1 text-small text-ink-muted">
        <span className="flex items-center gap-2"><span aria-hidden="true" className="inline-block h-3 w-3" style={{ backgroundColor: COLOR_BASELINE }} />공공장소 심정지 (왼쪽 축)</span>
        <span className="flex items-center gap-2"><span aria-hidden="true" className="inline-block h-0.5 w-5" style={{ backgroundColor: COLOR_OURS, height: 3 }} />열려 있는 AED 비율, 평일 (오른쪽 축)</span>
        <span className="flex items-center gap-2"><span aria-hidden="true" className="inline-block h-3 w-3 border border-line-strong" style={{ backgroundColor: "rgba(20,23,28,0.12)" }} />밤 {nightStart}시~{nightEnd}시</span>
      </figcaption>
    </figure>
  );
}
