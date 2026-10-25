import type { TerminalKind } from "./types";

export interface KindStyle {
  /** Short Korean name, used as the accessible label. */
  label: string;
  bg: string;
  fg: string;
  /** Accent used for the big number and the flash ring. */
  accent: string;
}

/** WCAG AAA (7:1) is enforced for every fg/bg and accent/bg pair in present.test.ts. */
export const KIND_STYLE: Readonly<Record<TerminalKind | "idle", KindStyle>> = {
  idle: { label: "안내", bg: "#0b1220", fg: "#ffffff", accent: "#ffd54a" },
  heat: { label: "폭염", bg: "#7a2300", fg: "#ffffff", accent: "#ffe08a" },
  fall: { label: "응급", bg: "#7a0010", fg: "#ffffff", accent: "#ffe08a" },
  rain: { label: "호우", bg: "#08265f", fg: "#ffffff", accent: "#9fdcff" },
  clear: { label: "안전", bg: "#0a4220", fg: "#ffffff", accent: "#b8f5c8" },
};

/** Fallback line when a message has no title/body (the screen must never be blank). */
export const KIND_DEFAULT_TEXT: Readonly<Record<TerminalKind, { title: string; body: string }>> = {
  heat: { title: "폭염 주의", body: "그늘에서 쉬시고 물을 드세요" },
  fall: { title: "쓰러진 분이 있습니다", body: "도움이 오고 있습니다" },
  rain: { title: "비가 많이 옵니다", body: "안전한 곳에 머무르세요" },
  clear: { title: "안전합니다", body: "이제 괜찮습니다" },
};

function channel(hex: string, i: number): number {
  const v = parseInt(hex.slice(1 + i * 2, 3 + i * 2), 16) / 255;
  return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
}

export function luminance(hex: string): number {
  return 0.2126 * channel(hex, 0) + 0.7152 * channel(hex, 1) + 0.0722 * channel(hex, 2);
}

export function contrastRatio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi! + 0.05) / (lo! + 0.05);
}
