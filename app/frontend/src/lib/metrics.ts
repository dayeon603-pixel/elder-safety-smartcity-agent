import { PRIMARY_METRIC } from "../config";

export interface MetricSpec {
  label: string;
  /** false = smaller is better (calibration errors). */
  higherIsBetter: boolean;
}

export const METRICS: Record<string, MetricSpec> = {
  auroc: { label: "AUROC", higherIsBetter: true },
  auprc: { label: "AUPRC", higherIsBetter: true },
  accuracy: { label: "정확도", higherIsBetter: true },
  f1: { label: "F1", higherIsBetter: true },
  brier: { label: "Brier 점수", higherIsBetter: false },
  ece: { label: "ECE", higherIsBetter: false },
};

export function metricLabel(key: string): string {
  return METRICS[key]?.label ?? key.toUpperCase();
}

export function higherIsBetter(key: string): boolean {
  return METRICS[key]?.higherIsBetter ?? true;
}

/** Order metrics: primary first, then known metrics in declaration order, then the rest alphabetically. */
export function orderMetrics(keys: readonly string[]): string[] {
  const known = Object.keys(METRICS);
  const rank = (k: string): number => (k === PRIMARY_METRIC ? -1 : known.includes(k) ? known.indexOf(k) : known.length);
  return [...keys].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
}

/** True when the difference CI excludes 0 in the favourable direction. */
export function isSignificantGain(delta: { lo: number; hi: number } | undefined, key: string): boolean {
  if (delta === undefined) return false;
  return higherIsBetter(key) ? delta.lo > 0 : delta.hi < 0;
}
