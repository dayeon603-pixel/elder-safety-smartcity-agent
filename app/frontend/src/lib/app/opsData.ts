/** Loaders for the static /ops shards (see scripts/gen_ops_data.py). Shapes are validated; a bad file raises instead of rendering. */
import { OPS_DATA_BASE, OPS_INDEX_URL } from "../../config";
import type { CrosswalkRow } from "./crossing";
import type { Flag } from "./hours";

export interface AedRow {
  id: string;
  lat: number;
  lon: number;
  org: string;
  place: string;
  address: string;
  /** 8-slot hours cell, see hours.ts */
  hours: string;
}

export interface ShelterRow {
  id: string;
  lat: number;
  lon: number;
  name: string;
  nightOpen: Flag;
  weekendOpen: Flag;
  capacity: number | null;
}

export interface RegionLayers {
  crosswalks: CrosswalkRow[];
  aeds: AedRow[];
  shelters: ShelterRow[];
}

export interface OpsSource {
  name: string;
  url: string;
  file: string;
  rows?: number;
}

export interface OpsIndex {
  generatedAt: string;
  sources: OpsSource[];
  note: string;
  /** sido -> file, and per region code the row counts [crosswalks, aeds, shelters]. */
  sidos: { sido: string; file: string; regions: Record<string, [number, number, number]> }[];
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const isStr = (v: unknown): v is string => typeof v === "string";

async function getJson(url: string): Promise<unknown> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return res.json();
}

export function parseIndex(raw: unknown): OpsIndex {
  if (!isObj(raw) || !isObj(raw.meta) || !Array.isArray(raw.sidos)) throw new Error("운영 지도 자료 목록 형식이 올바르지 않습니다.");
  const meta = raw.meta;
  const sources = Array.isArray(meta.sources) ? meta.sources.filter(isObj).map((s) => ({ name: String(s.name), url: String(s.url), file: String(s.file), ...(isNum(s.rows) ? { rows: s.rows } : {}) })) : [];
  const sidos = raw.sidos.filter(isObj).map((s) => ({ sido: String(s.sido), file: String(s.file), regions: (isObj(s.regions) ? s.regions : {}) as Record<string, [number, number, number]> }));
  return { generatedAt: String(meta.generated_at ?? ""), sources, note: String(meta.note ?? ""), sidos };
}

export function parseRegionLayers(code: string, raw: unknown): RegionLayers {
  const empty: RegionLayers = { crosswalks: [], aeds: [], shelters: [] };
  if (!isObj(raw) || !isObj(raw.regions)) throw new Error("운영 지도 자료 형식이 올바르지 않습니다.");
  const cell = raw.regions[code];
  if (!isObj(cell)) return empty;
  const cw: CrosswalkRow[] = [];
  (Array.isArray(cell.cw) ? cell.cw : []).forEach((r: unknown, i: number) => {
    if (Array.isArray(r) && isNum(r[0]) && isNum(r[1]) && isNum(r[2]) && isNum(r[3])) cw.push({ id: `${code}-cw${i}`, lat: r[0], lon: r[1], lengthM: r[2], greenSec: r[3], address: isStr(r[4]) ? r[4] : "" });
  });
  const aeds: AedRow[] = [];
  (Array.isArray(cell.aed) ? cell.aed : []).forEach((r: unknown, i: number) => {
    if (Array.isArray(r) && isNum(r[0]) && isNum(r[1])) aeds.push({ id: `${code}-aed${i}`, lat: r[0], lon: r[1], org: isStr(r[2]) ? r[2] : "", place: isStr(r[3]) ? r[3] : "", address: isStr(r[4]) ? r[4] : "", hours: isStr(r[5]) ? r[5] : "" });
  });
  const shelters: ShelterRow[] = [];
  (Array.isArray(cell.sh) ? cell.sh : []).forEach((r: unknown, i: number) => {
    if (Array.isArray(r) && isNum(r[0]) && isNum(r[1])) {
      const flag = (v: unknown): Flag => (v === 0 || v === 1 ? v : null);
      shelters.push({ id: `${code}-sh${i}`, lat: r[0], lon: r[1], name: isStr(r[2]) ? r[2] : "", nightOpen: flag(r[3]), weekendOpen: flag(r[4]), capacity: isNum(r[5]) ? r[5] : null });
    }
  });
  return { crosswalks: cw, aeds, shelters };
}

let indexRequest: Promise<OpsIndex> | null = null;
export function loadOpsIndex(): Promise<OpsIndex> {
  indexRequest ??= getJson(OPS_INDEX_URL).then(parseIndex).catch((e: unknown) => {
    indexRequest = null;
    throw e;
  });
  return indexRequest;
}

const shardRequests = new Map<string, Promise<unknown>>();

/** Layers of one 시군구. A region with no rows anywhere returns empty lists (the page then says "자료 없음"). */
export async function loadRegionLayers(sido: string, code: string): Promise<RegionLayers> {
  const index = await loadOpsIndex();
  const entry = index.sidos.find((s) => s.sido === sido);
  if (entry === undefined || entry.regions[code] === undefined) return { crosswalks: [], aeds: [], shelters: [] };
  let req = shardRequests.get(entry.file);
  if (req === undefined) {
    req = getJson(`${OPS_DATA_BASE}/${entry.file}`).catch((e: unknown) => {
      shardRequests.delete(entry.file);
      throw e;
    });
    shardRequests.set(entry.file, req);
  }
  return parseRegionLayers(code, await req);
}
