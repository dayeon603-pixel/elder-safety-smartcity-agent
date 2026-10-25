import type { Interval } from "../api/types";
import type { HeatEvalRow, HeatMethod, HeatVariant } from "../api/heatTypes";
import type { ComparisonRow } from "../components/ComparisonChart";

export const CAPTURE = "capture";
/**
 * /heat/eval rows with year === POOLED_YEAR are the 2023-25 pooled estimate (independent block draws per year).
 * Contract with the backend: per-year rows are always present; pooled rows are optional.
 */
export const POOLED_YEAR = 0;

export function yearLabel(year: number): string {
  return year === POOLED_YEAR ? "전체 합산" : `${year}`;
}

export const METHOD_LABEL: Record<HeatMethod, string> = {
  B1: "B1 특보식 규칙",
  B1S: "B1S 하루 규칙",
  B2: "B2 시도 단위 모형",
  HEUR: "HEUR 과거 발생률×기온",
  OURS: "OURS 우리 모형",
};

export const METHOD_NOTE: Record<HeatMethod, string> = {
  B1: "특보 문구를 재현한 규칙",
  B1S: "특보 규칙을 하루 기준으로 완화 (확인용)",
  B2: "시도 단위 모형을 인구 비례로 나눔",
  HEUR: "과거 발생률 × 기온 효과",
  OURS: "시군구 단위 통계 모델",
};

export const VARIANT_EXPLAIN: Record<HeatVariant, string> = {
  F: "당일·다음날 기온(예보 대용) 사용",
  O: "전날까지 정보만 사용",
};

/** One plain-Korean sentence per statistical term, shown on first appearance. */
export const GLOSS = {
  capture: "포착률 = 경보를 낸 지역에서 나온 환자 ÷ 전체 환자",
  ci: "95% 신뢰구간 = 같은 실험을 반복해도 결과가 들어올 가능성이 높은 범위",
  pp: "%p = 퍼센트끼리의 차이 (42% → 56% = 14%p)",
  k: "같은 경보 수(K) = 그날 특보 규칙이 경보한 지역 수만큼만 우리도 경보",
  variant: "F: 당일·다음날 기온(예보 대용) 사용 / O: 전날까지 정보만 사용",
} as const;

/** CI may be missing (n_alerts, point-only metrics); collapse to the point so charts still draw. */
export function toInterval(r: { value: number; ci_lo: number | null; ci_hi: number | null }): Interval {
  return { point: r.value, lo: r.ci_lo ?? r.value, hi: r.ci_hi ?? r.value };
}

export function pick(rows: readonly HeatEvalRow[], year: number, method: HeatMethod, metric = CAPTURE): HeatEvalRow | undefined {
  return rows.find((r) => r.year === year && r.method === method && r.metric === metric);
}

/** Baseline (B1) vs ours per year, for ComparisonChart. Years missing either side are skipped. */
export function comparisonRows(rows: readonly HeatEvalRow[], years: readonly number[]): ComparisonRow[] {
  return years.flatMap((year) => {
    const b = pick(rows, year, "B1");
    const o = pick(rows, year, "OURS");
    return b === undefined || o === undefined ? [] : [{ key: String(year), label: `${year}`, baseline: toInterval(b), ours: toInterval(o) }];
  });
}

export interface ForestRow {
  key: string;
  year: number;
  variant: HeatVariant;
  label: string;
  value: number;
  lo: number;
  hi: number;
  /** Interval excludes 0 on the positive side. */
  significant: boolean;
}

/** OURS - B1 paired difference per year and variant, years ascending, F before O. */
export function forestRows(byVariant: Partial<Record<HeatVariant, readonly HeatEvalRow[]>>): ForestRow[] {
  const out: ForestRow[] = [];
  for (const variant of ["F", "O"] as const) {
    const rows = byVariant[variant] ?? [];
    const years = [...new Set(rows.map((r) => r.year))].sort((a, b) => a - b);
    for (const year of years) {
      const d = pick(rows, year, "OURS")?.delta_vs_b1;
      if (d === null || d === undefined) continue;
      const iv = toInterval(d);
      out.push({ key: `${variant}-${year}`, year, variant, label: `${yearLabel(year)} · ${variant}`, value: iv.point, lo: iv.lo, hi: iv.hi, significant: iv.lo > 0 });
    }
  }
  return out;
}

/** Percentage points with a real minus sign: 0.042 -> "+4.2%p". */
export function formatPoints(x: number, digits = 1): string {
  if (!Number.isFinite(x)) return "–";
  const body = Math.abs(x * 100).toFixed(digits);
  if (Number(body) === 0) return `${body}%p`;
  return `${x > 0 ? "+" : "−"}${body}%p`;
}

/** Plain-language count of years whose gain interval excludes 0, per variant. Reports losses as plainly as wins. */
export function summarizeGains(rows: readonly ForestRow[]): string[] {
  return (["F", "O"] as const).flatMap((variant) => {
    const mine = rows.filter((r) => r.variant === variant && r.year !== POOLED_YEAR);
    if (mine.length === 0) return [];
    const wins = mine.filter((r) => r.significant).map((r) => r.year);
    const negative = mine.filter((r) => r.hi < 0).map((r) => r.year);
    const head = `${variant}: 시험 연도 ${mine.length}개 중 ${wins.length}개에서 95% 신뢰구간이 0을 제외합니다`;
    const tail = wins.length > 0 ? ` (${wins.join(", ")}).` : ".";
    const loss = negative.length > 0 ? ` ${negative.join(", ")}년은 B1보다 낮습니다.` : "";
    return [`${head}${tail}${loss}`];
  });
}

/** Curve series display order and line style; baselines gray, ours accent (set by the chart). */
export const CURVE_ORDER: readonly HeatMethod[] = ["B1", "B1S", "B2", "HEUR", "OURS"];

export interface HeroPair {
  /** POOLED_YEAR when the pooled rows exist, otherwise the latest per-year pair. */
  year: number;
  b1: Interval;
  ours: Interval;
  delta: Interval | null;
}

/** Headline pair: pooled 2023-25 if the API provides it; otherwise the latest year, never an average of CIs. */
export function heroPair(rows: readonly HeatEvalRow[]): HeroPair | null {
  const years = [...new Set(rows.map((r) => r.year))].sort((a, b) => b - a);
  const order = years.includes(POOLED_YEAR) ? [POOLED_YEAR, ...years.filter((y) => y !== POOLED_YEAR)] : years;
  for (const year of order) {
    const b = pick(rows, year, "B1");
    const o = pick(rows, year, "OURS");
    if (b !== undefined && o !== undefined) {
      return { year, b1: toInterval(b), ours: toInterval(o), delta: o.delta_vs_b1 === null ? null : toInterval(o.delta_vs_b1) };
    }
  }
  return null;
}

export interface DecompStep {
  method: HeatMethod;
  capture: Interval;
  /** Point change versus the previous step (0 for the first). */
  gain: number;
  /** Share of the total B1 -> OURS gain this step contributes; null if the total is not positive. */
  share: number | null;
}

export const DECOMP_ORDER: readonly HeatMethod[] = ["B1", "B2", "HEUR", "OURS"];

export const DECOMP_LABEL: Record<HeatMethod, string> = {
  B1: "특보 규칙",
  B2: "+ 인구·취약성 반영",
  HEUR: "+ 시군구 과거 발생률",
  OURS: "+ 시군구 모델 = 우리 엔진",
  B1S: "특보 규칙(하루)",
};

export const DECOMP_NOTE: Record<HeatMethod, string> = {
  B1: "기온만 보고 경보",
  B2: "시도별 예상 환자를 인구·고령 비율에 맞춰 시군구로 나눔",
  HEUR: "시군구마다 과거 발생률을 곱함",
  OURS: "시군구 단위 통계 모델로 순위를 다듬음",
  B1S: "",
};

/** B1 -> B2 -> HEUR -> OURS step gains for one year (or POOLED_YEAR). Null when a step is missing. */
export function decomposition(rows: readonly HeatEvalRow[], year: number): DecompStep[] | null {
  const picked = DECOMP_ORDER.map((m) => ({ m, r: pick(rows, year, m) }));
  if (picked.some((p) => p.r === undefined)) return null;
  const caps = picked.map((p) => toInterval(p.r as HeatEvalRow));
  const total = (caps[caps.length - 1]?.point ?? 0) - (caps[0]?.point ?? 0);
  return picked.map((p, i) => {
    const cap = caps[i] as Interval;
    const gain = i === 0 ? 0 : cap.point - (caps[i - 1] as Interval).point;
    return { method: p.m, capture: cap, gain, share: i === 0 || total <= 0 ? null : gain / total };
  });
}
