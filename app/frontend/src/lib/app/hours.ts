/**
 * Open-now logic for AEDs and 무더위쉼터, from the public opening-hours columns.
 * AED cell format (scripts/gen_ops_data.py): 8 day slots "mon;tue;wed;thu;fri;sat;sun;hol", each "HHMM-HHMM" or "-".
 * "0000-2400" is 24 h. An end before the start (e.g. 2200-0600) wraps past midnight. Equal start and end ("0000-0000"), a missing pair or a
 * malformed pair is "unknown", never "closed": the source does not say the device is locked.
 */
import { ZP_AED_NIGHT_END, ZP_AED_NIGHT_START } from "../../config";
import { isNightHour } from "../zpAed";
import { KR_HOLIDAYS } from "./holidays";

export const DAY_SLOTS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun", "hol"] as const;
export type DaySlot = (typeof DAY_SLOTS)[number];
export type OpenState = "open" | "closed" | "unknown";

const MIN_PER_HOUR = 60;
const MIN_PER_DAY = 24 * MIN_PER_HOUR;
const KST_OFFSET_MS = 9 * 3_600_000;
const MS_PER_DAY = 86_400_000;
/** JS getUTCDay() order -> slot. */
const SLOT_BY_UTC_DAY: readonly DaySlot[] = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];

export interface DayHours {
  startMin: number;
  endMin: number;
}

function parseHhmm(text: string): number | null {
  if (!/^\d{4}$/.test(text)) return null;
  const h = Number(text.slice(0, 2));
  const m = Number(text.slice(2));
  if (m > 59 || h > 24 || (h === 24 && m !== 0)) return null;
  return h * MIN_PER_HOUR + m;
}

/** "0900-1800" -> minutes since midnight. Anything unusable (including "-", "0000-0000") -> null. */
export function parseDayHours(cell: string): DayHours | null {
  const parts = cell.trim().split("-");
  if (parts.length !== 2) return null;
  const start = parseHhmm(parts[0] ?? "");
  const end = parseHhmm(parts[1] ?? "");
  if (start === null || end === null) return null;
  if (start === end) return null; // 0000-0000 or 0900-0900: the source does not say
  return { startMin: start, endMin: end };
}

/** The 8-slot hours cell -> one entry per slot (null = no usable hours). A cell with the wrong slot count is rejected. */
export function parseAedHours(cell: string): (DayHours | null)[] | null {
  const slots = cell.split(";");
  if (slots.length !== DAY_SLOTS.length) return null;
  return slots.map(parseDayHours);
}

/** Open/closed at `minute` (0..1439) for one day's hours. The wrap past midnight is read inside the same slot (no spill into the next day). */
export function openAt(h: DayHours | null, minute: number): OpenState {
  if (h === null) return "unknown";
  if (!Number.isInteger(minute) || minute < 0 || minute >= MIN_PER_DAY) throw new RangeError("minute must be an integer in [0, 1440)");
  const { startMin, endMin } = h;
  if (endMin > startMin) return minute >= startMin && minute < endMin ? "open" : "closed";
  // end < start: wraps past midnight (e.g. 2200-0600). Equal start and end was already rejected.
  return minute >= startMin || minute < endMin ? "open" : "closed";
}

/** AED state on a day slot at an hour. */
export function aedStateAt(cell: string, slot: DaySlot, hour: number): OpenState {
  const parsed = parseAedHours(cell);
  if (parsed === null) return "unknown";
  return openAt(parsed[DAY_SLOTS.indexOf(slot)] ?? null, hour * MIN_PER_HOUR);
}

/** What the day control means. "weekday" reads the Wednesday column; the others read their own column. */
export type DayChoice = "weekday" | "sat" | "sun" | "hol";
export const DAY_CHOICES: readonly DayChoice[] = ["weekday", "sat", "sun", "hol"];
export const DAY_CHOICE_LABEL: Readonly<Record<DayChoice, string>> = { weekday: "평일", sat: "토요일", sun: "일요일", hol: "공휴일" };
const SLOT_OF_CHOICE: Readonly<Record<DayChoice, DaySlot>> = { weekday: "wed", sat: "sat", sun: "sun", hol: "hol" };

export function slotOfChoice(c: DayChoice): DaySlot {
  return SLOT_OF_CHOICE[c];
}

export interface KstNow {
  /** YYYY-MM-DD in KST */
  iso: string;
  hour: number;
  choice: DayChoice;
}

/** Wall-clock in Korea (KST, UTC+9) for any Date, independent of the browser time zone. A 공휴일 beats the weekday. */
export function kstNow(date: Date, holidays: ReadonlySet<string> = KR_HOLIDAYS): KstNow {
  const k = new Date(date.getTime() + KST_OFFSET_MS);
  const iso = k.toISOString().slice(0, 10);
  const slot = SLOT_BY_UTC_DAY[k.getUTCDay()] ?? "mon";
  const choice: DayChoice = holidays.has(iso) ? "hol" : slot === "sat" ? "sat" : slot === "sun" ? "sun" : "weekday";
  return { iso, hour: k.getUTCHours(), choice };
}

/** Calendar date shifted by whole days (UTC arithmetic on an ISO date), for tests and the date picker. */
export function addDays(iso: string, days: number): string {
  return new Date(Date.parse(`${iso}T00:00:00Z`) + days * MS_PER_DAY).toISOString().slice(0, 10);
}

export type Flag = boolean | 0 | 1 | null | undefined;

/**
 * 무더위쉼터: the data has only two flags (야간 개방, 주말 개방) and no hours.
 *  - weekend / holiday: the 주말 flag.  - weekday night (22-06): the 야간 flag.  - weekday daytime: unknown (no hours in the data).
 */
export function shelterStateAt(nightOpen: Flag, weekendOpen: Flag, choice: DayChoice, hour: number): OpenState {
  const weekday = choice === "weekday";
  if (weekday && !isNightHour(hour, ZP_AED_NIGHT_START, ZP_AED_NIGHT_END)) return "unknown";
  const flag = weekday ? nightOpen : weekendOpen;
  if (flag === null || flag === undefined) return "unknown";
  return flag === true || flag === 1 ? "open" : "closed";
}

export const OPEN_STATE_LABEL: Readonly<Record<OpenState, string>> = { open: "열림", closed: "닫힘", unknown: "시간 자료 없음" };
