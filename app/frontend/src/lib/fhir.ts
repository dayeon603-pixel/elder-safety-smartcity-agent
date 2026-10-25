/**
 * HL7 FHIR R4 Bundle (type=collection) for the field drill: anonymous Patient, Encounter, Location and Observations.
 * Everything is tagged HTEST (v3-ActReason "test health data") and Observations are status=preliminary: a drill, not a record.
 * Nothing is transmitted.
 */
import type { Crosswalk, HeatStage } from "./field";
import { HEAT_STAGE_LABEL } from "./field";

const SYS_ACT_REASON = "http://terminology.hl7.org/CodeSystem/v3-ActReason";
const SYS_ACT_CODE = "http://terminology.hl7.org/CodeSystem/v3-ActCode";
const SYS_DAR = "http://terminology.hl7.org/CodeSystem/data-absent-reason";
const SYS_UCUM = "http://unitsofmeasure.org";

export interface FhirInput {
  incidentId: string;
  at: Date;
  trackId: number | null;
  crosswalk: Crosswalk;
  stage: HeatStage;
  tmaxC: number | null;
  torsoAngleDeg: number | null;
  estSpeedMps: number | null;
  /** deterministic ids in tests; crypto.randomUUID in the app */
  uuid: () => string;
}

type Json = Record<string, unknown>;

const DRILL_TAG = { system: SYS_ACT_REASON, code: "HTEST", display: "test health data" };

function quantity(v: number, unit: string, code: string): Json {
  return { value: Number(v.toFixed(2)), unit, system: SYS_UCUM, code };
}

export function buildFhirBundle(i: FhirInput): Json {
  const patient = i.uuid();
  const encounter = i.uuid();
  const location = i.uuid();
  const ref = (u: string): Json => ({ reference: `urn:uuid:${u}` });
  const when = i.at.toISOString();
  const obs = (text: string, value: Json | null): { u: string; resource: Json } => {
    const u = i.uuid();
    return {
      u,
      resource: {
        resourceType: "Observation",
        meta: { tag: [DRILL_TAG] },
        status: "preliminary",
        category: [{ coding: [{ system: "http://terminology.hl7.org/CodeSystem/observation-category", code: "survey" }] }],
        code: { text },
        subject: ref(patient),
        encounter: ref(encounter),
        effectiveDateTime: when,
        ...(value ?? { dataAbsentReason: { coding: [{ system: SYS_DAR, code: "not-performed" }] } }),
      },
    };
  };
  const observations = [
    obs("Fall detected (prototype rule, not validated)", { valueBoolean: true }),
    obs("Estimated gait speed (camera, needs calibration)", i.estSpeedMps === null ? null : { valueQuantity: quantity(i.estSpeedMps, "m/s", "m/s") }),
    obs("Torso angle from vertical", i.torsoAngleDeg === null ? null : { valueQuantity: quantity(i.torsoAngleDeg, "degree", "deg") }),
    obs("Daily maximum air temperature (public weather data)", i.tmaxC === null ? null : { valueQuantity: quantity(i.tmaxC, "degree Celsius", "Cel") }),
    obs("Heat stage (daily max temperature proxy)", { valueString: HEAT_STAGE_LABEL[i.stage] }),
  ];
  const entry = (u: string, resource: Json): Json => ({ fullUrl: `urn:uuid:${u}`, resource });
  return {
    resourceType: "Bundle",
    id: i.incidentId,
    meta: { tag: [DRILL_TAG] },
    type: "collection",
    timestamp: when,
    entry: [
      entry(patient, {
        resourceType: "Patient",
        meta: { tag: [DRILL_TAG] },
        active: true,
        text: { status: "generated", div: `<div xmlns="http://www.w3.org/1999/xhtml">Anonymous subject${i.trackId === null ? "" : ` (track ${i.trackId})`}. Drill.</div>` },
      }),
      entry(location, {
        resourceType: "Location",
        meta: { tag: [DRILL_TAG] },
        status: "active",
        name: i.crosswalk.address,
        mode: "instance",
        position: { longitude: i.crosswalk.lon, latitude: i.crosswalk.lat },
      }),
      entry(encounter, {
        resourceType: "Encounter",
        meta: { tag: [DRILL_TAG] },
        status: "in-progress",
        class: { system: SYS_ACT_CODE, code: "EMER", display: "emergency" },
        subject: ref(patient),
        period: { start: when },
        location: [{ location: ref(location) }],
      }),
      ...observations.map((o) => entry(o.u, o.resource)),
    ],
  };
}
