/**
 * 담당자 판단 기록: POST /feedback and GET /feedback/summary (backend app/routers/feedback.py).
 * Until the route answers, the buttons stay disabled ("준비 중") and nothing is pretended to be saved.
 */
import { demoHeaders } from "./agent";
import { API_BASE } from "./client";

/** The API takes the Korean words themselves. */
export type Decision = "채택" | "보류" | "부적합";
export const DECISIONS: readonly Decision[] = ["채택", "보류", "부적합"];
export const NOTE_MAX_CHARS = 200;
const HTTP_NOT_FOUND = 404;
const HTTP_METHOD_NOT_ALLOWED = 405;
const HTTP_UNAUTHORIZED = 401;
const HTTP_SERVER_ERROR = 500;
const SUMMARY_PATH = "/feedback/summary";
export const DEMO_TOKEN_REQUIRED_REASON = "데모 토큰 필요";

export interface FeedbackInput {
  sgg_code: string;
  date: string;
  /** e.g. "heat.0": module and position in the briefing's action list */
  action_id: string;
  decision: Decision;
  note?: string;
}

export type FeedbackSummary = Record<Decision, number>;
export type FeedbackResult = { kind: "saved" } | { kind: "failed"; reason: string };

/** True once the backend has the feedback routes. A missing route answers 404; 5xx means unusable. */
export async function feedbackAvailable(): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}${SUMMARY_PATH}`, { headers: demoHeaders() });
    await res.text();
    return res.status < HTTP_SERVER_ERROR && res.status !== HTTP_NOT_FOUND;
  } catch (err) {
    if (err instanceof TypeError) return false;
    throw err;
  }
}

export async function postFeedback(input: FeedbackInput): Promise<FeedbackResult> {
  try {
    const res = await fetch(`${API_BASE}/feedback`, { method: "POST", headers: { "Content-Type": "application/json", ...demoHeaders() }, body: JSON.stringify(input) });
    await res.text();
    if (res.ok) return { kind: "saved" };
    if (res.status === HTTP_UNAUTHORIZED) return { kind: "failed", reason: DEMO_TOKEN_REQUIRED_REASON };
    if (res.status === HTTP_NOT_FOUND || res.status === HTTP_METHOD_NOT_ALLOWED) return { kind: "failed", reason: "준비 중" };
    return { kind: "failed", reason: `서버 응답 ${res.status}` };
  } catch (err) {
    if (err instanceof TypeError) return { kind: "failed", reason: "서버에 연결하지 못함" };
    throw err;
  }
}

const rec = (v: unknown): Record<string, unknown> | null => (typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : null);

/** Reads {by_decision: {채택, 보류, 부적합}}; anything else is "no summary". */
export function normalizeSummary(raw: unknown): FeedbackSummary | null {
  const by = rec(rec(raw)?.by_decision);
  if (by === null || !DECISIONS.every((d) => typeof by[d] === "number")) return null;
  return { 채택: by["채택"] as number, 보류: by["보류"] as number, 부적합: by["부적합"] as number };
}

export async function getFeedbackSummary(sggCode: string): Promise<FeedbackSummary | null> {
  try {
    const res = await fetch(`${API_BASE}${SUMMARY_PATH}?sgg_code=${encodeURIComponent(sggCode)}`, { headers: demoHeaders() });
    if (!res.ok) {
      await res.text(); // drain, so the request counts as finished
      return null;
    }
    return normalizeSummary(await res.json());
  } catch (err) {
    if (err instanceof TypeError || err instanceof SyntaxError) return null;
    throw err;
  }
}
