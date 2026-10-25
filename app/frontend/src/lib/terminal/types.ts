export const TERMINAL_KINDS = ["heat", "fall", "rain", "clear"] as const;
export type TerminalKind = (typeof TERMINAL_KINDS)[number];

/** One message on the terminal channel (POST /terminal/publish body, as echoed by /terminal/stream). */
export interface TerminalMessage {
  kind: TerminalKind;
  title: string;
  body: string;
  audio_url: string | null;
  stop_id: string | null;
  /** Server-side id or timestamp when present; used to recognise a replay. */
  id: string | null;
  published_at: string | null;
}

export type ConnectionState = "connecting" | "open" | "reconnecting";

export interface StreamEvent {
  message: TerminalMessage;
  /** True when this is the replay of the message already handled before a reconnect: show it, do not speak it again. */
  replay: boolean;
}

export interface NearPlace {
  name: string;
  meters: number;
  minutes: number;
  /** "route" = 보행망 최단경로 (/twin/route); "line" = 직선거리 기준. */
  basis: "route" | "line";
}

export interface IdleData {
  /** 체감온도 ℃ and where it came from, e.g. "2025-07-22 서울 관측 체감 최고". */
  feelsC: number | null;
  weatherLabel: string | null;
  aed: NearPlace | null;
  shelter: NearPlace | null;
  /** Why a place is missing ("자료 없음" etc.), straight from the API. */
  aedNote: string | null;
  shelterNote: string | null;
}

export interface StopInfo {
  stop_id: string;
  name: string;
  lat: number;
  lon: number;
  sgg_code: string;
}
