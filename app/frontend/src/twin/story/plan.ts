/** Builds the 시연 인물's walk: a building near the real stop -> walk network -> the real bus stop. All inputs are API data. */
import { HOME_MAX_M, HOME_MIN_M, HOME_SNAP_MAX_M, STAND_OFF_M, TARGET_WALK_SEC, WALK_MPS } from "./constants";
import { cumulative, dist, pointAt } from "./geo";
import type { XY } from "./geo";
import { dijkstra, nearestNode, pathTo } from "./graph";
import type { Graph } from "./graph";

export interface StopIn { id: string; name: string; xy: XY }
export interface CrossingIn { id: string; address: string; xy: XY; greenS: number | null; needS: number | null; short: boolean }

export interface StoryPlan {
  stop: StopIn;
  home: XY;
  path: XY[];
  cum: number[];
  lengthM: number;
  /** seconds a real 0.7 m/s walk of this path takes */
  realSec: number;
  /** time-lapse factor shown on screen */
  speedup: number;
  crossing: CrossingIn | null;
}

/** Centre of the bounding box of the building nearest to (x,y), or null if none within maxD. Buildings: flat XY + start indices. */
export function nearestBuildingCenter(pos: Float32Array, starts: Uint32Array, count: number, x: number, y: number, maxD: number): XY | null {
  let best: XY | null = null;
  let bd = maxD;
  const totalV = pos.length / 2;
  for (let b = 0; b < count; b++) {
    const v0 = starts[b] as number;
    const v1 = b + 1 < starts.length ? (starts[b + 1] as number) : totalV;
    let minX = Infinity; let maxX = -Infinity; let minY = Infinity; let maxY = -Infinity;
    for (let v = v0; v < v1; v++) {
      const px = pos[2 * v] as number; const py = pos[2 * v + 1] as number;
      if (px < minX) minX = px; if (px > maxX) maxX = px; if (py < minY) minY = py; if (py > maxY) maxY = py;
    }
    if (maxX < x - maxD || minX > x + maxD || maxY < y - maxD || minY > y + maxD) continue;
    const c: XY = [(minX + maxX) / 2, (minY + maxY) / 2];
    const d = dist(c, [x, y]);
    if (d < bd) { bd = d; best = c; }
  }
  return best;
}

export function planWalk(g: Graph, stop: StopIn, crossing: CrossingIn | null, buildingNear: (x: number, y: number, maxD: number) => XY | null): StoryPlan | null {
  const sNode = nearestNode(g, stop.xy[0], stop.xy[1]).node;
  const tree = dijkstra(g, sNode, HOME_MAX_M);
  let homeNode = -1;
  let far = -1;
  for (const [n, d] of tree.dist) {
    if (d < HOME_MIN_M || d > HOME_MAX_M) continue;
    const e = dist([g.nodeX[n] as number, g.nodeY[n] as number], stop.xy);
    if (e > far) { far = e; homeNode = n; }
  }
  if (homeNode < 0) return null;
  const rev = pathTo(g, tree, sNode, homeNode);
  if (rev === null) return null;
  const walk = [...rev].reverse();
  const nodeXY: XY = walk[0] as XY;
  const home = buildingNear(nodeXY[0], nodeXY[1], HOME_SNAP_MAX_M) ?? nodeXY;
  const lastXY = walk[walk.length - 1] as XY;
  const full: XY[] = [...(dist(home, nodeXY) > 0.5 ? [home] : []), ...walk, ...(dist(lastXY, stop.xy) > 0.5 ? [stop.xy] : [])];
  const fullCum = cumulative(full);
  const total = fullCum[fullCum.length - 1] as number;
  const standAt = Math.max(0, total - STAND_OFF_M);
  const cut = full.filter((_, i) => (fullCum[i] as number) < standAt);
  const path: XY[] = [...cut, pointAt(full, fullCum, standAt).p];
  const cum = cumulative(path);
  const lengthM = cum[cum.length - 1] as number;
  const realSec = lengthM / WALK_MPS;
  return { stop, home, path, cum, lengthM, realSec, speedup: Math.max(1, Math.ceil(realSec / TARGET_WALK_SEC)), crossing };
}

/** Human text for a signal check: real green seconds vs seconds a 0.7 m/s walker needs. */
export function signalText(c: Pick<CrossingIn, "greenS" | "needS" | "short">): { line: string; tone: "ok" | "short" | "none" } {
  if (c.needS === null) return { line: "신호 자료 없음", tone: "none" };
  if (c.greenS === null) return { line: `신호 자료 없음 · 0.7 m/s로 건너는 데 ${c.needS.toFixed(1)}초`, tone: "none" };
  const margin = c.greenS - c.needS;
  return margin < 0 || c.short
    ? { line: `녹색 ${c.greenS.toFixed(0)}초 < 필요 ${c.needS.toFixed(1)}초 · 보행신호 연장 권고(훈련)`, tone: "short" }
    : { line: `녹색 ${c.greenS.toFixed(0)}초 ≥ 필요 ${c.needS.toFixed(1)}초 (0.7 m/s) · 여유 ${margin.toFixed(1)}초`, tone: "ok" };
}
