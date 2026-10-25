/** Terminal channel reads/writes used by the story (same endpoints the phone terminal uses). */
import { demoHeaders } from "../../api/agent";
import { API_BASE } from "../../api/client";
import { TWIN_SGG_CODE } from "../constants";

export interface TerminalMsg { id: number | null; kind: string; title: string; body: string; stopId: string | null }
type Rec = Record<string, unknown>;
const rec = (v: unknown): Rec | null => (typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Rec) : null);

export function parseTerminalLast(raw: unknown): TerminalMsg | null {
  const m = rec(rec(raw)?.message);
  if (m === null || typeof m.title !== "string" || typeof m.body !== "string") return null;
  return { id: typeof m.id === "number" ? m.id : null, kind: typeof m.kind === "string" ? m.kind : "", title: m.title, body: m.body, stopId: typeof m.stop_id === "string" ? m.stop_id : null };
}

export async function fetchTerminalLast(): Promise<TerminalMsg | null> {
  try {
    const res = await fetch(`${API_BASE}/terminal/last`, { cache: "no-store" });
    return res.ok ? parseTerminalLast(await res.json()) : null;
  } catch (e) {
    if (e instanceof TypeError) return null;
    throw e;
  }
}

/** POST /twin/{sgg}/publish-heat: the backend builds the heat message from the same state the 3D view shows. */
export async function publishHeat(date: string, hour: number): Promise<"ok" | "failed"> {
  try {
    const res = await fetch(`${API_BASE}/twin/${TWIN_SGG_CODE}/publish-heat?date=${date}&hour=${Math.floor(hour)}`, { method: "POST", headers: demoHeaders() });
    await res.text();
    return res.ok ? "ok" : "failed";
  } catch (e) {
    if (e instanceof TypeError) return "failed";
    throw e;
  }
}
