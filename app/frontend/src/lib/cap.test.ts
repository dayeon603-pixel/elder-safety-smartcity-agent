import { describe, expect, it } from "vitest";
import { buildCapJson, buildCapXml, capDateTime, escapeXml } from "./cap";

const INPUT = {
  identifier: "zp-20261024-0001", sent: new Date("2026-10-24T05:00:00Z"), event: "보행자 쓰러짐(훈련)",
  headline: "h & <x>", description: "d", instruction: "i", areaDesc: "서울 동작구", lat: 37.4997, lon: 126.9319,
};

describe("CAP 1.2 builder", () => {
  it("formats sent with a numeric offset, never Z", () => {
    expect(capDateTime(INPUT.sent)).toBe("2026-10-24T14:00:00+09:00");
    expect(capDateTime(INPUT.sent, -300)).toBe("2026-10-24T00:00:00-05:00");
  });
  it("carries the required schema fields with drill values", () => {
    const a = buildCapJson(INPUT).alert;
    expect(a).toMatchObject({ sender: "zero-point.demo", status: "Exercise", msgType: "Alert", scope: "Restricted", xmlns: "urn:oasis:names:tc:emergency:cap:1.2" });
    expect(a.restriction).not.toBe("");
    expect(a.info).toMatchObject({ urgency: "Immediate", severity: "Severe", certainty: "Possible" });
    expect(a.info.category).toEqual(["Health", "Safety"]);
    expect(a.info.area.circle).toBe("37.4997,126.9319 0.1");
  });
  it("emits well-formed XML in CAP element order with escaped text", () => {
    const xml = buildCapXml(INPUT);
    const order = ["identifier", "sender", "sent", "status", "msgType", "scope", "restriction", "info", "category", "event", "urgency", "severity", "certainty", "area", "circle"];
    let at = -1;
    for (const tag of order) {
      const idx = xml.indexOf(`<${tag}>`, at + 1);
      expect(idx, tag).toBeGreaterThan(at);
      at = idx;
    }
    expect(xml).toContain("<headline>h &amp; &lt;x&gt;</headline>");
    expect(xml).toContain('<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">');
    const doc = new DOMParser().parseFromString(xml, "application/xml");
    expect(doc.getElementsByTagName("parsererror")).toHaveLength(0);
    expect(doc.getElementsByTagName("status")[0]?.textContent).toBe("Exercise");
  });
  it("rejects bad coordinates and identifiers", () => {
    expect(() => buildCapJson({ ...INPUT, lat: 91 })).toThrow(RangeError);
    expect(() => buildCapJson({ ...INPUT, lon: Number.NaN })).toThrow(RangeError);
    expect(() => buildCapJson({ ...INPUT, identifier: "a b" })).toThrow(RangeError);
    expect(() => buildCapJson({ ...INPUT, radiusKm: -1 })).toThrow(RangeError);
  });
  it("escapes all five XML specials", () => {
    expect(escapeXml(`&<>"'`)).toBe("&amp;&lt;&gt;&quot;&apos;");
  });
});
