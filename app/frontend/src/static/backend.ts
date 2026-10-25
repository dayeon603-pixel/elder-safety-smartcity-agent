/** In-browser stand-in for the FastAPI backend: GETs replay recorded fixtures, POSTs replay recorded responses. No network except the fixture files. */
import { canonicalKey, parseKey, TIME_PARAMS } from "./fixtureKey";
import type { ParsedKey } from "./fixtureKey";
import { TerminalBus } from "./terminalBus";
import type { BusMessage } from "./terminalBus";

export interface IndexEntry { f: string; t: string; s: number }
export type Index = Record<string, IndexEntry>;
interface FeedbackRec { request: { action_id: string; decision: string }; response: Record<string, unknown> }
interface FeedbackSeq { feedback: FeedbackRec[]; audit: Record<string, unknown>[] }
interface TerminalRec { label: string; hour?: number; request?: { kind?: string; title?: string }; message: BusMessage }
interface AgentEvent { t: number; raw?: string; json?: unknown }
interface AgentSeq { totalMs: number; events: AgentEvent[] }

export const MAX_REPLAY_GAP_MS = 3000;
const NOT_FOUND = 404;
const SERVICE_UNAVAILABLE = 503;
/** Paths where the identity params (lat/lon) may also be ignored as a last resort. */
const LOOSE_PATHS: readonly RegExp[] = [/^\/twin\/route$/, /^\/twin\/\d+\/fall-summary$/];
const DATE_MISS_PENALTY = 1000;
const NON_DEMO_DATE_PENALTY = 0.5;
const DEMO_DATE = "2025-07-22";
export const PUBLIC_LAN_NOTE = "공개 시연판 단말 주소";

export interface BackendDeps {
  index: Index;
  readJson: (file: string) => Promise<unknown>;
  fileUrl: (file: string) => string;
  fetchFile: (url: string) => Promise<Response>;
  terminalUrl: () => string;
  storage: { getItem(k: string): string | null; setItem(k: string, v: string): void } | null;
  channelFactory: (() => BroadcastChannel) | null;
  sleep: (ms: number) => Promise<void>;
  onAgentPlayback?: (playing: boolean, totalMs: number) => void;
  warn: (msg: string) => void;
}

const json = (body: unknown, status = 200): Response => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const asRec = (v: unknown): Record<string, unknown> => (typeof v === "object" && v !== null ? (v as Record<string, unknown>) : {});

export class StaticBackend {
  readonly misses: string[] = [];
  private bus!: TerminalBus;
  private feedback: FeedbackSeq = { feedback: [], audit: [] };
  private terminalRecs: TerminalRec[] = [];
  private agent: AgentSeq | null = null;
  private feedbackCount = 0;
  private servedIds = new Set<number>();
  private readonly parsed: [string, ParsedKey][];

  constructor(private readonly d: BackendDeps) {
    this.parsed = Object.keys(d.index).filter((k) => !k.startsWith("SEQ ")).map((k) => [k, parseKey(k)]);
  }

  /** Loads the recorded sequences; must be awaited before handle(). */
  async init(): Promise<void> {
    const load = async (key: string): Promise<unknown> => { const e = this.d.index[key]; return e === undefined ? null : this.d.readJson(e.f); };
    this.feedback = (await load("SEQ feedback") as FeedbackSeq | null) ?? this.feedback;
    this.terminalRecs = asRec(await load("SEQ terminal")).messages as TerminalRec[] ?? [];
    this.agent = (await load("SEQ agent")) as AgentSeq | null;
    const lastEntry = this.d.index["GET /terminal/last"];
    const rec = lastEntry === undefined ? null : asRec(await this.d.readJson(lastEntry.f)).message as BusMessage | undefined;
    this.bus = new TerminalBus(rec ?? this.terminalRecs.at(-1)?.message ?? null, this.d.storage, this.d.channelFactory);
  }

  get terminal(): TerminalBus { return this.bus; }

  private miss(method: string, path: string): Response {
    const m = `${method} ${path}`;
    this.misses.push(m);
    this.d.warn(`[시연판] 기록 없음: ${m}`);
    return json({ detail: "시연판에 기록되지 않은 요청입니다" }, NOT_FOUND);
  }

  private async serve(entry: IndexEntry): Promise<Response> {
    const res = await this.d.fetchFile(this.d.fileUrl(entry.f));
    if (!res.ok) return json({ detail: `fixture ${entry.f} ${res.status}` }, NOT_FOUND);
    return new Response(await res.arrayBuffer(), { status: entry.s, headers: { "Content-Type": entry.t } });
  }

  /** Exact key first; then same path with the same non-time params, nearest date/hour (logged). */
  lookup(method: string, pathWithQuery: string): IndexEntry | null {
    const key = canonicalKey(method, pathWithQuery);
    const exact = this.d.index[key];
    if (exact !== undefined) return exact;
    const want = parseKey(key);
    const wp = new Map(want.params);
    const wantHour = Number(wp.get("hour") ?? 0);
    const pick = (ident: boolean): IndexEntry | null => {
      let best: { k: string; score: number } | null = null;
      for (const [k, p] of this.parsed) {
        if (p.method !== want.method || p.path !== want.path) continue;
        const cp = new Map(p.params);
        if (ident && [...wp].some(([n, v]) => !TIME_PARAMS.has(n) && cp.get(n) !== v)) continue;
        const dateMiss = (cp.get("date") ?? DEMO_DATE) === (wp.get("date") ?? DEMO_DATE) ? 0 : DATE_MISS_PENALTY;
        const score = dateMiss + (cp.get("date") === DEMO_DATE ? 0 : NON_DEMO_DATE_PENALTY) + Math.abs(Number(cp.get("hour") ?? wantHour) - wantHour);
        if (best === null || score < best.score) best = { k, score };
      }
      return best === null ? null : (this.d.index[best.k] ?? null);
    };
    const hit = pick(true) ?? (LOOSE_PATHS.some((re) => re.test(want.path)) ? pick(false) : null);
    if (hit !== null) this.d.warn(`[시연판] 가장 가까운 기록 사용: ${key} -> ${hit.f}`);
    return hit;
  }

  async handle(method: string, pathWithQuery: string, body: unknown, accept: string): Promise<Response> {
    const path = pathWithQuery.split("?", 1)[0] ?? "";
    if (method === "GET") return this.get(path, pathWithQuery);
    if (method === "POST") return this.post(path, pathWithQuery, asRec(body), accept);
    return json({ detail: "method not allowed" }, SERVICE_UNAVAILABLE);
  }

  private async get(path: string, full: string): Promise<Response> {
    if (path === "/terminal/last") { const m = this.bus.last(); return json({ message: m }); }
    if (path === "/audit/verify") return json(this.auditNow());
    if (path === "/twin/lan-url") return json({ terminal_url: this.d.terminalUrl(), note: PUBLIC_LAN_NOTE });
    if (/^\/twin\/tiles\//.test(path)) return json({ detail: "no_vworld_key" }, SERVICE_UNAVAILABLE);
    const e = this.lookup("GET", full);
    return e === null ? this.miss("GET", full) : this.serve(e);
  }

  private auditNow(): Record<string, unknown> {
    const a = this.feedback.audit;
    if (a.length === 0) return { valid: true, entries: this.feedbackCount, head: null, broken_at: null, reason: null };
    const base = asRec(a[Math.min(this.feedbackCount, a.length - 1)]);
    const extra = Math.max(0, this.feedbackCount - (a.length - 1));
    return { ...base, entries: Number(base.entries ?? 0) + extra };
  }

  private postFeedback(b: Record<string, unknown>): Response {
    const decision = String(b.decision ?? "");
    const actionId = String(b.action_id ?? "");
    const rec = this.feedback.feedback.find((f) => f.request.action_id === actionId && f.request.decision === decision) ?? this.feedback.feedback.find((f) => f.request.decision === decision);
    if (rec === undefined) return json({ detail: "시연판에 기록되지 않은 판단" }, SERVICE_UNAVAILABLE);
    let id = Number(rec.response.id ?? 0);
    if (this.servedIds.has(id)) id = Math.max(...this.servedIds) + 1;
    this.servedIds.add(id);
    this.feedbackCount += 1;
    return json({ ...rec.response, id, action_id: actionId, decision }, 201);
  }

  private terminalFromRecs(label: string, hour: number | null, kind: string | null, title: string | null): BusMessage | null {
    const c = this.terminalRecs.filter((r) => r.label === label && (kind === null || r.request?.kind === kind) && (title === null || r.request?.title === title));
    if (c.length === 0) return null;
    const best = hour === null ? c[0] : c.reduce((a, r) => (Math.abs((r.hour ?? hour) - hour) < Math.abs((a.hour ?? hour) - hour) ? r : a));
    return best?.message ?? null;
  }

  private publishFrom(rec: BusMessage | null, fallback: Omit<BusMessage, "id" | "published_at" | "simulated" | "audio_url">): BusMessage {
    const base = rec ?? { ...fallback, audio_url: null };
    return this.bus.publish({ kind: base.kind, title: base.title, body: base.body, audio_url: base.audio_url, stop_id: base.stop_id ?? fallback.stop_id, drill: base.drill, meta: base.meta });
  }

  private async post(path: string, full: string, b: Record<string, unknown>, accept: string): Promise<Response> {
    if (path === "/feedback") return this.postFeedback(b);
    if (path === "/terminal/publish") {
      const rec = this.terminalFromRecs("publish", null, String(b.kind ?? ""), String(b.title ?? ""));
      const m = this.bus.publish({ kind: String(b.kind ?? "heat"), title: String(b.title ?? ""), body: String(b.body ?? ""), audio_url: rec?.audio_url ?? null, stop_id: typeof b.stop_id === "string" ? b.stop_id : null, drill: rec?.drill ?? "훈련 · 발송 안 함" });
      return json({ ok: true, message: m });
    }
    const q = new URLSearchParams(full.split("?", 2)[1] ?? "");
    if (/^\/twin\/\d+\/publish-heat$/.test(path)) {
      const m = this.publishFrom(this.terminalFromRecs("publish-heat", Number(q.get("hour") ?? 17), null, null), { kind: "heat", title: "폭염 대응 안내 (훈련)", body: "", stop_id: null });
      return json({ ok: true, message: m });
    }
    if (/^\/twin\/\d+\/publish-fall$/.test(path)) {
      const m = this.publishFrom(this.terminalFromRecs("publish-fall", null, null, null), { kind: "fall", title: "낙상 의심 감지 (훈련)", body: "", stop_id: q.get("stop_id") });
      return json({ ok: true, message: m });
    }
    if (path === "/agent/run") return this.agentRun(accept);
    const e = this.lookup("POST", full);
    return e === null ? this.miss("POST", full) : this.serve(e);
  }

  /** Replays the recorded SSE (steps with their recorded ms), gaps capped so a visitor does not wait for the original LLM latency twice. */
  private agentRun(accept: string): Response {
    const run = this.agent;
    if (run === null || run.events.length === 0) return json({ detail: "기록된 실행 없음" }, SERVICE_UNAVAILABLE);
    const enc = new TextEncoder();
    const wantsStream = accept.includes("text/event-stream") && run.events.every((e) => e.raw !== undefined);
    const { events, totalMs } = run;
    this.d.onAgentPlayback?.(true, totalMs);
    if (!wantsStream) {
      const last = events[events.length - 1];
      const payload = last?.json ?? this.finalFromRaw(events);
      return new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    const sleep = this.d.sleep;
    const done = (): void => this.d.onAgentPlayback?.(false, totalMs);
    const stream = new ReadableStream<Uint8Array>({
      async start(ctrl): Promise<void> {
        let prev = 0;
        for (const ev of events) {
          await sleep(Math.min(MAX_REPLAY_GAP_MS, Math.max(0, ev.t - prev)));
          prev = ev.t;
          ctrl.enqueue(enc.encode(`${ev.raw ?? ""}\n\n`));
        }
        done();
        ctrl.close();
      },
    });
    return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
  }

  private finalFromRaw(events: AgentEvent[]): unknown {
    for (const ev of [...events].reverse()) {
      const data = (ev.raw ?? "").split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trimStart()).join("\n");
      try { const o = JSON.parse(data) as unknown; if (Array.isArray(asRec(o).steps)) return o; } catch (err) { if (!(err instanceof SyntaxError)) throw err; }
    }
    return {};
  }
}
