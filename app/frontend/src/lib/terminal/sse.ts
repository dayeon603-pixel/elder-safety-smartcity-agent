import { CONNECT_TIMEOUT_MS, RECONNECT_BASE_MS, RECONNECT_MAX_MS, STREAM_EVENT_NAMES } from "./config";
import { messageKey, parseTerminalMessage } from "./message";
import type { ConnectionState, StreamEvent } from "./types";

/** The subset of EventSource this client uses (so tests can inject a fake). */
export interface EventSourceLike {
  onopen: ((ev: Event) => void) | null;
  onerror: ((ev: Event) => void) | null;
  onmessage: ((ev: MessageEvent<string>) => void) | null;
  addEventListener(type: string, listener: (ev: MessageEvent<string>) => void): void;
  close(): void;
}

export interface StreamDeps {
  createSource: (url: string) => EventSourceLike;
  setTimer: (fn: () => void, ms: number) => unknown;
  clearTimer: (handle: unknown) => void;
  log: (msg: string) => void;
}

export interface StreamHandlers {
  onEvent: (ev: StreamEvent) => void;
  onState: (state: ConnectionState) => void;
}

/** Delay before reconnect attempt number `attempt` (0-based): base * 2^attempt, capped. */
export function backoffMs(attempt: number, base = RECONNECT_BASE_MS, max = RECONNECT_MAX_MS): number {
  return Math.min(max, base * 2 ** Math.max(0, attempt));
}

/**
 * SSE client with its own backoff (native EventSource retry is fixed-interval and silent).
 * After a reconnect the server replays its last message; if it equals the one already handled, it is flagged `replay`.
 */
export class TerminalStream {
  private source: EventSourceLike | null = null;
  private timer: unknown = null;
  private connectTimer: unknown = null;
  private attempt = 0;
  private stopped = true;
  private lastKey: string | null = null;
  private hasConnectedBefore = false;
  private afterReconnect = false;

  constructor(
    private readonly url: string,
    private readonly handlers: StreamHandlers,
    private readonly deps: StreamDeps,
  ) {}

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.open();
  }

  stop(): void {
    this.stopped = true;
    if (this.timer !== null) this.deps.clearTimer(this.timer);
    this.timer = null;
    this.clearConnectTimer();
    this.source?.close();
    this.source = null;
  }

  private open(): void {
    this.handlers.onState(this.hasConnectedBefore ? "reconnecting" : "connecting");
    const src = this.deps.createSource(this.url);
    this.source = src;
    this.connectTimer = this.deps.setTimer(() => {
      this.connectTimer = null;
      this.deps.log("terminal stream: no response in time, retrying");
      this.fail(src);
    }, CONNECT_TIMEOUT_MS);
    src.onopen = () => {
      this.clearConnectTimer();
      this.attempt = 0;
      this.afterReconnect = this.hasConnectedBefore;
      this.hasConnectedBefore = true;
      this.handlers.onState("open");
    };
    src.onerror = () => this.fail(src);
    const receive = (ev: MessageEvent<string>): void => this.receive(ev.data);
    src.onmessage = receive;
    for (const name of STREAM_EVENT_NAMES) src.addEventListener(name, receive);
  }

  private receive(data: string): void {
    const message = parseTerminalMessage(data);
    if (message === null) {
      this.deps.log("terminal stream: ignored malformed message");
      return;
    }
    const key = messageKey(message);
    const replay = this.afterReconnect && key === this.lastKey;
    this.afterReconnect = false;
    this.lastKey = key;
    this.handlers.onEvent({ message, replay });
  }

  private clearConnectTimer(): void {
    if (this.connectTimer !== null) this.deps.clearTimer(this.connectTimer);
    this.connectTimer = null;
  }

  private fail(src: EventSourceLike): void {
    if (this.stopped || this.source !== src) return;
    this.clearConnectTimer();
    src.close();
    this.source = null;
    this.handlers.onState("reconnecting");
    const delay = backoffMs(this.attempt);
    this.attempt += 1;
    this.timer = this.deps.setTimer(() => {
      this.timer = null;
      if (!this.stopped) this.open();
    }, delay);
  }
}

export const browserDeps: StreamDeps = {
  createSource: (url) => new EventSource(url) as unknown as EventSourceLike,
  setTimer: (fn, ms) => window.setTimeout(fn, ms),
  clearTimer: (h) => window.clearTimeout(h as number),
  // eslint-disable-next-line no-console -- browser console is the only log sink on a kiosk
  log: (msg) => console.warn(msg),
};
