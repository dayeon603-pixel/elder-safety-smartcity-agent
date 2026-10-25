/**
 * Response shapes of the 에이전트 module endpoints: /silver/*, /aed/*, /briefing/{sgg_code}.
 * Ratios are fractions in [0, 1]. Fields the backend may send in more than one shape are typed as unions
 * and normalized in lib/zpBriefing.ts, so the pages only ever see one form.
 */

export interface ZpInterval {
  value: number;
  lo: number;
  hi: number;
}

// ---------- /silver ----------

export type SilverMethod = "current" | "random_k" | "pop65_k" | "past_k" | "ours_full";

export interface SilverMethodRow {
  method: SilverMethod;
  coverage: number;
  lo: number;
  hi: number;
}

export interface SilverStratum {
  /** Test hotspots in this stratum (2022-24 folds pooled). */
  n_hotspots: number;
  /** Casualties summed over those hotspots. */
  casualties: number;
  methods: SilverMethodRow[];
}

export interface SilverNote {
  title: string;
  text: string;
}

/** GET /silver/summary */
export interface SilverSummary {
  /** Number of zones every method may pick (the current count of 노인보호구역). */
  k: number;
  radius_m: number;
  test_years: number[];
  /** All test hotspots. */
  all: SilverStratum;
  /** Hotspots with no past hotspot within the radius ("신규 다발지"). */
  new_sites: SilverStratum;
  /** Q&A notes: the past_k tie, pool definitions, limits. */
  notes: SilverNote[];
}

export type SilverPointKind = "current" | "ours" | "hotspot_2024";

export interface SilverMapPoint {
  lat: number;
  lon: number;
  kind: SilverPointKind;
  name: string | null;
  /** Casualties for hotspot points, null otherwise. */
  cas: number | null;
  /** 1 when a current zone is within the radius of this point. */
  cur_within: 0 | 1;
  ours_within: 0 | 1;
}

/** GET /silver/map?city=seoul */
export interface SilverMap {
  city: string;
  radius_m: number;
  points: SilverMapPoint[];
}

// ---------- /aed ----------

/** GET /aed/summary */
export interface AedSummary {
  /** Share of AEDs open on weekdays, daytime (fraction). */
  day_open_share: number;
  night_open_share: number;
  /** Share of public-place cardiac arrests that happen 22:00-06:00 (fraction) with 95% CI. */
  night_arrest_share: ZpInterval;
  n_public_arrests: number;
  /** Scope sentence from the API (area, source, period, what the numbers are not). */
  scope: string;
}

export interface AedHour {
  hour: number;
  public_arrests: number;
  /** Percent of AEDs open at this hour on weekdays, 0-100. */
  pct_open: number;
}

export interface AedGu {
  code: string;
  gu: string;
  n_aed: number;
  night_open_share: number;
  day_open_share: number;
  /** Estimated public arrests at night per year in the gu. */
  night_arrests_per_year: number;
}

export interface AedCandidate {
  gu: string;
  org: string;
  address: string;
  place: string | null;
  /** Hours open on weekdays, e.g. "0900-1600"; null if missing. */
  hours: string | null;
  night_open_share: number;
  gain_score: number;
}

// ---------- /briefing (backend shape) ----------

export interface BriefingShelter {
  name: string;
  dist_km: number | null;
  night_open?: boolean | null;
  weekend_open?: boolean | null;
}

export interface BriefingAedCandidate {
  org: string;
  address: string;
  place: string | null;
}

export interface BriefingAction {
  /** Stable id from the API (e.g. "heat.alert"); older responses have none. */
  action_id?: string;
  /** The numbers the action was made from, as the API returned them. */
  evidence?: Record<string, unknown>;
  module: string;
  text: string;
  reason: string;
}

/** GET /briefing/{sgg_code}. Nulls mean "not evaluated" (heat) or "outside Seoul / not loaded" (silver, aed). */
export interface BriefingResponse {
  date?: string;
  teams?: number;
  region: { code: string; sido: string; name: string; pop65?: number };
  heat: {
    rank: number | null;
    n_regions?: number;
    alerted_b1: boolean | null;
    alerted_ours: boolean | null;
    cases_actual: number;
    plan_allocation_for_region: { teams: number; teams_total?: number; expected_cases?: number | null; reason?: string | null };
    nearby_open_shelters: { coverage: "data" | "none"; items: BriefingShelter[] };
  };
  silver: {
    current_zones_in_region: number | null;
    our_picks_in_region: number | null;
    hotspots_2024_in_region: number | null;
    hotspots_covered_current?: number | null;
    hotspots_covered_ours?: number | null;
    crosswalk_green_deficit_count: number | null;
    note?: string | null;
  };
  aed: {
    night_open_share: number;
    day_open_share: number;
    gap_rank?: number;
    candidates_top5: BriefingAedCandidate[];
  } | null;
  aed_note?: string | null;
  actions: BriefingAction[];
}

// ---------- raw backend payloads of /silver and /aed (adapted in lib/zpAdapt.ts) ----------

export interface RawSilverMethod { method: string; label: string; coverage: number; ci_lo: number; ci_hi: number }
export interface RawSilverDelta { vs: string; label: string; delta_pp: number; lo_pp: number; hi_pp: number }
export interface RawSilverStratum { stratum: "all" | "new"; n_hot: number; casualties: number; methods: RawSilverMethod[]; ours_minus: RawSilverDelta[] }
export interface RawSilverSummary { k: number; variant: string; pool: string; definitions: Record<string, string>; strata: RawSilverStratum[] }
export interface RawSilverSite { lat: number; lon: number; name: string | null; sgg_code: string | null; near_current: boolean; near_ours: boolean }
export interface RawSilverHotspot { lat: number; lon: number; casualties: number; sgg_code: string | null; covered_current: boolean; covered_ours: boolean }
export interface RawSilverMap { city: string; k: number; current: RawSilverSite[]; ours: RawSilverSite[]; hotspots_2024: RawSilverHotspot[] }

export interface RawInterval { value: number; ci_lo: number | null; ci_hi: number | null; n: number | null }
export interface RawAedSummary {
  scope: string;
  weekday: { day: number; night: number };
  public_arrests_total: number;
  night_share_of_public_arrests: RawInterval;
}
export interface RawAedHour { hour: number; public_arrests: number; open_share_weekday: number; open_share_weekend: number }
export interface RawAedGu { gu: string; sgg_code: string | null; night_open_share: number; day_open_share: number; gap_score: number; public_arrests_night: number; n_aed: number }
export interface RawAedCandidate { gu: string; rank: number; org: string; address: string; place: string | null; weekday_hours: string | null; night_open_weekday_share: number; gain_score: number }

// ---------- GET /actions/{sgg_code}: the 행동 채널 drafts (nothing is sent) ----------

export interface ActionsCbs {
  text: string;
  easy_text: string;
  length: number;
  easy_length: number;
  limit: number;
  shelter_note?: string | null;
  status: string;
}
export interface ActionsBit { rank: number; stop_id: string; name: string; dong: string; covered_pop65_est: number; message: { lines: string[] } }
export interface ActionsRouteStop { order: number; dong: string; pop65: number; leg_km: number }
export interface ActionsRoute {
  stops: ActionsRouteStop[];
  km: number;
  distance_basis: string;
  note?: string | null;
}
export interface ActionsSignalsCoverage { n_crosswalks_in_region: number; n_evaluable: number; n_short: number; note?: string | null }
export interface ActionsSignal {
  crosswalk_id: string;
  address: string;
  length_m: number;
  green_s: number;
  required_green_s: number;
  extend_s: number;
}
/** The API returns the list, or {note} when the region has no evaluable crosswalk. */
export type ActionsSignals = ActionsSignal[] | { note: string };
export interface ActionsResponse {
  sgg_code: string;
  region_name: string;
  context?: { tmax_c: number | null };
  cbs: ActionsCbs;
  bit: ActionsBit[];
  signals: ActionsSignals;
  signals_coverage: ActionsSignalsCoverage;
  route: ActionsRoute;
  disclaimer: string;
}
