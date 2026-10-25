import { useMemo } from "react";
import type { OpsBit, OpsSilverPoint } from "../../api/opsApi";
import { OPS_LABEL_TOP } from "../../config";
import type { BoundaryCollection } from "../../lib/geo";
import type { CrosswalkVerdict } from "../../lib/app/crossing";
import type { AedRow, ShelterRow } from "../../lib/app/opsData";
import type { Focus, LayerId, WithState } from "../../lib/app/opsModel";
import { boundsOf, hotspotRadius, makeFrame, outlinePath } from "../../lib/zpMap";
import { COLOR_BASELINE, COLOR_OURS } from "../ComparisonChart";

const LAND = "#eceef1";
const LAND_EDGE = "#ffffff";
const INK = "#14171c";
const DANGER = "#b3261e";
const OPEN = "#1d6b3a";
const MUTED = "#626a76";
const MAP_WIDTH = 640;
const PAD_DEG_MIN = 0.012;
const R_ZONE = 6;
const R_PICK = 3.4;
const R_OPEN = 4.6;
const R_FOCUS = 15;

export type Scenario = "current" | "ours";

export interface OpsMapProps {
  regionName: string;
  center: { lat: number; lon: number };
  boundary: BoundaryCollection | null;
  layers: ReadonlySet<LayerId>;
  scenario: Scenario;
  silver: readonly OpsSilverPoint[];
  crosswalks: readonly CrosswalkVerdict[];
  aeds: readonly WithState<AedRow>[];
  shelters: readonly WithState<ShelterRow>[];
  bit: readonly OpsBit[];
  /** frame colour for the 폭염 layer */
  heatColor: string | null;
  focus: Focus | null;
  summary: string;
}

type P = { x: number; y: number };

function Diamond({ p, r, fill, stroke }: { p: P; r: number; fill: string; stroke: string }) {
  return <polygon points={`${p.x},${p.y - r} ${p.x + r},${p.y} ${p.x},${p.y + r} ${p.x - r},${p.y}`} fill={fill} stroke={stroke} strokeWidth={1.4} strokeLinejoin="round" />;
}

function Triangle({ p, r, fill, stroke, dash }: { p: P; r: number; fill: string; stroke: string; dash?: string }) {
  return <polygon points={`${p.x},${p.y - r} ${p.x + r},${p.y + r * 0.8} ${p.x - r},${p.y + r * 0.8}`} fill={fill} stroke={stroke} strokeWidth={1.4} strokeDasharray={dash} strokeLinejoin="round" />;
}

function Square({ p, r, fill, stroke, dash }: { p: P; r: number; fill: string; stroke: string; dash?: string }) {
  return <rect x={p.x - r} y={p.y - r} width={2 * r} height={2 * r} fill={fill} stroke={stroke} strokeWidth={1.4} strokeDasharray={dash} />;
}

/** Halo text so labels stay readable over dots and the land fill. */
function Label({ p, text, color = INK, dy = -10 }: { p: P; text: string; color?: string; dy?: number }) {
  return (
    <text x={p.x} y={p.y + dy} textAnchor="middle" fontSize={11} fontWeight={700} fill={color} stroke="#ffffff" strokeWidth={3} paintOrder="stroke" className="num">
      {text}
    </text>
  );
}

/**
 * One SVG map of a 시군구 with five switchable layers. Shapes carry the meaning, colour only reinforces it:
 * circle = 실버존/다발지, diamond = 횡단보도, square = AED, triangle = 쉼터, tag = 안내단말 정류장.
 * Filled = open / short of green; hollow = closed; dashed = no hours in the data.
 */
export function OpsMap(props: OpsMapProps) {
  const { regionName, center, boundary, layers, scenario, silver, crosswalks, aeds, shelters, bit, heatColor, focus, summary } = props;
  const frame = useMemo(() => {
    const pts: { lat: number; lon: number }[] = [center, ...silver, ...crosswalks, ...aeds.map((a) => a.row), ...shelters.map((s) => s.row), ...bit.filter((b) => b.lat !== undefined && b.lon !== undefined).map((b) => ({ lat: b.lat as number, lon: b.lon as number }))];
    const b = boundsOf(pts);
    if (b === null) return null;
    const grow = (lo: number, hi: number): [number, number] => (hi - lo < PAD_DEG_MIN ? [lo - PAD_DEG_MIN, hi + PAD_DEG_MIN] : [lo, hi]);
    const [lonMin, lonMax] = grow(b.lonMin, b.lonMax);
    const [latMin, latMax] = grow(b.latMin, b.latMax);
    return makeFrame({ lonMin, lonMax, latMin, latMax }, MAP_WIDTH);
  }, [center, silver, crosswalks, aeds, shelters, bit]);
  const outline = useMemo(() => (frame === null || boundary === null ? null : outlinePath(boundary, frame)), [frame, boundary]);
  if (frame === null) return <p className="text-body text-ink-muted">표시할 위치가 없습니다.</p>;
  const at = (p: { lat: number; lon: number }): P => frame.project(p.lon, p.lat);

  const zones = silver.filter((s) => s.kind === "current");
  const picks = silver.filter((s) => s.kind === "ours");
  const hot = silver.filter((s) => s.kind === "hotspot").sort((a, b) => (b.casualties ?? 0) - (a.casualties ?? 0));
  const shorts = crosswalks.filter((c) => c.short);
  const okCw = crosswalks.filter((c) => !c.short);
  const topShort = new Set(shorts.slice(0, OPS_LABEL_TOP).map((c) => c.id));
  const stateOrder = { unknown: 0, closed: 1, open: 2 } as const;
  const aedSorted = [...aeds].sort((a, b) => stateOrder[a.state] - stateOrder[b.state]);
  const shSorted = [...shelters].sort((a, b) => stateOrder[a.state] - stateOrder[b.state]);

  return (
    <svg viewBox={`0 0 ${frame.width} ${frame.height}`} role="img" aria-label={`${regionName} 운영 지도. ${summary}`} className="block h-auto w-full" style={heatColor === null ? undefined : { outline: `3px solid ${heatColor}`, outlineOffset: -3 }}>
      <rect width={frame.width} height={frame.height} fill="#f8f9fa" />
      {outline !== null && <path d={outline} fill={LAND} stroke={LAND_EDGE} strokeWidth={0.8} strokeLinejoin="round" />}

      {layers.has("silver") && (
        <g data-layer="silver">
          {scenario === "current" && zones.map((z) => <circle key={`z${z.lat}${z.lon}`} cx={at(z).x} cy={at(z).y} r={R_ZONE} fill="none" stroke={COLOR_BASELINE} strokeWidth={2} />)}
          {scenario === "ours" && picks.map((z) => <circle key={`o${z.lat}${z.lon}`} cx={at(z).x} cy={at(z).y} r={R_PICK} fill={COLOR_OURS} fillOpacity={0.6} />)}
          {hot.map((h) => {
            const within = scenario === "current" ? h.currentWithin : h.oursWithin;
            const r = hotspotRadius(h.casualties);
            const p = at(h);
            return within ? (
              <circle key={`h${h.lat}${h.lon}`} cx={p.x} cy={p.y} r={r} fill={INK} fillOpacity={0.85} stroke="#fff" strokeWidth={1}><title>{`2024 사고다발지 · 사상자 ${h.casualties ?? "?"}명 · 구역 안`}</title></circle>
            ) : (
              <circle key={`h${h.lat}${h.lon}`} cx={p.x} cy={p.y} r={r} fill="#fff" fillOpacity={0.7} stroke={DANGER} strokeWidth={2.4}><title>{`2024 사고다발지 · 사상자 ${h.casualties ?? "?"}명 · 구역 밖`}</title></circle>
            );
          })}
        </g>
      )}

      {layers.has("crosswalk") && (
        <g data-layer="crosswalk">
          {okCw.map((c) => <circle key={c.id} cx={at(c).x} cy={at(c).y} r={2} fill={MUTED} fillOpacity={0.55} />)}
        </g>
      )}

      {layers.has("time") && (
        <g data-layer="time">
          {[...shSorted.map((x) => ({ ...x, kind: "sh" as const })), ...aedSorted.map((x) => ({ ...x, kind: "aed" as const }))]
            .sort((x, y) => stateOrder[x.state] - stateOrder[y.state])
            .map(({ row, state, kind }) => {
              const p = at(row);
              if (state === "open") return kind === "sh" ? <Triangle key={row.id} p={p} r={R_OPEN} fill={OPEN} stroke="#ffffff" /> : <Square key={row.id} p={p} r={R_OPEN * 0.8} fill={OPEN} stroke="#ffffff" />;
              return state === "closed" ? <circle key={row.id} cx={p.x} cy={p.y} r={1.9} fill={MUTED} fillOpacity={0.7} /> : <circle key={row.id} cx={p.x} cy={p.y} r={2.6} fill="#ffffff" stroke="#8a5a00" strokeWidth={1.2} strokeDasharray="1.6 1.4" />;
            })}
        </g>
      )}

      {layers.has("crosswalk") && (
        <g data-layer="crosswalk-short">
          {shorts.map((c) => {
            const p = at(c);
            return (
              <g key={c.id}>
                <Diamond p={p} r={Math.min(11, 5.5 + c.extensionSec * 0.5)} fill={DANGER} stroke="#ffffff" />
                {topShort.has(c.id) && <Label p={p} text={`+${c.extensionSec}초`} color="#7a1a15" dy={-13} />}
                <title>{`${c.address} · 길이 ${c.lengthM}m · 녹색 ${c.greenSec}초 · 필요 ${c.requiredSec.toFixed(1)}초 · +${c.extensionSec}초`}</title>
              </g>
            );
          })}
        </g>
      )}

      {layers.has("bit") && (
        <g data-layer="bit">
          {bit.filter((b) => b.lat !== undefined && b.lon !== undefined).map((b) => {
            const p = at({ lat: b.lat as number, lon: b.lon as number });
            return (
              <g key={b.stop_id}>
                <rect x={p.x - 9} y={p.y - 9} width={18} height={18} rx={4} fill={COLOR_OURS} stroke="#ffffff" strokeWidth={1.6} />
                <text x={p.x} y={p.y + 4.5} textAnchor="middle" fontSize={12} fontWeight={800} fill="#ffffff">{b.rank}</text>
                <title>{`안내단말 ${b.rank}순위 · ${b.name} (${b.dong}) · 65세 이상 약 ${b.covered_pop65_est.toLocaleString("ko-KR")}명`}</title>
              </g>
            );
          })}
        </g>
      )}

      {focus !== null && (
        <g data-testid="map-focus">
          <circle cx={at(focus).x} cy={at(focus).y} r={R_FOCUS} fill="none" stroke={INK} strokeWidth={3} />
          <circle cx={at(focus).x} cy={at(focus).y} r={R_FOCUS + 3} fill="none" stroke="#ffffff" strokeWidth={2} />
          <Label p={at(focus)} text={focus.label} dy={-R_FOCUS - 6} />
        </g>
      )}
    </svg>
  );
}
