import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE } from "../../api/client";
import { fetchIdleData, fetchStops, pickStop } from "./api";
import { CLOCK_TICK_MS, IDLE_REFRESH_MS, TERMINAL_STREAM_PATH, TERMINAL_TZ } from "./config";
import { TerminalStream, browserDeps } from "./sse";
import type { ConnectionState, IdleData, StopInfo, StreamEvent } from "./types";

export type StopState = { status: "loading" } | { status: "error"; message: string } | { status: "ready"; stop: StopInfo };

/** Resolve `?stop=` (or the top recommended stop) through the API. */
export function useStop(stopParam: string | null): StopState {
  const [state, setState] = useState<StopState>({ status: "loading" });
  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    fetchStops()
      .then((stops) => {
        if (cancelled) return;
        const stop = pickStop(stops, stopParam);
        setState(stop === null ? { status: "error", message: "정류장을 찾을 수 없습니다" } : { status: "ready", stop });
      })
      .catch(() => {
        if (!cancelled) setState({ status: "error", message: "서버에 연결할 수 없습니다" });
      });
    return () => { cancelled = true; };
  }, [stopParam]);
  return state;
}

export function useStream(stopId: string | null, onEvent: (ev: StreamEvent) => void): ConnectionState {
  const [conn, setConn] = useState<ConnectionState>("connecting");
  const cb = useRef(onEvent);
  cb.current = onEvent;
  useEffect(() => {
    if (stopId === null) return;
    const url = `${API_BASE}${TERMINAL_STREAM_PATH}?stop_id=${encodeURIComponent(stopId)}`;
    const stream = new TerminalStream(url, { onEvent: (e) => cb.current(e), onState: setConn }, browserDeps);
    stream.start();
    return () => stream.stop();
  }, [stopId]);
  return conn;
}

export function useClock(): Date {
  const [now, setNow] = useState<Date>(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), CLOCK_TICK_MS);
    return () => window.clearInterval(id);
  }, []);
  return now;
}

export function formatClock(now: Date): { ampm: string; time: string } {
  const parts = new Intl.DateTimeFormat("ko-KR", { timeZone: TERMINAL_TZ, hour: "numeric", minute: "2-digit", hour12: true }).formatToParts(now);
  const get = (t: string): string => parts.find((p) => p.type === t)?.value ?? "";
  return { ampm: get("dayPeriod"), time: `${get("hour")}:${get("minute")}` };
}

export function useIdleData(stop: StopInfo | null): IdleData | null {
  const [data, setData] = useState<IdleData | null>(null);
  useEffect(() => {
    if (stop === null) return;
    let cancelled = false;
    const load = (): void => {
      fetchIdleData(stop)
        .then((d) => { if (!cancelled) setData(d); })
        .catch(() => undefined); // keep the last good values; the next tick retries
    };
    load();
    const id = window.setInterval(load, IDLE_REFRESH_MS);
    return () => { cancelled = true; window.clearInterval(id); };
  }, [stop]);
  return data;
}

interface WakeLockSentinelLike { release(): Promise<void> }
interface WakeLockNav { wakeLock?: { request(type: "screen"): Promise<WakeLockSentinelLike> } }

export type WakeState = "off" | "on" | "unsupported";

/** Hold a screen wake lock while `active`; re-acquire when the tab becomes visible again. Needs a secure context (https or localhost). */
export function useWakeLock(active: boolean): WakeState {
  const [state, setState] = useState<WakeState>("off");
  useEffect(() => {
    if (!active) return;
    const wl = (navigator as unknown as WakeLockNav).wakeLock;
    if (wl === undefined) { setState("unsupported"); return; }
    let sentinel: WakeLockSentinelLike | null = null;
    let cancelled = false;
    const acquire = (): void => {
      wl.request("screen")
        .then((s) => { if (cancelled) void s.release(); else { sentinel = s; setState("on"); } })
        .catch((err: unknown) => { if (err instanceof DOMException) setState("unsupported"); else throw err; });
    };
    const onVisible = (): void => { if (document.visibilityState === "visible") acquire(); };
    acquire();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisible);
      if (sentinel !== null) void sentinel.release();
    };
  }, [active]);
  return state;
}

export function useFullscreenRequest(): () => Promise<void> {
  return useCallback(async () => {
    const el = document.documentElement;
    if (document.fullscreenElement !== null || typeof el.requestFullscreen !== "function") return;
    try {
      await el.requestFullscreen({ navigationUI: "hide" });
    } catch (err) {
      if (!(err instanceof TypeError || err instanceof DOMException)) throw err; // iPhone Safari refuses: harmless
    }
  }, []);
}
