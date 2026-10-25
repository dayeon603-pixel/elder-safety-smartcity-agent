import { describe, expect, it } from "vitest";
import { haversineM, kstParts, nearestOpen, pickStop, walkMinutes } from "./api";
import type { StopInfo } from "./types";

const stop = (id: string): StopInfo => ({ stop_id: id, name: id, lat: 37.5, lon: 127, sgg_code: "11620" });

describe("terminal api helpers", () => {
  it("haversine: 0.01 deg latitude is about 1.11 km", () => {
    expect(haversineM(37.5, 127, 37.51, 127)).toBeGreaterThan(1100);
    expect(haversineM(37.5, 127, 37.51, 127)).toBeLessThan(1120);
    expect(haversineM(1, 2, 1, 2)).toBe(0);
  });
  it("walkMinutes rounds up and is at least 1", () => {
    expect(walkMinutes(0)).toBe(1);
    expect(walkMinutes(48, 0.8)).toBe(1);
    expect(walkMinutes(49, 0.8)).toBe(2);
  });
  it("nearestOpen skips closed and unknown", () => {
    const items = [
      { lat: 37.5001, lon: 127, open_now: false },
      { lat: 37.5002, lon: 127, open_now: null },
      { lat: 37.51, lon: 127, open_now: true },
      { lat: 37.52, lon: 127, open_now: true },
    ];
    expect(nearestOpen(items, 37.5, 127)?.item).toBe(items[2]);
    expect(nearestOpen([items[0]!, items[1]!], 37.5, 127)).toBeNull();
    expect(nearestOpen([], 37.5, 127)).toBeNull();
  });
  it("kstParts uses Seoul time across the UTC date boundary", () => {
    expect(kstParts(new Date("2026-10-24T16:30:00Z"))).toEqual({ date: "2026-10-25", hour: 1, daytype: "sun" });
    expect(kstParts(new Date("2026-10-23T05:00:00Z"))).toEqual({ date: "2026-10-23", hour: 14, daytype: "weekday" });
    expect(kstParts(new Date("2026-10-24T03:00:00Z")).daytype).toBe("sat");
  });
  it("pickStop: default is the first, unknown id is null", () => {
    const list = [stop("A"), stop("B")];
    expect(pickStop(list, null)?.stop_id).toBe("A");
    expect(pickStop(list, "B")?.stop_id).toBe("B");
    expect(pickStop(list, "Z")).toBeNull();
    expect(pickStop([], null)).toBeNull();
  });
});
