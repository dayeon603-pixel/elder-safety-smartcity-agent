/**
 * Exact ranks are statistically unstable (the 95% interval of a district's rank is wide: ~19 places out of 25),
 * so the UI states set membership ("상위 5구 안") instead of "N위".
 */
const HEAT_BANDS: readonly { share: number; label: string }[] = [
  { share: 0.1, label: "상위 10% 안" },
  { share: 0.25, label: "상위 25% 안" },
  { share: 0.5, label: "상위 절반 안" },
];
const HEAT_LOWER = "하위 절반";
const AED_BANDS: readonly number[] = [5, 10];

/** Rank of n regions (1 = highest risk) as a coarse band. */
export function heatRankBand(rank: number, n: number | null): string {
  if (n === null || n <= 0) return "순위 자료 없음";
  for (const b of HEAT_BANDS) if (rank <= Math.ceil(n * b.share)) return b.label;
  return HEAT_LOWER;
}

/** Night-gap rank among Seoul's gu (1 = emptiest at night). */
export function guRankBand(rank: number): string {
  for (const k of AED_BANDS) if (rank <= k) return `상위 ${k}구 안`;
  return `상위 ${AED_BANDS[AED_BANDS.length - 1] ?? 0}구 밖`;
}
