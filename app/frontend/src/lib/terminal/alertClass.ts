/**
 * Maps a terminal message to the Korean 재난문자 presentation it imitates (ALERT_FORMAT.md has the sources).
 * Every label here is a 훈련 rendering: the sender always carries "(시연)" and the title "[훈련]"; nothing is sent.
 */
import type { StopInfo, TerminalKind, TerminalMessage } from "./types";

/** 재난문자방송 classes (재난문자방송 기준 및 운영규정) plus "local" for on-site 응급 calls that are not CBS at all. */
export type AlertClass = "emergency" | "urgent" | "safety" | "local";

export interface ClassPresentation {
  /** Header text as phones show it (spaced form, e.g. "안전 안내 문자"). */
  header: string;
  /** Short class name used in the stop-terminal banner. */
  short: string;
  /** Dialog accent colour (header bar / button). */
  accent: string;
  /** Text colour on the accent. */
  onAccent: string;
  /** navigator.vibrate pattern, ms on/off. */
  vibrate: readonly number[];
}

/**
 * Which class each kind is presented as (재난문자방송 기준 및 운영규정 별표 1, ALERT_FORMAT.md §1):
 * 폭염 경보 is not in the 위급/긴급 lists, so it is 안전안내문자; 호우 is 긴급재난문자 only when 기상청장 sends it
 * directly on the 50mm/1h+90mm/3h or 72mm/1h criterion, so a 구-level 호우 notice is 안전안내문자 too.
 * fall is an on-site 응급 call, not a 재난문자 class.
 */
export const KIND_CLASS: Readonly<Record<TerminalKind, AlertClass>> = {
  heat: "safety",
  rain: "safety",
  fall: "local",
  clear: "safety",
};

export const CLASS_PRESENTATION: Readonly<Record<AlertClass, ClassPresentation>> = {
  emergency: { header: "위급 재난 문자", short: "위급재난", accent: "#c4001a", onAccent: "#ffffff", vibrate: [1000, 300, 1000, 300, 1000] },
  urgent: { header: "긴급 재난 문자", short: "긴급재난", accent: "#d14900", onAccent: "#ffffff", vibrate: [700, 250, 700, 250, 700] },
  safety: { header: "안전 안내 문자", short: "안전안내", accent: "#1f5fbf", onAccent: "#ffffff", vibrate: [400, 200, 400] },
  local: { header: "현장 응급 알림", short: "응급알림", accent: "#c4001a", onAccent: "#ffffff", vibrate: [800, 200, 800, 200, 800] },
};

/** 훈련 marker that prefixes every title / sender. */
export const DRILL_PREFIX = "[훈련]";
/** Persistent watermark text. */
export const DRILL_WATERMARK = "훈련 · 실제 발송 아님";

/** 법정동 시군구 code → 구 name, for the demo gu only; anything else falls back to the default gu. */
const SGG_NAME: Readonly<Record<string, string>> = { "11620": "관악구", "11590": "동작구" };

/** Sender line, e.g. "관악구(시연)". Never a bare real sender ("관악구청", "행정안전부"). */
export function senderOf(stop: Pick<StopInfo, "sgg_code"> | null, fallbackGu: string): string {
  const gu = (stop !== null ? SGG_NAME[stop.sgg_code] : undefined) ?? fallbackGu;
  return `${gu}(시연)`;
}

export function classOf(m: Pick<TerminalMessage, "kind">): AlertClass {
  return KIND_CLASS[m.kind];
}

/**
 * Message text the way a 재난문자 body reads. 별표 2 표준문안 end with the sending agency in brackets
 * ("... 건강관리에 유의. [행정안전부]") and 제10조② requires the agency name; here it is "[관악구(시연)]" and the
 * text opens with "[훈련]".
 */
export function smsText(sender: string, title: string, body: string): string {
  const t = title.trim();
  const b = body.trim();
  const end = (x: string): string => (/[.!?。]$/.test(x) ? x : `${x}.`);
  const joined = t === "" ? end(b) : b === "" ? end(t) : `${end(t)} ${end(b)}`;
  return `${DRILL_PREFIX} ${joined} [${sender}]`;
}

/** "2026. 10. 25. 오후 2:03" style time, Asia/Seoul. */
export function formatAlertTime(d: Date, tz: string): string {
  const parts = new Intl.DateTimeFormat("ko-KR", { timeZone: tz, year: "numeric", month: "numeric", day: "numeric", hour: "numeric", minute: "2-digit", hour12: true }).formatToParts(d);
  const get = (t: string): string => parts.find((p) => p.type === t)?.value ?? "";
  return `${get("year")}. ${get("month")}. ${get("day")}. ${get("dayPeriod")} ${get("hour")}:${get("minute")}`;
}

/** Lock-screen date line, e.g. "10월 25일 일요일". */
export function formatLockDate(d: Date, tz: string): string {
  return new Intl.DateTimeFormat("ko-KR", { timeZone: tz, month: "long", day: "numeric", weekday: "long" }).format(d);
}
