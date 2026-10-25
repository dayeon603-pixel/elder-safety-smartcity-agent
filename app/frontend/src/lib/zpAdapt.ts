import type {
  AedCandidate, AedGu, AedHour, AedSummary, RawAedCandidate, RawAedGu, RawAedHour, RawAedSummary, RawSilverMap, RawSilverStratum,
  RawSilverSummary, SilverMap, SilverMethod, SilverMethodRow, SilverNote, SilverStratum, SilverSummary,
} from "../api/zpTypes";
import { ZP_SILVER_RADIUS_M, ZP_SILVER_TEST_YEARS } from "../config";
import { SILVER_ORDER } from "./zpSilver";

const PERCENT = 100;
/** The backend names our method ours_opt (max-coverage placement); the UI keeps one internal id for it. */
const OURS_ALIASES: ReadonlyMap<string, SilverMethod> = new Map([["ours_opt", "ours_full"], ["ours_full", "ours_full"]]);
const canonicalMethod = (m: string): string => OURS_ALIASES.get(m) ?? m;
const KNOWN: ReadonlySet<string> = new Set<string>(SILVER_ORDER);

function stratum(s: RawSilverStratum | undefined): SilverStratum {
  if (s === undefined) return { n_hotspots: 0, casualties: 0, methods: [] };
  const methods: SilverMethodRow[] = s.methods
    .filter((m) => KNOWN.has(canonicalMethod(m.method)))
    .map((m) => ({ method: canonicalMethod(m.method) as SilverMethod, coverage: m.coverage, lo: m.ci_lo, hi: m.ci_hi }));
  return { n_hotspots: s.n_hot, casualties: s.casualties, methods };
}

const pct1 = (x: number): string => `${(x * PERCENT).toFixed(1)}%`;

/** Q&A notes written from the numbers the API sent, so they never drift from the headline. */
function silverNotes(raw: RawSilverSummary, all: SilverStratum, fresh: SilverStratum): SilverNote[] {
  const notes: SilverNote[] = [];
  const past = all.methods.find((m) => m.method === "past_k");
  const ours = all.methods.find((m) => m.method === "ours_full");
  const delta = raw.strata.find((s) => s.stratum === "all")?.ours_minus.find((d) => d.vs.endsWith(":past_k"));
  if (past !== undefined && ours !== undefined) {
    const diff = delta === undefined ? "" : ` 두 방법의 차이는 ${delta.delta_pp.toFixed(1)}%p(95% 신뢰구간 ${delta.lo_pp.toFixed(1)} ~ ${delta.hi_pp.toFixed(1)})입니다.`;
    notes.push({
      title: "과거 사고 순위(past_k)와의 차이",
      text: `같은 ${raw.k.toLocaleString("ko-KR")}곳을 과거 사상자 순으로 고르면 ${pct1(past.coverage)}, 에이전트 분석은 ${pct1(ours.coverage)}입니다.${diff}`,
    });
  }
  notes.push({
    title: "후보 풀",
    text: `후보 풀은 ${raw.pool}입니다. 과거 다발지 중심, 현행 노인보호구역, 행정동 중심을 합쳐 50m 안 중복을 제거한 지점입니다.`,
  });
  notes.push({
    title: "평가 방식",
    text: `${ZP_SILVER_TEST_YEARS.join("·")}년을 각각 그 전 해까지의 자료만으로 예측해 폴드를 합쳤습니다. 신규 다발지는 ${fresh.n_hotspots}곳(사상자 ${Math.round(fresh.casualties)}명)이라 신뢰구간이 넓습니다.`,
  });
  for (const [k, v] of Object.entries(raw.definitions)) notes.push({ title: `용어: ${k}`, text: v });
  return notes;
}

export function adaptSilverSummary(raw: RawSilverSummary): SilverSummary {
  const all = stratum(raw.strata.find((s) => s.stratum === "all"));
  const fresh = stratum(raw.strata.find((s) => s.stratum === "new"));
  return { k: raw.k, radius_m: ZP_SILVER_RADIUS_M, test_years: [...ZP_SILVER_TEST_YEARS], all, new_sites: fresh, notes: silverNotes(raw, all, fresh) };
}

export function adaptSilverMap(raw: RawSilverMap): SilverMap {
  const flag = (b: boolean): 0 | 1 => (b ? 1 : 0);
  return {
    city: raw.city,
    radius_m: ZP_SILVER_RADIUS_M,
    points: [
      ...raw.current.map((s) => ({ lat: s.lat, lon: s.lon, kind: "current" as const, name: s.name, cas: null, cur_within: flag(s.near_current), ours_within: flag(s.near_ours) })),
      ...raw.ours.map((s) => ({ lat: s.lat, lon: s.lon, kind: "ours" as const, name: s.name, cas: null, cur_within: flag(s.near_current), ours_within: flag(s.near_ours) })),
      ...raw.hotspots_2024.map((h) => ({ lat: h.lat, lon: h.lon, kind: "hotspot_2024" as const, name: null, cas: h.casualties, cur_within: flag(h.covered_current), ours_within: flag(h.covered_ours) })),
    ],
  };
}

/** Shares arrive as fractions; if every value of a series is above 1 the series is percent and is rescaled. */
function asFractions(values: readonly number[]): number {
  return values.some((v) => v > 1) ? 1 / PERCENT : 1;
}

export function adaptAedSummary(raw: RawAedSummary): AedSummary {
  const f = asFractions([raw.weekday.day, raw.weekday.night, raw.night_share_of_public_arrests.value]);
  const n = raw.night_share_of_public_arrests;
  return {
    day_open_share: raw.weekday.day * f,
    night_open_share: raw.weekday.night * f,
    night_arrest_share: { value: n.value * f, lo: (n.ci_lo ?? n.value) * f, hi: (n.ci_hi ?? n.value) * f },
    n_public_arrests: raw.public_arrests_total,
    scope: raw.scope,
  };
}

export function adaptAedHourly(raw: readonly RawAedHour[]): AedHour[] {
  const scale = raw.some((r) => r.open_share_weekday > 1) ? 1 : PERCENT;
  return raw.map((r) => ({ hour: r.hour, public_arrests: r.public_arrests, pct_open: r.open_share_weekday * scale }));
}

export function adaptAedGu(raw: readonly RawAedGu[]): AedGu[] {
  const f = asFractions(raw.flatMap((g) => [g.night_open_share, g.day_open_share]));
  return raw.map((g) => ({
    code: g.sgg_code ?? g.gu,
    gu: g.gu,
    n_aed: g.n_aed,
    night_open_share: g.night_open_share * f,
    day_open_share: g.day_open_share * f,
    night_arrests_per_year: g.public_arrests_night,
  }));
}

export function adaptAedCandidates(raw: readonly RawAedCandidate[]): AedCandidate[] {
  const f = asFractions(raw.map((c) => c.night_open_weekday_share));
  return raw.map((c) => ({
    gu: c.gu, org: c.org, address: c.address, place: c.place === "" ? null : c.place, hours: c.weekday_hours,
    night_open_share: c.night_open_weekday_share * f, gain_score: c.gain_score,
  }));
}
