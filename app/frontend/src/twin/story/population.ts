/** GET /twin/{sgg}/population?date=&hour= -> [{adm_cd, adm_nm, total, age65p}] (서울 생활인구, 행정동 x 시간 x 연령). */
import { API_BASE } from "../../api/client";
import { TWIN_SGG_CODE } from "../constants";
import { DONG_URL, MAX_HOUR } from "./constants";
import type { DongGeom, DongRow } from "./sampler";
import type { XY } from "./geo";
import { toXY } from "./geo";
import type { Frame } from "./geo";

const HTTP_NOT_FOUND = 404;
export type PopResult =
  | { status: "ok"; rows: DongRow[]; names: Map<string, string>; source: string | null }
  | { status: "pending" }
  | { status: "error"; message: string };

type Rec = Record<string, unknown>;
const rec = (v: unknown): Rec | null => (typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Rec) : null);
const numOf = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** Tolerant parse: a bare array or an object holding the array under rows/items/dongs/data/population. */
export function parsePopulation(raw: unknown): { rows: DongRow[]; names: Map<string, string>; source: string | null } | null {
  const r = rec(raw);
  const list = Array.isArray(raw) ? raw : r === null ? null : (["rows", "items", "dongs", "data", "population", "result"].map((k) => r[k]).find((x) => Array.isArray(x)) as unknown[] | undefined) ?? null;
  if (list === null) return null;
  const rows: DongRow[] = [];
  const names = new Map<string, string>();
  for (const x of list) {
    const o = rec(x);
    const cd = o === null ? null : (typeof o.adm_cd === "string" ? o.adm_cd : numOf(o.adm_cd) !== null ? String(o.adm_cd) : null);
    const total = o === null ? null : numOf(o.total);
    const a65 = o === null ? null : numOf(o.age65p);
    if (o === null || cd === null || total === null || a65 === null) continue;
    rows.push({ adm_cd: cd, total, age65p: a65 });
    if (typeof o.adm_nm === "string") names.set(cd, o.adm_nm);
  }
  const src = r === null ? null : rec(r.sources) !== null ? JSON.stringify(r.sources) : typeof r.source === "string" ? r.source : null;
  return rows.length === 0 ? null : { rows, names, source: src };
}

export async function loadPopulation(date: string, hour: number, code: string = TWIN_SGG_CODE): Promise<PopResult> {
  try {
    const h = Math.min(MAX_HOUR, Math.max(0, Math.floor(hour)));
    const res = await fetch(`${API_BASE}/twin/${code}/population?date=${date}&hour=${h}`);
    if (res.status === HTTP_NOT_FOUND) return { status: "pending" };
    if (!res.ok) return { status: "error", message: `population ${res.status}` };
    const p = parsePopulation(await res.json());
    return p === null ? { status: "error", message: "population 응답 형식 오류" } : { status: "ok", ...p };
  } catch (e) {
    if (e instanceof TypeError || e instanceof SyntaxError) return { status: "error", message: e.message };
    throw e;
  }
}

/** 행정동 polygons (static asset derived from the official boundary file), projected to the scene's metre frame. */
export async function loadDongs(frame: Frame): Promise<DongGeom[]> {
  const res = await fetch(DONG_URL);
  if (!res.ok) throw new Error(`gwanak_dong ${res.status}`);
  const j = rec(await res.json());
  const list = Array.isArray(j?.dongs) ? (j?.dongs as Rec[]) : [];
  return list.map((d) => ({
    adm_cd: String(d.adm_cd),
    adm_nm: String(d.adm_nm),
    rings: (d.rings as number[][][]).map((ring) => ring.map((p): XY => toXY(frame, p[0] as number, p[1] as number))),
  }));
}

/** Join key: the API may give the 10-digit or the 8-digit 행정동 code. */
export function joinRowsToDongs(rows: readonly DongRow[], dongs: readonly DongGeom[]): DongRow[] {
  const byFull = new Map(dongs.map((d) => [d.adm_cd, d.adm_cd]));
  const by8 = new Map(dongs.map((d) => [d.adm_cd.slice(0, 8), d.adm_cd]));
  return rows.flatMap((r) => {
    const cd = byFull.get(r.adm_cd) ?? by8.get(r.adm_cd.slice(0, 8));
    return cd === undefined ? [] : [{ ...r, adm_cd: cd }];
  });
}
