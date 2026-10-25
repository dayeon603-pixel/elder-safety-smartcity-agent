import { useId, useMemo, useState } from "react";
import type { SilverMapPoint } from "../../api/zpTypes";
import type { BoundaryCollection } from "../../lib/geo";
import type { MapScenario } from "../../lib/zpMap";
import { boundsOf, hotspotRadius, makeFrame, mapCounts, outlinePath } from "../../lib/zpMap";
import { COLOR_BASELINE, COLOR_OURS } from "../ComparisonChart";

const COLOR_LAND = "#eceef1";
const COLOR_LAND_EDGE = "#ffffff";
const COLOR_INK = "#14171c";
const COLOR_UNCOVERED = "#b3261e";
const ZONE_R = 3.2;
const PICK_R = 1.9;

const SCENARIO_LABEL: Readonly<Record<MapScenario, string>> = { current: "현행 구역 기준", ours: "에이전트 추천 기준" };

function Swatch({ children }: { children: React.ReactNode }) {
  return (
    <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true" className="shrink-0">
      {children}
    </svg>
  );
}

/**
 * Seoul map. Gray rings = current zones, blue dots = our picks, black discs = 2024 hotspots (size = casualties).
 * A hotspot is a filled disc when a zone of the chosen scenario lies within the radius, a hollow red ring when not.
 */
export function SilverMap({ points, boundary, radiusM }: { points: readonly SilverMapPoint[]; boundary: BoundaryCollection | null; radiusM: number }) {
  const [scenario, setScenario] = useState<MapScenario>("ours");
  const name = useId();
  const frame = useMemo(() => {
    const b = boundsOf(points);
    return b === null ? null : makeFrame(b);
  }, [points]);
  const outline = useMemo(() => (frame === null || boundary === null ? null : outlinePath(boundary, frame)), [frame, boundary]);
  const counts = useMemo(() => mapCounts(points), [points]);
  if (frame === null) return <p className="text-small text-ink-muted">표시할 위치가 없습니다.</p>;

  const placed = points.map((p) => ({ p, ...frame.project(p.lon, p.lat) }));
  const zones = placed.filter((q) => q.p.kind === "current");
  const picks = placed.filter((q) => q.p.kind === "ours");
  const hot = placed.filter((q) => q.p.kind === "hotspot_2024").sort((a, b) => (b.p.cas ?? 0) - (a.p.cas ?? 0));
  const covered = counts.covered[scenario];
  const summary = `${SCENARIO_LABEL[scenario]}으로 2024 사고다발지 ${counts.hotspots}곳 중 ${covered}곳이 ${radiusM}m 안에 들어옵니다.`;

  return (
    <div className="space-y-4">
      <fieldset className="m-0 flex flex-wrap items-center gap-x-5 gap-y-2 border-0 p-0">
        <legend className="mb-1 text-small font-medium">사고다발지를 어느 구역 기준으로 칠할까요</legend>
        {(Object.keys(SCENARIO_LABEL) as MapScenario[]).map((s) => (
          <label key={s} className="flex min-h-11 items-center gap-2 text-body sm:min-h-8">
            <input type="radio" name={name} checked={scenario === s} onChange={() => setScenario(s)} className="h-4 w-4 accent-[#0072B2]" />
            {SCENARIO_LABEL[s]}
          </label>
        ))}
      </fieldset>

      <svg viewBox={`0 0 ${frame.width} ${frame.height}`} role="img" aria-label={`서울 지도. ${summary}`} className="block h-auto w-full max-w-[40rem]">
        {outline !== null && <path d={outline} fill={COLOR_LAND} stroke={COLOR_LAND_EDGE} strokeWidth={0.8} strokeLinejoin="round" />}
        {scenario === "current" && zones.map(({ p, x, y }) => <circle key={`z${p.lat}${p.lon}`} cx={x} cy={y} r={ZONE_R} fill="none" stroke={COLOR_BASELINE} strokeWidth={1.4} />)}
        {scenario === "ours" && picks.map(({ p, x, y }) => <circle key={`o${p.lat}${p.lon}`} cx={x} cy={y} r={PICK_R} fill={COLOR_OURS} fillOpacity={0.55} />)}
        {hot.map(({ p, x, y }) => {
          const within = scenario === "current" ? p.cur_within : p.ours_within;
          const r = hotspotRadius(p.cas);
          return within === 1 ? (
            <circle key={`h${p.lat}${p.lon}`} cx={x} cy={y} r={r} fill={COLOR_INK} fillOpacity={0.85} stroke="#ffffff" strokeWidth={1}>
              <title>{`${p.name ?? "사고다발지"} · 사상자 ${p.cas ?? "?"}명 · 구역 안`}</title>
            </circle>
          ) : (
            <circle key={`h${p.lat}${p.lon}`} cx={x} cy={y} r={r} fill="#ffffff" fillOpacity={0.6} stroke={COLOR_UNCOVERED} strokeWidth={2.2}>
              <title>{`${p.name ?? "사고다발지"} · 사상자 ${p.cas ?? "?"}명 · 구역 밖`}</title>
            </circle>
          );
        })}
      </svg>

      <p aria-live="polite" className="num text-body font-semibold">{summary}</p>
      <ul className="m-0 flex list-none flex-wrap gap-x-5 gap-y-2 p-0 text-small" aria-label="범례">
        <li className="flex items-center gap-2"><Swatch><circle cx="11" cy="11" r="6" fill="none" stroke={COLOR_BASELINE} strokeWidth="1.6" /></Swatch>현행 구역 <span className="num text-ink-muted">{counts.currentZones}곳</span></li>
        <li className="flex items-center gap-2"><Swatch><circle cx="11" cy="11" r="3" fill={COLOR_OURS} fillOpacity="0.6" /></Swatch>에이전트 추천 <span className="num text-ink-muted">{counts.ourPicks}곳</span></li>
        <li className="flex items-center gap-2"><Swatch><circle cx="11" cy="11" r="7" fill={COLOR_INK} fillOpacity="0.85" /></Swatch>2024 다발지, 구역 안</li>
        <li className="flex items-center gap-2"><Swatch><circle cx="11" cy="11" r="7" fill="#fff" stroke={COLOR_UNCOVERED} strokeWidth="2.2" /></Swatch>2024 다발지, 구역 밖</li>
      </ul>
      <p className="text-caption text-ink-faint">원 크기 = 다발지 사상자 수 · 반경 {radiusM}m 안에 구역이 있으면 &quot;안&quot;으로 셉니다 · 배경 경계는 2013년 기준</p>
    </div>
  );
}
