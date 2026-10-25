/** V-World satellite photo as the default ground, through the backend proxy (the key never reaches the browser). Falls back to the OSM basemap. */
import type { Layer } from "@deck.gl/core";
import { TileLayer } from "@deck.gl/geo-layers";
import { BitmapLayer } from "@deck.gl/layers";
import { API_BASE } from "../../api/client";
import { dayFactor } from "./palette";
import { basemapStatus } from "./store";

export const SAT_URL = `${API_BASE}/twin/tiles/satellite/{z}/{x}/{y}.jpg`;
const SAT_MIN_ZOOM = 12;
const SAT_MAX_ZOOM = 19;
const TILE_SIZE = 256;
const MAX_REQUESTS = 12;
const PROBE_ZOOM = 15;
const ERRORS_BEFORE_FALLBACK = 4;
const T0 = performance.now();

export const tileXY = (lon: number, lat: number, z: number): { x: number; y: number } => {
  const n = 2 ** z;
  const rad = (lat * Math.PI) / 180;
  return { x: Math.floor(((lon + 180) / 360) * n), y: Math.floor(((1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2) * n) };
};

/** One request for the tile under the scene centre decides the initial ground (503 no_vworld_key / network error -> OSM). */
export async function probeSatellite(center: readonly [number, number]): Promise<boolean> {
  const { x, y } = tileXY(center[0], center[1], PROBE_ZOOM);
  try {
    const res = await fetch(SAT_URL.replace("{z}", String(PROBE_ZOOM)).replace("{x}", String(x)).replace("{y}", String(y)), { cache: "no-store" });
    return res.ok && (res.headers.get("content-type") ?? "").startsWith("image");
  } catch (e) {
    if (e instanceof TypeError) return false;
    throw e;
  }
}

export interface SatHandlers { onFail: () => void }

export function satelliteLayer(day: number, h: SatHandlers): Layer {
  let loaded = 0;
  let errors = 0;
  const k = Math.round(dayFactor(day) * 255);
  return new TileLayer({
    id: "bm-sat",
    data: SAT_URL,
    minZoom: SAT_MIN_ZOOM,
    maxZoom: SAT_MAX_ZOOM,
    tileSize: TILE_SIZE,
    maxRequests: MAX_REQUESTS,
    refinementStrategy: "best-available",
    onTileLoad: () => { loaded++; if (basemapStatus.get().firstTileMs === null) basemapStatus.set({ firstTileMs: performance.now() - T0 }); },
    onTileError: () => { errors++; if (loaded === 0 && errors >= ERRORS_BEFORE_FALLBACK) h.onFail(); },
    renderSubLayers: (props: { id: string; data?: unknown; tile: { boundingBox: [[number, number], [number, number]] } }) => {
      const [[w, s], [e, n]] = props.tile.boundingBox;
      return new BitmapLayer({ id: props.id, image: props.data as never, bounds: [w, s, e, n], tintColor: [k, k, Math.round(k * (0.9 + 0.1 * Math.max(0, Math.min(1, day))))], parameters: { depthCompare: "always", depthWriteEnabled: false } as never, pickable: false } as never);
    },
    updateTriggers: { renderSubLayers: k },
    pickable: false,
  } as never);
}
