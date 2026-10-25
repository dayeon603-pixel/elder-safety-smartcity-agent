/**
 * Walk graph built from the scene's road polylines (metres). Edge endpoints that coincide are the same node
 * (the backend emits network nodes as edge endpoints). Pure functions, used both in the sampling worker and on the
 * main thread for the demo person's route.
 */
import type { XY } from "./geo";

export interface Graph {
  nodeX: Float64Array;
  nodeY: Float64Array;
  /** CSR adjacency: edges of node n are adjStart[n]..adjStart[n+1] */
  adjStart: Uint32Array;
  adjEdge: Uint32Array;
  adjTo: Uint32Array;
  edgeLen: Float64Array;
  /** per edge: flat vertex run in `pos` (vertex index range) from the original polyline, oriented a->b */
  edgeV0: Uint32Array;
  edgeV1: Uint32Array;
  edgeA: Uint32Array;
  pos: Float32Array;
  nodeCount: number;
  edgeCount: number;
}

const KEY_SCALE = 100;

export function buildGraph(pos: Float32Array, startIndices: Uint32Array, edgeCount: number): Graph {
  const totalV = pos.length / 2;
  const ids = new Map<number, number>();
  const xs: number[] = [];
  const ys: number[] = [];
  const nodeOf = (v: number): number => {
    const x = pos[2 * v] as number;
    const y = pos[2 * v + 1] as number;
    const key = Math.round(x * KEY_SCALE) * 4_000_003 + Math.round(y * KEY_SCALE);
    let id = ids.get(key);
    if (id === undefined) { id = xs.length; ids.set(key, id); xs.push(x); ys.push(y); }
    return id;
  };
  const edgeA = new Uint32Array(edgeCount);
  const edgeB = new Uint32Array(edgeCount);
  const edgeV0 = new Uint32Array(edgeCount);
  const edgeV1 = new Uint32Array(edgeCount);
  const edgeLen = new Float64Array(edgeCount);
  const deg: number[] = [];
  for (let e = 0; e < edgeCount; e++) {
    const v0 = startIndices[e] as number;
    const v1 = (e + 1 < edgeCount ? (startIndices[e + 1] as number) : totalV) - 1;
    edgeV0[e] = v0;
    edgeV1[e] = v1;
    edgeA[e] = nodeOf(v0);
    edgeB[e] = nodeOf(v1);
    let len = 0;
    for (let v = v0 + 1; v <= v1; v++) len += Math.hypot((pos[2 * v] as number) - (pos[2 * v - 2] as number), (pos[2 * v + 1] as number) - (pos[2 * v - 1] as number));
    edgeLen[e] = len;
    deg[edgeA[e] as number] = (deg[edgeA[e] as number] ?? 0) + 1;
    deg[edgeB[e] as number] = (deg[edgeB[e] as number] ?? 0) + 1;
  }
  const n = xs.length;
  const adjStart = new Uint32Array(n + 1);
  for (let i = 0; i < n; i++) adjStart[i + 1] = (adjStart[i] as number) + (deg[i] ?? 0);
  const fill = adjStart.slice(0, n);
  const adjEdge = new Uint32Array(adjStart[n] as number);
  const adjTo = new Uint32Array(adjStart[n] as number);
  for (let e = 0; e < edgeCount; e++) {
    const a = edgeA[e] as number;
    const b = edgeB[e] as number;
    adjEdge[fill[a] as number] = e; adjTo[fill[a] as number] = b; fill[a] = (fill[a] as number) + 1;
    adjEdge[fill[b] as number] = e; adjTo[fill[b] as number] = a; fill[b] = (fill[b] as number) + 1;
  }
  return { nodeX: Float64Array.from(xs), nodeY: Float64Array.from(ys), adjStart, adjEdge, adjTo, edgeLen, edgeV0, edgeV1, edgeA, pos, nodeCount: n, edgeCount };
}

export function nearestNode(g: Graph, x: number, y: number): { node: number; d: number } {
  let best = -1;
  let bd = Infinity;
  for (let i = 0; i < g.nodeCount; i++) {
    const d = Math.hypot((g.nodeX[i] as number) - x, (g.nodeY[i] as number) - y);
    if (d < bd) { bd = d; best = i; }
  }
  return { node: best, d: bd };
}

/** Polyline of one edge oriented so that it starts at `from`. */
export function edgePolyline(g: Graph, edge: number, from: number): XY[] {
  const out: XY[] = [];
  const v0 = g.edgeV0[edge] as number;
  const v1 = g.edgeV1[edge] as number;
  for (let v = v0; v <= v1; v++) out.push([g.pos[2 * v] as number, g.pos[2 * v + 1] as number]);
  return g.edgeA[edge] === from ? out : out.reverse();
}

class MinHeap {
  private k: number[] = [];
  private v: number[] = [];
  get size(): number { return this.k.length; }
  push(key: number, val: number): void {
    let i = this.k.length;
    this.k.push(key); this.v.push(val);
    while (i > 0) {
      const p = (i - 1) >> 1;
      if ((this.k[p] as number) <= key) break;
      this.k[i] = this.k[p] as number; this.v[i] = this.v[p] as number; i = p;
    }
    this.k[i] = key; this.v[i] = val;
  }
  pop(): [number, number] {
    const rk = this.k[0] as number;
    const rv = this.v[0] as number;
    const lk = this.k.pop() as number;
    const lv = this.v.pop() as number;
    const n = this.k.length;
    if (n > 0) {
      let i = 0;
      for (;;) {
        let c = 2 * i + 1;
        if (c >= n) break;
        if (c + 1 < n && (this.k[c + 1] as number) < (this.k[c] as number)) c++;
        if ((this.k[c] as number) >= lk) break;
        this.k[i] = this.k[c] as number; this.v[i] = this.v[c] as number; i = c;
      }
      this.k[i] = lk; this.v[i] = lv;
    }
    return [rk, rv];
  }
}

export interface Dijkstra { dist: Map<number, number>; prevNode: Map<number, number>; prevEdge: Map<number, number> }

/** Shortest-path tree from `src`, optionally bounded by `maxDist` metres (and stopped early at `target`). */
export function dijkstra(g: Graph, src: number, maxDist = Infinity, target = -1): Dijkstra {
  const dist = new Map<number, number>([[src, 0]]);
  const prevNode = new Map<number, number>();
  const prevEdge = new Map<number, number>();
  const heap = new MinHeap();
  heap.push(0, src);
  const done = new Set<number>();
  while (heap.size > 0) {
    const [d, u] = heap.pop();
    if (done.has(u)) continue;
    done.add(u);
    if (u === target) break;
    for (let k = g.adjStart[u] as number; k < (g.adjStart[u + 1] as number); k++) {
      const w = g.adjTo[k] as number;
      const nd = d + (g.edgeLen[g.adjEdge[k] as number] as number);
      if (nd > maxDist) continue;
      if (nd < (dist.get(w) ?? Infinity)) { dist.set(w, nd); prevNode.set(w, u); prevEdge.set(w, g.adjEdge[k] as number); heap.push(nd, w); }
    }
  }
  return { dist, prevNode, prevEdge };
}

/** Full polyline src -> dst from a Dijkstra tree, or null if unreachable. */
export function pathTo(g: Graph, t: Dijkstra, src: number, dst: number): XY[] | null {
  if (!t.dist.has(dst)) return null;
  const nodes: number[] = [dst];
  const edges: number[] = [];
  let cur = dst;
  while (cur !== src) {
    edges.push(t.prevEdge.get(cur) as number);
    cur = t.prevNode.get(cur) as number;
    nodes.push(cur);
  }
  nodes.reverse(); edges.reverse();
  const out: XY[] = [];
  edges.forEach((e, i) => {
    const seg = edgePolyline(g, e, nodes[i] as number);
    out.push(...(i === 0 ? seg : seg.slice(1)));
  });
  if (out.length === 0) out.push([g.nodeX[src] as number, g.nodeY[src] as number]);
  return out;
}
