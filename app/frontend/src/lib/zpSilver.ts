import type { SilverMethod, SilverMethodRow, SilverStratum } from "../api/zpTypes";

/** Display order: baselines first (gray), our engine last. */
export const SILVER_ORDER: readonly SilverMethod[] = ["current", "random_k", "pop65_k", "past_k", "ours_full"];

export const SILVER_LABEL: Readonly<Record<SilverMethod, string>> = {
  current: "현행 노인보호구역",
  random_k: "무작위",
  pop65_k: "노인 인구",
  past_k: "과거 사고",
  ours_full: "에이전트 분석",
};

export const SILVER_NOTE: Readonly<Record<SilverMethod, string>> = {
  current: "지금 지정된 구역",
  random_k: "같은 개수를 무작위로 200번 뽑은 평균",
  pop65_k: "65세 이상 인구밀도가 높은 동 순",
  past_k: "과거 사상자가 많은 곳 순",
  ours_full: "과거 사고 · 노인 인구 · 신호 시간 부족을 함께 보고 같은 개수의 구역을 최대 커버리지로 재배치",
};

export interface SilverBar {
  method: SilverMethod;
  label: string;
  note: string;
  coverage: number;
  lo: number;
  hi: number;
  ours: boolean;
}

/** Order rows baseline-first and attach labels. Methods the API did not send are skipped, unknown ones ignored. */
export function silverBars(rows: readonly SilverMethodRow[]): SilverBar[] {
  const byMethod = new Map(rows.map((r) => [r.method, r]));
  const out: SilverBar[] = [];
  for (const m of SILVER_ORDER) {
    const r = byMethod.get(m);
    if (r === undefined) continue;
    out.push({ method: m, label: SILVER_LABEL[m], note: SILVER_NOTE[m], coverage: r.coverage, lo: r.lo, hi: r.hi, ours: m === "ours_full" });
  }
  return out;
}

export interface SilverHeadline {
  current: SilverMethodRow;
  ours: SilverMethodRow;
  /** Percentage points, ours - current. */
  gainPp: number;
}

function pair(s: SilverStratum): SilverHeadline | null {
  const current = s.methods.find((m) => m.method === "current");
  const ours = s.methods.find((m) => m.method === "ours_full");
  if (current === undefined || ours === undefined) return null;
  return { current, ours, gainPp: (ours.coverage - current.coverage) * 100 };
}

export const silverHeadline = pair;

export const pct0 = (x: number): string => `${Math.round(x * 100)}%`;
