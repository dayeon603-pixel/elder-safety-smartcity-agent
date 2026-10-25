import { describe, expect, it } from "vitest";
import { buildQueue, countStates, openShare } from "./opsModel";
import type { QueueInput } from "./opsModel";

const base: QueueInput = { regionName: "동작구", speedMps: 0.7, crosswalks: null, briefing: null, actions: null, rawActions: [], tmaxC: null, aed: null, capFeedHref: "/api/cap/feed.atom", silverHeadline: null };

describe("buildQueue", () => {
  it("with no data: a not-evaluated signal item and the CAP link, nothing invented", () => {
    const q = buildQueue(base);
    expect(q.map((x) => x.id)).toEqual(["signal.extend", "cap.feed"]);
    expect(q[0]?.decidable).toBe(false);
    expect(q[1]?.evidenceLink.external).toBe(true);
  });
  it("every item says where it can go; webhook is the one route not live yet", () => {
    const q = buildQueue({ ...base, crosswalks: { evaluated: 3, short: [], shortAtBaseline: 0 } });
    expect(q[0]?.channels.map((c) => c.kind)).toEqual(["gongmun", "webhook"]);
    expect(q[0]?.channels.map((c) => [c.kind, c.available])).toEqual([["gongmun", true], ["webhook", false]]);
    expect(q[0]?.channels[0]?.docId).toBe("signals");
  });
});

describe("open counts", () => {
  it("unknown hours never count as closed, and an all-unknown set has no share", () => {
    const c = countStates(["open", "closed", "unknown", "unknown"]);
    expect(c).toEqual({ open: 1, closed: 1, unknown: 2, total: 4 });
    expect(openShare(c)).toBe(0.5);
    expect(openShare(countStates(["unknown"]))).toBeNull();
  });
});
