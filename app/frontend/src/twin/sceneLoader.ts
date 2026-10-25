import { RENDER, TWIN_SGG_CODE } from "./constants";
import { sceneUrls } from "./api";
import type { WorkerRequest, WorkerResponse } from "./sceneWorker";
import type { SceneBuffers } from "./types";

export interface LoadedScene { scene: SceneBuffers; fetchMs: number; url: string }

/** Fetch + parse + triangulate in a dedicated worker; resolves with transferred typed arrays. */
export function loadScene(code: string = TWIN_SGG_CODE): Promise<LoadedScene> {
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL("./sceneWorker.ts", import.meta.url), { type: "module" });
    worker.onmessage = (ev: MessageEvent<WorkerResponse>): void => {
      worker.terminate();
      if (ev.data.ok) resolve({ scene: ev.data.scene, fetchMs: ev.data.fetchMs, url: ev.data.url });
      else reject(new Error(ev.data.error));
    };
    worker.onerror = (ev: ErrorEvent): void => {
      worker.terminate();
      reject(new Error(ev.message));
    };
    const req: WorkerRequest = { urls: sceneUrls(code), style: { low: RENDER.buildingLow, high: RENDER.buildingHigh, heightPercentile: RENDER.heightPercentile } };
    worker.postMessage(req);
  });
}
