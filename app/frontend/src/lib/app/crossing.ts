/**
 * 횡단보도: which crosswalks give less green than an older walker needs. Rule (same as the backend's 신호 연장 권고):
 * needed = length / speed; short when needed > green; extension = ceil(needed - green) seconds.
 * Assumes the walker starts at the beginning of the green and walks at a constant speed (no start-up delay), so it is a lower bound.
 */
import { CROSSWALK_MAX_LENGTH_M, FIELD_SPEED_MAX, FIELD_SPEED_MIN } from "../../config";
import { assessCrossing } from "../field";

export interface CrosswalkRow {
  id: string;
  lat: number;
  lon: number;
  lengthM: number;
  greenSec: number;
  address: string;
}

export interface CrosswalkVerdict extends CrosswalkRow {
  requiredSec: number;
  extensionSec: number;
  short: boolean;
  /** true = length above CROSSWALK_MAX_LENGTH_M: a data error ("자료 확인 필요"), never short and never extended. */
  dataCheck?: boolean;
}

export function clampSpeed(v: number): number {
  if (!Number.isFinite(v)) return FIELD_SPEED_MIN;
  return Math.min(FIELD_SPEED_MAX, Math.max(FIELD_SPEED_MIN, v));
}

/** Rows longer than the cap come back flagged `dataCheck`. Rows with a non-positive length or a negative/non-finite green are skipped, never counted as OK. */
export function assessAll(rows: readonly CrosswalkRow[], speedMps: number): CrosswalkVerdict[] {
  const out: CrosswalkVerdict[] = [];
  for (const r of rows) {
    if (!(r.lengthM > 0) || !(r.greenSec >= 0) || !Number.isFinite(r.lengthM) || !Number.isFinite(r.greenSec)) continue;
    if (r.lengthM > CROSSWALK_MAX_LENGTH_M) {
      out.push({ ...r, requiredSec: Number.NaN, extensionSec: 0, short: false, dataCheck: true });
      continue;
    }
    const a = assessCrossing(r.lengthM, speedMps, r.greenSec);
    out.push({ ...r, requiredSec: a.requiredSec, extensionSec: a.extensionSec, short: !a.finishes });
  }
  return out;
}

/** Short crosswalks, largest needed extension first; ties by address so the order is stable. */
export function shortestFirst(verdicts: readonly CrosswalkVerdict[]): CrosswalkVerdict[] {
  return verdicts.filter((v) => v.short).sort((a, b) => b.extensionSec - a.extensionSec || a.address.localeCompare(b.address, "ko") || a.id.localeCompare(b.id));
}
