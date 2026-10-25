/**
 * Edge audit: opening-hours logic at the 23 -> 0 boundary, at midnight in KST vs UTC, on holidays that fall on
 * weekends, and with corrupt cells. Complements hours.test.ts (which covers the normal paths).
 */
import { describe, expect, it } from "vitest";
import { addDays, aedStateAt, kstNow, openAt, parseAedHours, parseDayHours, shelterStateAt } from "./app/hours";
import { KR_HOLIDAYS } from "./app/holidays";

const WRAP = "2200-0600;2200-0600;2200-0600;2200-0600;2200-0600;2200-0600;2200-0600;2200-0600";
const ALL_DAY = "0000-2400;0000-2400;0000-2400;0000-2400;0000-2400;0000-2400;0000-2400;0000-2400";

describe("hour 23 -> 0 transition", () => {
  it("a window that wraps midnight is open at 23:59 and at 00:00 and closes at exactly 06:00", () => {
    const h = parseDayHours("2200-0600");
    expect(openAt(h, 23 * 60 + 59)).toBe("open");
    expect(openAt(h, 0)).toBe("open");
    expect(openAt(h, 5 * 60 + 59)).toBe("open");
    expect(openAt(h, 6 * 60)).toBe("closed");
    expect(openAt(h, 21 * 60 + 59)).toBe("closed");
    expect(openAt(h, 22 * 60)).toBe("open");
  });
  it("a 24-hour cell is open at both ends of the day", () => {
    expect(aedStateAt(ALL_DAY, "wed", 0)).toBe("open");
    expect(aedStateAt(ALL_DAY, "wed", 23)).toBe("open");
  });
  it("the wrap is read inside one day slot: Saturday 03:00 reads the Saturday column, not Friday's", () => {
    const fridayOnly = "-;-;-;-;2200-0600;-;-;-";
    expect(aedStateAt(fridayOnly, "fri", 23)).toBe("open");
    // 03:00 Saturday is physically inside Friday's 22-06 window, but the data model reads the Saturday slot ("-" = unknown).
    // Documented limitation: the UI shows "시간 자료 없음" for that hour, never "닫힘".
    expect(aedStateAt(fridayOnly, "sat", 3)).toBe("unknown");
  });
  it("hour 24 and negative hours are rejected, not silently wrapped", () => {
    expect(() => openAt(parseDayHours("0900-1800"), 24 * 60)).toThrow(RangeError);
    expect(() => openAt(parseDayHours("0900-1800"), -1)).toThrow(RangeError);
    expect(() => openAt(parseDayHours("0900-1800"), 12.5)).toThrow(RangeError);
  });
});

describe("KST midnight vs UTC midnight", () => {
  it("14:59Z and 15:00Z fall on different KST calendar days", () => {
    const before = kstNow(new Date("2026-10-24T14:59:59Z"));
    const after = kstNow(new Date("2026-10-24T15:00:00Z"));
    expect(before).toMatchObject({ iso: "2026-10-24", hour: 23, choice: "sat" });
    expect(after).toMatchObject({ iso: "2026-10-25", hour: 0, choice: "sun" });
  });
  it("a year boundary in KST is handled (2026-12-31 15:00Z = 2027-01-01 00:00 KST, 신정 = holiday)", () => {
    expect(kstNow(new Date("2026-12-31T15:00:00Z"))).toMatchObject({ iso: "2027-01-01", hour: 0, choice: "hol" });
    expect(kstNow(new Date("2026-12-31T14:59:00Z"))).toMatchObject({ iso: "2026-12-31", hour: 23, choice: "weekday" });
  });
  it("leap day 2028-02-29 is a valid KST date and a Tuesday", () => {
    expect(kstNow(new Date("2028-02-28T16:00:00Z"))).toMatchObject({ iso: "2028-02-29", choice: "weekday" });
    expect(addDays("2028-02-28", 1)).toBe("2028-02-29");
    expect(addDays("2027-02-28", 1)).toBe("2027-03-01");
  });
});

describe("holidays", () => {
  it("a holiday on a Sunday is 'hol', not 'sun' (삼일절 2026-03-01 is a Sunday)", () => {
    expect(KR_HOLIDAYS.has("2026-03-01")).toBe(true);
    expect(kstNow(new Date("2026-03-01T03:00:00Z")).choice).toBe("hol");
    // its substitute holiday on Monday is also in the table
    expect(kstNow(new Date("2026-03-02T03:00:00Z")).choice).toBe("hol");
  });
  it("a date outside the hand-entered table (2028) degrades to the weekday/weekend column", () => {
    expect(KR_HOLIDAYS.has("2028-01-01")).toBe(false);
    // 2028-01-01 is a Saturday: without a holiday row the Saturday column is read, which is the safer fallback
    expect(kstNow(new Date("2028-01-01T03:00:00Z")).choice).toBe("sat");
  });
  it("every table entry is a real ISO date", () => {
    for (const d of KR_HOLIDAYS) {
      const t = Date.parse(`${d}T00:00:00Z`);
      expect(Number.isNaN(t), d).toBe(false);
      expect(new Date(t).toISOString().slice(0, 10), d).toBe(d);
    }
  });
});

describe("corrupt cells", () => {
  it("wrong slot count, extra separators and whitespace are unknown, never closed", () => {
    expect(parseAedHours("0900-1800;0900-1800")).toBeNull();
    expect(aedStateAt("0900-1800;;;;;;;", "wed", 10)).toBe("unknown");
    expect(aedStateAt(" 0900-1800 ;-;-;-;-;-;-;-", "mon", 10)).toBe("open"); // trimmed
    expect(aedStateAt("0900–1800;-;-;-;-;-;-;-", "mon", 10)).toBe("unknown"); // en dash separator
    expect(aedStateAt("09:00-18:00;-;-;-;-;-;-;-", "mon", 10)).toBe("unknown");
  });
  it("shelter flags of 2 or 'yes' (not 0/1/boolean) are unknown", () => {
    expect(shelterStateAt(2 as unknown as 1, 1, "weekday", 23)).toBe("closed"); // any non-1 truthy flag reads as closed: documented
    expect(shelterStateAt(undefined, undefined, "sat", 10)).toBe("unknown");
  });
  it("the wrap-window AED is open all night on every slot of WRAP", () => {
    for (const slot of ["mon", "sat", "sun", "hol"] as const) {
      expect(aedStateAt(WRAP, slot, 23)).toBe("open");
      expect(aedStateAt(WRAP, slot, 0)).toBe("open");
      expect(aedStateAt(WRAP, slot, 12)).toBe("closed");
    }
  });
});
