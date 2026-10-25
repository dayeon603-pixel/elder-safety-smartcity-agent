import { FPS_WINDOW_MS, PERF_WINDOW } from "../config";

/** Fixed-size window of the most recent samples. */
export class RollingWindow {
  private readonly buf: number[] = [];
  constructor(private readonly size: number = PERF_WINDOW) {
    if (!(size > 0)) throw new RangeError("size must be > 0");
  }
  push(v: number): void {
    this.buf.push(v);
    if (this.buf.length > this.size) this.buf.shift();
  }
  get count(): number {
    return this.buf.length;
  }
  /** Linear-interpolated percentile, p in [0, 100]. null when empty. */
  percentile(p: number): number | null {
    if (this.buf.length === 0) return null;
    if (!(p >= 0 && p <= 100)) throw new RangeError("p must be in [0, 100]");
    const s = [...this.buf].sort((a, b) => a - b);
    const pos = ((s.length - 1) * p) / 100;
    const lo = Math.floor(pos);
    const hi = Math.ceil(pos);
    const a = s[lo] as number;
    return a + ((s[hi] as number) - a) * (pos - lo);
  }
}

/** Frames per second over the last FPS_WINDOW_MS, from completion timestamps (mutates `stamps`). */
export function fpsFrom(stamps: number[], nowMs: number, windowMs: number = FPS_WINDOW_MS): number {
  while (stamps.length > 0 && nowMs - (stamps[0] as number) > windowMs) stamps.shift();
  return (stamps.length * 1000) / windowMs;
}

interface ChromeMemory {
  usedJSHeapSize: number;
}

/** JS heap in MB where the browser exposes performance.memory (Chromium); null elsewhere. */
export function heapUsedMb(): number | null {
  const mem = (performance as unknown as { memory?: ChromeMemory }).memory;
  return mem === undefined ? null : mem.usedJSHeapSize / (1024 * 1024);
}
