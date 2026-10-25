import { TERMINAL_KINDS } from "./types";
import type { TerminalKind, TerminalMessage } from "./types";

const str = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);

export function isTerminalKind(v: unknown): v is TerminalKind {
  return typeof v === "string" && (TERMINAL_KINDS as readonly string[]).includes(v);
}

/** Parse one SSE data payload. Returns null for anything that is not a well-formed terminal message. */
export function parseTerminalMessage(data: string): TerminalMessage | null {
  let raw: unknown;
  try {
    raw = JSON.parse(data);
  } catch (err) {
    if (err instanceof SyntaxError) return null;
    throw err;
  }
  if (typeof raw !== "object" || raw === null) return null;
  const o = raw as Record<string, unknown>;
  // Tolerate an envelope {message: {...}}.
  const inner = typeof o.message === "object" && o.message !== null ? (o.message as Record<string, unknown>) : o;
  if (!isTerminalKind(inner.kind)) return null;
  const idRaw = inner.id ?? inner.message_id ?? inner.seq;
  return {
    kind: inner.kind,
    title: str(inner.title) ?? "",
    body: str(inner.body) ?? "",
    audio_url: str(inner.audio_url),
    stop_id: str(inner.stop_id),
    id: typeof idRaw === "string" || typeof idRaw === "number" ? String(idRaw) : null,
    published_at: str(inner.published_at) ?? str(inner.ts) ?? str(inner.created_at),
  };
}

/** Identity of a message for replay detection. */
export function messageKey(m: TerminalMessage): string {
  return m.id !== null ? `id:${m.id}` : `${m.published_at ?? ""}|${m.kind}|${m.title}|${m.body}|${m.audio_url ?? ""}`;
}

/** Age in ms of a message that carries a parseable timestamp, else null. */
export function messageAgeMs(m: TerminalMessage, nowMs: number): number | null {
  if (m.published_at === null) return null;
  const t = Date.parse(m.published_at);
  return Number.isNaN(t) ? null : nowMs - t;
}

/** Resolve audio_url to something the phone can fetch: always same-origin under the /api proxy, never the laptop's 127.0.0.1. */
export function resolveAudioUrl(audioUrl: string, apiBase: string): string {
  let path = audioUrl;
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(audioUrl)) {
    const u = new URL(audioUrl);
    path = `${u.pathname}${u.search}`;
  }
  if (path.startsWith(`${apiBase}/`)) return path;
  return `${apiBase}${path.startsWith("/") ? "" : "/"}${path}`;
}

/** Does this message address the stop? No stop_id = broadcast. */
export function addressedTo(m: TerminalMessage, stopId: string): boolean {
  return m.stop_id === null || m.stop_id === stopId;
}
