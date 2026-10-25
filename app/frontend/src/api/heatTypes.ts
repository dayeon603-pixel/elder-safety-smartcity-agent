/** Response shapes of /api/heat/*. Mirrors the "API 응답 형태" table in ideas/G2_A_heat.md. */

export type HeatVariant = "F" | "O";
export type HeatMethod = "B1" | "B1S" | "B2" | "HEUR" | "OURS";
export type HeatSort = "gap" | "date";

/** The season may arrive as a label ("05-20~09-15") or as a range object; the UI only prints it. */
export type HeatSeason = string | { start: string; end: string };

export interface HeatSource {
  name: string;
  license?: string;
  url?: string;
}

export interface HeatMeta {
  years: number[];
  variants: HeatVariant[];
  methods: HeatMethod[];
  season: HeatSeason;
  sources: (string | HeatSource)[];
}

export interface HeatInterval {
  value: number;
  ci_lo: number | null;
  ci_hi: number | null;
}

export interface HeatEvalRow extends HeatInterval {
  year: number;
  method: HeatMethod;
  /** capture | precision | auc | prauc | n_alerts */
  metric: string;
  /** Paired difference against B1 (same year, same variant). Null for B1 itself. */
  delta_vs_b1: HeatInterval | null;
}

export interface HeatCurvePoint {
  /** Budget multiple of the B1 alert count (1 = same number of alerts as B1). */
  x: number;
  capture: number;
}

export interface HeatCurve {
  b1_point: HeatCurvePoint;
  series: Partial<Record<HeatMethod, HeatCurvePoint[]>>;
}

/** One test-season day. `*_captured` are patient counts caught by each method at the same K. */
export interface HeatDay {
  date: string;
  n_cases: number;
  k_budget: number;
  b1_captured: number;
  ours_captured: number;
}

export interface HeatRegion {
  code: string;
  sido: string;
  name: string;
  lat: number;
  lon: number;
  pop65: number;
}

export interface HeatAlertEntry {
  code: string;
  score: number;
  cases: number;
}

export interface HeatAlerts {
  date: string;
  /** Alerts per method on this day (same for both). */
  k: number;
  total_cases: number;
  b1: HeatAlertEntry[];
  ours: HeatAlertEntry[];
  cases: { code: string; n: number }[];
}

/** Stored as 0/1/NULL in SQLite; the API may pass either booleans or integers. */
export type HeatFlag = boolean | 0 | 1 | null;

export interface HeatShelter {
  name: string;
  dist_km: number;
  night_open: HeatFlag;
  weekend_open: HeatFlag;
}

export interface HeatRegionDetail {
  region: HeatRegion;
  cases_today: number;
  scores: { b1: number; ours: number; rank: number };
  drivers: {
    /** Daily max temperature, degrees C. */
    tmax: number | null;
    /** 체감 최고 (KMA ASOS apparent temperature daily max); absent on older responses. */
    apparent_max?: number | null;
    /** Weather station the temperatures come from. */
    station?: { name: string; dist_km: number } | null;
    /** Historical daily incidence for this region. */
    hist_rate: number;
    /** Share of residents aged 65+, as a fraction in [0, 1]. */
    old_share: number;
  };
  shelters: {
    /** "none" means no coordinate data for this region (not zero shelters). */
    coverage: "data" | "none";
    nearby: HeatShelter[];
  };
}

export interface HeatAllocation {
  code: string;
  name: string;
  teams: number;
  expected_cases: number;
  actual_cases: number;
  /** Plain-Korean reason the API generated for this allocation. */
  reason: string;
}

export interface HeatShelterExtension {
  shelter_id: number;
  name: string;
  region_code: string;
  reason: string;
}

/** GET /heat/plan: where to send N response teams on one day, plus shelters worth keeping open longer. */
export interface HeatPlan {
  date: string;
  teams: number;
  allocations: HeatAllocation[];
  shelter_extensions: HeatShelterExtension[];
  /** Shelters skipped because their opening hours are unknown. */
  unknown_hours_count: number;
  /** Patients in districts that received a team: ours vs the rule-based (B1) allocation with the same teams. */
  compare: { ours_reached: number; b1_reached: number };
}

/** GET /heat/plan/eval: the same allocation rule replayed over a whole test season. Extra fields are ignored. */
export interface HeatPlanEval {
  year: number;
  teams: number;
  n_days: number;
  total_cases: number;
  /** Patients in districts that received a team, summed over the season. */
  ours_reached: number;
  b1_reached: number;
  delta: number;
  ci_lo: number;
  ci_hi: number;
}
