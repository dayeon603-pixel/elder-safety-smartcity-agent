import type { HeatAlerts, HeatFlag, HeatRegion } from "../api/heatTypes";
import type { Point } from "./geo";

/** How a district was alerted on the selected day. Drives dot style and the legend. */
export type AlertClass = "b1_only" | "ours_only" | "both" | "none";

export interface MapDot {
  code: string;
  region: HeatRegion;
  cls: AlertClass;
  /** Actual patients that day. */
  cases: number;
}

export interface LegendItem {
  cls: AlertClass;
  label: string;
}

/** Order matters: it is the order of the legend. */
export const LEGEND: readonly LegendItem[] = [
  { cls: "b1_only", label: "B1만 경보" },
  { cls: "ours_only", label: "우리만 경보" },
  { cls: "both", label: "둘 다 경보" },
  { cls: "none", label: "경보 없음, 환자 발생" },
];

/** Dot radius in viewBox units: area grows with patients, with a floor so alerted empty districts stay visible. */
export const DOT_R_MIN = 3.5;
export const DOT_R_PER_ROOT = 2.4;
export const DOT_R_MAX = 17;
export const SIZE_LEGEND_CASES: readonly number[] = [1, 5, 15];

export function classify(b1: boolean, ours: boolean): AlertClass {
  if (b1 && ours) return "both";
  if (b1) return "b1_only";
  if (ours) return "ours_only";
  return "none";
}

export function dotRadius(cases: number): number {
  const n = Math.max(0, cases);
  return Math.min(DOT_R_MAX, DOT_R_MIN + DOT_R_PER_ROOT * Math.sqrt(n));
}

export interface DotModel {
  dots: MapDot[];
  /** Codes present in the alerts payload but missing from /heat/regions (data problem; never drawn). */
  unknownCodes: string[];
}

/**
 * Join the day's alerts and patient counts onto the 2026 district list by code.
 * A district is drawn if either method alerted it or it had at least one patient.
 * Largest dots first so smaller ones stay clickable on top.
 */
export function buildDots(regions: readonly HeatRegion[], alerts: HeatAlerts): DotModel {
  const byCode = new Map(regions.map((r) => [r.code, r]));
  const b1 = new Map(alerts.b1.map((a) => [a.code, a]));
  const ours = new Map(alerts.ours.map((a) => [a.code, a]));
  const cases = new Map(alerts.cases.map((c) => [c.code, c.n]));
  const codes = new Set<string>([...b1.keys(), ...ours.keys(), ...[...cases].filter(([, n]) => n > 0).map(([c]) => c)]);
  const dots: MapDot[] = [];
  const unknownCodes: string[] = [];
  for (const code of codes) {
    const region = byCode.get(code);
    if (region === undefined) {
      unknownCodes.push(code);
      continue;
    }
    const n = cases.get(code) ?? b1.get(code)?.cases ?? ours.get(code)?.cases ?? 0;
    dots.push({ code, region, cls: classify(b1.has(code), ours.has(code)), cases: n });
  }
  dots.sort((a, b) => b.cases - a.cases || a.code.localeCompare(b.code));
  return { dots, unknownCodes };
}

/** Districts per alert class on the day (the legend counts). */
export function countByClass(dots: readonly MapDot[]): Record<AlertClass, number> {
  const out: Record<AlertClass, number> = { b1_only: 0, ours_only: 0, both: 0, none: 0 };
  for (const d of dots) out[d.cls] += 1;
  return out;
}

export interface DaySummary {
  b1Caught: number;
  oursCaught: number;
  /** Alerted by us, not by B1, with at least one patient: the "blind spot" districts. Most patients first. */
  onlyOurs: MapDot[];
  /** Patients in districts neither method alerted. */
  missedByBoth: number;
}

export function summarizeDay(dots: readonly MapDot[]): DaySummary {
  let b1Caught = 0;
  let oursCaught = 0;
  let missedByBoth = 0;
  for (const d of dots) {
    if (d.cls === "b1_only" || d.cls === "both") b1Caught += d.cases;
    if (d.cls === "ours_only" || d.cls === "both") oursCaught += d.cases;
    if (d.cls === "none") missedByBoth += d.cases;
  }
  const onlyOurs = dots.filter((d) => d.cls === "ours_only" && d.cases > 0).sort((a, b) => b.cases - a.cases || a.code.localeCompare(b.code));
  return { b1Caught, oursCaught, onlyOurs, missedByBoth };
}

const SIDO_SHORT: Readonly<Record<string, string>> = {
  충청북도: "충북", 충청남도: "충남", 전라북도: "전북", 전라남도: "전남", 경상북도: "경북", 경상남도: "경남",
  강원특별자치도: "강원", 전북특별자치도: "전북", 제주특별자치도: "제주",
};
const SIDO_SUFFIX = /(특별자치시|특별시|광역시|도)$/;

/** "서울특별시" -> "서울", "충청남도" -> "충남". Unknown names pass through with the generic suffix removed. */
export function shortSido(sido: string): string {
  const known = SIDO_SHORT[sido];
  if (known !== undefined) return known;
  const short = sido.replace(SIDO_SUFFIX, "");
  return short === "" ? sido : short;
}

export function regionTitle(r: Pick<HeatRegion, "sido" | "name">): string {
  return `${shortSido(r.sido)} ${r.name}`;
}

const CLASS_TEXT: Record<AlertClass, string> = {
  b1_only: "B1만 경보",
  ours_only: "우리만 경보",
  both: "B1과 우리 모두 경보",
  none: "경보 없음",
};

export function classText(cls: AlertClass): string {
  return CLASS_TEXT[cls];
}

/** Accessible name of a dot; also used for the hover readout. */
export function dotLabel(d: MapDot): string {
  return `${regionTitle(d.region)}, 환자 ${d.cases}명, ${classText(d.cls)}`;
}

export type ArrowKey = "ArrowUp" | "ArrowDown" | "ArrowLeft" | "ArrowRight";
const PERPENDICULAR_PENALTY = 2;

/**
 * Spatial arrow-key navigation among dots: the nearest dot in the pressed direction,
 * penalising sideways offset so the focus moves roughly in a straight line. Null at an edge.
 */
export function neighborInDirection(points: readonly (Point & { code: string })[], fromCode: string, key: ArrowKey): string | null {
  const from = points.find((p) => p.code === fromCode);
  if (from === undefined) return null;
  let best: { code: string; cost: number } | null = null;
  for (const p of points) {
    if (p.code === fromCode) continue;
    const dx = p.x - from.x;
    const dy = p.y - from.y;
    const along = key === "ArrowRight" ? dx : key === "ArrowLeft" ? -dx : key === "ArrowDown" ? dy : -dy;
    const across = key === "ArrowRight" || key === "ArrowLeft" ? Math.abs(dy) : Math.abs(dx);
    if (along <= 0) continue;
    const cost = along + PERPENDICULAR_PENALTY * across;
    if (best === null || cost < best.cost) best = { code: p.code, cost };
  }
  return best?.code ?? null;
}

export type FlagState = "open" | "closed" | "unknown";

/** SQLite 0/1/NULL or boolean -> tri-state. Unknown is not the same as closed. */
export function flagState(v: HeatFlag | undefined): FlagState {
  if (v === null || v === undefined) return "unknown";
  return v === true || v === 1 ? "open" : "closed";
}
