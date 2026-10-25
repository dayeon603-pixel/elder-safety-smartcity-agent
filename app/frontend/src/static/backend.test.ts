import { describe, expect, it } from "vitest";
import { StaticBackend } from "./backend";
import type { Index } from "./backend";
import { canonicalKey } from "./fixtureKey";

const entry = (f: string) => ({ f, t: "application/json", s: 200 });
const index: Index = {
  "GET /twin/11620/state?date=2025-07-22&daytype=weekday&hour=9": entry("s9.json"),
  "GET /twin/11620/state?date=2025-07-22&daytype=weekday&hour=17": entry("s17.json"),
  "GET /twin/route?date=2025-07-22&daytype=weekday&hour=17&lat=1&lon=2": entry("r.json"),
};
const mk = (): StaticBackend => new StaticBackend({
  index, readJson: async () => null, fileUrl: (f) => f, fetchFile: async () => new Response("{}"), terminalUrl: () => "u", storage: null, channelFactory: null, sleep: async () => undefined, warn: () => undefined,
});

describe("static backend lookup", () => {
  it("canonical key sorts params", () => {
    expect(canonicalKey("get", "/a?b=2&a=1")).toBe("GET /a?a=1&b=2");
  });
  it("exact hit", () => {
    expect(mk().lookup("GET", "/twin/11620/state?hour=9&date=2025-07-22&daytype=weekday")?.f).toBe("s9.json");
  });
  it("nearest hour on another date (terminal clock)", () => {
    expect(mk().lookup("GET", "/twin/11620/state?date=2026-10-25&hour=15&daytype=weekday")?.f).toBe("s17.json");
  });
  it("route with a different coordinate is not silently reused unless the path is loose", () => {
    expect(mk().lookup("GET", "/twin/route?lat=9&lon=9&date=2025-07-22&hour=17&daytype=weekday")?.f).toBe("r.json");
    expect(mk().lookup("GET", "/twin/11620/state?x=1&date=2025-07-22&hour=9")).toBeNull();
  });
});
