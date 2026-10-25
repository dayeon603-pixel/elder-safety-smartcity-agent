/**
 * Typed stand-ins for /api/heat/*, selected by VITE_HEAT_MOCK=1 (see heat.ts).
 *
 * Dev-only responses so the UI works before the backend is up. What is real and what is not:
 *   - /heat/eval: exact snapshot of experiments/heat/out/results.json (per year and pooled 2023-25).
 *   - districts and the representative day 2025-07-08 (alerts, patients): from map_day_2025_F.csv.
 *   - every other day, the budget curve and shelters: SEEDED SIMULATION, not data. Never quote them.
 * The pages show a "모의 응답" badge whenever HEAT_MOCK is on. Real responses replace each function one-for-one.
 */
import { ApiError } from "./client";
import { MOCK_EVAL, MOCK_UNITS, REP_DATE, REP_DAY } from "./heatMockData";
import type {
  HeatAlerts, HeatAlertEntry, HeatCurve, HeatCurvePoint, HeatDay, HeatEvalRow, HeatFlag, HeatMeta, HeatMethod, HeatPlan, HeatPlanEval, HeatAllocation,
  HeatRegion, HeatRegionDetail, HeatShelter, HeatSort, HeatVariant,
} from "./heatTypes";

const MOCK_LATENCY_MS = 80;
const TEST_YEARS: readonly number[] = [2023, 2024, 2025];
export const MOCK_REP_DATE = REP_DATE;
const REP_YEAR = Number(REP_DATE.slice(0, 4));
const METHODS: readonly HeatMethod[] = ["B1", "B1S", "B2", "HEUR", "OURS"];
const VARIANTS: readonly HeatVariant[] = ["F", "O"];
const SEASON_START = "05-20";
const SEASON_END = "09-15";
const B1_THRESHOLD_C = 33;
const TMAX_COEF = 0.3;
const TMAX_REF_C = 30;
const BASE_RATE = 2.5e-6;
/** Score noise (log scale) per variant: O sees only D-1, so it ranks worse than F. */
const OURS_NOISE: Record<HeatVariant, number> = { F: 0.35, O: 0.6 };
const HEUR_NOISE = 0.55;
const BUDGET_MULTIPLES: readonly number[] = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 2];
const SHELTER_SIDO = new Set(["충청남도", "부산광역시"]);
const MAX_SHELTERS = 4;
const MS_PER_DAY = 86_400_000;

const METRO_SIDO = new Set(["서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시", "대전광역시", "울산광역시"]);

// ---------- seeded randomness ----------

type Rng = () => number;

function hashSeed(text: string): number {
  let h = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    h = Math.imul(h ^ text.charCodeAt(i), 16777619);
  }
  return h >>> 0;
}

function mulberry32(seed: number): Rng {
  let a = seed;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function gauss(rng: Rng): number {
  const u = Math.max(rng(), 1e-12);
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * rng());
}

function poisson(rng: Rng, lambda: number): number {
  const limit = Math.exp(-lambda);
  let k = 0;
  let p = 1;
  do {
    k += 1;
    p *= rng();
  } while (p > limit);
  return k - 1;
}

// ---------- static region table ----------

interface MockRegion extends HeatRegion {
  pop: number;
  oldShare: number;
  /** Per-person baseline daily rate at the reference temperature (simulation only). */
  h: number;
}

const REGIONS: readonly MockRegion[] = (() => {
  const rng = mulberry32(hashSeed("regions"));
  return MOCK_UNITS.map(([code, sido, name, lat, lon, pop, oldShare]) => {
    const rural = !METRO_SIDO.has(sido);
    return {
      code, sido, name, lat, lon, pop, oldShare,
      pop65: Math.round(pop * oldShare),
      h: BASE_RATE * (rural ? 1.3 : 1) * Math.exp(gauss(rng) * 0.25),
    };
  });
})();
const INDEX_BY_CODE = new Map(REGIONS.map((r, i) => [r.code, i]));

// ---------- season simulation ----------

function seasonDates(year: number): string[] {
  const start = Date.parse(`${year}-${SEASON_START}T00:00:00Z`);
  const end = Date.parse(`${year}-${SEASON_END}T00:00:00Z`);
  const out: string[] = [];
  for (let t = start; t <= end; t += MS_PER_DAY) out.push(new Date(t).toISOString().slice(0, 10));
  return out;
}

interface Season {
  dates: string[];
  /** [day][region] */
  tmax: number[][];
  cases: number[][];
  lambda: number[][];
}

const seasonCache = new Map<number, Season>();

function simulateSeason(year: number): Season {
  const cached = seasonCache.get(year);
  if (cached !== undefined) return cached;
  const rng = mulberry32(hashSeed(`season-${year}`));
  const dates = seasonDates(year);
  const offsets = REGIONS.map((r) => -0.8 * (r.lat - 36) + gauss(rng) * 0.7);
  let anomaly = 0;
  const tmax: number[][] = [];
  const lambda: number[][] = [];
  const cases: number[][] = [];
  dates.forEach((_, d) => {
    const phase = Math.sin((Math.PI * d) / (dates.length - 1));
    anomaly = 0.8 * anomaly + gauss(rng) * 1.7;
    const national = 21.5 + 8 * phase + anomaly;
    const t = offsets.map((o) => national + o + gauss(rng) * 0.6);
    const lam = REGIONS.map((r, i) => r.pop65 * r.h * Math.exp(TMAX_COEF * ((t[i] ?? 0) - TMAX_REF_C)));
    tmax.push(t);
    lambda.push(lam);
    cases.push(lam.map((l) => poisson(rng, l)));
  });
  const season: Season = { dates, tmax, cases, lambda };
  if (year === REP_YEAR) {
    const day = dates.indexOf(REP_DATE);
    for (const [code, , , n, , t] of REP_DAY) {
      const i = INDEX_BY_CODE.get(code);
      if (i === undefined || day < 0) continue;
      (cases[day] as number[])[i] = n;
      (tmax[day] as number[])[i] = t;
    }
  }
  seasonCache.set(year, season);
  return season;
}

// ---------- per-variant alert sets ----------

interface Scored {
  /** Alert score per [method][day][region]. */
  score: Record<HeatMethod, number[][]>;
  /** Budget K_d from B1. */
  k: number[];
  /** Alerted region indices per [method][day]. */
  picked: Record<HeatMethod, number[][]>;
}

function topK(scores: readonly number[], k: number): number[] {
  return scores
    .map((s, i) => [s, i] as const)
    .sort((a, b) => b[0] - a[0] || a[1] - b[1])
    .slice(0, Math.max(0, k))
    .map(([, i]) => i);
}

const scoredCache = new Map<string, Scored>();

function scoreSeason(year: number, variant: HeatVariant): Scored {
  const key = `${year}|${variant}`;
  const cached = scoredCache.get(key);
  if (cached !== undefined) return cached;
  const season = simulateSeason(year);
  const rng = mulberry32(hashSeed(`scores-${key}`));
  const heurBias = REGIONS.map(() => gauss(rng) * HEUR_NOISE);
  const score: Record<HeatMethod, number[][]> = { B1: [], B1S: [], B2: [], HEUR: [], OURS: [] };
  const picked: Record<HeatMethod, number[][]> = { B1: [], B1S: [], B2: [], HEUR: [], OURS: [] };
  const k: number[] = [];
  season.dates.forEach((_, d) => {
    const today = season.tmax[d] ?? [];
    const prev = season.tmax[d - 1] ?? today.map(() => -Infinity);
    const lam = season.lambda[d] ?? [];
    const b1 = today.map((t, i) => Math.min(t, prev[i] ?? -Infinity));
    const kd = b1.filter((s) => s >= B1_THRESHOLD_C).length;
    // B2: allocate each province's expected cases to its districts by elderly population.
    const sidoLam = new Map<string, number>();
    const sidoPop = new Map<string, number>();
    REGIONS.forEach((r, i) => {
      sidoLam.set(r.sido, (sidoLam.get(r.sido) ?? 0) + (lam[i] ?? 0));
      sidoPop.set(r.sido, (sidoPop.get(r.sido) ?? 0) + r.pop65);
    });
    const b2 = REGIONS.map((r) => ((sidoLam.get(r.sido) ?? 0) * r.pop65) / (sidoPop.get(r.sido) ?? 1));
    const heur = REGIONS.map((r, i) => r.pop65 * r.h * Math.exp(heurBias[i] ?? 0) * Math.exp(TMAX_COEF * ((today[i] ?? 0) - TMAX_REF_C)));
    const ours = lam.map((l) => l * Math.exp(gauss(rng) * OURS_NOISE[variant]));
    score.B1.push(b1);
    score.B1S.push(today);
    score.B2.push(b2);
    score.HEUR.push(heur);
    score.OURS.push(ours);
    k.push(kd);
    picked.B1.push(topK(b1, kd));
    picked.B1S.push(today.flatMap((t, i) => (t >= B1_THRESHOLD_C ? [i] : [])));
    picked.B2.push(topK(b2, kd));
    picked.HEUR.push(topK(heur, kd));
    picked.OURS.push(topK(ours, kd));
  });
  const repDay = year === REP_YEAR && variant === "F" ? season.dates.indexOf(REP_DATE) : -1;
  if (repDay >= 0) {
    const real = (col: 1 | 2): number[] => REP_DAY.flatMap((r) => (r[col] === 1 ? [INDEX_BY_CODE.get(r[0]) ?? -1] : [])).filter((i) => i >= 0);
    const b1 = real(1);
    k[repDay] = b1.length;
    picked.B1[repDay] = b1;
    picked.OURS[repDay] = real(2);
    const ours = REGIONS.map(() => 0);
    for (const [code, , , , sc] of REP_DAY) ours[INDEX_BY_CODE.get(code) ?? 0] = sc;
    score.OURS[repDay] = ours;
  }
  const scored: Scored = { score, k, picked };
  scoredCache.set(key, scored);
  return scored;
}

function capturedOn(year: number, day: number, idx: readonly number[]): number {
  const row = simulateSeason(year).cases[day] ?? [];
  return idx.reduce((n, i) => n + (row[i] ?? 0), 0);
}

function totalOn(year: number, day: number): number {
  return (simulateSeason(year).cases[day] ?? []).reduce((a, b) => a + b, 0);
}

// ---------- endpoint implementations ----------

const delay = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, MOCK_LATENCY_MS));

function assertTestYear(year: number): void {
  if (!TEST_YEARS.includes(year)) throw new ApiError(422, `year ${year} is not a test year`);
}

export async function mockMeta(): Promise<HeatMeta> {
  await delay();
  return {
    years: [...TEST_YEARS],
    variants: [...VARIANTS],
    methods: [...METHODS],
    season: { start: SEASON_START, end: SEASON_END },
    sources: ["모의 응답 (합성 데이터)"],
  };
}

export async function mockEval(variant: HeatVariant): Promise<HeatEvalRow[]> {
  await delay();
  return MOCK_EVAL.filter((r) => r[0] === variant).map(([, year, method, value, lo, hi, d]) => ({
    year,
    method: method as HeatMethod,
    metric: "capture",
    value,
    ci_lo: lo,
    ci_hi: hi,
    delta_vs_b1: d === null ? null : { value: d[0], ci_lo: d[1], ci_hi: d[2] },
  }));
}

export async function mockCurve(year: number, variant: HeatVariant): Promise<HeatCurve> {
  await delay();
  assertTestYear(year);
  const scored = scoreSeason(year, variant);
  const days = scored.k.length;
  const total = Array.from({ length: days }, (_, d) => totalOn(year, d)).reduce((a, b) => a + b, 0);
  const point = (m: HeatMethod, mult: number): HeatCurvePoint => {
    let caught = 0;
    for (let d = 0; d < days; d += 1) {
      caught += capturedOn(year, d, topK(scored.score[m][d] ?? [], Math.round(mult * (scored.k[d] ?? 0))));
    }
    return { x: mult, capture: total === 0 ? 0 : caught / total };
  };
  const curveMethods: readonly HeatMethod[] = ["B1", "B2", "HEUR", "OURS"];
  const series: HeatCurve["series"] = {};
  for (const m of curveMethods) series[m] = BUDGET_MULTIPLES.map((x) => point(m, x));
  return { b1_point: point("B1", 1), series };
}

export async function mockDays(year: number, variant: HeatVariant, sort: HeatSort): Promise<HeatDay[]> {
  await delay();
  assertTestYear(year);
  const scored = scoreSeason(year, variant);
  const days: HeatDay[] = simulateSeason(year).dates.map((date, d) => ({
    date,
    n_cases: totalOn(year, d),
    k_budget: scored.k[d] ?? 0,
    b1_captured: capturedOn(year, d, scored.picked.B1[d] ?? []),
    ours_captured: capturedOn(year, d, scored.picked.OURS[d] ?? []),
  }));
  if (sort === "gap") {
    days.sort((a, b) => b.ours_captured - b.b1_captured - (a.ours_captured - a.b1_captured) || b.n_cases - a.n_cases || a.date.localeCompare(b.date));
  }
  return days;
}

export async function mockRegions(): Promise<HeatRegion[]> {
  await delay();
  return REGIONS.map(({ code, sido, name, lat, lon, pop65 }) => ({ code, sido, name, lat, lon, pop65 }));
}

function locate(date: string): { year: number; day: number } {
  const year = Number(date.slice(0, 4));
  assertTestYear(year);
  const day = simulateSeason(year).dates.indexOf(date);
  if (day < 0) throw new ApiError(422, `date ${date} is outside the test season`);
  return { year, day };
}

export async function mockAlerts(date: string, variant: HeatVariant): Promise<HeatAlerts> {
  await delay();
  const { year, day } = locate(date);
  const season = simulateSeason(year);
  const scored = scoreSeason(year, variant);
  const caseRow = season.cases[day] ?? [];
  const entries = (m: HeatMethod): HeatAlertEntry[] =>
    (scored.picked[m][day] ?? []).map((i) => ({
      code: REGIONS[i]?.code ?? "",
      score: Number((scored.score[m][day]?.[i] ?? 0).toFixed(4)),
      cases: caseRow[i] ?? 0,
    }));
  return {
    date,
    k: scored.k[day] ?? 0,
    total_cases: totalOn(year, day),
    b1: entries("B1"),
    ours: entries("OURS"),
    cases: caseRow.flatMap((n, i) => (n > 0 ? [{ code: REGIONS[i]?.code ?? "", n }] : [])),
  };
}

function mockShelters(region: MockRegion): HeatRegionDetail["shelters"] {
  if (!SHELTER_SIDO.has(region.sido)) return { coverage: "none", nearby: [] };
  const rng = mulberry32(hashSeed(`shelter-${region.code}`));
  const flag = (): HeatFlag => {
    const u = rng();
    return u < 0.15 ? null : u < 0.6;
  };
  const n = Math.floor(rng() * (MAX_SHELTERS + 1));
  const nearby: HeatShelter[] = Array.from({ length: n }, (_, i) => ({
    name: `${region.name} 무더위쉼터 ${i + 1}`,
    dist_km: Number((0.2 + rng() * 2.3).toFixed(2)),
    night_open: flag(),
    weekend_open: flag(),
  })).sort((a, b) => a.dist_km - b.dist_km);
  return { coverage: "data", nearby };
}

export async function mockRegion(code: string, date: string, variant: HeatVariant): Promise<HeatRegionDetail> {
  await delay();
  const i = INDEX_BY_CODE.get(code);
  const region = i === undefined ? undefined : REGIONS[i];
  if (i === undefined || region === undefined) throw new ApiError(404, "region not found");
  const { year, day } = locate(date);
  const season = simulateSeason(year);
  const scored = scoreSeason(year, variant);
  const ours = scored.score.OURS[day] ?? [];
  const mine = ours[i] ?? 0;
  const rank = 1 + ours.filter((s) => s > mine).length;
  return {
    region: { code: region.code, sido: region.sido, name: region.name, lat: region.lat, lon: region.lon, pop65: region.pop65 },
    cases_today: season.cases[day]?.[i] ?? 0,
    scores: { b1: Number((scored.score.B1[day]?.[i] ?? 0).toFixed(2)), ours: Number(mine.toFixed(4)), rank },
    drivers: {
      tmax: Number((season.tmax[day]?.[i] ?? 0).toFixed(1)),
      hist_rate: Number((region.pop65 * region.h).toFixed(4)),
      old_share: Number(region.oldShare.toFixed(3)),
    },
    shelters: mockShelters(region),
  };
}

/** Greedy team allocation: each team goes where score / (teams already there + 1) is highest. */
function allocate(scores: readonly number[], teams: number): Map<number, number> {
  const given = new Map<number, number>();
  for (let t = 0; t < teams; t += 1) {
    let best = -1;
    let bestValue = -Infinity;
    scores.forEach((s, i) => {
      const v = s / ((given.get(i) ?? 0) + 1);
      if (v > bestValue) {
        bestValue = v;
        best = i;
      }
    });
    if (best >= 0) given.set(best, (given.get(best) ?? 0) + 1);
  }
  return given;
}

export async function mockPlan(date: string, variant: HeatVariant, teams: number, shelters: number): Promise<HeatPlan> {
  await delay();
  const { year, day } = locate(date);
  const season = simulateSeason(year);
  const scored = scoreSeason(year, variant);
  const cases = season.cases[day] ?? [];
  const ours = allocate(scored.score.OURS[day] ?? [], teams);
  const b1 = allocate((scored.score.B1[day] ?? []).map((t) => Math.exp(t / 4)), teams);
  const reached = (m: Map<number, number>): number => [...m.keys()].reduce((n, i) => n + (cases[i] ?? 0), 0);
  const allocations: HeatAllocation[] = [...ours.entries()]
    .sort((a, b) => b[1] - a[1] || (scored.score.OURS[day]?.[b[0]] ?? 0) - (scored.score.OURS[day]?.[a[0]] ?? 0))
    .map(([i, n]) => {
      const r = REGIONS[i] as MockRegion;
      const expected = scored.score.OURS[day]?.[i] ?? 0;
      return {
        code: r.code,
        name: `${r.sido} ${r.name}`,
        teams: n,
        expected_cases: Number(expected.toFixed(3)),
        actual_cases: cases[i] ?? 0,
        reason: `예상 환자 ${expected.toFixed(1)}명, 65세 이상 ${(r.pop65 / 10_000).toFixed(1)}만 명, 오늘 최고기온 ${(season.tmax[day]?.[i] ?? 0).toFixed(1)}℃`,
      };
    });
  const extensions = allocations
    .filter((a) => SHELTER_SIDO.has(REGIONS[INDEX_BY_CODE.get(a.code) ?? 0]?.sido ?? ""))
    .slice(0, shelters)
    .map((a, k) => ({
      shelter_id: k + 1,
      name: `${a.name.split(" ").slice(1).join(" ")} 무더위쉼터 ${k + 1}`,
      region_code: a.code,
      reason: "팀이 배치된 지역에서 야간 운영이 확인된 쉼터 (모의 응답)",
    }));
  return { date, teams, allocations, shelter_extensions: extensions, unknown_hours_count: 0, compare: { ours_reached: reached(ours), b1_reached: reached(b1) } };
}

/** Season replay of the same allocation. The interval is a plain day-resampling bootstrap (simulation only). */
export async function mockPlanEval(year: number, teams: number): Promise<HeatPlanEval> {
  await delay();
  assertTestYear(year);
  const season = simulateSeason(year);
  const scored = scoreSeason(year, "F");
  const perDay = season.dates.map((_, d) => {
    const cases = season.cases[d] ?? [];
    const sum = (m: Map<number, number>): number => [...m.keys()].reduce((n, i) => n + (cases[i] ?? 0), 0);
    return {
      ours: sum(allocate(scored.score.OURS[d] ?? [], teams)),
      b1: sum(allocate((scored.score.B1[d] ?? []).map((t) => Math.exp(t / 4)), teams)),
      total: cases.reduce((a, b) => a + b, 0),
    };
  });
  const ours = perDay.reduce((n, p) => n + p.ours, 0);
  const b1 = perDay.reduce((n, p) => n + p.b1, 0);
  const rng = mulberry32(hashSeed(`plan-${year}-${teams}`));
  const deltas: number[] = [];
  for (let b = 0; b < 200; b += 1) {
    let d = 0;
    for (let k = 0; k < perDay.length; k += 1) {
      const p = perDay[Math.floor(rng() * perDay.length)];
      d += (p?.ours ?? 0) - (p?.b1 ?? 0);
    }
    deltas.push(d);
  }
  deltas.sort((a, b) => a - b);
  const delta = ours - b1;
  return {
    year, teams, n_days: perDay.length, total_cases: perDay.reduce((n, p) => n + p.total, 0),
    ours_reached: ours, b1_reached: b1, delta,
    ci_lo: Math.min(delta, deltas[Math.floor(0.025 * deltas.length)] ?? delta),
    ci_hi: Math.max(delta, deltas[Math.floor(0.975 * deltas.length)] ?? delta),
  };
}
