import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactElement } from "react";
import { useSearchParams } from "react-router-dom";
import { API_BASE } from "../api/client";
import { AlertScreen, ConnectionDot, IdleScreen, PhoneAlert, PhoneIdle, PhoneStartScreen, StartScreen, themeVars } from "../components/terminal/TerminalScreens";
import { CLASS_PRESENTATION, classOf, formatAlertTime, formatLockDate, senderOf } from "../lib/terminal/alertClass";
import { CLEAR_HOLD_MS, FLASH_PERIOD_MS, FLASH_PULSES, REPLAY_SILENT_AFTER_MS, TERMINAL_DEFAULT_GU, TERMINAL_TZ } from "../lib/terminal/config";
import { formatClock, useClock, useFullscreenRequest, useIdleData, useStop, useStream, useWakeLock } from "../lib/terminal/hooks";
import { addressedTo, messageAgeMs, messageKey, resolveAudioUrl } from "../lib/terminal/message";
import { AlertTone } from "../lib/terminal/tone";
import type { StreamEvent, TerminalMessage } from "../lib/terminal/types";
import { VoicePlayer } from "../lib/terminal/voice";
import type { VoiceState } from "../lib/terminal/voice";

const STOP_PARAM = "stop";
/** `?view=phone` renders the 재난문자 phone dialog; anything else is the 정류장 단말 (BIT). */
const VIEW_PARAM = "view";

export type TerminalView = "phone" | "stop";

/** 현장 단말: a bus-stop terminal / 마을방송 speaker. One full-screen page, no chrome, no scrolling. */
export function TerminalPage(): ReactElement {
  const [params] = useSearchParams();
  const stopState = useStop(params.get(STOP_PARAM));
  const stop = stopState.status === "ready" ? stopState.stop : null;
  const view: TerminalView = params.get(VIEW_PARAM) === "phone" ? "phone" : "stop";
  const sender = senderOf(stop, TERMINAL_DEFAULT_GU);
  const toneRef = useRef<AlertTone | null>(null);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const [receivedAt, setReceivedAt] = useState<Date>(() => new Date());

  const [started, setStarted] = useState(false);
  const startedRef = useRef(false);
  const [current, setCurrent] = useState<TerminalMessage | null>(null);
  const [voice, setVoice] = useState<VoiceState>("idle");
  const playerRef = useRef<VoicePlayer | null>(null);
  const clock = useClock();
  const idle = useIdleData(stop);
  const wake = useWakeLock(started);
  const requestFullscreen = useFullscreenRequest();

  const player = useCallback((): VoicePlayer => {
    if (playerRef.current === null) {
      playerRef.current = new VoicePlayer();
      playerRef.current.subscribe(setVoice);
    }
    return playerRef.current;
  }, []);

  const speak = useCallback((m: TerminalMessage): void => {
    const url = m.audio_url === null ? null : resolveAudioUrl(m.audio_url, API_BASE);
    void player().play(url, `${m.title}. ${m.body}`);
  }, [player]);

  const onEvent = useCallback((ev: StreamEvent): void => {
    if (stop === null || !addressedTo(ev.message, stop.stop_id)) return;
    const m = ev.message;
    const age = messageAgeMs(m, Date.now());
    const stale = age !== null && age > REPLAY_SILENT_AFTER_MS;
    // A "clear" that is a replay (or old, or arrived before the operator tapped start) has nothing left to say: show idle.
    const clearIsHistory = m.kind === "clear" && (ev.replay || stale || !startedRef.current || (age !== null && age > CLEAR_HOLD_MS));
    setCurrent(clearIsHistory ? null : m);
    setReceivedAt(m.published_at !== null && !Number.isNaN(Date.parse(m.published_at)) ? new Date(m.published_at) : new Date());
    const silent = ev.replay || !startedRef.current || stale;
    if (m.kind === "clear") player().cancel();
    if (silent) return;
    const cls = classOf(m);
    if (typeof navigator.vibrate === "function") navigator.vibrate([...CLASS_PRESENTATION[cls].vibrate]);
    // Generated alert tone first, then the voice (so the two never talk over each other).
    const tone = toneRef.current;
    const toneDone = tone === null ? Promise.resolve() : tone.play(cls);
    if (m.kind !== "clear") void toneDone.then(() => speak(m));
  }, [stop, speak, player]);

  const conn = useStream(stop?.stop_id ?? null, onEvent);

  // The clear screen hands back to the idle screen after a while.
  useEffect(() => {
    if (current?.kind !== "clear") return;
    const id = window.setTimeout(() => setCurrent(null), CLEAR_HOLD_MS);
    return () => window.clearTimeout(id);
  }, [current]);

  // Flash for a bounded number of pulses per message, then stay calm.
  const [flashOn, setFlashOn] = useState(false);
  useEffect(() => {
    if (current === null || current.kind === "clear") { setFlashOn(false); return; }
    setFlashOn(true);
    const id = window.setTimeout(() => setFlashOn(false), FLASH_PERIOD_MS * FLASH_PULSES);
    return () => window.clearTimeout(id);
  }, [current]);

  const onStart = useCallback((): void => {
    startedRef.current = true;
    setStarted(true);
    void player().unlock();
    if (toneRef.current === null) toneRef.current = new AlertTone();
    toneRef.current.unlock();
    void requestFullscreen();
  }, [player, requestFullscreen]);

  useEffect(() => () => { playerRef.current?.cancel(); toneRef.current?.close(); }, []);

  // No scrolling, ever (iOS rubber-band included).
  useEffect(() => {
    const prev = document.documentElement.style.overflow;
    document.documentElement.style.overflow = "hidden";
    document.body.style.overflow = "hidden";
    return () => { document.documentElement.style.overflow = prev; document.body.style.overflow = ""; };
  }, []);

  const kind = started && current !== null ? current.kind : "idle";
  const theme = themeVars(kind);

  if (stopState.status !== "ready") {
    return (
      <div className="tm-root" style={themeVars("idle")} data-testid="terminal-root">
        <div className="tm-error" role="status">{stopState.status === "loading" ? "불러오는 중" : stopState.message}</div>
      </div>
    );
  }
  if (view === "phone") {
    const show = started && current !== null && dismissed !== messageKey(current);
    return (
      <div className="tm-root ph-root" data-testid="terminal-root" data-view="phone" data-kind={kind}>
        {!started ? (
          <PhoneStartScreen sender={sender} onStart={onStart} />
        ) : show ? (
          <PhoneAlert message={current} sender={sender} time={formatAlertTime(receivedAt, TERMINAL_TZ)} onOk={() => { player().cancel(); setDismissed(messageKey(current)); }} />
        ) : (
          <PhoneIdle clock={formatClock(clock)} date={formatLockDate(clock, TERMINAL_TZ)} sender={sender} />
        )}
        {started && <ConnectionDot state={conn} />}
      </div>
    );
  }
  if (!started) {
    return (
      <div className="tm-root" style={themeVars("idle")} data-testid="terminal-root">
        <StartScreen stopName={stopState.stop.name} onStart={onStart} />
      </div>
    );
  }
  return (
    <div className="tm-root" style={theme} data-testid="terminal-root" data-kind={kind} data-view="stop">
      <ConnectionDot state={conn} />
      {current === null ? (
        <IdleScreen stop={stopState.stop} clock={formatClock(clock)} data={idle} wakeUnsupported={wake === "unsupported"} />
      ) : (
        <AlertScreen message={current} voice={voice} flash={flashOn} sender={sender} onTap={() => speak(current)} />
      )}
    </div>
  );
}
