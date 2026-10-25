import { ApiError, request } from "../../api/client";
import {
  AED_LIST_LIMIT, FALLBACK_WEATHER_DATE, NEAR_MAX_M, SECONDS_PER_MINUTE, TERMINAL_DEFAULT_GU, TERMINAL_STOPS_LIMIT, TERMINAL_TZ, WALK_SPEED_MPS,
} from "./config";
import type { IdleData, NearPlace, StopInfo } from "./types";

const EARTH_RADIUS_M = 6_371_000;

interface StopsResponse { items: { stop_id: string; name: string; lat: number; lon: number; sgg_code: string }[] }
interface AedResponse { items: { place: string; org: string; lat: number; lon: number; open_now: boolean | null }[]; note: string | null }
interface ShelterResponse { items: { name: string; lat: number; lon: number; open_now: boolean | null }[]; note: string | null }
interface TwinStateResponse { weather?: { feels?: number | null; temp?: number | null } }
interface TwinRouteResponse { aed: { name?: string }; meters: number; seconds: number }
interface HeatRegionResponse { drivers: { apparent_max: number | null; station: { name: string } | null } }

export function haversineM(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const rad = (d: number): number => (d * Math.PI) / 180;
  const a = Math.sin(rad(lat2 - lat1) / 2) ** 2 + Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(rad(lon2 - lon1) / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
}

export function walkMinutes(meters: number, speed = WALK_SPEED_MPS): number {
  return Math.max(1, Math.ceil(meters / speed / SECONDS_PER_MINUTE));
}

/** Nearest item that is open (open_now === true) by straight-line distance. */
export function nearestOpen<T extends { lat: number; lon: number; open_now: boolean | null }>(
  items: readonly T[], lat: number, lon: number,
): { item: T; meters: number } | null {
  let best: { item: T; meters: number } | null = null;
  for (const item of items) {
    if (item.open_now !== true) continue;
    const meters = haversineM(lat, lon, item.lat, item.lon);
    if (best === null || meters < best.meters) best = { item, meters };
  }
  return best;
}

/** KST wall-clock parts for "now". */
export function kstParts(now: Date): { date: string; hour: number; daytype: "weekday" | "sat" | "sun" } {
  const f = new Intl.DateTimeFormat("en-CA", { timeZone: TERMINAL_TZ, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", hourCycle: "h23", weekday: "short" });
  const p = Object.fromEntries(f.formatToParts(now).map((x) => [x.type, x.value]));
  const daytype = p.weekday === "Sat" ? "sat" : p.weekday === "Sun" ? "sun" : "weekday";
  return { date: `${p.year}-${p.month}-${p.day}`, hour: Number(p.hour), daytype };
}

/** Stops of the default gu, API order (top recommended first). */
export async function fetchStops(gu = TERMINAL_DEFAULT_GU): Promise<StopInfo[]> {
  const r = await request<StopsResponse>(`/mobility/stops?gu=${encodeURIComponent(gu)}&limit=${TERMINAL_STOPS_LIMIT}`);
  return r.items.map(({ stop_id, name, lat, lon, sgg_code }) => ({ stop_id, name, lat, lon, sgg_code }));
}

/** `stop` param -> StopInfo; no param -> top recommended stop. null when the id is not in the list. */
export function pickStop(stops: readonly StopInfo[], stopId: string | null): StopInfo | null {
  if (stopId === null) return stops[0] ?? null;
  return stops.find((s) => s.stop_id === stopId) ?? null;
}

async function optional<T>(fn: () => Promise<T>): Promise<T | null> {
  try {
    return await fn();
  } catch (err) {
    if (err instanceof ApiError || err instanceof TypeError) return null;
    throw err;
  }
}

async function fetchWeather(stop: StopInfo, now: Date): Promise<{ feelsC: number | null; label: string | null }> {
  const { date, hour, daytype } = kstParts(now);
  const live = await optional(() => request<TwinStateResponse>(`/twin/${stop.sgg_code}/state?date=${date}&hour=${hour}&daytype=${daytype}`));
  const feels = live?.weather?.feels;
  if (typeof feels === "number") return { feelsC: feels, label: "현재 체감온도" };
  const day = await optional(() => request<HeatRegionResponse>(`/heat/region/${stop.sgg_code}?date=${FALLBACK_WEATHER_DATE}`));
  const apparent = day?.drivers.apparent_max;
  if (typeof apparent === "number") return { feelsC: apparent, label: `${FALLBACK_WEATHER_DATE} ${day?.drivers.station?.name ?? ""} 관측 체감 최고`.replace("  ", " ") };
  return { feelsC: null, label: null };
}

async function fetchAed(stop: StopInfo, now: Date): Promise<{ place: NearPlace | null; note: string | null }> {
  const { date, hour, daytype } = kstParts(now);
  const route = await optional(() => request<TwinRouteResponse>(`/twin/route?lat=${stop.lat}&lon=${stop.lon}&date=${date}&hour=${hour}&daytype=${daytype}`));
  if (route !== null && route.meters <= NEAR_MAX_M) {
    return { place: { name: route.aed.name ?? "AED", meters: Math.round(route.meters), minutes: Math.max(1, Math.ceil(route.seconds / SECONDS_PER_MINUTE)), basis: "route" }, note: null };
  }
  const r = await optional(() => request<AedResponse>(`/aed?sgg_code=${stop.sgg_code}&day=${daytype}&hour=${hour}&limit=${AED_LIST_LIMIT}`));
  if (r === null) return { place: null, note: "불러오지 못했습니다" };
  const best = nearestOpen(r.items, stop.lat, stop.lon);
  if (best === null || best.meters > NEAR_MAX_M) return { place: null, note: r.note ?? "열린 AED 없음" };
  return { place: { name: best.item.place || best.item.org, meters: Math.round(best.meters), minutes: walkMinutes(best.meters), basis: "line" }, note: null };
}

async function fetchShelter(stop: StopInfo, now: Date): Promise<{ place: NearPlace | null; note: string | null }> {
  const { date, hour } = kstParts(now);
  const r = await optional(() => request<ShelterResponse>(`/shelters?sgg_code=${stop.sgg_code}&at=${date}T${String(hour).padStart(2, "0")}:00`));
  if (r === null) return { place: null, note: "불러오지 못했습니다" };
  const best = nearestOpen(r.items, stop.lat, stop.lon);
  if (best === null || best.meters > NEAR_MAX_M) return { place: null, note: r.note?.startsWith("자료 없음") ? "자료 없음" : "열린 쉼터 없음" };
  return { place: { name: best.item.name, meters: Math.round(best.meters), minutes: walkMinutes(best.meters), basis: "line" }, note: null };
}

/** Everything the idle screen shows, all from the API. A failing source degrades to a note, never a made-up number. */
export async function fetchIdleData(stop: StopInfo, now: Date = new Date()): Promise<IdleData> {
  const [w, aed, shelter] = await Promise.all([fetchWeather(stop, now), fetchAed(stop, now), fetchShelter(stop, now)]);
  return { feelsC: w.feelsC, weatherLabel: w.label, aed: aed.place, aedNote: aed.note, shelter: shelter.place, shelterNote: shelter.note };
}

