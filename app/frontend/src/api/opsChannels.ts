/** 전달 경로 calls: 공문 초안 (.docx) and 마을방송 음성 (CAP Exercise alert -> WAV). Both are drafts; nothing leaves the backend. */
import { demoHeaders } from "./agent";
import { API_BASE } from "./client";
import { buildCapJson } from "../lib/cap";
import { OPS_CAP_ID_PREFIX } from "../config";

const HTTP_UNAUTHORIZED = 401;
const HTTP_UNAVAILABLE = 503;
const AUDIO_PATH = (id: string): string => `${API_BASE}/cap/alerts/${encodeURIComponent(id)}/audio.wav`;
const DOC_TYPE_DOCX = "wordprocessingml";

export type ChannelResult = { kind: "ok"; note: string } | { kind: "failed"; reason: string };

function reasonFor(status: number): string {
  if (status === HTTP_UNAUTHORIZED) return "데모 토큰 필요";
  if (status === HTTP_UNAVAILABLE) return "이 서버에서 만들 수 없음";
  return `서버 응답 ${status}`;
}

/** GET /documents/{id}.docx (needs the demo token header, so it is fetched, not linked) and save it. */
export async function downloadDocx(docId: string, sggCode: string, date: string): Promise<ChannelResult> {
  try {
    const url = `${API_BASE}/documents/${encodeURIComponent(docId)}.docx?sgg_code=${encodeURIComponent(sggCode)}&date=${encodeURIComponent(date)}`;
    const res = await fetch(url, { headers: demoHeaders() });
    if (!res.ok) {
      await res.text();
      return { kind: "failed", reason: reasonFor(res.status) };
    }
    if (!(res.headers.get("content-type") ?? "").includes(DOC_TYPE_DOCX)) return { kind: "failed", reason: "문서 형식이 아닙니다" };
    const blob = await res.blob();
    const href = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = href;
    a.download = `${docId}_${sggCode}_${date}.docx`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(href);
    return { kind: "ok", note: "초안을 내려받았습니다 (발송 안 함)" };
  } catch (err) {
    if (err instanceof TypeError) return { kind: "failed", reason: "서버에 연결하지 못함" };
    throw err;
  }
}

export interface AudioSource {
  regionName: string;
  lat: number;
  lon: number;
  /** the 쉬운 말 재난문자 text read aloud */
  text: string;
  date: string;
}

/**
 * Stores an Exercise-status CAP alert (visible in /cap/feed.atom, not transmitted anywhere) so the backend can synthesise
 * the 마을방송 WAV, then plays it. The alert is the only thing written.
 */
export async function playVillageBroadcast(src: AudioSource, play: (url: string) => Promise<void>): Promise<ChannelResult> {
  try {
    const identifier = `${OPS_CAP_ID_PREFIX}-${Date.now()}`;
    const cap = buildCapJson({
      identifier, sent: new Date(), event: "폭염 안내(훈련)", headline: `${src.regionName} 폭염 안내`, description: src.text,
      instruction: "낮 12시부터 오후 5시까지는 외출을 피하고 물을 자주 드세요.", areaDesc: src.regionName, lat: src.lat, lon: src.lon,
    });
    const post = await fetch(`${API_BASE}/cap/alerts`, { method: "POST", headers: { "Content-Type": "application/json", ...demoHeaders() }, body: JSON.stringify({ cap }) });
    if (!post.ok) {
      await post.text();
      return { kind: "failed", reason: reasonFor(post.status) };
    }
    await post.text();
    const wav = await fetch(AUDIO_PATH(identifier));
    if (!wav.ok) {
      await wav.text();
      return { kind: "failed", reason: reasonFor(wav.status) };
    }
    const url = URL.createObjectURL(await wav.blob());
    try {
      await play(url);
    } finally {
      URL.revokeObjectURL(url);
    }
    return { kind: "ok", note: "훈련용 CAP 경보로 저장하고 음성을 재생했습니다 (외부 방송 안 함)" };
  } catch (err) {
    if (err instanceof TypeError || (err instanceof DOMException && err.name === "NotAllowedError")) return { kind: "failed", reason: "재생하지 못함" };
    throw err;
  }
}

/** Play a blob URL to its end. */
export function playUrl(url: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const a = new Audio(url);
    a.addEventListener("ended", () => resolve());
    a.addEventListener("error", () => reject(new TypeError("audio error")));
    a.play().catch(reject);
  });
}
