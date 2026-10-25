/// <reference lib="webworker" />
/** Path sampling off the main thread: builds the walk graph once, then answers per-hour sampling requests. */
import { buildGraph } from "./graph";
import type { Graph } from "./graph";
import { nodesByDong, peoplePerDot, planDots, sampleTrips } from "./sampler";
import type { DongGeom, DongRow, TripBuffers } from "./sampler";
import { TRIP_COLOR, TRIP_COLOR_65 } from "./constants";

export type WorkerIn =
  | { kind: "init"; roads: Float32Array; starts: Uint32Array; edges: number; dongs: DongGeom[] }
  | { kind: "sample"; id: number; rows: DongRow[] };
export type WorkerOut =
  | { kind: "ready"; ms: number; nodes: number; edges: number }
  | { kind: "sampled"; id: number; ms: number; perDot: number; dots: number; dots65: number; buffers: TripBuffers }
  | { kind: "error"; message: string };

let graph: Graph | null = null;
let nodes: Map<string, number[]> = new Map();
const ctx = self as unknown as DedicatedWorkerGlobalScope;

ctx.onmessage = (ev: MessageEvent<WorkerIn>): void => {
  const m = ev.data;
  try {
    if (m.kind === "init") {
      const t = performance.now();
      graph = buildGraph(m.roads, m.starts, m.edges);
      nodes = nodesByDong(graph, m.dongs);
      ctx.postMessage({ kind: "ready", ms: performance.now() - t, nodes: graph.nodeCount, edges: graph.edgeCount } satisfies WorkerOut);
    } else if (graph !== null) {
      const t = performance.now();
      const perDot = peoplePerDot(m.rows);
      const plans = planDots(m.rows, perDot);
      const buffers = sampleTrips(graph, nodes, plans, TRIP_COLOR, TRIP_COLOR_65);
      const out: WorkerOut = { kind: "sampled", id: m.id, ms: performance.now() - t, perDot, dots: buffers.count, dots65: buffers.is65.reduce((s, v) => s + v, 0), buffers };
      ctx.postMessage(out, [buffers.startIndices.buffer, buffers.positions.buffer, buffers.timestamps.buffer, buffers.colors.buffer, buffers.is65.buffer]);
    }
  } catch (e) {
    ctx.postMessage({ kind: "error", message: e instanceof Error ? e.message : String(e) } satisfies WorkerOut);
  }
};
