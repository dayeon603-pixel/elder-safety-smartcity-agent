import { describe, expect, it } from "vitest";
import { formatBytes, formatDate, formatInt, formatInterval, formatPercent, formatSigned, publisherOf, shortHash } from "./format";

describe("format", () => {
  it("groups integers ko-KR and guards non-finite", () => {
    expect(formatInt(1234567)).toBe("1,234,567");
    expect(formatInt(Number.NaN)).toBe("–");
    expect(formatInt(Number.POSITIVE_INFINITY)).toBe("–");
  });

  it("signs differences with a real minus", () => {
    expect(formatSigned(0.05)).toBe("+0.050");
    expect(formatSigned(-0.0688)).toBe("−0.069");
    expect(formatSigned(0)).toBe("0.000");
  });

  it("formats an interval", () => {
    expect(formatInterval({ point: 0.9, lo: 0.8, hi: 1 })).toBe("0.900 [0.800, 1.000]");
    expect(formatInterval({ point: 0.1, lo: -0.1, hi: 0.2 }, true)).toBe("+0.100 [−0.100, +0.200]");
  });

  it("formats percent, bytes, hash, dates", () => {
    expect(formatPercent(0.1234)).toBe("12.3%");
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(-1)).toBe("–");
    expect(shortHash("a".repeat(64))).toHaveLength(12);
    expect(formatDate("2026-10-23T17:38:25+00:00")).toBe("2026. 10. 24");
    expect(formatDate("garbage")).toBe("garbage");
  });

  it("extracts the publisher host and tolerates bad URLs", () => {
    expect(publisherOf("https://www.data.go.kr/x/y")).toBe("data.go.kr");
    expect(publisherOf("not a url")).toBe("not a url");
  });
});
