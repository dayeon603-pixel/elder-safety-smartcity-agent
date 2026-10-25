/** Pure logic for the /field simulation: crossing-time arithmetic, heat stage, nearest-point search, 119 payload preview. */
import { FIELD_HEAT_ADVISORY_C, FIELD_HEAT_WARNING_C } from "../config";

const EARTH_RADIUS_M = 6_371_000;
const FLOAT_EPS = 1e-9;

export interface Hotspot {
  id: string;
  name: string;
  lat: number;
  lon: number;
  occurrences: number;
  casualties: number;
  deaths: number;
  distanceM: number;
}

export interface Crosswalk {
  id: string;
  sido: string;
  sigungu: string;
  address: string;
  lat: number;
  lon: number;
  greenSec: number;
  redSec: number | null;
  lengthM: number;
  widthM: number | null;
  lanes: number;
  hasIsland: boolean;
  hotspot: Hotspot;
}

export interface Aed {
  org: string;
  place: string;
  sido: string;
  gugun: string;
  address: string;
  lat: number;
  lon: number;
}

export interface DataSource {
  name: string;
  url: string;
  file: string;
  sha256: string;
  rows?: number;
  sha256_note?: string;
}

export interface FieldMeta {
  generated_at: string;
  script: string;
  sources: DataSource[];
  selection: string;
}

export interface CrosswalkFile {
  meta: FieldMeta;
  crosswalks: Crosswalk[];
}

export interface AedFile {
  meta: FieldMeta;
  aeds: Aed[];
}

export type HeatStage = "none" | "advisory" | "warning";

export const HEAT_STAGE_LABEL: Record<HeatStage, string> = {
  none: "폭염 특보 수준 아님",
  advisory: "폭염주의보 수준",
  warning: "폭염경보 수준",
};

/** Daily max temperature -> demo heat stage. Proxy for KMA's feels-like criteria. */
export function stageFromTmax(tmaxC: number): HeatStage {
  if (!Number.isFinite(tmaxC)) throw new RangeError("tmax must be a finite number");
  if (tmaxC >= FIELD_HEAT_WARNING_C) return "warning";
  if (tmaxC >= FIELD_HEAT_ADVISORY_C) return "advisory";
  return "none";
}

export function haversineM(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const p1 = (lat1 * Math.PI) / 180;
  const p2 = (lat2 * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a = Math.sin((p2 - p1) / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(a)));
}

export interface Nearest<T> {
  item: T;
  distanceM: number;
}

/** Closest item to (lat, lon) or null for an empty list. Ties keep the earlier item. */
export function nearestBy<T extends { lat: number; lon: number }>(items: readonly T[], lat: number, lon: number): Nearest<T> | null {
  let best: Nearest<T> | null = null;
  for (const item of items) {
    const d = haversineM(lat, lon, item.lat, item.lon);
    if (best === null || d < best.distanceM) best = { item, distanceM: d };
  }
  return best;
}

export interface CrossingAssessment {
  lengthM: number;
  speedMps: number;
  greenRemainingSec: number;
  /** length / speed */
  requiredSec: number;
  /** required - remaining green; positive means the pedestrian will not finish. */
  deficitSec: number;
  finishes: boolean;
  /** Whole seconds to add: ceil(deficit), 0 when the pedestrian finishes. */
  extensionSec: number;
}

/** Predicted crossing time against the remaining green. Throws on non-positive length/speed or negative green. */
export function assessCrossing(lengthM: number, speedMps: number, greenRemainingSec: number): CrossingAssessment {
  if (!(lengthM > 0)) throw new RangeError("lengthM must be > 0");
  if (!(speedMps > 0)) throw new RangeError("speedMps must be > 0");
  if (!(greenRemainingSec >= 0)) throw new RangeError("greenRemainingSec must be >= 0");
  const requiredSec = lengthM / speedMps;
  const deficitSec = requiredSec - greenRemainingSec;
  const finishes = deficitSec <= FLOAT_EPS;
  return { lengthM, speedMps, greenRemainingSec, requiredSec, deficitSec, finishes, extensionSec: finishes ? 0 : Math.ceil(deficitSec - FLOAT_EPS) };
}

/** Pedestrian distance walked after t seconds, clamped to the crosswalk. */
export function pedestrianPosition(lengthM: number, speedMps: number, tSec: number): number {
  return Math.min(lengthM, Math.max(0, speedMps * tSec));
}

export function greenRemaining(startRemainingSec: number, tSec: number): number {
  return Math.max(0, startRemainingSec - tSec);
}

export function vmsText(stage: HeatStage): string {
  return stage === "none" ? "⚠ 고령 보행자 횡단 중 · 서행" : "⚠ 폭염 · 고령 보행자 횡단 중 · 서행";
}

export function aedSpeech(aed: Nearest<Aed> | null): string {
  const lead = "근처에 쓰러진 분이 있습니다. 119에 신고하고,";
  if (aed === null) return "근처에 쓰러진 분이 있습니다. 119에 신고해 주세요.";
  const where = [aed.item.org, aed.item.place].filter((s) => s !== "").join(" ");
  return `${lead} 가장 가까운 자동심장충격기는 ${where}에 있습니다.`;
}

export interface EmergencyPayload {
  type: "fall_detected";
  simulated: true;
  sent: false;
  trigger: "button" | "camera";
  detected_at: string;
  location: { lat: number; lon: number; crosswalk_id: string; address: string };
  heat: { date: string | null; stage: HeatStage; stage_label: string; tmax_c: number | null; source: "api" | "manual" };
  nearest_aed: { org: string; place: string; address: string; lat: number; lon: number; distance_m: number } | null;
}

export interface PayloadInput {
  trigger: "button" | "camera";
  at: Date;
  crosswalk: Crosswalk;
  heat: EmergencyPayload["heat"];
  aed: Nearest<Aed> | null;
}

export function buildPayload({ trigger, at, crosswalk, heat, aed }: PayloadInput): EmergencyPayload {
  return {
    type: "fall_detected",
    simulated: true,
    sent: false,
    trigger,
    detected_at: at.toISOString(),
    location: { lat: crosswalk.lat, lon: crosswalk.lon, crosswalk_id: crosswalk.id, address: crosswalk.address },
    heat,
    nearest_aed:
      aed === null
        ? null
        : {
            org: aed.item.org, place: aed.item.place, address: aed.item.address,
            lat: aed.item.lat, lon: aed.item.lon, distance_m: Math.round(aed.distanceM),
          },
  };
}

/** Heat input resolved for the chosen crosswalk and date, from the /heat API or from the user's manual choice. */
export interface HeatInfo {
  stage: HeatStage;
  tmaxC: number | null;
  date: string | null;
  source: "api" | "manual";
  regionName: string | null;
  regionKm: number | null;
}

export function formatMeters(m: number): string {
  return m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`;
}
