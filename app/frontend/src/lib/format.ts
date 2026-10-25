import { HASH_SHORT } from "../config";

const LOCALE = "ko-KR";
const INT = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 });
const DATE = new Intl.DateTimeFormat(LOCALE, { year: "numeric", month: "2-digit", day: "2-digit", timeZone: "Asia/Seoul" });
const DATETIME = new Intl.DateTimeFormat(LOCALE, {
  year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Seoul",
});
const BYTE_UNITS = ["B", "KB", "MB", "GB"] as const;
const BYTES_STEP = 1024;

export interface IntervalLike {
  point: number;
  lo: number;
  hi: number;
}

/** 1,234,567 (ko-KR grouping). Non-finite values render as an en dash so tables never show NaN. */
export function formatInt(x: number): string {
  return Number.isFinite(x) ? INT.format(x) : "–";
}

export function formatDecimal(x: number, digits = 3): string {
  if (!Number.isFinite(x)) return "–";
  return new Intl.NumberFormat(LOCALE, { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(x);
}

/** Signed decimal with a real minus sign, for differences. */
export function formatSigned(x: number, digits = 3): string {
  if (!Number.isFinite(x)) return "–";
  const body = formatDecimal(Math.abs(x), digits);
  if (x === 0) return body;
  return `${x > 0 ? "+" : "−"}${body}`;
}

export function formatInterval(i: IntervalLike, signed = false, digits = 3): string {
  const f = signed ? formatSigned : formatDecimal;
  return `${f(i.point, digits)} [${f(i.lo, digits)}, ${f(i.hi, digits)}]`;
}

export function formatPercent(x: number, digits = 1): string {
  if (!Number.isFinite(x)) return "–";
  return `${new Intl.NumberFormat(LOCALE, { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(x * 100)}%`;
}

function parse(iso: string): Date | null {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** 2026. 10. 24. style date in KST; returns the input unchanged when unparsable. */
export function formatDate(iso: string): string {
  const d = parse(iso);
  return d === null ? iso : DATE.format(d).replace(/\.$/, "");
}

export function formatDateTime(iso: string): string {
  const d = parse(iso);
  return d === null ? iso : DATETIME.format(d);
}

export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "–";
  let v = n;
  let u = 0;
  while (v >= BYTES_STEP && u < BYTE_UNITS.length - 1) {
    v /= BYTES_STEP;
    u += 1;
  }
  const unit = BYTE_UNITS[u] ?? "B";
  return `${u === 0 ? INT.format(v) : new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 1 }).format(v)} ${unit}`;
}

export function shortHash(sha: string, n = HASH_SHORT): string {
  return sha.slice(0, n);
}

/** Hostname of a URL, used as the publisher label; falls back to the raw string. */
export function publisherOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch (err) {
    if (err instanceof TypeError) return url;
    throw err;
  }
}
