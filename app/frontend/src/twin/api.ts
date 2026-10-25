/** Typed adapter for the /twin endpoints (TWIN_CONTRACT.md) and the existing /actions, /feedback, /cap, /audit endpoints. */
import { demoHeaders } from "../api/agent";
import { API_BASE } from "../api/client";
import { clockKst } from "./clock";
import { FORBIDDEN_SOURCE_RE, SILVER_RADIUS_FALLBACK_M, TWIN_SGG_CODE } from "./constants";
import type {
  AccidentPoint, ActionCard, CardLetter, EffectChip, Letter, ActionsView, AedCandidate, AedPoint, CrosswalkPoint, EvidenceRow, DayType, HeatSummary, LonLat,
  RainDay, ShelterPoint, SilverEval, SilverSite, StopPoint, TwinRoute, TwinState, WeatherNow,
} from "./types";

const HTTP_NOT_FOUND = 404;

type Rec = Record<string, unknown>;
export const rec = (v: unknown): Rec | null => (typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Rec) : null);
export const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
export const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
export const str = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);
const bool = (v: unknown): boolean | null => (typeof v === "boolean" ? v : null);

/** Source text for display: the API's `display_source` if present; internal identifiers (experiments/, promoted, fold ...) are never shown. */
export function cleanSource(raw: string | null, display: string | null = null): string | null {
  if (display !== null && !FORBIDDEN_SOURCE_RE.test(display)) return display;
  return raw !== null && !FORBIDDEN_SOURCE_RE.test(raw) ? raw : null;
}

export class TwinMissing extends Error {
  constructor(path: string) {
    super(`${path} 없음`);
    this.name = "TwinMissing";
  }
}

const HTTP_TOO_MANY = 429;
const RETRY_MAX = 3;
const RETRY_DEFAULT_MS = 1000;

/** GET JSON; a 429 is retried after Retry-After (default 1 s) up to RETRY_MAX times. */
async function getJson(path: string, init?: RequestInit): Promise<unknown> {
  for (let attempt = 0; ; attempt++) {
    const res = await fetch(`${API_BASE}${path}`, init);
    if (res.status === HTTP_TOO_MANY && attempt < RETRY_MAX) {
      const wait = Number(res.headers.get("Retry-After"));
      await new Promise((r) => setTimeout(r, Number.isFinite(wait) && wait > 0 ? wait * 1000 : RETRY_DEFAULT_MS * (attempt + 1)));
      continue;
    }
    if (res.status === HTTP_NOT_FOUND) throw new TwinMissing(path);
    if (!res.ok) throw new Error(`${path} ${res.status}`);
    return res.json();
  }
}

/** GET JSON for optional endpoints: null when the route does not exist (yet). */
export async function getJsonPublic(path: string): Promise<unknown> {
  try {
    return await getJson(path);
  } catch (e) {
    if (e instanceof TwinMissing) return null;
    throw e;
  }
}

export function daytypeOf(date: string, holiday: boolean = false): DayType {
  if (holiday) return "holiday";
  const dow = new Date(`${date}T12:00:00+09:00`).getUTCDay();
  const SUNDAY = 0;
  const SATURDAY = 6;
  return dow === SUNDAY ? "sun" : dow === SATURDAY ? "sat" : "weekday";
}

/** Scene endpoint (the dev fixture fallback was removed when the endpoint landed). */
export const sceneUrls = (code: string): string[] => [`${API_BASE}/twin/${code}/scene`];

// ---------- state ----------

const pt = (o: Rec): { lat: number; lon: number } | null => {
  const lat = num(o.lat);
  const lon = num(o.lon);
  return lat === null || lon === null ? null : { lat, lon };
};

function mapList<T>(v: unknown, f: (o: Rec, i: number) => T | null): T[] {
  return arr(v).flatMap((x, i) => {
    const o = rec(x);
    const r = o === null ? null : f(o, i);
    return r === null ? [] : [r];
  });
}

const sites = (v: unknown): SilverSite[] => mapList(v, (o) => { const p = pt(o); return p === null ? null : { ...p, name: str(o.name) }; });

function accidents(v: unknown): AccidentPoint[] {
  return mapList(v, (o) => {
    const p = pt(o);
    if (p === null) return null;
    const inCur = bool(o.in_current) ?? bool(o.inside_current) ?? bool(o.covered_current);
    const inOurs = bool(o.in_ours) ?? bool(o.inside_ours) ?? bool(o.covered_ours);
    if (inCur === null || inOurs === null) return null;
    return { ...p, inCurrent: inCur, inOurs, casualties: num(o.casualties) };
  });
}

function parseHeat(v: unknown): HeatSummary {
  const o = rec(v) ?? {};
  return {
    apparentMaxC: num(o.apparent_max_c) ?? num(o["체감"]) ?? num(o.apparent),
    tmaxC: num(o.tmax_c) ?? num(o.tmax),
    b1Alert: bool(o.b1_alert), oursAlert: bool(o.ours_alert), rank: num(o.rank), nCells: num(o.n_cells),
    expectedCases: num(o.expected_cases), rankText: str(o.rank_text), expectedCasesText: str(o.expected_cases_text),
  };
}

/** Seoul zone counts for the honest silver caption: read from silver_eval_seoul (API) or the top level (older payloads). */
function seoulZonesOf(o: Record<string, unknown>): { current: number; ours: number } | null {
  const current = num(o.seoul_zones_current);
  const ours = num(o.seoul_zones_ours);
  return current !== null && ours !== null ? { current, ours } : null;
}

export function parseTwinState(raw: unknown, date: string, hour: number, daytype: DayType): TwinState | null {
  const r = rec(raw);
  if (r === null) return null;
  const summary = rec(r.summary) ?? {};
  const w = rec(r.weather);
  const weather: WeatherNow | null = w === null ? null : { temp: num(w.temp), feels: num(w.feels), rainMm: num(w.rain_mm) };
  const aed = mapList<AedPoint>(r.aed, (o, i) => { const p = pt(o); return p === null ? null : { ...p, id: str(o.id) ?? String(i), name: str(o.name) ?? "AED", open: bool(o.open), openAfter: bool(o.open_after), recommended24h: o.recommended_24h === true, hours: str(o.hours) }; });
  const evOf = (v: unknown): SilverEval | null => {
    const ev = rec(v);
    return ev === null || num(ev.n_hotspots) === null ? null : { year: num(ev.year), n: num(ev.n_hotspots) ?? 0, coveredCurrent: num(ev.covered_current) ?? 0, coveredOurs: num(ev.covered_ours) ?? 0, source: cleanSource(str(ev.source), str(ev.display_source)) };
  };
  const open = aed.filter((a) => a.open === true).length;
  /** Seoul-wide result (all 구): the district-only silver_eval is not shown as a scoreboard (n too small). */
  const silverEval = evOf(r.silver_eval_seoul) ?? evOf(rec(r.silver_eval)?.seoul);
  return {
    date, hour, daytype, aed, aedCandidates: aed.filter((a) => a.recommended24h).map((a, i): AedCandidate => ({ lat: a.lat, lon: a.lon, name: a.name, rank: i + 1 })),
    shelters: mapList<ShelterPoint>(r.shelters, (o, i) => { const p = pt(o); return p === null ? null : { ...p, id: str(o.id) ?? String(i), name: str(o.name) ?? "쉼터", open: bool(o.open) }; }),
    crosswalks: mapList<CrosswalkPoint>(r.crosswalks, (o, i) => { const p = pt(o); return p === null ? null : { ...p, id: str(o.id) ?? String(i), address: str(o.address) ?? "", greenS: num(o.green_s), needS: num(o.need_s), short: bool(o.short) ?? false }; }),
    silverCurrent: sites(r.silver_current), silverOurs: sites(r.silver_ours), silverRadiusM: num(r.silver_radius_m) ?? SILVER_RADIUS_FALLBACK_M,
    accidents: accidents(r.silver_hotspots ?? r.silver_accidents ?? r.accidents), silverEval, aedOpenPctAfter: num(summary.aed_open_pct_after),
    seoulZones: seoulZonesOf(rec(r.silver_eval_seoul) ?? r),
    stops: mapList<StopPoint>(r.stops_recommended, (o, i) => { const p = pt(o); return p === null ? null : { ...p, id: str(o.stop_id) ?? str(o.id) ?? String(i), name: str(o.name) ?? "정류장", recommended: true }; }),
    heat: parseHeat(r.heat), weather,
    aedOpenPct: num(summary.aed_open_pct), aedOpenCount: num(summary.aed_open) ?? (aed.length > 0 ? open : null), aedTotal: num(summary.aed_total) ?? (aed.length > 0 ? aed.length : null),
    sources: arr(r.sources).flatMap((x) => { const o = rec(x); return o === null ? [] : [str(o.what) ?? str(o.name) ?? ""]; }).filter((x) => x !== ""),
  };
}

export async function loadTwinState(date: string, hour: number, daytype: DayType, code: string = TWIN_SGG_CODE): Promise<TwinState> {
  const raw = await getJson(`/twin/${code}/state?date=${date}&hour=${hour}&daytype=${daytype}`);
  const s = parseTwinState(raw, date, hour, daytype);
  if (s === null) throw new Error("state 응답 형식 오류");
  return s;
}

/** Real observed rain nights (ASOS). Each day is placed at its heaviest hour (`max_at`). */
export async function loadRainDays(): Promise<RainDay[] | null> {
  try {
    const raw = rec(await getJson("/twin/rain-days"));
    return mapList<RainDay>(raw?.days, (o) => {
      const at = rec(o.max_at);
      const d = str(at?.date) ?? str(o.date);
      return d === null ? null : { date: d, hour: num(at?.hour), mm: num(o.max_mm_h) };
    });
  } catch (e) {
    if (e instanceof TwinMissing) return null;
    throw e;
  }
}

const EARTH_R_M = 6_371_000;
export function haversineM(a: LonLat, b: LonLat): number {
  const rad = Math.PI / 180;
  const dLat = (b[1] - a[1]) * rad;
  const dLon = (b[0] - a[0]) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[1] * rad) * Math.cos(b[1] * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_R_M * Math.asin(Math.sqrt(h));
}

export interface RouteResult { route: TwinRoute }

/** GET /twin/route: OSM walk-network shortest path to the nearest OPEN AED. */
export async function loadRoute(lat: number, lon: number, date: string, hour: number, daytype: DayType): Promise<RouteResult | null> {
  try {
    const raw = rec(await getJson(`/twin/route?lat=${lat}&lon=${lon}&date=${date}&hour=${hour}&daytype=${daytype}`));
    const aed = rec(raw?.aed);
    const p = aed === null ? null : pt(aed);
    const path = arr(raw?.path).flatMap((c): LonLat[] => (Array.isArray(c) && typeof c[0] === "number" && typeof c[1] === "number" ? [[c[0], c[1]]] : []));
    const meters = num(raw?.meters);
    const seconds = num(raw?.seconds);
    if (aed !== null && p !== null && path.length > 1 && meters !== null && seconds !== null) {
      return { route: { aed: { id: str(aed.id) ?? "", name: str(aed.name) ?? "AED", ...p }, path, meters, seconds } };
    }
  } catch (e) {
    if (!(e instanceof TwinMissing)) throw e;
  }
  return null;
}

// ---------- effects (GET /twin/effects) ----------

/** Effects keyed by whatever step name the API uses (lower-cased); values are display strings from the API only. */
/** Number as the API gave it (trailing zeros dropped), with its unit. */
const withUnit = (v: unknown, unit: string | null): string | null => {
  const n = typeof v === "number" ? v : typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v)) ? Number(v) : null;
  return n === null ? null : `${Number(n.toFixed(2))}${unit ?? ""}`;
};

export async function loadEffects(): Promise<Record<string, EffectChip> | null> {
  try {
    const raw = await getJson("/twin/effects");
    const r = rec(raw);
    const list: unknown[] = Array.isArray(raw) ? raw : arr(r?.items ?? r?.effects ?? r?.steps);
    const out: Record<string, EffectChip> = {};
    for (const x of list) {
      const o = rec(x);
      const key = str(o?.key) ?? str(o?.id);
      if (o === null || key === null || o.available === false) continue;
      const unit = str(o.unit);
      const ours = str(o.ours_text) ?? withUnit(o.ours, unit);
      if (ours === null) continue;
      const bl = str(o.baseline_label);
      const baseline = str(o.baseline_text) ?? withUnit(o.baseline, unit);
      out[key.toLowerCase()] = {
        ours, baseline: baseline === null ? null : `${bl === null ? "" : `${bl} `}${baseline}`,
        scope: str(o.label) ?? str(o.scope), source: cleanSource(str(o.source), str(o.display_source)),
      };
    }
    return Object.keys(out).length > 0 ? out : null;
  } catch (e) {
    if (e instanceof Error) return null;
    throw e;
  }
}

/** The chip for a step: first effect whose key matches the step's number or one of its aliases. */
export function effectFor(effects: Record<string, EffectChip> | null, no: number, aliases: readonly string[]): EffectChip | null {
  if (effects === null) return null;
  for (const k of [String(no), ...aliases]) { const hit = effects[k.toLowerCase()]; if (hit !== undefined) return hit; }
  return null;
}

// ---------- actions ----------

/** Korean display of an evidence row: never prints raw booleans or the symbol λ. */
export function formatEvidence(label: string, val: unknown): { label: string; value: string } {
  if (typeof val === "boolean") return label.includes("경보") ? { label: "에이전트 권고", value: val ? "사전 조치" : "조치 없음" } : { label, value: val ? "예" : "아니오" };
  if (label.includes("λ") && typeof val === "number") return { label: `${label.replace(/\s*λ/, "")} (명)`, value: val.toFixed(1) };
  return { label, value: typeof val === "string" ? val : typeof val === "number" ? String(val) : JSON.stringify(val) };
}

function evidenceRows(v: unknown): EvidenceRow[] {
  return mapList<EvidenceRow>(v, (o) => {
    const label = str(o.label);
    if (label === null) return null;
    const f = formatEvidence(label, o.value);
    if (FORBIDDEN_SOURCE_RE.test(f.label) || FORBIDDEN_SOURCE_RE.test(f.value)) return null;
    return { label: f.label, value: f.value, source: cleanSource(str(o.source), str(o.display_source)) };
  });
}

function draftsOf(v: unknown): ActionCard["drafts"] {
  return mapList(v, (o) => {
    const text = str(o.text);
    const limit = num(o.limit);
    return text === null || limit === null ? null : { label: str(o.label), limit, length: num(o.length) ?? text.length, text };
  });
}

/** GET /twin/{sgg}/actions?date=&hour= : heat actions by day, AED 24h-conversion actions at night. */
export async function loadActions(date: string, hour: number, code: string = TWIN_SGG_CODE): Promise<ActionsView> {
  const raw = rec(await getJson(`/twin/${code}/actions?date=${date}&hour=${hour}`)) ?? {};
  const cards = mapList<ActionCard>(raw.actions, (o) => {
    const id = str(o.id);
    const title = str(o.title);
    if (id === null || title === null) return null;
    const ref = rec(o.feedback_ref);
    return { id, kind: str(o.kind) ?? "", title, summary: str(o.body) ?? "", evidence: evidenceRows(o.evidence), feedbackId: str(ref?.action_id) ?? id, capUrl: str(o.cap_url), lat: num(o.lat), lon: num(o.lon), chars: str(o.chars), destination: str(rec(o.destination)?.label) ?? str(o.destination), destinationOrg: str(rec(o.destination)?.org), documentUrl: str(o.document_url), flowNote: str(o.flow_note), evidenceText: str(o.evidence_text), audioUrl: str(o.audio_url), drafts: draftsOf(o.drafts) };
  });
  return { cards, night: raw.night === true, notes: arr(raw.notes).filter((n): n is string => typeof n === "string"), disclaimer: str(raw.disclaimer), date, hour, generatedAt: clockKst() };
}

/** "149/157" -> {length, limit}. */
export function parseChars(chars: string | null): { length: number; limit: number } | null {
  const m = chars === null ? null : /^(\d+)\s*\/\s*(\d+)$/.exec(chars);
  return m === null ? null : { length: Number(m[1]), limit: Number(m[2]) };
}

/** GET /twin/lan-url: the phone-reachable terminal URL, or null when the laptop has no LAN address (or the route does not exist). */
export async function loadLanUrl(): Promise<string | null> {
  try {
    const r = rec(await getJson("/twin/lan-url"));
    return str(r?.terminal_url);
  } catch (e) {
    if (e instanceof Error) return null;
    throw e;
  }
}

// ---------- fall summary / publish-fall ----------

export interface FallSummary { alertText: string; cameraLabel: string | null; cameraId: string | null; realCctv: boolean }

/** GET /twin/{sgg}/fall-summary: the control-room alert text and camera honesty label, all strings from the API. */
export async function loadFallSummary(lat: number, lon: number, date: string, hour: number, minute: number, code: string = TWIN_SGG_CODE): Promise<FallSummary | null> {
  try {
    const r = rec(await getJson(`/twin/${code}/fall-summary?lat=${lat}&lon=${lon}&date=${date}&hour=${hour}&minute=${minute}`));
    const text = str(r?.alert_text);
    if (r === null || text === null) return null;
    const cam = rec(r.camera);
    return { alertText: text, cameraLabel: str(cam?.label), cameraId: str(cam?.id_label), realCctv: cam?.real_cctv === true };
  } catch (e) {
    if (e instanceof Error) return null;
    throw e;
  }
}

/** POST /twin/{sgg}/publish-fall: the control-room alert onto the terminals (훈련). */
export async function publishFall(lat: number, lon: number, date: string, hour: number, minute: number, stopId: string | null, code: string = TWIN_SGG_CODE): Promise<"ok" | "missing" | "failed"> {
  try {
    const q = `lat=${lat}&lon=${lon}&date=${date}&hour=${hour}&minute=${minute}${stopId === null ? "" : `&stop_id=${encodeURIComponent(stopId)}`}`;
    const res = await postJson(`/twin/${code}/publish-fall?${q}`, {});
    return res.status < 300 ? "ok" : res.status === HTTP_NOT_FOUND ? "missing" : "failed";
  } catch (e) {
    if (e instanceof TypeError) return "failed";
    throw e;
  }
}

// ---------- terminal / audit ----------

export async function publishTerminal(kind: "heat" | "fall" | "rain" | "clear", title: string, body: string, stopId: string | null): Promise<"ok" | "missing" | "failed"> {
  try {
    const res = await fetch(`${API_BASE}/terminal/publish`, {
      method: "POST", headers: { "Content-Type": "application/json", ...demoHeaders() },
      body: JSON.stringify({ kind, title, body, ...(stopId === null ? {} : { stop_id: stopId }) }),
    });
    await res.text();
    return res.ok ? "ok" : res.status === HTTP_NOT_FOUND ? "missing" : "failed";
  } catch (e) {
    if (e instanceof TypeError) return "failed";
    throw e;
  }
}

export interface AuditHead { valid: boolean; entries: number; head: string | null }
/** /audit/verify has a tiny rate budget (5 calls, 0.1/s refill): no retries, a 429 simply leaves the previous value. */
export async function loadAuditHead(): Promise<AuditHead | null> {
  try {
    const res = await fetch(`${API_BASE}/audit/verify`);
    if (!res.ok) return null;
    const r = rec(await res.json());
    return r === null ? null : { valid: r.valid === true, entries: num(r.entries) ?? 0, head: str(r.head) };
  } catch (e) {
    if (e instanceof TypeError) return null;
    throw e;
  }
}

export interface FeedbackRecord { id: number; createdAt: string | null; actionId: string; decision: string }
export async function postDecision(code: string, date: string, actionId: string, decision: "채택" | "보류", note: string = ""): Promise<FeedbackRecord | { error: string }> {
  try {
    const res = await fetch(`${API_BASE}/feedback`, { method: "POST", headers: { "Content-Type": "application/json", ...demoHeaders() }, body: JSON.stringify({ sgg_code: code, date, action_id: actionId, decision, ...(note === "" ? {} : { note }) }) });
    const j: unknown = await res.json().catch(() => null);
    const r = rec(j);
    if (!res.ok || r === null) return { error: `서버 응답 ${res.status}` };
    return { id: num(r.id) ?? 0, createdAt: str(r.created_at), actionId: str(r.action_id) ?? actionId, decision: str(r.decision) ?? decision };
  } catch (e) {
    if (e instanceof TypeError) return { error: "서버에 연결하지 못함" };
    throw e;
  }
}

// ---------- protocol documents ----------

export async function fetchText(path: string): Promise<string> {
  const res = await fetch(`${API_BASE}${path}`, { headers: demoHeaders() });
  if (!res.ok) throw new Error(`${path} ${res.status}`);
  return res.text();
}

export async function postJson(path: string, body: unknown): Promise<{ status: number; json: unknown }> {
  const res = await fetch(`${API_BASE}${path}`, { method: "POST", headers: { "Content-Type": "application/json", ...demoHeaders() }, body: JSON.stringify(body) });
  return { status: res.status, json: await res.json().catch(() => null) };
}

export async function loadSimInbox(receiptId: string): Promise<unknown> {
  return getJson(`/sim/inbox/${encodeURIComponent(receiptId)}`, { headers: demoHeaders() });
}

/** GET document_url: the structured 공문 (never sent anywhere). */
export async function loadLetter(documentUrl: string, signal?: AbortSignal): Promise<Letter | null> {
  const r = rec(await getJson(documentUrl, { signal }));
  if (r === null) return null;
  const evidence = arr(r["근거"]).flatMap((x): { item: string; value: string }[] => {
    const o = rec(x);
    const item = str(o?.["항목"]);
    const value = o?.["값"];
    return item === null || value === undefined ? [] : [{ item, value: String(value) }];
  }).filter((e) => !FORBIDDEN_SOURCE_RE.test(e.item) && !FORBIDDEN_SOURCE_RE.test(e.value));
  return { to: str(r["수신"]), org: str(r["수신_기관"]), title: str(r["제목"]), paragraphs: arr(r["본문"]).filter((x): x is string => typeof x === "string"), evidence, createdAt: str(r["생성시각"]), footer: str(r.footer), never: str(r.never_sent) };
}

/** The letter to show for a card: only a letter fetched for exactly that card, never another card's. */
export function letterFor(cardId: string, held: CardLetter | null): Letter | null {
  return held !== null && held.cardId === cardId ? held.letter : null;
}

/** Cards whose API document reuses another card's letter text (voice reuses the 재난문자 letter): only 수신 comes from the document. */
export const LETTER_RECIPIENT_ONLY: ReadonlySet<string> = new Set(["voice"]);
