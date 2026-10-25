/** Glue between TwinEngine's extension points (onTick / setExtraLayers / setViewState / setStoryLock) and StoryController. */
import type { Layer, WebMercatorViewport } from "@deck.gl/core";
import type { TwinEngine, ViewState } from "../engine";
import type { StoryController } from "./controller";

export function bindStory(engine: TwinEngine, c: StoryController): () => void {
  let last: Layer[] = [];
  c.attachStage({
    currentView: () => {
      const v = engine.getViewport() as WebMercatorViewport | undefined;
      return v === undefined ? null : { longitude: v.longitude, latitude: v.latitude, zoom: v.zoom, pitch: v.pitch, bearing: v.bearing };
    },
    size: () => { const v = engine.getViewport(); return v === undefined ? null : [v.width, v.height]; },
    project: (lon, lat, z) => {
      const v = engine.getViewport();
      if (v === undefined) return null;
      const p = v.project([lon, lat, z]);
      const x = p[0] as number;
      const y = p[1] as number;
      return Number.isFinite(x) && Number.isFinite(y) && x >= 0 && y >= 0 && x <= v.width && y <= v.height ? [x, y] : null;
    },
  });
  engine.onTick = (now: number): Layer[] | null => {
    const f = c.frame(now);
    engine.setStoryLock(f.controlled, f.street);
    if (f.view !== null) engine.setViewState(f.view as ViewState);
    last = f.layers;
    if (!f.animating) engine.setExtraLayers(f.layers, false);
    return f.layers;
  };
  engine.setExtraLayers(last, true);
  const unsub = c.subscribe(() => engine.setExtraLayers(last, true));
  return () => { unsub(); engine.onTick = null; engine.setExtraLayers([], false); engine.setStoryLock(false); };
}
