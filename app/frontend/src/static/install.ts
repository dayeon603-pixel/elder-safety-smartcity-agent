/** Installs the static backend: patches fetch / EventSource / media src for /api URLs, then shows the permanent demo badge. */
import { API_BASE } from "../api/client";
import { StaticBackend } from "./backend";
import type { Index } from "./backend";
import { BADGE_AGENT_TEXT, BADGE_OTHER_DEVICE_TEXT, BADGE_TEXT, fixtureBase } from "./mode";
import { BUS_NAME } from "./terminalBus";
import type { BusMessage } from "./terminalBus";

const BADGE_ID = "static-demo-badge";
const BADGE_Z = 2147483000;

function apiPath(url: URL): string | null {
  return url.pathname === API_BASE || url.pathname.startsWith(`${API_BASE}/`) ? `${url.pathname.slice(API_BASE.length)}${url.search}` : null;
}

const BADGE_REFRESH_MS = 400;

function mountBadge(): { setAgent: (on: boolean) => void } {
  const el = document.createElement("div");
  el.id = BADGE_ID;
  el.setAttribute("data-testid", "static-badge");
  el.setAttribute("role", "note");
  Object.assign(el.style, {
    position: "fixed", ...(window.innerWidth >= 900 ? { left: "336px", bottom: "108px" } : { left: "50%", bottom: "4px", transform: "translateX(-50%)" }), zIndex: String(BADGE_Z), pointerEvents: "none",
    background: "rgba(11,31,58,.92)", color: "#fff", font: "600 14px/1.4 Pretendard, system-ui, sans-serif", padding: "3px 12px", borderRadius: window.innerWidth >= 900 ? "999px" : "12px", whiteSpace: window.innerWidth >= 900 ? "nowrap" : "normal", maxWidth: "96vw", overflow: "hidden", textOverflow: "ellipsis", textAlign: "center",
  });
  const render = (agent: boolean): void => {
    const onTerminal = /^#\/terminal/.test(location.hash);
    el.style.display = /^#\/m(\?|$)/.test(location.hash) ? "none" : "block"; // the phone page carries its own 훈련 / 무관 notice
    el.textContent = [BADGE_TEXT, agent ? BADGE_AGENT_TEXT : null, onTerminal ? BADGE_OTHER_DEVICE_TEXT : null].filter((x) => x !== null).join("  |  ");
  };
  let agent = false;
  render(agent);
  window.addEventListener("hashchange", () => render(agent));
  window.setInterval(() => render(agent), BADGE_REFRESH_MS); // router redirects use replaceState, which fires no hashchange
  document.body.appendChild(el);
  return { setAgent: (on: boolean): void => { agent = on; render(agent); } };
}

class StaticEventSource {
  onopen: ((ev: Event) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent<string>) => void) | null = null;
  private readonly listeners = new Map<string, ((ev: MessageEvent<string>) => void)[]>();
  private unsub: (() => void) | null = null;
  private closed = false;
  constructor(readonly url: string, backend: StaticBackend) {
    const emit = (m: BusMessage): void => {
      if (this.closed) return;
      const ev = new MessageEvent<string>("message", { data: JSON.stringify(m) });
      this.onmessage?.(ev);
      for (const l of this.listeners.get("message") ?? []) l(ev);
    };
    setTimeout(() => {
      if (this.closed) return;
      this.onopen?.(new Event("open"));
      const last = backend.terminal.last();
      if (last !== null) emit(last);
      this.unsub = backend.terminal.subscribe(emit);
    }, 0);
  }
  addEventListener(type: string, l: (ev: MessageEvent<string>) => void): void { this.listeners.set(type, [...(this.listeners.get(type) ?? []), l]); }
  close(): void { this.closed = true; this.unsub?.(); }
}

export async function installStatic(): Promise<StaticBackend> {
  const origFetch = window.fetch.bind(window);
  const base = fixtureBase();
  const index = (await (await origFetch(`${base}/index.json`)).json()) as Index;
  const badge = mountBadge();
  const backend = new StaticBackend({
    index,
    readJson: async (f) => (await origFetch(`${base}/${f}`)).json() as Promise<unknown>,
    fileUrl: (f) => `${base}/${f}`,
    fetchFile: origFetch,
    terminalUrl: () => new URL(`${import.meta.env.BASE_URL}#/terminal`, location.href).href,
    storage: window.localStorage,
    channelFactory: typeof BroadcastChannel === "function" ? () => new BroadcastChannel(BUS_NAME) : null,
    sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
    onAgentPlayback: (on) => badge.setAgent(on),
    // eslint-disable-next-line no-console -- the browser console is the only sink for fixture-miss diagnostics
    warn: (m) => console.warn(m),
  });
  await backend.init();
  (window as unknown as { __staticBackend?: StaticBackend }).__staticBackend = backend;

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const raw = input instanceof Request ? input.url : String(input);
    const url = new URL(raw, location.href);
    const p = url.origin === location.origin ? apiPath(url) : null;
    if (p === null) return origFetch(input, init);
    const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
    let body: unknown = null;
    if (typeof init?.body === "string") { try { body = JSON.parse(init.body); } catch (e) { if (!(e instanceof SyntaxError)) throw e; } }
    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
    return backend.handle(method, p, body, headers.get("Accept") ?? "");
  };

  (window as unknown as { EventSource: unknown }).EventSource = class extends StaticEventSource { constructor(u: string) { super(u, backend); } };

  const rewrite = (src: string): string => {
    const u = new URL(src, location.href);
    const p = u.origin === location.origin ? apiPath(u) : null;
    if (p === null) return src;
    const e = backend.lookup("GET", p);
    return e === null ? src : `${base}/${e.f}`;
  };
  const desc = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, "src");
  if (desc?.set !== undefined && desc.get !== undefined) {
    const { get, set } = desc;
    Object.defineProperty(HTMLMediaElement.prototype, "src", { configurable: true, enumerable: true, get, set(v: string) { set.call(this, rewrite(String(v))); } });
  }
  (window as unknown as { Audio: unknown }).Audio = function StaticAudio(src?: string): HTMLAudioElement {
    const a = document.createElement("audio");
    if (src !== undefined) a.src = src;
    return a;
  };
  return backend;
}
