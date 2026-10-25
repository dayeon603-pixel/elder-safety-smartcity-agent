/** 현장 단말 (/terminal) knobs. Display state comes from the API; these are timing and unit conventions only. */
import { ZP_DEFAULT_DATE, ZP_DEFAULT_SIGUNGU } from "../../config";

/** Gu used to pick the default stop (top recommended stop of /mobility/stops). */
export const TERMINAL_DEFAULT_GU = ZP_DEFAULT_SIGUNGU;
/** Stops are listed once to resolve the name of `?stop=`; the endpoint caps `limit`. */
export const TERMINAL_STOPS_LIMIT = 200;
export const TERMINAL_STREAM_PATH = "/terminal/stream";

/** SSE reconnect: exponential backoff base * 2^n, capped. */
export const RECONNECT_BASE_MS = 500;
export const RECONNECT_MAX_MS = 8000;
/** No response headers within this long: drop the attempt and retry (a healthy server answers in milliseconds). */
export const CONNECT_TIMEOUT_MS = 4000;
/** Event names the stream may use besides the unnamed `message` event. */
export const STREAM_EVENT_NAMES: readonly string[] = ["terminal", "alert", "publish"];
/** A message older than this (when it carries a timestamp) is shown but not spoken. */
export const REPLAY_SILENT_AFTER_MS = 120_000;

/** 훈련 tag shown on every alert (contract: 발송 없음). */
export const DRILL_TAG = "훈련 · 실제 발송 아님";
/** The clear screen returns to idle after this long. */
export const CLEAR_HOLD_MS = 30_000;
/** Alert flash: 1 Hz border/overlay pulse (WCAG 2.3.1: well under 3 flashes per second), stops after this many pulses. */
export const FLASH_PERIOD_MS = 1000;
export const FLASH_PULSES = 30;
/** navigator.vibrate patterns (ms on/off). */
export const VIBRATE_DEFAULT: readonly number[] = [400, 200, 400];
export const VIBRATE_FALL: readonly number[] = [800, 200, 800, 200, 800];

/** Idle data. */
export const CLOCK_TICK_MS = 1000;
export const IDLE_REFRESH_MS = 60_000;
export const TERMINAL_TZ = "Asia/Seoul";
/** Elderly walking speed, TWIN_CONTRACT section 4 (/twin/route seconds@0.8m/s). Used only when /twin/route is not available. */
export const WALK_SPEED_MPS = 0.8;
export const SECONDS_PER_MINUTE = 60;
/** Fallback weather source when the live state has no 체감: the demo day's ASOS observation (labelled as such on screen). */
export const FALLBACK_WEATHER_DATE = ZP_DEFAULT_DATE;
export const AED_LIST_LIMIT = 1000;
/** AED/shelter farther than this straight-line is shown as "먼 거리" data, never as a walking time. */
export const NEAR_MAX_M = 3000;
