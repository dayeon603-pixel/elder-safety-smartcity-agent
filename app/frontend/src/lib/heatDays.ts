import type { HeatDay } from "../api/heatTypes";

const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"] as const;
const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/** Patients our alerts caught beyond B1 at the same alert count. */
export function gapOf(d: Pick<HeatDay, "b1_captured" | "ours_captured">): number {
  return d.ours_captured - d.b1_captured;
}

/** Biggest advantage first; ties: more patients, then earlier date. Does not mutate the input. */
export function sortByGap(days: readonly HeatDay[]): HeatDay[] {
  return [...days].sort((a, b) => gapOf(b) - gapOf(a) || b.n_cases - a.n_cases || a.date.localeCompare(b.date));
}

/** "2025-07-23" -> "07-23 (수)". Weekday is computed in UTC from the calendar date, so it never shifts with the viewer's zone. */
export function dayLabel(iso: string): string {
  const m = ISO_DATE.exec(iso);
  if (m === null) return iso;
  const weekday = WEEKDAYS[new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]))).getUTCDay()] ?? "";
  return `${m[2]}-${m[3]} (${weekday})`;
}

/** Share of the day's patients, whole percent; null when nobody was ill (the ratio is undefined, not zero). */
export function sharePercent(part: number, total: number): number | null {
  return total > 0 ? Math.round((part / total) * 100) : null;
}
