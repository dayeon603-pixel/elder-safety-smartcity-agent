import { describe, expect, it } from "vitest";
import { buildFhirBundle } from "./fhir";
import type { Crosswalk } from "./field";

const CW = { id: "cw01", address: "서울특별시 동작구 상도동 320-21", lat: 37.4997, lon: 126.9319 } as Crosswalk;
function uuidGen(): () => string {
  let n = 0;
  return () => `00000000-0000-4000-8000-${String(++n).padStart(12, "0")}`;
}
const base = { incidentId: "zp-1", at: new Date("2026-10-24T05:00:00Z"), trackId: 2, crosswalk: CW, stage: "warning", tmaxC: 35.4, torsoAngleDeg: 78.26, estSpeedMps: 0.456, uuid: uuidGen() } as const;

type Res = { resourceType: string; [k: string]: unknown };
const resources = (b: unknown): Res[] => (b as { entry: { resource: Res }[] }).entry.map((e) => e.resource);

describe("FHIR R4 bundle", () => {
  it("is a collection with the required resources, every one tagged HTEST", () => {
    const b = buildFhirBundle({ ...base, uuid: uuidGen() }) as { resourceType: string; type: string; timestamp: string };
    expect(b).toMatchObject({ resourceType: "Bundle", type: "collection", timestamp: "2026-10-24T05:00:00.000Z" });
    const rs = resources(b);
    expect(rs.map((r) => r.resourceType)).toEqual(["Patient", "Location", "Encounter", "Observation", "Observation", "Observation", "Observation", "Observation"]);
    for (const r of rs) expect(JSON.stringify((r.meta as { tag: unknown }).tag)).toContain("HTEST");
  });
  it("patient is anonymous; location carries lat/lon", () => {
    const rs = resources(buildFhirBundle({ ...base, uuid: uuidGen() }));
    expect(rs[0]).not.toHaveProperty("name");
    expect(rs[1]).toMatchObject({ position: { latitude: 37.4997, longitude: 126.9319 } });
  });
  it("observations have status and code, reference the patient, and carry measured values with UCUM units", () => {
    const rs = resources(buildFhirBundle({ ...base, uuid: uuidGen() }));
    const obs = rs.filter((r) => r.resourceType === "Observation");
    for (const o of obs) {
      expect(o.status).toBe("preliminary");
      expect(o.code).toHaveProperty("text");
      expect(o.subject).toEqual({ reference: "urn:uuid:00000000-0000-4000-8000-000000000001" });
    }
    expect(obs[0]?.valueBoolean).toBe(true);
    expect(obs[1]?.valueQuantity).toMatchObject({ value: 0.46, code: "m/s", system: "http://unitsofmeasure.org" });
    expect(obs[2]?.valueQuantity).toMatchObject({ value: 78.26, code: "deg" });
    expect(obs[3]?.valueQuantity).toMatchObject({ value: 35.4, code: "Cel" });
  });
  it("missing measurements use dataAbsentReason instead of a made-up value", () => {
    const rs = resources(buildFhirBundle({ ...base, uuid: uuidGen(), estSpeedMps: null, torsoAngleDeg: null, tmaxC: null }));
    const obs = rs.filter((r) => r.resourceType === "Observation");
    for (const k of [1, 2, 3]) {
      expect(obs[k]).toHaveProperty("dataAbsentReason");
      expect(obs[k]).not.toHaveProperty("valueQuantity");
    }
  });
});
