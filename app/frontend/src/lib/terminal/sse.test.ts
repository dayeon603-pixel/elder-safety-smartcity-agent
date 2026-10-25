import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CONNECT_TIMEOUT_MS, RECONNECT_BASE_MS, RECONNECT_MAX_MS } from "./config";
import { TerminalStream, backoffMs } from "./sse";
import type { EventSourceLike, StreamDeps } from "./sse";
import type { ConnectionState, StreamEvent } from "./types";

class FakeSource implements EventSourceLike {
  static all: FakeSource[] = [];
  onopen: ((ev: Event) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent<string>) => void) | null = null;
  closed = false;
  listeners = new Map<string, (ev: MessageEvent<string>) => void>();
  constructor(public url: string) { FakeSource.all.push(this); }
  addEventListener(type: string, l: (ev: MessageEvent<string>) => void): void { this.listeners.set(type, l); }
  close(): void { this.closed = true; }
  open(): void { this.onopen?.(new Event("open")); }
  fail(): void { this.onerror?.(new Event("error")); }
  send(obj: unknown): void { this.onmessage?.({ data: typeof obj === "string" ? obj : JSON.stringify(obj) } as MessageEvent<string>); }
}

const HEAT = { kind: "heat", title: "폭염", body: "쉬세요", audio_url: "/cap/x.wav" };

describe("backoffMs", () => {
  it("doubles from the base and caps", () => {
    expect([0, 1, 2, 3].map((n) => backoffMs(n))).toEqual([RECONNECT_BASE_MS, RECONNECT_BASE_MS * 2, RECONNECT_BASE_MS * 4, RECONNECT_BASE_MS * 8]);
    expect(backoffMs(50)).toBe(RECONNECT_MAX_MS);
    expect(backoffMs(-3)).toBe(RECONNECT_BASE_MS);
  });
});

describe("TerminalStream", () => {
  let states: ConnectionState[];
  let events: StreamEvent[];
  let log: string[];
  let stream: TerminalStream;

  beforeEach(() => {
    vi.useFakeTimers();
    FakeSource.all = [];
    states = []; events = []; log = [];
    const deps: StreamDeps = {
      createSource: (url) => new FakeSource(url),
      setTimer: (fn, ms) => setTimeout(fn, ms),
      clearTimer: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
      log: (m) => log.push(m),
    };
    stream = new TerminalStream("/api/terminal/stream?stop_id=S1", { onEvent: (e) => events.push(e), onState: (s) => states.push(s) }, deps);
  });
  afterEach(() => { stream.stop(); vi.useRealTimers(); });

  it("connects, delivers parsed messages and reports state", () => {
    stream.start();
    FakeSource.all[0]!.open();
    FakeSource.all[0]!.send(HEAT);
    expect(states).toEqual(["connecting", "open"]);
    expect(events).toHaveLength(1);
    expect(events[0]!.message.kind).toBe("heat");
    expect(events[0]!.replay).toBe(false);
  });

  it("reconnects with growing backoff and resets after a successful open", () => {
    stream.start();
    FakeSource.all[0]!.fail();
    expect(FakeSource.all[0]!.closed).toBe(true);
    vi.advanceTimersByTime(RECONNECT_BASE_MS - 1);
    expect(FakeSource.all).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeSource.all).toHaveLength(2);
    FakeSource.all[1]!.fail(); // second failure waits 2x
    vi.advanceTimersByTime(RECONNECT_BASE_MS * 2 - 1);
    expect(FakeSource.all).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(FakeSource.all).toHaveLength(3);
    FakeSource.all[2]!.open();
    FakeSource.all[2]!.fail();
    vi.advanceTimersByTime(RECONNECT_BASE_MS); // reset back to base
    expect(FakeSource.all).toHaveLength(4);
    expect(states).toContain("reconnecting");
  });

  it("flags the same message after a reconnect as a replay, but a different one as new", () => {
    stream.start();
    FakeSource.all[0]!.open();
    FakeSource.all[0]!.send(HEAT);
    FakeSource.all[0]!.fail();
    vi.advanceTimersByTime(RECONNECT_BASE_MS);
    FakeSource.all[1]!.open();
    FakeSource.all[1]!.send(HEAT);
    FakeSource.all[1]!.send({ ...HEAT, title: "다른 문구" });
    expect(events.map((e) => e.replay)).toEqual([false, true, false]);
  });

  it("does not treat a repeated identical publish on a healthy connection as a replay", () => {
    stream.start();
    FakeSource.all[0]!.open();
    FakeSource.all[0]!.send(HEAT);
    FakeSource.all[0]!.send(HEAT);
    expect(events.map((e) => e.replay)).toEqual([false, false]);
  });

  it("ignores malformed payloads without throwing", () => {
    stream.start();
    FakeSource.all[0]!.open();
    FakeSource.all[0]!.send("not json");
    FakeSource.all[0]!.send({ kind: "bogus", title: "x" });
    expect(events).toHaveLength(0);
    expect(log).toHaveLength(2);
  });

  it("listens to named events too", () => {
    stream.start();
    FakeSource.all[0]!.open();
    FakeSource.all[0]!.listeners.get("terminal")!({ data: JSON.stringify(HEAT) } as MessageEvent<string>);
    expect(events).toHaveLength(1);
  });

  it("abandons an attempt that gets no response and retries", () => {
    stream.start();
    vi.advanceTimersByTime(CONNECT_TIMEOUT_MS);
    expect(FakeSource.all[0]!.closed).toBe(true);
    expect(states.at(-1)).toBe("reconnecting");
    vi.advanceTimersByTime(RECONNECT_BASE_MS);
    expect(FakeSource.all).toHaveLength(2);
    FakeSource.all[1]!.open();
    vi.advanceTimersByTime(CONNECT_TIMEOUT_MS * 3); // opened: the watchdog must be gone
    expect(FakeSource.all).toHaveLength(2);
    expect(states.at(-1)).toBe("open");
  });

  it("stop() cancels a pending reconnect and closes the source", () => {
    stream.start();
    FakeSource.all[0]!.fail();
    stream.stop();
    vi.advanceTimersByTime(RECONNECT_MAX_MS * 2);
    expect(FakeSource.all).toHaveLength(1);
  });

  it("ignores errors from a source that was already replaced", () => {
    stream.start();
    const first = FakeSource.all[0]!;
    first.fail();
    vi.advanceTimersByTime(RECONNECT_BASE_MS);
    FakeSource.all[1]!.open();
    first.fail(); // stale
    vi.advanceTimersByTime(RECONNECT_MAX_MS);
    expect(FakeSource.all).toHaveLength(2);
  });
});
