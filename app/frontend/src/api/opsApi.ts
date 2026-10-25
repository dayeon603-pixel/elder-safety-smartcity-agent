/**
 * Data access for the /app 도시 운영 지도, on top of the existing clients.
 * Endpoints still missing on the backend (see the report): crosswalks / AED / shelters by 시군구 with coordinates and
 * hours. Until then they are static JSON shards built from the same public data (lib/app/opsData.ts).
 */
import { request } from "./client";
import { ZP_MOCK, zpApi } from "./zp";
import type { HeatRegion } from "./heatTypes";
import type { ActionsBit, BriefingAction, RawSilverMap } from "./zpTypes";
import { OPS_FALLBACK_RADIUS_KM } from "../config";
import { haversineM } from "../lib/field";

export type SilverKind = "current" | "ours" | "hotspot";

export interface OpsSilverPoint {
  lat: number;
  lon: number;
  kind: SilverKind;
  name: string | null;
  casualties: number | null;
  /** zone of the current / recommended set within the radius (for hotspots: covered) */
  currentWithin: boolean;
  oursWithin: boolean;
}

let silverRequest: Promise<RawSilverMap> | null = null;

/** The national /silver/map payload carries sgg_code on every point; fetched once per page load. */
function nationalSilver(): Promise<RawSilverMap> {
  silverRequest ??= request<RawSilverMap>("/silver/map").catch((e: unknown) => {
    silverRequest = null;
    throw e;
  });
  return silverRequest;
}

export async function loadRegionSilver(region: HeatRegion): Promise<OpsSilverPoint[]> {
  if (ZP_MOCK) {
    // Mock points carry no sgg_code: keep those near the district centroid.
    const m = await zpApi.silverMap("seoul");
    return m.points
      .filter((p) => haversineM(region.lat, region.lon, p.lat, p.lon) <= OPS_FALLBACK_RADIUS_KM * 1000)
      .map((p) => ({ lat: p.lat, lon: p.lon, kind: p.kind === "hotspot_2024" ? "hotspot" : p.kind, name: p.name, casualties: p.cas, currentWithin: p.cur_within === 1, oursWithin: p.ours_within === 1 }));
  }
  const raw = await nationalSilver();
  const zone = (kind: "current" | "ours") => (kind === "current" ? raw.current : raw.ours)
    .filter((s) => s.sgg_code === region.code)
    .map((s): OpsSilverPoint => ({ lat: s.lat, lon: s.lon, kind, name: s.name, casualties: null, currentWithin: s.near_current, oursWithin: s.near_ours }));
  const hot = raw.hotspots_2024
    .filter((h) => h.sgg_code === region.code)
    .map((h): OpsSilverPoint => ({ lat: h.lat, lon: h.lon, kind: "hotspot", name: null, casualties: h.casualties, currentWithin: h.covered_current, oursWithin: h.covered_ours }));
  return [...zone("current"), ...zone("ours"), ...hot];
}

/** Fields the live /actions and /briefing send that the shared types do not list yet. */
export type OpsBit = ActionsBit & { lat?: number; lon?: number };
export type OpsAction = BriefingAction & { action_id?: string };
