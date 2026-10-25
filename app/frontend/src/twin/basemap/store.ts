/** Tiny external store: which ground the map shows (for the attribution line) and the first-paint timing. */
export type GroundMode = "probing" | "satellite" | "osm";
export interface BasemapStatus { mode: GroundMode; firstTileMs: number | null; osmReady: boolean }

let status: BasemapStatus = { mode: "probing", firstTileMs: null, osmReady: false };
const listeners = new Set<() => void>();

export const basemapStatus = {
  get: (): BasemapStatus => status,
  set(patch: Partial<BasemapStatus>): void { status = { ...status, ...patch }; listeners.forEach((f) => f()); },
  subscribe: (fn: () => void): (() => void) => { listeners.add(fn); return () => { listeners.delete(fn); }; },
};

export const ATTRIBUTION: Readonly<Record<GroundMode, string>> = {
  probing: "ⓒ OpenStreetMap · 국토교통부 GIS건물통합정보",
  osm: "ⓒ OpenStreetMap, Protomaps · 국토교통부 GIS건물통합정보",
  satellite: "ⓒ 브이월드 · 국토교통부 GIS건물통합정보 · © OpenStreetMap",
};
