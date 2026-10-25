import type { BriefingAction, BriefingAedCandidate, BriefingResponse, BriefingShelter } from "../api/zpTypes";

/** Values above this are read as percentages (0-100) instead of fractions (0-1). */
const PERCENT_THRESHOLD = 1;
const PERCENT_SCALE = 100;

/** Accept either 0.49 or 49 and return a fraction clamped to [0, 1]. Non-finite input becomes null. */
export function toFraction(x: number | null | undefined): number | null {
  if (x === null || x === undefined || !Number.isFinite(x)) return null;
  const f = x > PERCENT_THRESHOLD ? x / PERCENT_SCALE : x;
  return Math.min(1, Math.max(0, f));
}

export type AlertClass = "both" | "b1_only" | "ours_only" | "none" | "unknown";

export interface HeatPanelModel {
  /** null when the API did not evaluate this region on this day. */
  rank: number | null;
  nRegions: number | null;
  alertedB1: boolean | null;
  alertedOurs: boolean | null;
  casesActual: number;
  teams: number;
  /** Expected patients in this region from the day's visit plan; null when not allocated. */
  expectedCases: number | null;
  shelters: BriefingShelter[];
  shelterCount: number;
  /** false when the region has no shelter coordinates: "0곳" would then be wrong, it is "자료 없음". */
  shelterData: boolean;
  alertClass: AlertClass;
}

export interface SilverPanelModel {
  /** null for every count when the region is outside Seoul or the module is not loaded. */
  available: boolean;
  note: string | null;
  currentZones: number;
  ourPicks: number;
  hotspots: number;
  /** Hotspots inside a current zone / inside a recommended zone; null when the API did not return them. */
  hotspotsCovered: { current: number; ours: number } | null;
  /** null when the API has no crosswalk signal timings for this region. */
  greenDeficit: number | null;
}

export interface AedPanelModel {
  nightOpen: number | null;
  dayOpen: number | null;
  gapRank: number | null;
  candidates: BriefingAedCandidate[];
}

export interface BriefingModel {
  region: BriefingResponse["region"];
  date: string | null;
  heat: HeatPanelModel;
  silver: SilverPanelModel;
  /** null when the region is outside Seoul: the AED panel then shows `aedNote` ("서울만 분석"). */
  aed: AedPanelModel | null;
  aedNote: string | null;
  actions: BriefingAction[];
}

function alertClass(b1: boolean | null, ours: boolean | null): AlertClass {
  if (b1 === null || ours === null) return "unknown";
  if (b1 && ours) return "both";
  if (b1) return "b1_only";
  if (ours) return "ours_only";
  return "none";
}

/** Collapse the backend's nullable fields into one shape, so panels render without branching on raw nulls. */
export function normalizeBriefing(r: BriefingResponse): BriefingModel {
  const h = r.heat;
  const sh = h.nearby_open_shelters;
  const s = r.silver;
  return {
    region: r.region,
    date: r.date ?? null,
    heat: {
      rank: h.rank,
      nRegions: h.n_regions ?? null,
      alertedB1: h.alerted_b1,
      alertedOurs: h.alerted_ours,
      casesActual: h.cases_actual,
      teams: h.plan_allocation_for_region.teams,
      expectedCases: h.plan_allocation_for_region.expected_cases ?? null,
      shelters: sh.items,
      shelterCount: sh.items.length,
      shelterData: sh.coverage === "data",
      alertClass: alertClass(h.alerted_b1, h.alerted_ours),
    },
    silver: {
      available: s.current_zones_in_region !== null,
      note: s.note ?? null,
      currentZones: s.current_zones_in_region ?? 0,
      ourPicks: s.our_picks_in_region ?? 0,
      hotspots: s.hotspots_2024_in_region ?? 0,
      hotspotsCovered:
        s.hotspots_covered_current == null || s.hotspots_covered_ours == null ? null : { current: s.hotspots_covered_current, ours: s.hotspots_covered_ours },
      greenDeficit: s.crosswalk_green_deficit_count,
    },
    aed:
      r.aed === null
        ? null
        : {
            nightOpen: toFraction(r.aed.night_open_share),
            dayOpen: toFraction(r.aed.day_open_share),
            gapRank: r.aed.gap_rank ?? null,
            candidates: r.aed.candidates_top5,
          },
    aedNote: r.aed_note ?? null,
    actions: r.actions,
  };
}

export const ALERT_CLASS_LABEL: Readonly<Record<AlertClass, string>> = {
  unknown: "이 날은 평가 대상이 아닙니다",
  both: "특보도, 우리 경보도 냄",
  b1_only: "특보만 냄",
  ours_only: "우리 경보만 냄 (특보가 놓친 곳)",
  none: "둘 다 경보 없음",
};

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/** True for a real calendar date written YYYY-MM-DD. */
export function isIsoDate(text: string): boolean {
  const m = ISO_DATE.exec(text);
  if (m === null) return false;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const dt = new Date(Date.UTC(y, mo - 1, d));
  return dt.getUTCFullYear() === y && dt.getUTCMonth() === mo - 1 && dt.getUTCDate() === d;
}

/** Clamp a team count typed by the user into [min, max]; non-numbers fall back to `fallback`. */
export function clampTeams(raw: string | null, min: number, max: number, fallback: number): number {
  if (raw === null || raw.trim() === "") return fallback;
  const n = Math.trunc(Number(raw));
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}
