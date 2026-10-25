const KST_OFFSET_MS = 9 * 3_600_000;
const two = (n: number): string => String(n).padStart(2, "0");
/** HH:MM:SS.mmm in KST. */
export const clockKst = (d: Date = new Date()): string => {
  const k = new Date(d.getTime() + KST_OFFSET_MS);
  return `${two(k.getUTCHours())}:${two(k.getUTCMinutes())}:${two(k.getUTCSeconds())}.${String(k.getUTCMilliseconds()).padStart(3, "0")}`;
};
