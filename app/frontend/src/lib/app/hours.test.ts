import { describe, expect, it } from "vitest";
import { addDays, aedStateAt, kstNow, openAt, parseAedHours, parseDayHours, shelterStateAt, slotOfChoice } from "./hours";

const FULL = "0900-1800;0900-1800;0900-1800;0900-1800;0900-1800;0000-2400;-;1000-1200";

describe("parseDayHours", () => {
  it("parses a normal pair and 24 h", () => {
    expect(parseDayHours("0900-1800")).toEqual({ startMin: 540, endMin: 1080 });
    expect(parseDayHours("0000-2400")).toEqual({ startMin: 0, endMin: 1440 });
  });
  it("rejects missing, equal, malformed and impossible pairs as unknown", () => {
    for (const bad of ["-", "", "0000-0000", "0900-0900", "09:00-18:00", "2500-0100", "0960-1000", "2430-2400", "nan-nan"]) expect(parseDayHours(bad)).toBeNull();
  });
});

describe("openAt", () => {
  it("is half-open at the end", () => {
    const h = parseDayHours("0900-1800");
    expect(openAt(h, 9 * 60)).toBe("open");
    expect(openAt(h, 18 * 60 - 1)).toBe("open");
    expect(openAt(h, 18 * 60)).toBe("closed");
    expect(openAt(h, 8 * 60 + 59)).toBe("closed");
  });
  it("handles a window that wraps midnight", () => {
    const h = parseDayHours("2200-0600");
    expect(openAt(h, 23 * 60)).toBe("open");
    expect(openAt(h, 3 * 60)).toBe("open");
    expect(openAt(h, 12 * 60)).toBe("closed");
  });
  it("unknown hours are unknown, not closed", () => expect(openAt(null, 600)).toBe("unknown"));
  it("rejects out-of-range minutes", () => expect(() => openAt(parseDayHours("0900-1800"), 1440)).toThrow(RangeError));
});

describe("aedStateAt", () => {
  it("reads the weekday, weekend and holiday columns separately", () => {
    expect(aedStateAt(FULL, slotOfChoice("weekday"), 10)).toBe("open");
    expect(aedStateAt(FULL, slotOfChoice("weekday"), 23)).toBe("closed");
    expect(aedStateAt(FULL, slotOfChoice("sat"), 23)).toBe("open");
    expect(aedStateAt(FULL, slotOfChoice("sun"), 10)).toBe("unknown");
    expect(aedStateAt(FULL, slotOfChoice("hol"), 10)).toBe("open");
    expect(aedStateAt(FULL, slotOfChoice("hol"), 13)).toBe("closed");
  });
  it("a malformed cell is unknown", () => {
    expect(parseAedHours("0900-1800")).toBeNull();
    expect(aedStateAt("", "mon", 10)).toBe("unknown");
  });
});

describe("kstNow", () => {
  it("converts to KST regardless of the browser zone", () => {
    expect(kstNow(new Date("2026-10-23T15:00:00Z"))).toEqual({ iso: "2026-10-24", hour: 0, choice: "sat" });
    expect(kstNow(new Date("2026-10-25T03:30:00Z")).choice).toBe("sun");
    expect(kstNow(new Date("2026-10-26T03:30:00Z"))).toMatchObject({ hour: 12, choice: "weekday" });
  });
  it("a weekday holiday beats the weekday (추석 2026-09-25 is a Friday)", () => {
    expect(kstNow(new Date("2026-09-25T03:00:00Z")).choice).toBe("hol");
  });
  it("accepts a custom holiday set", () => expect(kstNow(new Date("2026-10-26T03:00:00Z"), new Set(["2026-10-26"])).choice).toBe("hol"));
  it("addDays crosses month ends", () => expect(addDays("2026-02-28", 1)).toBe("2026-03-01"));
});

describe("shelterStateAt", () => {
  it("weekday night uses the night flag, weekend the weekend flag", () => {
    expect(shelterStateAt(1, 0, "weekday", 23)).toBe("open");
    expect(shelterStateAt(0, 1, "weekday", 3)).toBe("closed");
    expect(shelterStateAt(0, 1, "sat", 14)).toBe("open");
    expect(shelterStateAt(1, 0, "hol", 14)).toBe("closed");
  });
  it("weekday daytime and null flags are unknown, never open", () => {
    expect(shelterStateAt(1, 1, "weekday", 14)).toBe("unknown");
    expect(shelterStateAt(null, null, "sun", 14)).toBe("unknown");
  });
});
