import type { AedCandidate, AedGu, AedHour } from "../api/zpTypes";

export interface HourDatum {
  hour: number;
  label: string;
  arrests: number;
  pctOpen: number;
  night: boolean;
}

/** True for hours in the night window [start, end) that wraps midnight, e.g. 22 <= h or h < 6. */
export function isNightHour(hour: number, start: number, end: number): boolean {
  return start > end ? hour >= start || hour < end : hour >= start && hour < end;
}

/** Hourly rows sorted by hour, with the night flag for shading. Hours outside 0-23 are dropped. */
export function hourData(rows: readonly AedHour[], nightStart: number, nightEnd: number): HourDatum[] {
  return rows
    .filter((r) => Number.isInteger(r.hour) && r.hour >= 0 && r.hour <= 23)
    .map((r) => ({
      hour: r.hour,
      label: `${String(r.hour).padStart(2, "0")}시`,
      arrests: r.public_arrests,
      pctOpen: r.pct_open,
      night: isNightHour(r.hour, nightStart, nightEnd),
    }))
    .sort((a, b) => a.hour - b.hour);
}

/** Worst night open share first; ties broken by more night arrests, then name, so the order is stable. */
export function rankGu(rows: readonly AedGu[]): AedGu[] {
  return [...rows].sort(
    (a, b) => a.night_open_share - b.night_open_share || b.night_arrests_per_year - a.night_arrests_per_year || a.gu.localeCompare(b.gu, "ko"),
  );
}

/** Candidates for one 구, best gain first. Empty gu returns all. */
export function candidatesFor(rows: readonly AedCandidate[], gu: string): AedCandidate[] {
  return rows.filter((c) => gu === "" || c.gu === gu).sort((a, b) => b.gain_score - a.gain_score || a.address.localeCompare(b.address, "ko"));
}

/** "0900-1600" -> "09:00–16:00". Anything else (missing, "nan-nan") -> "시간 정보 없음". */
export function formatHours(h: string | null): string {
  if (h === null) return "시간 정보 없음";
  const m = /^(\d{2})(\d{2})-(\d{2})(\d{2})$/.exec(h);
  return m === null ? "시간 정보 없음" : `${m[1]}:${m[2]}–${m[3]}:${m[4]}`;
}
