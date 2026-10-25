/** Terminal channel of the public demo: same-browser tabs talk over BroadcastChannel; other devices only see the recorded latest message. */
export interface BusMessage {
  kind: string; title: string; body: string; audio_url: string | null; stop_id: string | null;
  id: number; published_at: string; drill?: string; meta?: unknown; simulated?: boolean;
}
export const BUS_NAME = "kcf-static-terminal";
export const LAST_STORAGE_KEY = "kcf.static.terminal.last";

type Listener = (m: BusMessage) => void;
export interface StorageLike { getItem(k: string): string | null; setItem(k: string, v: string): void }

export class TerminalBus {
  private current: BusMessage | null;
  private readonly listeners = new Set<Listener>();
  private readonly channel: BroadcastChannel | null;

  constructor(private readonly recorded: BusMessage | null, private readonly storage: StorageLike | null, channelFactory: (() => BroadcastChannel) | null) {
    this.current = this.readStored() ?? recorded;
    this.channel = channelFactory === null ? null : channelFactory();
    if (this.channel !== null) {
      this.channel.onmessage = (ev: MessageEvent<BusMessage>): void => { this.current = ev.data; for (const l of this.listeners) l(ev.data); };
    }
  }

  private readStored(): BusMessage | null {
    try {
      const raw = this.storage?.getItem(LAST_STORAGE_KEY) ?? null;
      return raw === null ? null : (JSON.parse(raw) as BusMessage);
    } catch (e) {
      if (e instanceof SyntaxError || e instanceof DOMException) return null;
      throw e;
    }
  }

  last(): BusMessage | null { return this.current; }

  /** Highest id seen so far (new simulated ids always exceed it, so "id changed" polling works). */
  private nextId(): number { return Math.max(this.current?.id ?? 0, this.recorded?.id ?? 0) + 1; }

  publish(partial: Omit<BusMessage, "id" | "published_at" | "simulated">, now: Date = new Date()): BusMessage {
    const m: BusMessage = { ...partial, id: this.nextId(), published_at: now.toISOString(), simulated: true };
    this.current = m;
    try { this.storage?.setItem(LAST_STORAGE_KEY, JSON.stringify(m)); } catch (e) { if (!(e instanceof DOMException)) throw e; }
    this.channel?.postMessage(m);
    for (const l of this.listeners) l(m);
    return m;
  }

  subscribe(l: Listener): () => void { this.listeners.add(l); return () => { this.listeners.delete(l); }; }
}
