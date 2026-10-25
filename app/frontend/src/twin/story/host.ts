/** Contract between TwinEngine (Frontend A) and the story module. The engine only knows these shapes. */
import type { Layer } from "@deck.gl/core";

export interface StoryViewState { longitude: number; latitude: number; zoom: number; pitch: number; bearing: number; position?: [number, number, number] }
export interface StoryFrame {
  layers: Layer[];
  /** when non-null the engine applies it as the camera this frame */
  view: StoryViewState | null;
  /** keeps the engine redrawing every frame */
  animating: boolean;
  /** true = story owns the camera (mouse controller off) */
  controlled: boolean;
  /** true once the camera is near street level: the control-room overlays are hidden */
  street: boolean;
}
export interface StoryHost { frame(now: number): StoryFrame }
/** What the story needs back from the engine. */
export interface StageApi {
  currentView(): StoryViewState | null;
  project(lon: number, lat: number, z: number): [number, number] | null;
  /** viewport size in CSS px */
  size(): [number, number] | null;
}

/** Fall events reach the story through this bridge (set by the story controller, called from useTwin.triggerFall). */
export const storyBridge: { onFall: ((src: "camera" | "button") => void) | null } = { onFall: null };
