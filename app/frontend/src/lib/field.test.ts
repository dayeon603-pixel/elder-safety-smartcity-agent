import { describe, expect, it } from "vitest";
import { aedSpeech, assessCrossing, greenRemaining, haversineM, nearestBy, pedestrianPosition, stageFromTmax, vmsText } from "./field";
import type { Aed } from "./field";

describe("assessCrossing", () => {
  it("25 m at 0.7 m/s needs 35.7 s: 8.7 s short of 27 s green, recommend +9 s", () => {
    const a = assessCrossing(25, 0.7, 27);
    expect(a.requiredSec).toBeCloseTo(35.714, 3);
    expect(a.deficitSec).toBeCloseTo(8.714, 3);
    expect(a.finishes).toBe(false);
    expect(a.extensionSec).toBe(9);
  });
  it("finishes exactly at the boundary with no extension", () => {
    const a = assessCrossing(21, 0.7, 30);
    expect(a.finishes).toBe(true);
    expect(a.extensionSec).toBe(0);
  });
  it("an integer deficit is not rounded up to the next second", () => {
    expect(assessCrossing(14, 0.5, 20).extensionSec).toBe(8);
  });
  it("rejects zero/negative inputs", () => {
    expect(() => assessCrossing(0, 0.7, 10)).toThrow(RangeError);
    expect(() => assessCrossing(10, 0, 10)).toThrow(RangeError);
    expect(() => assessCrossing(10, 0.7, -1)).toThrow(RangeError);
    expect(() => assessCrossing(10, Number.NaN, 10)).toThrow(RangeError);
  });
  it("position and green clamp", () => {
    expect(pedestrianPosition(10, 0.7, 100)).toBe(10);
    expect(pedestrianPosition(10, 0.7, -5)).toBe(0);
    expect(greenRemaining(5, 9)).toBe(0);
  });
});

describe("heat stage and geo", () => {
  it("maps tmax to stages at 33 and 35", () => {
    expect(stageFromTmax(32.9)).toBe("none");
    expect(stageFromTmax(33)).toBe("advisory");
    expect(stageFromTmax(35)).toBe("warning");
    expect(() => stageFromTmax(Number.NaN)).toThrow(RangeError);
  });
  it("haversine ~111 km per degree of latitude, nearest picks the closest, empty gives null", () => {
    expect(haversineM(37, 127, 38, 127)).toBeCloseTo(111_195, -2);
    const pts = [{ lat: 37.1, lon: 127 }, { lat: 37.01, lon: 127 }];
    expect(nearestBy(pts, 37, 127)?.item).toBe(pts[1]);
    expect(nearestBy([], 37, 127)).toBeNull();
  });
  it("speech and VMS text", () => {
    const aed: Aed = { org: "OO경로당", place: "2층 입구", sido: "", gugun: "", address: "", lat: 0, lon: 0 };
    expect(aedSpeech({ item: aed, distanceM: 10 })).toBe("근처에 쓰러진 분이 있습니다. 119에 신고하고, 가장 가까운 자동심장충격기는 OO경로당 2층 입구에 있습니다.");
    expect(aedSpeech(null)).toContain("119에 신고");
    expect(vmsText("warning")).toBe("⚠ 폭염 · 고령 보행자 횡단 중 · 서행");
    expect(vmsText("none")).not.toContain("폭염");
  });
});
