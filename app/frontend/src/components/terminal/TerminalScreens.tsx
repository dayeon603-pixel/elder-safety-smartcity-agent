import type { CSSProperties, ReactElement } from "react";
import { DRILL_TAG, FLASH_PERIOD_MS, FLASH_PULSES } from "../../lib/terminal/config";
import { CLASS_PRESENTATION, DRILL_PREFIX, DRILL_WATERMARK, classOf, smsText } from "../../lib/terminal/alertClass";
import { KIND_DEFAULT_TEXT, KIND_STYLE } from "../../lib/terminal/present";
import type { ConnectionState, IdleData, NearPlace, StopInfo, TerminalKind, TerminalMessage } from "../../lib/terminal/types";
import type { VoiceState } from "../../lib/terminal/voice";
import "./terminal.css";


const CONN_LABEL: Record<ConnectionState, string> = { open: "연결됨", connecting: "연결 중", reconnecting: "다시 연결 중" };

export function themeVars(kind: TerminalKind | "idle"): CSSProperties {
  const s = KIND_STYLE[kind];
  return { "--tm-bg": s.bg, "--tm-fg": s.fg, "--tm-accent": s.accent, "--tm-flash-period": `${FLASH_PERIOD_MS}ms`, "--tm-flash-count": FLASH_PULSES } as CSSProperties;
}

export function ConnectionDot({ state }: { state: ConnectionState }): ReactElement {
  return <span className="tm-dot" role="status" aria-label={`서버 ${CONN_LABEL[state]}`} data-testid="terminal-conn" data-state={state} />;
}

export function StartScreen({ stopName, onStart }: { stopName: string; onStart: () => void }): ReactElement {
  return (
    <button type="button" className="tm-start" onClick={onStart} data-testid="terminal-start">
      <span className="tm-start-stop">{stopName}</span>
      <span className="tm-start-btn">시작</span>
      <span className="tm-start-hint">한 번 눌러 소리를 켭니다</span>
    </button>
  );
}

const ICONS: Record<TerminalKind, ReactElement> = {
  heat: (<><circle cx="32" cy="32" r="12" /><path d="M32 6v10M32 48v10M6 32h10M48 32h10M13.6 13.6l7 7M43.4 43.4l7 7M13.6 50.4l7-7M43.4 20.6l7-7" /></>),
  fall: (<><path d="M32 8 60 56H4Z" /><path d="M32 26v16M32 48v3" /></>),
  rain: (<><path d="M18 40a12 12 0 0 1 2-23 16 16 0 0 1 30 5 9 9 0 0 1-2 18Z" /><path d="M20 48l-4 8M32 48l-4 8M44 48l-4 8" /></>),
  clear: (<><circle cx="32" cy="32" r="26" /><path d="m19 33 9 9 18-20" /></>),
};

export function KindIcon({ kind }: { kind: TerminalKind }): ReactElement {
  return (
    <svg className="tm-icon" viewBox="0 0 64 64" fill="none" stroke="currentColor" strokeWidth="4.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {ICONS[kind]}
    </svg>
  );
}

/** 정류장 단말(BIT) alert: LED-style amber/red on black, one bounded blink of the class banner, one scrolling ticker line. */
export function AlertScreen({ message, voice, flash, onTap, sender = "관악구(시연)" }: { message: TerminalMessage; voice: VoiceState; flash: boolean; onTap: () => void; sender?: string }): ReactElement {
  const fallback = KIND_DEFAULT_TEXT[message.kind];
  const title = message.title || fallback.title;
  const body = message.body || fallback.body;
  const cls = CLASS_PRESENTATION[classOf(message)];
  const ticker = smsText(sender, title, body);
  return (
    <>
      {flash && <div className="tm-flash" aria-hidden="true" data-testid="terminal-flash" />}
      <div className="tm-alert tm-bit" role="alert" data-testid="terminal-alert" data-kind={message.kind} data-class={classOf(message)}>
        <div className={`tm-bit-banner${flash ? " is-blink" : ""}`} data-testid="terminal-banner">
          <span className="tm-bit-drill">{DRILL_PREFIX}</span>
          <span>{message.kind === "clear" ? "안내 해제" : cls.short}</span>
          <span className="tm-bit-sender">{sender}</span>
        </div>
        <div className="tm-bit-main">
          <KindIcon kind={message.kind} />
          <div className="tm-bit-text">
            <div className="tm-title" data-testid="terminal-title">{title}</div>
            <div className="tm-body" data-testid="terminal-body">{body}</div>
            {(voice === "blocked" || voice === "failed") && <div className="tm-sound">화면을 누르면 음성이 나옵니다</div>}
          </div>
        </div>
        <div className="tm-bit-ticker" aria-hidden="true"><span>{ticker}</span><span>{ticker}</span></div>
        <div className="tm-tag">{DRILL_TAG}</div>
      </div>
      <button type="button" className="tm-replay" onClick={onTap} aria-label="음성 다시 듣기" data-testid="terminal-replay" />
    </>
  );
}

/** Persistent diagonal watermark over the whole phone screen. */
export function DrillWatermark(): ReactElement {
  return (
    <div className="ph-watermark" aria-hidden="true" data-testid="drill-watermark">
      {Array.from({ length: 8 }, (_, i) => <span key={i}>{DRILL_WATERMARK}</span>)}
    </div>
  );
}

export function PhoneStartScreen({ sender, onStart }: { sender: string; onStart: () => void }): ReactElement {
  return (
    <div className="ph-start">
      <DrillWatermark />
      <div className="ph-start-card">
        <div className="ph-start-title">재난문자 수신 시연</div>
        <p className="ph-start-text">{sender} 훈련 문자를 이 화면에서만 보여 줍니다. 실제 문자·OS 알림은 보내지 않습니다.</p>
        <button type="button" className="ph-start-btn" onClick={onStart} data-testid="terminal-start">알림 받기 켜기</button>
        <p className="ph-start-hint">한 번 누르면 알림음과 진동이 켜집니다</p>
      </div>
    </div>
  );
}

export function PhoneIdle({ clock, date, sender }: { clock: { ampm: string; time: string }; date: string; sender: string }): ReactElement {
  return (
    <div className="ph-idle" data-testid="terminal-idle">
      <DrillWatermark />
      <div className="ph-lock-date">{date}</div>
      <div className="ph-lock-time">{clock.time}</div>
      <div className="ph-lock-note">{sender} 훈련 문자 대기 중</div>
    </div>
  );
}

const WARN_ICON = (<svg viewBox="0 0 24 24" aria-hidden="true" className="ph-icon"><path d="M12 2.5 23 21.5H1Z" fill="currentColor" /><path d="M12 9v6M12 17.6v.4" stroke="var(--ph-accent)" strokeWidth="2.4" strokeLinecap="round" /></svg>);

/** Full-screen 재난문자 dialog: class header, sender, time, body, 확인. */
export function PhoneAlert({ message, sender, time, onOk }: { message: TerminalMessage; sender: string; time: string; onOk: () => void }): ReactElement {
  const fallback = KIND_DEFAULT_TEXT[message.kind];
  const cls = CLASS_PRESENTATION[classOf(message)];
  const text = smsText(sender, message.title || fallback.title, message.body || fallback.body);
  return (
    <div className="ph-backdrop" style={{ "--ph-accent": cls.accent, "--ph-on-accent": cls.onAccent } as CSSProperties}>
      <DrillWatermark />
      <div className="ph-dialog" role="alertdialog" aria-modal="true" aria-labelledby="ph-header" aria-describedby="ph-body" data-testid="phone-alert" data-kind={message.kind} data-class={classOf(message)}>
        <div className="ph-header" id="ph-header" data-testid="phone-header">{WARN_ICON}<span>{DRILL_PREFIX} {cls.header}</span></div>
        <div className="ph-meta"><span data-testid="phone-sender">{sender}</span><span>{time}</span></div>
        <div className="ph-body" id="ph-body" data-testid="phone-body">{text}</div>
        <div className="ph-drill">{DRILL_WATERMARK}</div>
        <button type="button" className="ph-ok" onClick={onOk} data-testid="phone-ok">확인</button>
      </div>
    </div>
  );
}

function PlacePanel({ label, place, note, testId }: { label: string; place: NearPlace | null; note: string | null; testId: string }): ReactElement {
  return (
    <section className="tm-panel" data-testid={testId}>
      <div className="tm-label">{label}</div>
      {place === null ? (
        <div className="tm-note">{note ?? "확인 중"}</div>
      ) : (
        <>
          <div className="tm-big">걸어서 {place.minutes}<small>분</small></div>
          <div className="tm-sub">{place.name} · {place.meters.toLocaleString("ko-KR")}m{place.basis === "line" ? " (직선)" : ""}</div>
        </>
      )}
    </section>
  );
}

export function IdleScreen({ stop, clock, data, wakeUnsupported }: { stop: StopInfo; clock: { ampm: string; time: string }; data: IdleData | null; wakeUnsupported: boolean }): ReactElement {
  return (
    <div className="tm-idle" data-testid="terminal-idle">
      <header className="tm-head">
        <div className="tm-stop"><small>정류장 안내</small>{stop.name}</div>
        <div className="tm-clock" aria-label="현재 시각"><b>{clock.time}</b><span>{clock.ampm}</span></div>
      </header>
      <main className="tm-grid">
        <section className="tm-panel tm-panel-temp" data-testid="terminal-temp">
          <div className="tm-label">체감온도</div>
          {data?.feelsC == null ? (
            <div className="tm-note">{data === null ? "확인 중" : "자료 없음"}</div>
          ) : (
            <div className="tm-big">{data.feelsC.toFixed(1)}<small>℃</small></div>
          )}
          {data?.weatherLabel != null && <div className="tm-sub">{data.weatherLabel}</div>}
        </section>
        <PlacePanel label="가까운 AED (열림)" place={data?.aed ?? null} note={data === null ? null : data.aedNote} testId="terminal-aed" />
        <PlacePanel label="가까운 무더위쉼터" place={data?.shelter ?? null} note={data === null ? null : data.shelterNote} testId="terminal-shelter" />
      </main>
      {wakeUnsupported && <div className="tm-foot">이 기기는 화면 켜짐 유지를 지원하지 않습니다. 자동 잠금을 꺼 주세요.</div>}
    </div>
  );
}
