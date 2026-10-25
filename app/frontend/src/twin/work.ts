/** Work-environment helpers: 결재함 count from /feedback/summary, KST time strings from audit timestamps. */
import { demoHeaders } from "../api/agent";
import { API_BASE } from "../api/client";
import { num, rec } from "./api";
import { TWIN_SGG_CODE } from "./constants";

const KST_OFFSET_MS = 9 * 3_600_000;
const two = (n: number): string => String(n).padStart(2, "0");

/** "HH:MM" in KST for an ISO timestamp (the audit record's created_at), or the current time when none is given. */
export function hhmmKst(iso: string | null = null): string {
  const t = iso === null ? new Date() : new Date(iso);
  if (Number.isNaN(t.getTime())) return "";
  const k = new Date(t.getTime() + KST_OFFSET_MS);
  return `${two(k.getUTCHours())}:${two(k.getUTCMinutes())}`;
}

export interface InboxCounts { adopted: number; held: number }

/** 결재함: adopted decisions recorded for this 시군구 (GET /feedback/summary). */
export async function loadInbox(code: string = TWIN_SGG_CODE): Promise<InboxCounts | null> {
  try {
    const res = await fetch(`${API_BASE}/feedback/summary?sgg_code=${code}`, { headers: demoHeaders() });
    if (!res.ok) return null;
    const by = rec(rec(await res.json())?.by_decision);
    const adopted = num(by?.["채택"]);
    return adopted === null ? null : { adopted, held: num(by?.["보류"]) ?? 0 };
  } catch (e) {
    if (e instanceof TypeError) return null;
    throw e;
  }
}

export interface CardTimes { draft: string; sent?: string; approved?: string }
export interface Toast { id: number; text: string }
export const TOAST_MS = 3000;
