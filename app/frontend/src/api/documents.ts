/** 공문 초안 (.docx): GET /documents/{action_id}.docx. Needs the demo token header, so it is fetched, not linked. */
import { demoHeaders } from "./agent";
import { API_BASE } from "./client";

const HTTP_UNAUTHORIZED = 401;
const HTTP_NOT_FOUND = 404;

export type DocumentResult = { kind: "ok"; blob: Blob; filename: string } | { kind: "failed"; reason: string };

export async function fetchDocument(actionId: string, sggCode: string, date: string): Promise<DocumentResult> {
  const qs = new URLSearchParams({ sgg_code: sggCode, date });
  try {
    const res = await fetch(`${API_BASE}/documents/${encodeURIComponent(actionId)}.docx?${qs.toString()}`, { headers: demoHeaders() });
    if (res.status === HTTP_UNAUTHORIZED) return { kind: "failed", reason: "데모 토큰 필요" };
    if (res.status === HTTP_NOT_FOUND) {
      await res.text();
      return { kind: "failed", reason: "이 행동의 공문 양식이 아직 없습니다" };
    }
    if (!res.ok) {
      await res.text();
      return { kind: "failed", reason: `서버 응답 ${res.status}` };
    }
    return { kind: "ok", blob: await res.blob(), filename: `${actionId}_${sggCode}_${date}.docx` };
  } catch (err) {
    if (err instanceof TypeError) return { kind: "failed", reason: "서버에 연결하지 못함" };
    throw err;
  }
}
