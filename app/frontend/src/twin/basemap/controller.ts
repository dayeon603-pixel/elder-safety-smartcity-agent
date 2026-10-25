/** Loads the baked basemap once; decides satellite vs OSM ground; hands the engine cached layers. */
import type { Layer } from "@deck.gl/core";
import { loadBasemap } from "./data";
import type { Basemap } from "./data";
import { basemapLayers, labelLayers } from "./layers";
import type { BaseCtx } from "./layers";
import { probeSatellite, satelliteLayer } from "./satellite";
import { basemapStatus } from "./store";

export type EngineBaseCtx = Omit<BaseCtx, "photo">;

export class BasemapController {
  private map: Basemap | null = null;
  private key = "";
  private base: Layer[] = [];
  private labels: Layer[] = [];
  private onChange: (() => void) | null = null;
  error: string | null = null;

  get ready(): boolean { return this.map !== null; }

  load(center: readonly [number, number] | null, onChange: () => void): void {
    this.onChange = onChange;
    loadBasemap().then((m) => { this.map = m; this.key = ""; basemapStatus.set({ osmReady: true }); onChange(); }).catch((e: unknown) => { this.error = e instanceof Error ? e.message : String(e); });
    if (center !== null) this.probe(center);
  }

  /** Called with the scene centre once it is known (the first probe decides the ground). */
  probe(center: readonly [number, number]): void {
    if (basemapStatus.get().mode !== "probing") return;
    void probeSatellite(center).then((ok) => { basemapStatus.set({ mode: ok ? "satellite" : "osm" }); this.key = ""; this.onChange?.(); });
  }

  private fallBack(): void {
    if (basemapStatus.get().mode === "osm") return;
    basemapStatus.set({ mode: "osm" });
    this.key = "";
    this.onChange?.();
  }

  /** Layers under the buildings and the label layer drawn above them. */
  layers(c: EngineBaseCtx): { under: Layer[]; over: Layer[] } {
    const m = this.map;
    if (m === null) return { under: [], over: [] };
    const photo = basemapStatus.get().mode === "satellite";
    const ctx: BaseCtx = { ...c, photo };
    const k = `${Math.round(c.day * 20)}|${c.heat ? 1 : 0}|${c.street ? 1 : 0}|${c.boundary.length}|${photo ? 1 : 0}`;
    if (k !== this.key) {
      this.key = k;
      this.base = [...(photo ? [satelliteLayer(c.day, { onFail: () => this.fallBack() })] : []), ...basemapLayers(m, ctx)];
      this.labels = labelLayers(m, ctx);
    }
    return { under: this.base, over: this.labels };
  }
}
