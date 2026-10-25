import { CITIZEN_WATCH_MULTIPLE } from "../config";

export type RiskLevel = "high" | "watch" | "low";

/**
 * Plain-language level for a district on one day from its rank among all districts by our score.
 * Inside the alert budget K = 높음; up to CITIZEN_WATCH_MULTIPLE x K = 주의; the rest = 낮음.
 * With K = 0 nothing is alerted, so everything is 낮음.
 */
export function riskLevel(rank: number, k: number): RiskLevel {
  if (!Number.isFinite(rank) || k <= 0) return "low";
  if (rank <= k) return "high";
  return rank <= k * CITIZEN_WATCH_MULTIPLE ? "watch" : "low";
}

export interface RiskStyle {
  word: string;
  /** Shape glyph so the level is never conveyed by colour alone. */
  glyph: string;
  line: string;
  box: string;
  advice: string;
}

export const RISK_STYLE: Record<RiskLevel, RiskStyle> = {
  high: { word: "높음", glyph: "▲", line: "#b3261e", box: "border-[#b3261e] bg-[#fbeceb] text-[#7a1a15]", advice: "오늘은 낮 시간 야외 활동을 피하고, 가까운 쉼터에서 쉬세요." },
  watch: { word: "주의", glyph: "◆", line: "#8a5a00", box: "border-[#8a5a00] bg-[#fff4d6] text-[#5c3b00]", advice: "물을 자주 마시고, 더운 시간에는 그늘에서 쉬세요." },
  low: { word: "낮음", glyph: "●", line: "#1d6b3a", box: "border-[#1d6b3a] bg-[#e8f3ec] text-[#14502a]", advice: "그래도 더위를 느끼면 바로 쉬고 물을 드세요." },
};
