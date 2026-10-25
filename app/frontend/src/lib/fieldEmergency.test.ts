import { describe, expect, it } from "vitest";
import { incidentId, runEmergency } from "./fieldEmergency";
import type { Aed, Crosswalk } from "./field";

const CW = {
  id: "cw01", sido: "서울특별시", sigungu: "동작구", address: "서울특별시 동작구 상도동 320-21", lat: 37.4997, lon: 126.9319,
  greenSec: 27, redSec: 30, lengthM: 10.6, widthM: 5.9, lanes: 2, hasIsland: false,
  hotspot: { id: "h", name: "n", lat: 0, lon: 0, occurrences: 1, casualties: 1, deaths: 0, distanceM: 25 },
} satisfies Crosswalk;
const AEDS: Aed[] = [
  { org: "먼곳", place: "", sido: "", gugun: "", address: "a", lat: 37.52, lon: 126.93 },
  { org: "상도경로당", place: "1층", sido: "", gugun: "", address: "b", lat: 37.5, lon: 126.932 },
];
const HEAT = { stage: "warning", tmaxC: 35.4, date: "2025-08-15", source: "api", regionName: "동작구", regionKm: 1 } as const;

function run(aeds: readonly Aed[] | null, muted = false) {
  let clock = 100;
  return runEmergency({
    trigger: "camera", t0: 100, trackId: 2, aiProb: 0.83, ruleFiredAlready: false, uuid: () => "00000000-0000-4000-8000-000000000000", now: () => (clock += 2), at: new Date("2026-10-24T05:00:00Z"), crosswalk: CW, heat: HEAT, aeds,
    torsoAngleDeg: 78.26, estSpeedMps: 0.456, muted, speakFn: (_t, m) => (m ? "muted" : "spoken"),
  });
}

describe("runEmergency", () => {
  it("times each step with the injected clock, monotonically", () => {
    const e = run(AEDS);
    const at = e.steps.map((s) => s.atMs);
    expect(at).toEqual([...at].sort((a, b) => a - b));
    expect(at[0]).toBe(2);
    expect(e.buildMs).toBeGreaterThanOrEqual(at[at.length - 1] as number);
    expect(e.steps.map((s) => s.id)).toEqual(["locate", "heat", "aed", "speech", "payload", "cap", "incident", "fhir"]);
  });
  it("picks the nearest AED and speaks its real name", () => {
    const e = run(AEDS);
    expect(e.aed?.item.org).toBe("상도경로당");
    expect(e.speechText).toContain("상도경로당 1층에 있습니다");
  });
  it("reports AED 자료 없음 for null or empty data, and honors mute", () => {
    expect(run(null).aedStatus).toBe("unavailable");
    expect(run([]).aedStatus).toBe("none_nearby");
    expect(run([]).steps[2]?.lines[0]).toContain("AED 자료 없음");
    expect(run(AEDS, true).speechResult).toBe("muted");
  });
  it("never claims anything was sent; incident carries the measured pose values", () => {
    const e = run(AEDS);
    expect(e.payload.sent).toBe(false);
    expect(e.capJson.alert.status).toBe("Exercise");
    expect(e.incident.actions.cap_alert).toBe("생성됨·전송 안 함");
    expect(e.incident.victim_status).toEqual({ track_id: 2, ai_fall_probability: 0.83, torso_angle_deg: 78.3, est_speed_mps: 0.46, fall_detected: true });
    expect(JSON.stringify(e)).not.toMatch(/SUCCESS|SENT/);
  });
  it("incident id uses KST wall time", () => {
    expect(incidentId(new Date("2026-10-24T05:00:00Z"), "cw01")).toBe("zp-20261024140000-cw01");
  });
});
