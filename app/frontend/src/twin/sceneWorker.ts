/// <reference lib="webworker" />
import { buildScene } from "./sceneBuild";
import type { BuildStyle } from "./sceneBuild";
import type { SceneBuffers, SceneWire } from "./types";

export interface WorkerRequest { urls: string[]; style: BuildStyle }
export type WorkerResponse = { ok: true; scene: SceneBuffers; fetchMs: number; url: string } | { ok: false; error: string };

const HTTP_NOT_FOUND = 404;

async function fetchWire(urls: string[]): Promise<{ wire: SceneWire; url: string }> {
  let lastErr = "no url";
  for (const url of urls) {
    const res = await fetch(url);
    if (res.status === HTTP_NOT_FOUND) {
      lastErr = `${url} ${res.status}`;
      continue;
    }
    if (!res.ok) throw new Error(`${url} ${res.status}`);
    return { wire: (await res.json()) as SceneWire, url };
  }
  throw new Error(`장면 자료를 찾지 못함: ${lastErr}`);
}

self.onmessage = (ev: MessageEvent<WorkerRequest>): void => {
  const t0 = performance.now();
  fetchWire(ev.data.urls)
    .then(({ wire, url }) => {
      const fetchMs = performance.now() - t0;
      const scene = buildScene(wire, ev.data.style);
      const b = scene;
      const transfer: Transferable[] = [b.positions.buffer, b.startIndices.buffer, b.indices.buffer, b.vertexValid.buffer, b.elevations.buffer, b.colors.buffer, b.roadPositions.buffer, b.roadStartIndices.buffer, b.boundaryPositions.buffer];
      const msg: WorkerResponse = { ok: true, scene, fetchMs, url };
      (self as DedicatedWorkerGlobalScope).postMessage(msg, transfer);
    })
    .catch((e: unknown) => {
      const msg: WorkerResponse = { ok: false, error: e instanceof Error ? e.message : String(e) };
      (self as DedicatedWorkerGlobalScope).postMessage(msg);
    });
};
