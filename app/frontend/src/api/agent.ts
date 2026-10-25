/**
 * Client for the backend agent endpoint POST /agent/run (local planner + simulator tools).
 * Request and response follow app/routers/agent.py and app/agent/orchestrator.py. The endpoint is not streaming:
 * the page shows the instant local alert first, a "planning" state while the call runs, then the whole log with
 * each step's server-measured ms.
 */
import { API_BASE } from "./client";

/** A full run is ~11 s with the LLM planner and ~35 s when the VLM is on. */
export const AGENT_TIMEOUT_MS = 90_000;
/** Optional build-time token (VITE_DEMO_TOKEN); the dev proxy injects KCF_DEMO_TOKEN instead, which keeps it out of the bundle. */
const DEMO_TOKEN: string | undefined = import.meta.env.VITE_DEMO_TOKEN;
const DEMO_TOKEN_HEADER: string = import.meta.env.VITE_DEMO_TOKEN_HEADER ?? "X-Demo-Token";
const HTTP_UNAUTHORIZED = 401;
export const DEMO_TOKEN_REQUIRED = "데모 토큰 필요";
/** Header carrying the demo token, if one is configured (empty otherwise). */
export function demoHeaders(): Record<string, string> {
  return DEMO_TOKEN === undefined || DEMO_TOKEN === "" ? {} : { [DEMO_TOKEN_HEADER]: DEMO_TOKEN };
}
const SEND_TOOLS: Readonly<Record<string, string>> = { send_rsu_message: "RSU", send_119_report: "119" };
const SCENE_TOOL = "describe_scene";

export interface AgentRunRequest {
  /** 시군구 code (5 digits) */
  code: string;
  /** YYYY-MM-DD */
  date: string;
  lat: number;
  lon: number;
  est_speed: number | null;
  torso_angle: number | null;
  /** base64 JPEG of the face-masked canvas; only present when the user enabled the scene-description toggle */
  image_base64?: string;
}

export interface AgentStep {
  name: string;
  ms: number | null;
  summary: string;
  /** "llm" | "deterministic" | "deterministic_fallback" */
  source: string | null;
  ok: boolean;
}

export interface AgentReceipt {
  target: string;
  status: number | null;
  id: string | null;
}

export interface AgentResult {
  runId: string | null;
  /** "llm" | "llm+fallback" | "deterministic" */
  planner: string | null;
  plannerReason: string | null;
  model: string | null;
  modelDigest: string | null;
  steps: AgentStep[];
  receipts: AgentReceipt[];
  briefing: string | null;
  /** "llm" | "template" */
  briefingSource: string | null;
  sceneText: string | null;
  /** true = briefing numbers verified against tool outputs; false = the LLM text was rejected, template used */
  grounded: boolean | null;
  totalMs: number | null;
  notes: string[];
  /** CAP 1.2 document and 마을방송 audio the agent published (relative to the API), when make_cap_alert ran. */
  cap: { id: string; xmlUrl: string | null; audioUrl: string | null; audioScript: string | null; xsdValid: boolean | null } | null;
  capFeedUrl: string | null;
}

/** Absolute (proxied) URL for a path the API returned, e.g. "/cap/feed.atom". */
export const apiUrl = (path: string): string => `${API_BASE}${path}`;

export const agentRunUrl = (runId: string): string => `${API_BASE}/agent/runs/${encodeURIComponent(runId)}`;

const rec = (v: unknown): Record<string, unknown> | null => (typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : null);
const str = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

function normalizeStep(o: Record<string, unknown>): { step: AgentStep; receipt: AgentReceipt | null; scene: string | null; view: Record<string, unknown> | null } {
  const name = str(o.tool) ?? str(o.name) ?? "단계";
  const view = rec(o.result);
  const receipt = view !== null && name in SEND_TOOLS
    ? { target: str(view.simulator) ?? `${SEND_TOOLS[name]} 시뮬레이터`, status: num(view.http_status), id: str(view.receipt_id) }
    : null;
  const scene = view !== null && name === SCENE_TOOL ? str(view.description) : null;
  return { step: { name, ms: num(o.ms), summary: str(o.summary) ?? "", source: str(o.source), ok: o.ok !== false && o.status !== "failed" }, receipt, scene, view };
}

export function normalizeAgentResult(raw: unknown): AgentResult | null {
  const r = rec(raw);
  if (r === null || !Array.isArray(r.steps)) return null;
  const receipts: AgentReceipt[] = [];
  let sceneText: string | null = null;
  let cap: AgentResult["cap"] = null;
  const steps = r.steps.flatMap((s): AgentStep[] => {
    const o = rec(s);
    if (o === null) return [];
    const n = normalizeStep(o);
    if (n.receipt !== null) receipts.push(n.receipt);
    if (n.scene !== null) sceneText = n.scene;
    if (n.step.name === "make_cap_alert" && n.view !== null && str(n.view.cap_id) !== null) {
      cap = { id: n.view.cap_id as string, xmlUrl: str(n.view.xml_url), audioUrl: str(n.view.audio_url), audioScript: str(n.view.audio_script), xsdValid: typeof n.view.xsd_valid === "boolean" ? n.view.xsd_valid : null };
    }
    return [n.step];
  });
  const g = rec(r.grounding);
  const source = str(r.briefing_source);
  const grounded = source === "template" ? false : g !== null && typeof g.ok === "boolean" ? g.ok : null;
  return {
    runId: str(r.run_id),
    planner: str(r.planner),
    plannerReason: str(r.planner_reason),
    model: str(r.model_name),
    modelDigest: str(r.model_digest),
    steps,
    receipts,
    briefing: str(r.briefing),
    briefingSource: source,
    sceneText,
    grounded,
    totalMs: num(r.total_ms),
    notes: Array.isArray(r.notes) ? r.notes.filter((n): n is string => typeof n === "string") : [],
    cap,
    capFeedUrl: str(r.cap_feed_url),
  };
}

const SSE_MIME = "text/event-stream";
const SSE_FINAL_EVENTS: ReadonlySet<string> = new Set(["done", "result", "final", "run"]);

/** One parsed SSE message: the event name (default "message") and its JSON data, or null if the data is not JSON. */
export interface SseMessage {
  event: string;
  data: unknown;
}

/** Split a buffer into complete SSE messages; returns the messages and the unfinished remainder. */
export function parseSse(buffer: string): { messages: SseMessage[]; rest: string } {
  const blocks = buffer.replace(/\r\n/g, "\n").split("\n\n");
  const rest = blocks.pop() ?? "";
  const messages = blocks.flatMap((block): SseMessage[] => {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice("event:".length).trim();
      else if (line.startsWith("data:")) data.push(line.slice("data:".length).trimStart());
    }
    if (data.length === 0) return [];
    try {
      return [{ event, data: JSON.parse(data.join("\n")) as unknown }];
    } catch {
      return [];
    }
  });
  return { messages, rest };
}

/** Called for every step as it arrives over SSE; never called for the non-streaming fallback. */
export type StepListener = (step: AgentStep) => void;
/** Optional extra listeners for the other SSE events. */
export interface StreamListeners {
  onStep?: StepListener;
  /** Provisional briefing text so far; the final record replaces it (it may be rejected by the grounding check). */
  onBriefing?: (textSoFar: string) => void;
  onNote?: (text: string) => void;
  onStatus?: (planner: string | null) => void;
}

async function readStream(res: Response, on: StreamListeners): Promise<unknown> {
  const reader = res.body?.getReader();
  if (reader === undefined) return null;
  const decoder = new TextDecoder();
  let buffer = "";
  let final: unknown = null;
  const liveSteps: unknown[] = [];
  let briefing = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (value !== undefined) buffer += decoder.decode(value, { stream: !done });
    const { messages, rest } = parseSse(buffer);
    buffer = rest;
    for (const m of messages) {
      const o = rec(m.data);
      if (o === null) continue;
      if (m.event === "status") {
        on.onStatus?.(str(o.planner));
      } else if (m.event === "note") {
        const t = str(o.text);
        if (t !== null) on.onNote?.(t);
      } else if (m.event === "briefing_delta") {
        briefing += typeof o.text === "string" ? o.text : "";
        on.onBriefing?.(briefing);
      } else if (m.event === "error") {
        throw new SyntaxError(str(o.detail) ?? "에이전트 실행 오류");
      } else if (Array.isArray(o.steps) || SSE_FINAL_EVENTS.has(m.event)) {
        final = Array.isArray(o.steps) ? o : (rec(o.run) ?? o);
      } else if (typeof o.tool === "string" || typeof o.name === "string") {
        liveSteps.push(o);
        on.onStep?.(normalizeStep(o).step);
      }
    }
    if (done) break;
  }
  // A stream that ended without a final record still yields what arrived.
  return final ?? (liveSteps.length > 0 ? { steps: liveSteps } : null);
}

export type AgentOutcome = { kind: "ok"; result: AgentResult; clientMs: number } | { kind: "unavailable"; reason: string };

/**
 * Never throws for network/HTTP failures: those become "unavailable". Programming errors still throw.
 * Sends Accept: text/event-stream; if the server streams, listeners fire per event, otherwise the plain JSON record is
 * parsed once the call ends (the page then shows the planning state followed by the whole log).
 */
export async function runAgent(body: AgentRunRequest, on: StreamListeners = {}, now: () => number = () => performance.now()): Promise<AgentOutcome> {
  const t0 = now();
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), AGENT_TIMEOUT_MS);
  const { image_base64, ...rest } = body;
  const context = { ...rest, ...(image_base64 === undefined ? {} : { image_base64 }) };
  try {
    const res = await fetch(`${API_BASE}/agent/run`, {
      method: "POST", headers: {
        "Content-Type": "application/json", Accept: `${SSE_MIME}, application/json`,
        ...demoHeaders(),
      },
      body: JSON.stringify({ trigger: "fall", context }), signal: ctrl.signal,
    });
    if (res.status === HTTP_UNAUTHORIZED) return { kind: "unavailable", reason: `${DEMO_TOKEN_REQUIRED}: 서버가 KCF_DEMO_TOKEN을 요구합니다(프론트는 KCF_DEMO_TOKEN 또는 VITE_DEMO_TOKEN으로 전달)` };
    if (!res.ok) return { kind: "unavailable", reason: `서버 응답 ${res.status}` };
    const streamed = (res.headers.get("content-type") ?? "").includes(SSE_MIME);
    const raw: unknown = streamed ? await readStream(res, on) : await res.json();
    const result = normalizeAgentResult(raw);
    return result === null ? { kind: "unavailable", reason: "응답 형식을 해석하지 못함" } : { kind: "ok", result, clientMs: now() - t0 };
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") return { kind: "unavailable", reason: "응답 시간 초과" };
    if (err instanceof TypeError || err instanceof SyntaxError) return { kind: "unavailable", reason: "서버에 연결하지 못함 또는 응답 해석 실패" };
    throw err;
  } finally {
    clearTimeout(timer);
  }
}
