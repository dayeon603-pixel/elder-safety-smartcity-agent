import { CartesianGrid, Line, LineChart, ReferenceDot, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { HeatCurve, HeatMethod } from "../../api/heatTypes";
import { formatPercent } from "../../lib/format";
import { CURVE_ORDER, METHOD_LABEL } from "../../lib/heatEval";
import { COLOR_BASELINE, COLOR_OURS } from "../ComparisonChart";

const COLOR_AXIS = "#d9dde3";
const COLOR_TEXT = "#4a515c";
const COLOR_INK = "#14171c";
const CHART_HEIGHT = 300;
const Y_TICKS = [0, 0.25, 0.5, 0.75, 1];

interface LineStyle {
  stroke: string;
  width: number;
  dash?: string;
}

/** Baselines stay gray and differ by dash pattern; ours is the only colored, thick line. */
const STYLE: Record<HeatMethod, LineStyle> = {
  B1: { stroke: COLOR_BASELINE, width: 2 },
  B1S: { stroke: COLOR_BASELINE, width: 1.5, dash: "2 3" },
  B2: { stroke: COLOR_BASELINE, width: 1.5, dash: "6 3" },
  HEUR: { stroke: COLOR_BASELINE, width: 1.5, dash: "9 3 2 3" },
  OURS: { stroke: COLOR_OURS, width: 3 },
};

const multiple = (x: number): string => `×${x.toLocaleString("ko-KR")}`;

interface CurveChartProps {
  curve: HeatCurve;
  title: string;
}

/** Capture rate as the alert budget is scaled around the B1 alert count (x = 1 is "same number of alerts"). */
export function CurveChart({ curve, title }: CurveChartProps) {
  const methods = CURVE_ORDER.filter((m) => (curve.series[m]?.length ?? 0) > 0);
  const xs = [...new Set(methods.flatMap((m) => (curve.series[m] ?? []).map((p) => p.x)))].sort((a, b) => a - b);
  const valueAt = (m: HeatMethod, x: number): number | undefined => curve.series[m]?.find((p) => p.x === x)?.capture;
  return (
    <figure className="m-0">
      <figcaption className="mb-3 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
        <span className="text-small font-medium text-ink">{title}</span>
        <span className="flex flex-wrap gap-x-4 gap-y-1">
          {methods.map((m) => (
            <span key={m} className="inline-flex items-center gap-2 text-small">
              <svg width="28" height="10" aria-hidden="true">
                <line x1="1" x2="27" y1="5" y2="5" stroke={STYLE[m].stroke} strokeWidth={STYLE[m].width} strokeDasharray={STYLE[m].dash} />
              </svg>
              {METHOD_LABEL[m].split(" ")[0]}
            </span>
          ))}
        </span>
      </figcaption>
      <div style={{ height: CHART_HEIGHT }} aria-hidden="true">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart margin={{ top: 12, right: 16, bottom: 4, left: 0 }}>
            <CartesianGrid stroke={COLOR_AXIS} vertical={false} />
            <XAxis
              type="number"
              dataKey="x"
              domain={[xs[0] ?? 0, xs[xs.length - 1] ?? 1]}
              ticks={xs}
              tickFormatter={multiple}
              tickLine={false}
              axisLine={{ stroke: COLOR_AXIS }}
              tick={{ fill: COLOR_TEXT, fontSize: 12 }}
              allowDuplicatedCategory={false}
            />
            <YAxis
              type="number"
              domain={[0, 1]}
              ticks={Y_TICKS}
              tickFormatter={(v: number) => `${v * 100}%`}
              tickLine={false}
              axisLine={false}
              width={44}
              tick={{ fill: COLOR_TEXT, fontSize: 12 }}
            />
            <Tooltip
              formatter={(v: number, name: string) => [formatPercent(v), name]}
              labelFormatter={(x: number) => `경보 수 ${multiple(x)}`}
              contentStyle={{ borderRadius: 2, borderColor: "#b4bac4", fontSize: 13 }}
            />
            <ReferenceLine x={1} stroke={COLOR_INK} strokeDasharray="3 3" label={{ value: "B1과 같은 경보 수", position: "insideBottomRight", fill: COLOR_TEXT, fontSize: 12 }} />
            {methods.map((m) => (
              <Line
                key={m}
                data={curve.series[m]}
                dataKey="capture"
                name={METHOD_LABEL[m].split(" ")[0]}
                stroke={STYLE[m].stroke}
                strokeWidth={STYLE[m].width}
                strokeDasharray={STYLE[m].dash}
                dot={false}
                isAnimationActive={false}
              />
            ))}
            <ReferenceDot x={curve.b1_point.x} y={curve.b1_point.capture} r={5} fill="#ffffff" stroke={COLOR_INK} strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-2 text-caption text-ink-faint">
        가로축 = B1 경보 수의 배수. 속이 빈 점 = B1의 실제 운용점. 오른쪽으로 갈수록 경보를 더 많이 냅니다.
      </p>
      <div className="sr-only">
        <table>
          <caption>{title}</caption>
          <thead>
            <tr>
              <th scope="col">경보 수 배수</th>
              {methods.map((m) => <th key={m} scope="col">{METHOD_LABEL[m]}</th>)}
            </tr>
          </thead>
          <tbody>
            {xs.map((x) => (
              <tr key={x}>
                <th scope="row">{multiple(x)}</th>
                {methods.map((m) => {
                  const v = valueAt(m, x);
                  return <td key={m}>{v === undefined ? "–" : formatPercent(v)}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}
