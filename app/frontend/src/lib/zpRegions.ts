import type { HeatRegion } from "../api/heatTypes";
import { ZP_DEFAULT_SIDO, ZP_DEFAULT_SIGUNGU } from "../config";

const COLLATOR = new Intl.Collator("ko-KR");

export interface Pick {
  sido: string;
  code: string;
}

/** Sido names in display order and the 시군구 list of the chosen sido. */
export function regionOptions(regions: readonly HeatRegion[], sido: string): { sidos: string[]; sigungu: HeatRegion[] } {
  const sidos = [...new Set(regions.map((r) => r.sido))].sort((a, b) => (a === ZP_DEFAULT_SIDO ? -1 : b === ZP_DEFAULT_SIDO ? 1 : COLLATOR.compare(a, b)));
  const sigungu = regions.filter((r) => r.sido === sido).sort((a, b) => COLLATOR.compare(a.name, b.name));
  return { sidos, sigungu };
}

/** Resolve the selection from the URL code, else the default 관악구, else the first region. */
export function resolvePick(regions: readonly HeatRegion[], code: string | null): Pick | null {
  const byCode = regions.find((r) => r.code === code);
  const fallback = regions.find((r) => r.sido === ZP_DEFAULT_SIDO && r.name === ZP_DEFAULT_SIGUNGU) ?? regions[0];
  const r = byCode ?? fallback;
  return r === undefined ? null : { sido: r.sido, code: r.code };
}
