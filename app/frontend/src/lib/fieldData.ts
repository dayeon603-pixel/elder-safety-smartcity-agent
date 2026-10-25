import { FIELD_AEDS_URL, FIELD_CROSSWALKS_URL } from "../config";
import type { AedFile, CrosswalkFile } from "./field";
import { parseFallModel } from "./fallModel";
import type { FallModel } from "./fallModel";

async function getJson(url: string): Promise<unknown> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} ${res.status}`);
  return res.json();
}

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}

export async function loadCrosswalks(): Promise<CrosswalkFile> {
  const j = await getJson(FIELD_CROSSWALKS_URL);
  if (!isObj(j) || !Array.isArray(j.crosswalks) || !isObj(j.meta)) throw new Error("횡단보도 자료 형식이 올바르지 않습니다.");
  return j as unknown as CrosswalkFile;
}

export async function loadAeds(): Promise<AedFile> {
  const j = await getJson(FIELD_AEDS_URL);
  if (!isObj(j) || !Array.isArray(j.aeds) || !isObj(j.meta)) throw new Error("AED 자료 형식이 올바르지 않습니다.");
  return j as unknown as AedFile;
}

export async function loadFallModel(url: string): Promise<FallModel> {
  return parseFallModel(await getJson(url));
}
