import { describe, expect, it } from "vitest";
import { parseIndex, parseRegionLayers } from "./opsData";

describe("parseRegionLayers", () => {
  const shard = { sido: "서울특별시", regions: { "11590": { cw: [[37.5, 126.9, 20, 25, "주소"], ["x"]], aed: [[37.5, 126.9, "기관", "1층", "주소", "0900-1800;-;-;-;-;-;-;-"]], sh: [[37.5, 126.9, "쉼터", 1, 0, 20], [37.5, 126.9, "쉼터2", null, 1, null]] } } };
  it("reads rows and drops malformed ones", () => {
    const l = parseRegionLayers("11590", shard);
    expect(l.crosswalks).toEqual([{ id: "11590-cw0", lat: 37.5, lon: 126.9, lengthM: 20, greenSec: 25, address: "주소" }]);
    expect(l.aeds[0]?.hours).toContain("0900-1800");
    expect(l.shelters.map((s) => [s.nightOpen, s.weekendOpen])).toEqual([[1, 0], [null, 1]]);
  });
  it("a region without rows is empty, a broken file throws", () => {
    expect(parseRegionLayers("99999", shard)).toEqual({ crosswalks: [], aeds: [], shelters: [] });
    expect(() => parseRegionLayers("11590", { nope: 1 })).toThrow();
  });
});

describe("parseIndex", () => {
  it("rejects a file without sidos", () => expect(() => parseIndex({ meta: {} })).toThrow());
});
