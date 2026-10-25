import type { StageStats, TwinEngine } from "./engine";

/** Exposed as window.__twin for measurement (fps, frame-time percentiles). */
export class PerfProbe {
  constructor(private readonly engine: TwinEngine) {}
  view(): ReturnType<TwinEngine["debugView"]> { return this.engine.debugView(); }
  /** Measurement / screenshot aid: put the camera on a point (e.g. an overhead view of the fall route). */
  focus(lon: number, lat: number, zoom: number, pitch: number, bearing: number): void { this.engine.setViewState({ longitude: lon, latitude: lat, zoom, pitch, bearing }, 0); }
  stats(): StageStats { return this.engine.stats(); }
  /** Orbit the camera for `ms` and resolve with the stats of that window. */
  orbit(ms: number): Promise<StageStats> {
    this.engine.orbit(ms);
    return new Promise((resolve) => setTimeout(() => resolve(this.engine.stats()), ms));
  }
  /** Sample the stage for `ms` while a camera orbit keeps the GPU busy; resolves with the stats of that window. */
  sample(ms: number): Promise<StageStats> {
    return new Promise((resolve) => setTimeout(() => resolve(this.engine.stats()), ms));
  }
}
