import { useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { MAP_HEIGHT, MAP_WIDTH, project } from "../../lib/geo";
import type { AlertClass, ArrowKey, MapDot } from "../../lib/heatMap";
import { LEGEND, SIZE_LEGEND_CASES, dotLabel, dotRadius, neighborInDirection, regionTitle } from "../../lib/heatMap";
import { COLOR_BASELINE, COLOR_OURS } from "../ComparisonChart";

const COLOR_RING = "#4a515c";
const COLOR_LAND = "#eceef1";
const COLOR_LAND_EDGE = "#ffffff";
const COLOR_INK = "#14171c";
const FILL_OPACITY = 0.82;
const HIT_PAD = 3;
const SELECT_PAD = 4.5;
const LABEL_OFFSET = 7;
const ARROWS: readonly string[] = ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"];

interface DotPaint {
  fill: string;
  fillOpacity: number;
  stroke: string;
  strokeWidth: number;
  dash?: string;
}

/** One place defines how each alert class looks, for both the map and the legend swatches. */
function paintOf(cls: AlertClass): DotPaint {
  switch (cls) {
    case "b1_only":
      return { fill: "none", fillOpacity: 1, stroke: COLOR_RING, strokeWidth: 2 };
    case "ours_only":
      return { fill: COLOR_OURS, fillOpacity: FILL_OPACITY, stroke: "#ffffff", strokeWidth: 0.8 };
    case "both":
      return { fill: COLOR_OURS, fillOpacity: FILL_OPACITY, stroke: COLOR_RING, strokeWidth: 2 };
    case "none":
      return { fill: "none", fillOpacity: 1, stroke: COLOR_BASELINE, strokeWidth: 1.3, dash: "2.5 2" };
  }
}

function Swatch({ cls }: { cls: AlertClass }) {
  const p = paintOf(cls);
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true" className="shrink-0">
      <circle cx="10" cy="10" r="6.5" fill={p.fill} fillOpacity={p.fillOpacity} stroke={p.stroke} strokeWidth={p.strokeWidth} strokeDasharray={p.dash} />
    </svg>
  );
}

export function MapLegend({ counts }: { counts?: Readonly<Record<AlertClass, number>> }) {
  return (
    <div className="space-y-3 text-small">
      <ul className="m-0 flex list-none flex-wrap gap-x-5 gap-y-2 p-0" aria-label="범례">
        {LEGEND.map((l) => (
          <li key={l.cls} className="flex items-center gap-2">
            <Swatch cls={l.cls} />
            {l.label}
            {counts !== undefined && <span className="num text-ink-muted">{counts[l.cls]}곳</span>}
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-ink-muted">
        <span>점 크기 = 그날 환자 수</span>
        <span className="flex items-end gap-3" aria-label={`예시: 환자 ${SIZE_LEGEND_CASES.join("명, ")}명`}>
          {SIZE_LEGEND_CASES.map((n) => {
            const r = dotRadius(n);
            const box = r * 2 + 2;
            return (
              <span key={n} className="flex items-end gap-1">
                <svg width={box} height={box} viewBox={`0 0 ${box} ${box}`} aria-hidden="true">
                  <circle cx={box / 2} cy={box / 2} r={r} fill="none" stroke={COLOR_RING} strokeWidth={1.4} />
                </svg>
                <span className="num text-caption">{n}명</span>
              </span>
            );
          })}
        </span>
      </div>
    </div>
  );
}

interface HeatMapProps {
  dots: readonly MapDot[];
  /** SVG path of the background boundary; null while loading or when it failed (dots still work). */
  outline: string | null;
  selected: string | null;
  onSelect: (code: string) => void;
}

/**
 * Korea outline (background, 2013 boundaries) with one circle per district that was alerted or had patients.
 * Circles form one tab stop (roving tabindex); arrow keys move to the nearest circle in that direction.
 */
export function HeatMap({ dots, outline, selected, onSelect }: HeatMapProps) {
  const refs = useRef(new Map<string, SVGGElement>());
  const [active, setActive] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);

  const placed = useMemo(
    () => dots.map((d) => ({ code: d.code, dot: d, ...project(d.region.lon, d.region.lat), r: dotRadius(d.cases) })),
    [dots],
  );
  const tabStop = selected !== null && dots.some((d) => d.code === selected) ? selected : (dots[0]?.code ?? null);
  const shown = hover ?? active ?? selected;
  const shownDot = dots.find((d) => d.code === shown);
  const sel = placed.find((p) => p.dot.code === selected);

  const onKey = (e: KeyboardEvent<SVGGElement>, code: string): void => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onSelect(code);
    } else if (ARROWS.includes(e.key)) {
      e.preventDefault();
      const next = neighborInDirection(placed, code, e.key as ArrowKey);
      if (next !== null) refs.current.get(next)?.focus();
    }
  };

  return (
    <div>
      <svg
        viewBox={`0 0 ${MAP_WIDTH} ${MAP_HEIGHT}`}
        role="group"
        aria-label={`지도: 경보를 받았거나 환자가 있었던 시군구 ${dots.length}곳. 방향키로 이동하고 Enter로 선택합니다.`}
        className="block h-auto w-full max-w-[34rem]"
      >
        {outline !== null && <path d={outline} fill={COLOR_LAND} stroke={COLOR_LAND_EDGE} strokeWidth={0.4} strokeLinejoin="round" aria-hidden="true" />}
        {placed.map(({ dot, x, y, r }) => {
          const p = paintOf(dot.cls);
          return (
            <g
              key={dot.code}
              ref={(el) => {
                if (el === null) refs.current.delete(dot.code);
                else refs.current.set(dot.code, el);
              }}
              role="button"
              tabIndex={dot.code === tabStop ? 0 : -1}
              aria-label={dotLabel(dot)}
              aria-pressed={dot.code === selected}
              className="cursor-pointer outline-none"
              onClick={() => onSelect(dot.code)}
              onKeyDown={(e) => onKey(e, dot.code)}
              onFocus={() => setActive(dot.code)}
              onBlur={() => setActive((a) => (a === dot.code ? null : a))}
              onMouseEnter={() => setHover(dot.code)}
              onMouseLeave={() => setHover((h) => (h === dot.code ? null : h))}
            >
              <circle cx={x} cy={y} r={r + HIT_PAD} fill="transparent" />
              <circle cx={x} cy={y} r={r} fill={p.fill} fillOpacity={p.fillOpacity} stroke={p.stroke} strokeWidth={p.strokeWidth} strokeDasharray={p.dash} />
              {active === dot.code && (
                <circle cx={x} cy={y} r={r + SELECT_PAD} fill="none" stroke={COLOR_OURS} strokeWidth={2} strokeDasharray="3 2" />
              )}
            </g>
          );
        })}
        {sel !== undefined && (
          <g aria-hidden="true" pointerEvents="none">
            <circle cx={sel.x} cy={sel.y} r={sel.r + SELECT_PAD} fill="none" stroke={COLOR_INK} strokeWidth={2} />
            <text
              x={sel.x + (sel.x > MAP_WIDTH / 2 ? -1 : 1) * (sel.r + LABEL_OFFSET)}
              y={sel.y + 4}
              textAnchor={sel.x > MAP_WIDTH / 2 ? "end" : "start"}
              fontSize={13}
              fontWeight={600}
              fill={COLOR_INK}
              stroke="#ffffff"
              strokeWidth={3}
              paintOrder="stroke"
            >
              {regionTitle(sel.dot.region)}
            </text>
          </g>
        )}
      </svg>
      <p className="mt-2 min-h-[1.25rem] text-small text-ink-muted" aria-hidden="true">
        {shownDot === undefined ? "점에 마우스를 올리거나 방향키로 이동해 보세요." : dotLabel(shownDot)}
      </p>
    </div>
  );
}
