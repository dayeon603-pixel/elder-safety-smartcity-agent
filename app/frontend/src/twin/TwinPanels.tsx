import { createContext, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useApi } from "../api/useApi";
import { FieldCamera, CAM_OFF } from "../components/field/FieldCamera";
import type { CamTelemetry, FallInfo } from "../components/field/FieldCamera";
import { FALL_MODEL_URL } from "../config";
import { loadFallModel } from "../lib/fieldData";
import { CAP_NOTE, EDGE_TAGLINE, FACE_MASK_LABEL, FILE_BADGE, HTEST_NOTE, TRAINING_LABEL, TWIN_DEMO_DATE, TWIN_FIRST_HOUR, TWIN_LAST_HOUR, URFALL_CREDIT, URFALL_FILES, AED_NOTE, ALL_REFUSED_TEXT, DELIVER_CHIP_TEXT, REFUSE_REASON, SHELTER_NOTE, STAGES, STEPS, HOLD_REASON_MAX } from "./constants";
import { INFRA_LABEL } from "./infra";
import { LETTER_RECIPIENT_ONLY, effectFor, letterFor, parseChars } from "./api";
import { saveArtefact } from "./protocol";
import type { TwinCtl } from "./useTwin";
import type { ActionCard, Mode } from "./types";

/** Inside the right panel the zones render without their own header (the tab names the content). */
export const BareCtx = createContext(false);

export const Zone = ({ title, right, children, className = "", testId }: { title: ReactNode; right?: ReactNode; children: ReactNode; className?: string; testId?: string }) => {
  const bare = useContext(BareCtx);
  return (
    <section data-testid={testId} className={`relative flex min-h-0 min-w-0 flex-col overflow-hidden ${bare ? "h-full" : "border border-[var(--cr-line)]"} bg-[var(--cr-panel)] ${className}`}>
      {bare ? (right !== undefined && <div className="flex shrink-0 items-center justify-end gap-2 border-b border-[var(--cr-line)] px-3 py-1">{right}</div>) : (
        <header className="flex h-11 shrink-0 items-center justify-between gap-3 border-b border-[var(--cr-line)] px-4">
          <h2 className="m-0 text-[17px] font-semibold tracking-tight text-[var(--cr-text)]">{title}</h2>
          <div className="flex items-center gap-2">{right}</div>
        </header>
      )}
      <div className="relative min-h-0 flex-1">{children}</div>
    </section>
  );
};

const Btn = ({ children, onClick, active = false, disabled = false, title, testId, tone = "neutral", small = false }: { children: ReactNode; onClick?: () => void; active?: boolean; disabled?: boolean; title?: string; testId?: string; tone?: "neutral" | "bad"; small?: boolean }) => (
  <button
    type="button" onClick={onClick} disabled={disabled} title={title} data-testid={testId} aria-pressed={active}
    className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-[5px] font-medium transition-colors disabled:cursor-not-allowed disabled:[&:not([aria-pressed=true])]:opacity-40 ${small ? "h-10 px-3 text-[14px]" : "h-10 px-3.5 text-[15px]"} ${
      active ? "bg-[var(--cr-accent)] text-[#ffffff]" : tone === "bad" ? "border border-[#F2B8B5] bg-[#FDECEA] text-[#B3261E] hover:bg-[#FDECEA]" : "border border-[#D9DEE5] bg-[#F3F6FA] text-[var(--cr-text)] hover:bg-[#E8EEF6]"
    }`}
  >
    {children}
  </button>
);

export function ModeSwitch({ mode, onChange }: { mode: Mode; onChange: (m: Mode) => void }) {
  return (
    <div role="group" aria-label="기준 전환" className="inline-flex rounded border border-[#D9DEE5] bg-white/95 p-1 " data-testid="mode-switch">
      {(["asis", "ours"] as const).map((m) => (
        <button
          key={m} type="button" aria-pressed={mode === m} onClick={() => onChange(m)} data-testid={`mode-${m}`}
          className={`h-9 rounded px-5 text-[16px] font-semibold transition-colors ${mode === m ? "bg-[var(--cr-accent)] text-[#ffffff]" : "text-[var(--cr-muted)] hover:text-[var(--cr-text)]"}`}
        >
          {m === "asis" ? "현행 기준" : "에이전트"}
        </button>
      ))}
    </div>
  );
}

export function Scoreboard({ ctl }: { ctl: TwinCtl }) {
  const b = ctl.board;
  const big = false;
  if (b === null) return null;
  return (
    <div data-testid="scoreboard" className="rounded border border-[#D9DEE5] bg-white/95 px-4 py-2 ">
      <p className={`m-0 text-[var(--cr-muted)] ${big ? "text-[15px]" : "text-[14px]"}`}>서울 전체 · {b.year === null ? "" : `${b.year} `}TAAS 노인 보행사고 다발지 <b className="num text-[20px] text-[var(--cr-text)]">{b.total}</b>곳 중</p>
      {ctl.state?.seoulZones != null && <p className="m-0 text-[13px] text-[var(--cr-muted)]" data-testid="scoreboard-caption">서울 예시 · 구역이 사고 많은 곳으로 이동 (서울 현행 {ctl.state.seoulZones.current.toLocaleString("ko-KR")} → 에이전트 {ctl.state.seoulZones.ours.toLocaleString("ko-KR")}곳)</p>}
      <p className={`m-0 font-semibold ${big ? "text-[30px] leading-[40px]" : "text-[22px] leading-8"}`}>
        <span className="text-[var(--cr-muted)]">현행 <span className="num text-[var(--cr-text)]">{b.current}</span>곳</span>
        <span className="mx-3 text-[#9AA5B4]">·</span>
        <span className="text-[var(--cr-accent)]">에이전트 <span className="num">{b.ours}</span>곳</span>
      </p>
    </div>
  );
}

/** One big chip per step (top centre of Zone 1). Figures come only from GET /twin/effects; hidden until the API provides them. */
export function EffectChipView({ ctl }: { ctl: TwinCtl }) {
  if ((ctl.actions?.cards.find((c) => c.id === ctl.selectedCard)?.items.length ?? 0) > 0) return null; // the signal note owns the top of the map
  const step = STEPS.find((x) => x.id === ctl.scenario);
  if (step === undefined) return null;
  if (step.id === "s3") {
    return <div className="rounded border border-[#D9DEE5] bg-[#ffffff] px-4 py-1.5" data-testid="effect-chip" data-step="3"><p className="m-0 text-[22px] font-bold text-[var(--cr-accent)]">{DELIVER_CHIP_TEXT}</p></div>;
  }
  const e = effectFor(ctl.effects, step.no, step.effectKeys);
  if (e === null) return null;
  return (
    <div className="max-w-[470px] rounded border border-[#D9DEE5] bg-[#ffffff] px-4 py-1.5 text-center" data-testid="effect-chip" data-step={step.no}>
      <p className="num m-0 flex items-baseline justify-center gap-3">
        <span className="text-[34px] font-extrabold leading-10 text-[var(--cr-accent)]">{e.ours}</span>
        {e.baseline !== null && <span className="text-[16px] text-[var(--cr-muted)]">← {e.baseline}</span>}
      </p>
      {e.scope !== null && <p className="m-0 text-[14px] text-[var(--cr-text)]" data-testid="effect-scope">{e.scope}</p>}
      {e.source !== null && <p className="m-0 text-[13px] text-[var(--cr-muted)]">{e.source}</p>}
    </div>
  );
}

/** "도시 인프라 · CCTV n · 신호 횡단보도 n · 정류장 n · AED n": counts as known; a type with no count is omitted. */
export function InfraLegend({ ctl }: { ctl: TwinCtl }) {
  const parts = (Object.keys(INFRA_LABEL) as (keyof typeof INFRA_LABEL)[]).flatMap((k) => (ctl.infra.counts[k] === null ? [] : [`${INFRA_LABEL[k]} ${ctl.infra.counts[k]}`]));
  if (parts.length === 0) return null;
  return (
    <div className="rounded border border-[#D9DEE5] bg-white/95 px-3 py-1 text-[13px] " data-testid="infra-legend">
      <p className="m-0 text-[var(--cr-text)]"><b>도시 인프라</b> · {parts.join(" · ")}</p>
      {ctl.silverInside !== null && ctl.scenario === "silver" && <p className="m-0 text-[var(--cr-accent)]" data-testid="silver-infra">구역 안 인프라 {ctl.silverInside}</p>}
    </div>
  );
}

/** Honest framing while a 보행신호 연장 card is selected: 관악구's own crosswalks are fine, the example comes from the adjacent 구. */
export function SignalNote({ ctl }: { ctl: TwinCtl }) {
  const card = ctl.actions?.cards.find((c) => c.id === ctl.selectedCard);
  if (card === undefined || card.items.length === 0) return null;
  const first = card.items[0];
  const own = ctl.infra.counts.signal;
  return (
    <div className="absolute left-1/2 top-[150px] z-20 max-w-[640px] -translate-x-1/2 rounded border border-[var(--cr-line)] bg-white px-4 py-2 text-center shadow-lg" data-testid="signal-note">
      <p className="m-0 text-[16px] font-bold text-[var(--cr-text)]">{own === null ? "관악구" : `관악구 신호 횡단보도 ${own}곳은 모두 충분`} · 예시: {card.title.replace(/^인접\s*/, "").split(" · ")[0]} {first?.address.replace(/^서울특별시\s*[가-힣]+구\s*/, "")}</p>
      <p className="m-0 text-[15px] text-[var(--cr-muted)]">빨간 원 = 녹색 시간 &lt; 필요 시간 (0.7 m/s) · 표준데이터 {card.items.length}곳</p>
    </div>
  );
}

/** Map legend during the silver step: exactly the four colour meanings. */
export function SilverLegend({ ctl }: { ctl: TwinCtl }) {
  if (ctl.scenario !== "silver") return null;
  const Row = ({ swatch, text }: { swatch: ReactNode; text: string }) => <li className="flex items-center gap-2.5 text-[15px] font-semibold text-[var(--cr-text)]">{swatch}{text}</li>;
  const dot = (c: string, ring = false): ReactNode => <span className="inline-block h-4 w-4 shrink-0 rounded-full" style={ring ? { border: `3px solid ${c}`, background: `${c}22` } : { background: c, boxShadow: `0 0 0 3px ${c}33` }} />;
  return (
    <div className="absolute left-4 top-4 z-20 rounded border border-[var(--cr-line)] bg-white px-4 py-3 shadow-lg" data-testid="silver-legend">
      <p className="m-0 mb-1.5 text-[16px] font-extrabold text-[var(--cr-accent)]" data-testid="silver-phase">{ctl.mode === "ours" ? "에이전트 재배치" : "현행 실버존"}</p>
      <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
        <Row swatch={<span className="inline-block h-0 w-5 shrink-0 border-t-[3px] border-dashed border-[#78808C]" />} text="회색 점선 = 현행 실버존" />
        <Row swatch={dot("#1E7BD6", true)} text="파랑 = 에이전트 실버존" />
        <Row swatch={dot("#16A34A")} text="초록 = 새로 보호된 사고 다발지" />
        <Row swatch={dot("#D93025")} text="빨강 = 아직 미보호" />
      </ul>
    </div>
  );
}

export function StatusStrip({ ctl }: { ctl: TwinCtl }) {
  const st = ctl.state;
  const h = Math.floor(ctl.hour) % 24;
  const m = Math.floor((ctl.hour % 1) * 60);
  return (
    <div className="rounded border border-[#D9DEE5] bg-white/95 px-4 py-2 " data-testid="clock">
      <p className="m-0 text-[13px] text-[var(--cr-muted)]" data-testid="ref-date">시연 기준시 {TWIN_DEMO_DATE}{(st === null || st.date !== ctl.date || st.hour !== Math.floor(ctl.hour)) && <span className="ml-2 text-[var(--cr-warn)]" data-testid="state-loading">갱신 중…</span>}</p>
      <p className="num m-0 text-[28px] font-semibold leading-9">{ctl.date !== TWIN_DEMO_DATE && <span className="mr-2 text-[16px] text-[var(--cr-warn)]">{ctl.date}</span>}<span className="text-[var(--cr-accent)]">{String(h).padStart(2, "0")}:{String(m).padStart(2, "0")}</span></p>
      <p className="m-0 text-[15px] text-[var(--cr-muted)]">
        {st?.weather?.feels != null ? <>체감 <b className="num text-[var(--cr-text)]">{st.weather.feels}℃</b></> : st?.heat.apparentMaxC != null ? <>체감 최고 <b className="num text-[var(--cr-text)]">{st.heat.apparentMaxC}℃</b></> : "기온 자료 없음"}
        {ctl.rainMm != null && <> · 강수 <b className="num text-[var(--cr-text)]">{ctl.rainMm} mm</b></>}
      </p>
    </div>
  );
}

const Chip = ({ children }: { children: ReactNode }) => (
  <kbd className="ml-1 rounded-[5px] border border-black/10 bg-black/5 px-1.5 text-[13px] font-normal leading-4 text-black/70">{children}</kbd>
);

/** Left panel 「오늘의 업무」: the six steps of the ★ table, vertical, with time and done marks. */
export function StepList({ ctl }: { ctl: TwinCtl }) {
  const signalSel = (ctl.actions?.cards.find((c) => c.id === ctl.selectedCard)?.items.length ?? 0) > 0;
  const HOUR_TEXT: Readonly<Record<string, string>> = { s1: "09:00", s2: "17:00", s3: "17:30", silver: "17:30", s4: "17:40", night: "23:00" };
  return (
    <section aria-label="오늘의 업무">
      <h2 className="m-0 bg-[var(--cr-panel2)] px-4 py-2 text-[17px] font-bold">오늘의 업무</h2>
      <ol className="m-0 list-none p-2" data-testid="step-bar">
        {STEPS.map((st) => {
          const active = ctl.scenario === st.id && !signalSel;
          const done = ctl.stepsDone[st.no];
          return (
            <li key={st.id} className="mb-1">
              <button type="button" data-testid={`scn-${st.id}`} data-done={done ? "1" : "0"} aria-pressed={active} title={st.hint} onClick={() => ctl.runScenario(st.id)}
                className={`flex w-full items-center gap-3 rounded px-3 py-2 text-left text-[16px] transition-colors ${active ? "bg-[var(--cr-accent)] font-bold text-white" : "text-[var(--cr-text)] hover:bg-[#E8EEF6]"}`}>
                <span className={`inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[14px] font-bold ${done ? "bg-[var(--cr-ok)] text-white" : active ? "bg-white text-[var(--cr-accent)]" : "border border-[#9AA5B4] text-[var(--cr-muted)]"}`}>{done ? "✓" : st.no}</span>
                <span className="min-w-0 flex-1 truncate">{st.label}</span>
                <span className={`num text-[13px] ${active ? "text-white/90" : "text-[var(--cr-muted)]"}`}>{HOUR_TEXT[st.id]}</span>
                <Chip>{st.no}</Chip>
              </button>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

const LAYER_DEFS: readonly { id: string; label: string; count: (ctl: TwinCtl) => number | null; badge?: (ctl: TwinCtl) => string; sub?: (ctl: TwinCtl) => string | null }[] = [
  { id: "aed", label: "AED", count: (c) => c.infra.counts.aed },
  { id: "stop", label: "버스 정류장", count: (c) => c.infra.counts.stop, sub: (c) => (c.infra.bitCandidates === null ? null : `안내단말 연계 후보 ${c.infra.bitCandidates}`) },
  { id: "signal", label: "신호 횡단보도", count: (c) => c.infra.counts.signal },
  { id: "silver", label: "실버존", count: (c) => (c.state === null ? null : c.state.silverCurrent.length + c.state.silverOurs.length), badge: (c) => `${c.state?.silverCurrent.length ?? 0} / ${c.state?.silverOurs.length ?? 0}`, sub: () => "현행 / 에이전트" },
  { id: "accident", label: "노인 보행사고 다발지", count: (c) => (c.state === null || c.state.accidents.length === 0 ? null : c.state.accidents.length) },
  { id: "cctv", label: "CCTV", count: (c) => c.infra.counts.cctv },
];

/** Left panel 「주제도」: layer checkboxes with counts, plus the legend. */
export function LayerPanel({ ctl, hidden, onToggle, onAll }: { ctl: TwinCtl; hidden: ReadonlySet<string>; onToggle: (id: string) => void; onAll: (off: boolean) => void }) {
  const defs = LAYER_DEFS.filter((d) => d.count(ctl) !== null);
  const allOff = defs.every((d) => hidden.has(d.id));
  return (
    <section aria-label="주제도" data-testid="layer-panel">
      <div className="flex items-center justify-between bg-[var(--cr-panel2)] px-4 py-2">
        <h2 className="m-0 text-[17px] font-bold">주제도</h2>
        <button type="button" onClick={() => onAll(!allOff)} className="text-[13px] text-[var(--cr-accent)] underline" data-testid="layers-all">{allOff ? "전체 켜기" : "전체 끄기"}</button>
      </div>
      <ul className="m-0 list-none px-3 py-1">
        {defs.map((d) => (
          <li key={d.id}>
            <label className="flex cursor-pointer items-center gap-2 py-1.5 text-[15px]">
              <input type="checkbox" checked={!hidden.has(d.id)} onChange={() => onToggle(d.id)} data-testid={`layer-${d.id}`} className="h-4 w-4 accent-[var(--cr-accent)]" />
              <span className="min-w-0 flex-1"><span className="block truncate">{d.label}</span>{d.sub?.(ctl) != null && <span className="block truncate text-[13px] text-[var(--cr-muted)]" data-testid={`layer-sub-${d.id}`}>{d.sub(ctl)}</span>}</span>
              <span className="num rounded-full bg-[var(--cr-panel2)] px-2 text-[13px] font-semibold text-[var(--cr-muted)]" data-testid={`layer-count-${d.id}`}>{d.badge?.(ctl) ?? d.count(ctl)}</span>
            </label>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function Legend() {
  const Dot = ({ c, ring = false }: { c: string; ring?: boolean }) => <span className="inline-block h-3 w-3 rounded-full" style={ring ? { border: `2px solid ${c}` } : { background: c }} />;
  return (
    <section aria-label="범례" className="border-t border-[var(--cr-line)] px-4 py-3 text-[13px]" data-testid="legend">
      <h2 className="m-0 mb-1.5 text-[15px] font-bold">범례</h2>
      <ul className="m-0 grid list-none grid-cols-2 gap-x-3 gap-y-1 p-0 text-[var(--cr-muted)]">
        <li className="flex items-center gap-2"><Dot c="#3ddc97" />열린 AED</li>
        <li className="flex items-center gap-2"><Dot c="#8A94A3" />닫힌 AED</li>
        <li className="flex items-center gap-2"><Dot c="#4cc2ff" />24시간 개방 요청</li>
        <li className="flex items-center gap-2"><Dot c="#FFB040" />정류장</li>
        <li className="flex items-center gap-2"><Dot c="#8A94A3" ring />현행 실버존</li>
        <li className="flex items-center gap-2"><Dot c="#1E7BD6" ring />에이전트 실버존</li>
        <li className="flex items-center gap-2"><Dot c="#FF5252" />신호 부족 / 사고</li>
        <li className="flex items-center gap-2"><Dot c="#D93025" ring />낙상 의심 지점</li>
      </ul>
    </section>
  );
}

/** Bottom bar: time slider 06–24, play, and the current time. */
export function TimeBar({ ctl }: { ctl: TwinCtl }) {
  return (
    <div className="flex h-[72px] shrink-0 items-center gap-4 border-t border-[var(--cr-line)] bg-white px-5" data-testid="scenario-bar">
      <button type="button" onClick={ctl.togglePlay} data-testid="play-toggle" title="스페이스" className="inline-flex h-10 items-center gap-2 rounded border border-[#C9D1DB] bg-white px-4 text-[16px] font-medium hover:bg-[#F3F6FA]"><span>{ctl.playing ? "❚❚ 정지" : "▶ 재생"}</span><Chip>space</Chip></button>
      <div className="flex min-w-0 flex-1 flex-col justify-center">
        <input type="range" min={TWIN_FIRST_HOUR} max={TWIN_LAST_HOUR} step={0.25} value={Math.min(TWIN_LAST_HOUR, Math.max(TWIN_FIRST_HOUR, ctl.hour))} onChange={(e) => ctl.setHour(Number(e.target.value))} aria-label="시각" data-testid="hour-slider" className="h-2 w-full accent-[var(--cr-accent)]" />
        <div className="num mt-1 flex justify-between text-[13px] text-[var(--cr-muted)]">{[6, 9, 12, 15, 18, 21, 24].map((h) => <span key={h}>{h}시</span>)}</div>
      </div>
      <span className="num w-20 text-right text-[26px] font-bold text-[var(--cr-accent)]">{String(Math.floor(ctl.hour) % 24).padStart(2, "0")}시</span>
    </div>
  );
}

/** Top bar: 기관명, 시연 기준시 + clock, [현행 기준/에이전트], 훈련 badge. */
/** Work-environment chrome: 결재함 badge, notification bell with pending items, and the (demo) user chip. */
function UserArea({ ctl, onJump }: { ctl: TwinCtl; onJump?: (target: "camera" | { card: string }) => void }) {
  const [open, setOpen] = useState(false);
  const pending = (ctl.actions?.cards ?? []).filter((c) => ctl.decisions[c.id] === undefined);
  const fallPending = ctl.fall.status === "pending";
  const count = pending.length + (fallPending ? 1 : 0);
  return (
    <div className="flex items-center gap-2">
      <span className="inline-flex h-9 items-center gap-1.5 rounded border border-[var(--cr-line)] px-2.5 text-[14px]" data-testid="inbox-badge" title="결재함: 이 시연에서 채택·상신한 조치">
        결재함 <b className="num rounded-full bg-[var(--cr-accent)] px-2 text-[13px] text-white" data-testid="inbox-count">{ctl.tally.adopt}</b>
      </span>
      <div className="relative">
        <button type="button" onClick={() => setOpen((v) => !v)} aria-label={`알림 ${count}건`} aria-expanded={open} data-testid="bell" className="relative flex h-9 w-9 items-center justify-center rounded border border-[var(--cr-line)] text-[18px] hover:bg-[#E8EEF6]">
          <span aria-hidden="true">🔔</span>
          {count > 0 && <span className="num absolute -right-1.5 -top-1.5 min-w-[18px] rounded-full bg-[var(--cr-bad)] px-1 text-center text-[13px] font-bold leading-[18px] text-white" data-testid="bell-count">{count}</span>}
        </button>
        {open && (
          <div className="absolute right-0 z-50 mt-1 w-[340px] rounded border border-[var(--cr-line)] bg-white py-1 shadow-lg" data-testid="bell-list" role="menu">
            <p className="m-0 px-3 py-1.5 text-[13px] font-bold text-[var(--cr-muted)]">처리 대기 {count}건</p>
            {count === 0 && <p className="m-0 px-3 py-2 text-[14px] text-[var(--cr-muted)]">대기 중인 항목이 없습니다</p>}
            {fallPending && <button type="button" role="menuitem" onClick={() => { setOpen(false); onJump?.("camera"); }} className="block w-full px-3 py-2 text-left text-[14px] hover:bg-[#FDECEA]"><b className="text-[var(--cr-bad)]">관제 확인 대기</b> · 낙상 의심 사건</button>}
            {pending.map((c) => <button key={c.id} type="button" role="menuitem" onClick={() => { setOpen(false); onJump?.({ card: c.id }); }} className="block w-full truncate px-3 py-2 text-left text-[14px] hover:bg-[#F3F6FA]"><span className="mr-1.5 rounded-[5px] bg-[#FFF3CD] px-1.5 text-[13px] font-semibold text-[#8A5A00]">초안</span>{c.title}</button>)}
          </div>
        )}
      </div>
      <div className="flex items-center gap-2" data-testid="user-chip" title="시연 계정 (실제 개인정보 아님)">
        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-[var(--cr-accent)] text-[14px] font-bold text-white" aria-hidden="true">담</span>
        <span className="text-[13px] leading-4 text-[var(--cr-text)]">주무관<br /><span className="text-[var(--cr-muted)]">(시연 계정)</span></span>
      </div>
    </div>
  );
}

const BEEP_HZ = 880;
const BEEP_MS = 220;
const BEEP_COUNT = 3;

/** Short alarm beep via Web Audio (works after the first user interaction; failures are silent). */
function beep(): void {
  try {
    const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (Ctx === undefined) return;
    const ctx = new Ctx();
    void ctx.resume();
    for (let i = 0; i < BEEP_COUNT; i++) {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.frequency.value = BEEP_HZ;
      g.gain.value = 0.08;
      o.connect(g);
      g.connect(ctx.destination);
      const t = ctx.currentTime + (i * BEEP_MS * 2) / 1000;
      o.start(t);
      o.stop(t + BEEP_MS / 1000);
    }
    setTimeout(() => void ctx.close(), BEEP_MS * 2 * BEEP_COUNT + 200);
  } catch (e) {
    if (!(e instanceof DOMException) && !(e instanceof Error)) throw e;
  }
}

/** Control-room event alarm: red banner with a blinking indicator, location, time and elapsed timer, and a prominent 확인. */
export function FallBanner({ ctl }: { ctl: TwinCtl }) {
  const pending = ctl.fall.status === "pending";
  const [t0, setT0] = useState<number | null>(null);
  const [now, setNow] = useState(0);
  useEffect(() => {
    if (!pending) { setT0(null); return; }
    const start = Date.now();
    setT0(start);
    setNow(start);
    beep();
    const id = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(id);
  }, [pending]);
  if (!pending) return null;
  const sec = t0 === null ? 0 : Math.floor((now - t0) / 1000);
  const h = Math.floor(ctl.hour) % 24;
  const m = Math.floor((ctl.hour % 1) * 60);
  return (
    <div className="shrink-0 border-b-4 border-[#8F1D14] bg-[#D93025] px-4 py-3 text-white" role="alert" data-testid="fall-banner">
      <div className="flex items-center gap-2">
        <span className="twin-blink h-3.5 w-3.5 shrink-0 rounded-full bg-white" aria-hidden="true" />
        <b className="text-[19px] leading-6">낙상 의심 · 관제요원 확인 필요</b>
        <span className="ml-auto rounded-[5px] bg-white/20 px-2 py-0.5 text-[13px] font-bold">훈련</span>
      </div>
      <p className="m-0 mt-1 text-[15px] leading-5" data-testid="fall-banner-loc">{ctl.fall.alertText ?? `서울특별시 관악구 ${ctl.fall.where ?? ""}`}</p>
      <div className="mt-2 flex items-center gap-3">
        <span className="num text-[15px] font-semibold">{String(h).padStart(2, "0")}:{String(m).padStart(2, "0")} 발생 · 경과 <b data-testid="fall-elapsed">{sec}</b>초</span>
        <button type="button" onClick={ctl.confirmFall} data-testid="banner-confirm" className="ml-auto h-10 rounded-[5px] bg-white px-6 text-[17px] font-extrabold text-[#B3261E] hover:bg-[#FDECEA]">확인</button>
      </div>
    </div>
  );
}

/** Breadcrumb under the top bar: 관악구 통합관제 › 오늘의 업무 › <step>. */
export function Breadcrumb({ ctl }: { ctl: TwinCtl }) {
  const step = STEPS.find((x) => x.id === ctl.scenario);
  const signalSel = (ctl.actions?.cards.find((c) => c.id === ctl.selectedCard)?.items.length ?? 0) > 0;
  return (
    <nav aria-label="현재 위치" className="flex h-8 shrink-0 items-center gap-2 border-b border-[var(--cr-line)] bg-[var(--cr-panel2)] px-5 text-[13px] text-[var(--cr-muted)]" data-testid="breadcrumb">
      <span>관악구 통합관제</span><span aria-hidden="true">›</span><span>오늘의 업무</span><span aria-hidden="true">›</span>
      <b className="text-[var(--cr-text)]">{signalSel ? "보행신호 연장 협의 (17:00, 폭염 대응 중 인접 구 예시)" : step === undefined ? "시작 전" : `${step.no}. ${step.label}`}</b>
    </nav>
  );
}

/** Bottom-right toasts (3 s). */
export function Toasts({ ctl }: { ctl: TwinCtl }) {
  return (
    <div className="pointer-events-none absolute bottom-3 right-3 z-30 flex flex-col items-end gap-2" data-testid="toasts" aria-live="polite">
      {ctl.toasts.map((t) => <div key={t.id} className="twin-swap rounded border border-[#0F4C9A] bg-white px-4 py-2 text-[14px] font-semibold text-[var(--cr-text)] shadow-lg" data-testid="toast"><span className="mr-2 text-[var(--cr-accent)]">●</span>{t.text}</div>)}
    </div>
  );
}

export function TopBar({ ctl, children, onQr, onJump }: { ctl: TwinCtl; children?: ReactNode; onQr?: () => void; onJump?: (target: "camera" | { card: string }) => void }) {
  const st = ctl.state;
  const h = Math.floor(ctl.hour) % 24;
  const m = Math.floor((ctl.hour % 1) * 60);
  const loading = st === null || st.date !== ctl.date || st.hour !== Math.floor(ctl.hour);
  return (
    <header className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-[var(--cr-line)] bg-white px-5">
      <div className="flex min-w-0 items-center gap-3">
        <span className="truncate text-[20px] font-bold tracking-tight text-[var(--cr-accent)]">고령자 안전 스마트도시 에이전트</span>
        <span className="rounded-[5px] bg-[var(--cr-panel2)] px-2 py-0.5 text-[13px] font-semibold text-[var(--cr-muted)]">관악구 시연</span>
        <span className="hidden text-[13px] text-[var(--cr-muted)] min-[1700px]:inline" data-testid="edge-line">{EDGE_TAGLINE}</span>
      </div>
      <div className="flex items-baseline gap-3" data-testid="clock">
        <span className="text-[13px] text-[var(--cr-muted)]" data-testid="ref-date">시연 기준시 {TWIN_DEMO_DATE}{loading && <span className="ml-2 text-[var(--cr-warn)]" data-testid="state-loading">갱신 중…</span>}</span>
        {ctl.date !== TWIN_DEMO_DATE && <span className="num text-[15px] font-semibold text-[var(--cr-warn)]">{ctl.date}</span>}
        <span className="num text-[28px] font-bold text-[var(--cr-text)]">{String(h).padStart(2, "0")}:{String(m).padStart(2, "0")}</span>
        <span className="text-[14px] text-[var(--cr-muted)]">{st?.weather?.feels != null ? `체감 ${st.weather.feels}℃` : st?.heat.apparentMaxC != null ? `체감 최고 ${st.heat.apparentMaxC}℃` : ""}{ctl.rainMm != null && ctl.rainMm > 0 ? ` · 강수 ${ctl.rainMm} mm` : ""}</span>
      </div>
      <div className="flex shrink-0 items-center gap-3">
        <ModeSwitch mode={ctl.mode} onChange={ctl.setMode} />
        <span className="rounded border border-[#F2D58A] bg-[#FFF3CD] px-2 py-1 text-[13px] font-semibold text-[#8A5A00]">{TRAINING_LABEL}</span>
        {onQr !== undefined && <button type="button" onClick={onQr} data-testid="qr-button" title="단축키 Q" className="h-9 rounded border border-[#C9D1DB] bg-white px-3 text-[14px] font-medium hover:bg-[#E8EEF6]">단말 연결 QR</button>}
        <UserArea ctl={ctl} onJump={onJump} />
        {children}
      </div>
    </header>
  );
}

// ---------- Zone 2 ----------

const Stat = ({ label, big, sub, tone = "text", grow = 1 }: { label: string; big: ReactNode; sub?: ReactNode; tone?: "text" | "ok" | "bad" | "accent"; grow?: number }) => (
  <div className="flex min-w-0 items-center gap-2.5 rounded border border-[var(--cr-line)] bg-[var(--cr-panel2)] px-2.5 py-1" style={{ flex: grow }}>
    <div className={`num shrink-0 text-[24px] font-semibold leading-7 ${tone === "ok" ? "text-[var(--cr-ok)]" : tone === "bad" ? "text-[var(--cr-bad)]" : tone === "accent" ? "text-[var(--cr-accent)]" : ""}`}>{big}</div>
    <div className="min-w-0">
      <p className="m-0 truncate text-[13px] font-medium text-[var(--cr-text)]">{label}</p>
      <p className="m-0 truncate text-[13px] text-[var(--cr-muted)]" title={typeof sub === "string" ? sub : undefined}>{sub}</p>
    </div>
  </div>
);

const flagWord = (v: boolean | null, yes: string, no: string): string => (v === null ? "자료 없음" : v ? yes : no);

const draftTag = (label: string | null, limit: number): string => (label ?? `${limit}자형`).replace(/^\d+자형?\s*/, "").replace(/^\(|\)$/g, "") || `${limit}자형`;

function ActionCardView({ c, ctl, onWhy, next }: { c: ActionCard; ctl: TwinCtl; onWhy: () => void; next: boolean }) {
  const d = ctl.decisions[c.id];
  const stage = ctl.stages[c.id] ?? 1;
  const held = ctl.holds[c.id];
  const refused = c.kind === "aed" && held === REFUSE_REASON;
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState("");
  const count = parseChars(c.chars);
  const reasonRef = useRef<HTMLInputElement>(null);
  useEffect(() => { if (asking) reasonRef.current?.focus(); }, [asking]);
  const listenable = c.audioUrl !== null || c.id === "voice";
  return (
    <article className={`flex min-w-0 flex-col gap-0.5 rounded border bg-[var(--cr-panel2)] p-2.5 ${c.items.length > 0 && ctl.selectedCard === c.id ? "border-[3px] border-[#D93025] bg-[#FDECEA]" : next ? "border-2 border-[var(--cr-accent)] bg-[#E8F1FB]" : ctl.selectedCard === c.id ? "border-[var(--cr-accent)]" : "border-[var(--cr-line)]"} ${refused ? "" : ""}`} data-testid="action-card" data-refused={refused ? "1" : "0"} data-next={next ? "1" : "0"}>
      <div className="flex shrink-0 items-start justify-between gap-1">
        <h3 className="m-0 line-clamp-2 min-w-0 text-[16px] font-bold leading-5"><button type="button" onClick={() => ctl.selectCard(c.id)} className="m-0 max-w-full truncate border-0 bg-transparent p-0 text-left font-semibold text-inherit hover:underline" data-testid="select-card" title={`${c.title} · 공문 미리보기`}>{c.title}</button>{c.id === "route" && <span className="ml-1.5 rounded-[5px] border border-[#B8C2CF] px-1 text-[13px] font-normal text-[var(--cr-muted)]">참고용</span>}</h3>
        <span className={`shrink-0 rounded-[5px] px-2 py-0.5 text-[13px] font-bold ${held !== undefined ? (refused ? "bg-[#FDECEA] text-[#B3261E]" : "bg-[#FFF3CD] text-[#8A5A00]") : stage >= 3 ? "bg-[#E3F4EA] text-[#1B6B3A]" : stage === 2 ? "bg-[#E8F1FB] text-[#0F4C9A]" : "bg-[#EEF1F5] text-[#3F4A5A]"}`} data-testid="status-badge">{held !== undefined ? (refused ? "거절됨" : "보류") : stage >= 3 ? "결재 완료(시연)" : stage === 2 ? "결재 대기" : "초안"}</span>
        <button type="button" onClick={onWhy} data-testid="why" className="shrink-0 rounded-[5px] border border-[#D9DEE5] bg-transparent px-1.5 text-[13px] text-[var(--cr-muted)] hover:text-[var(--cr-text)]">왜?</button>
      </div>
      <div className="flex flex-col gap-0.5">
        {c.destination !== null && <p className="m-0 shrink-0 truncate text-[13px] text-[var(--cr-text)]" data-testid="destination" title={`${c.destination}${c.destinationOrg !== null ? ` · ${c.destinationOrg}` : ""}`}>전달 대상: {c.destination}</p>}
        <ol className="m-0 flex shrink-0 list-none gap-0.5 p-0 text-[13px] whitespace-nowrap" data-testid="approval-chip" data-stage={held !== undefined ? "hold" : stage}>
          {STAGES.map((label, i) => (
            <li key={label} className={`rounded-[5px] px-1 py-px ${held === undefined && stage >= i + 1 ? "bg-[var(--cr-accent)] text-[#ffffff]" : "bg-white/5 text-[var(--cr-muted)]"}`}>{i > 0 && "→ "}{label}</li>
          ))}
        </ol>
        {ctl.times[c.id] !== undefined && (
          <p className="num m-0 text-[13px] text-[var(--cr-muted)]" data-testid="card-timeline">
            초안 {ctl.times[c.id]?.draft}{ctl.times[c.id]?.sent !== undefined && <> → 상신 <b className="text-[var(--cr-text)]">{ctl.times[c.id]?.sent}</b></>}{ctl.times[c.id]?.approved !== undefined && <> → 결재(시연) <b className="text-[var(--cr-text)]">{ctl.times[c.id]?.approved}</b></>}
          </p>
        )}
        {refused && <p className="m-0 text-[13px] font-semibold text-[var(--cr-bad)]" data-testid="refused-line">거절됨 → 다음 후보 제안</p>}
        {next && <p className="m-0 text-[13px] font-semibold text-[var(--cr-accent)]" data-testid="next-candidate">다음 후보{c.gain !== null ? ` · 밤 2분 도달 65세 이상 +${c.gain.toLocaleString("ko-KR")}명` : ""}</p>}
        {held !== undefined && !refused && <p className="m-0 shrink-0 truncate text-[13px] text-[var(--cr-warn)]" data-testid="hold-line">보류 · 사유 기록: {held === "" ? "(사유 없음)" : held}</p>}
        {d !== undefined && held === undefined && (
          <p className="m-0 shrink-0 truncate text-[13px] text-[var(--cr-ok)]" data-testid="audit-line">{stage >= 3 ? "전달 대기" : "상신됨"} · 변조 탐지 기록 #{d.rec.id}{d.audit?.head ? ` · ${d.audit.head.slice(0, 12)}` : ""}</p>
        )}
        {c.flowNote !== null && <p className="m-0 shrink-0 truncate text-[13px] text-[var(--cr-muted)]" data-testid="flow-note" title={c.flowNote}>승인 흐름: {c.flowNote}</p>}
        {c.kind === "aed" && <p className="m-0 shrink-0 truncate text-[13px] text-[var(--cr-muted)]">{AED_NOTE}</p>}
        {c.drafts.length > 0
          ? <p className="num m-0 shrink-0 truncate text-[13px] font-semibold" data-testid="cbs-count" title="초안 글자 수">{c.drafts.map((x) => `${x.length}/${x.limit}자 (${draftTag(x.label, x.limit)})`).join(" · ")}</p>
          : count !== null && <p className={`num m-0 shrink-0 text-[13px] font-semibold ${count.length > count.limit ? "text-[var(--cr-bad)]" : ""}`} data-testid="cbs-count">{count.length} / {count.limit}자</p>}
        {c.drafts.length === 0 && <p className="m-0 line-clamp-2 min-h-0 overflow-hidden text-ellipsis text-[13px] max-[1700px]:hidden leading-[17px] text-[var(--cr-muted)]">{c.summary}</p>}
      </div>
      <div className="flex shrink-0 flex-wrap gap-1 pt-0.5">
        {asking ? (
          <form className="flex w-full gap-1" onSubmit={(e) => { e.preventDefault(); ctl.hold(c.id, reason.trim()); setAsking(false); }}>
            {c.kind === "aed" && <button type="button" onClick={() => { ctl.hold(c.id, REFUSE_REASON); setAsking(false); }} data-testid="refuse-chip" className="shrink-0 rounded-full border border-[#F2B8B5] bg-[#FDECEA] px-3 text-[13px] font-semibold text-[#B3261E]">{REFUSE_REASON}</button>}
            <input ref={reasonRef} value={reason} maxLength={HOLD_REASON_MAX} onChange={(e) => setReason(e.target.value)} placeholder="보류 사유" aria-label="보류 사유" data-testid="hold-reason" className="min-w-0 flex-1 rounded-[5px] border border-[#C9D1DB] bg-[#ffffff] px-2 text-[14px] text-[var(--cr-text)] outline-none focus:border-[var(--cr-accent)]" />
            <Btn small onClick={() => { ctl.hold(c.id, reason.trim()); setAsking(false); }} testId="hold-save">기록</Btn>
          </form>
        ) : (
          <>
            <Btn small onClick={() => ctl.decide(c.id, "채택")} testId="adopt" active={stage >= 2 && held === undefined} disabled={stage >= 2 && held === undefined}>주무관 채택(상신)</Btn>
            <Btn small onClick={() => setAsking(true)} testId="hold" active={held !== undefined}>보류(사유 기록)</Btn>
            {stage === 2 && held === undefined && <Btn small onClick={() => ctl.approve(c.id)} testId="approve">과장 결재(시연)</Btn>}
            {listenable && <Btn small onClick={() => ctl.listen(c.id)} testId="listen">▶ 듣기</Btn>}
          </>
        )}
      </div>
    </article>
  );
}

export function ActionsZone({ ctl }: { ctl: TwinCtl }) {
  const st = ctl.state;
  const a = ctl.actions;
  const [why, setWhy] = useState<string | null>(null);
  const whyCard = a?.cards.find((c) => c.id === why) ?? null;
  const aedPct = ctl.mode === "ours" ? st?.aedOpenPctAfter ?? st?.aedOpenPct ?? null : st?.aedOpenPct ?? null;
  const noShelter = st !== null && st.shelters.length === 0;
  const aedCards = (a?.cards ?? []).filter((c) => c.kind === "aed");
  const refusedCount = aedCards.filter((c) => ctl.holds[c.id] === REFUSE_REASON).length;
  // after a refusal, the highest-ranked candidate that has not been refused or decided is the proposed next one
  const nextAed = refusedCount > 0 ? aedCards.find((c) => ctl.holds[c.id] !== REFUSE_REASON && ctl.decisions[c.id] === undefined) ?? null : null;
  return (
    <Zone title={<>행정 조치 <span className="ml-2 text-[14px] font-normal text-[var(--cr-muted)]">{ctl.date} · {a?.night ? "야간 AED 공백" : "폭염"} · {TRAINING_LABEL}</span></>} testId="zone-actions">
      <div className="flex h-full min-h-0 flex-col gap-1.5 overflow-hidden p-2.5">
        <div className="flex shrink-0 flex-col gap-1.5" data-testid="result-cards">
          <Stat grow={1.7} big={<span className="text-[14.5px] leading-5">현행 특보 기준: <b>{flagWord(st?.heat.b1Alert ?? null, "대상", "대상 아님")}</b><br /><span className="text-[var(--cr-accent)]">에이전트 권고: <b>{flagWord(st?.heat.oursAlert ?? null, "사전 조치", "조치 없음")}</b></span></span>} label="폭염 특보 판단" sub={st?.heat.rankText ?? (st?.heat.rank != null ? `위험 순위 ${st.heat.rank}위` : "")} />
          <Stat label={`열린 AED · ${Math.floor(ctl.hour) % 24}시`} tone={aedPct != null && aedPct < 50 ? "bad" : "ok"} big={aedPct != null ? `${aedPct.toFixed(0)}%` : "–"} sub={`${st?.aedOpenCount != null ? `${st.aedOpenCount} / ${st.aedTotal}대 · ` : ""}${AED_NOTE}`} />
          <Stat grow={1.2} label="오늘의 조치" tone="accent" big={a === null ? "–" : `${a.cards.length}건`} sub={noShelter ? SHELTER_NOTE : a?.night ? "닫힌 AED의 24시간 개방 요청(초안)" : st?.heat.expectedCasesText ?? ""} />
        </div>
        {ctl.actionsError !== null && <p className="m-0 text-[15px] text-[var(--cr-bad)]">조치 자료를 불러오지 못함: {ctl.actionsError}</p>}
        <div key={`${a?.night ? "night" : "day"}-${a?.cards.map((c) => c.id).join("|") ?? ""}`} className="twin-swap grid min-h-0 flex-1 grid-cols-1 content-start gap-2 overflow-auto pr-0.5" data-testid="action-cards" data-night={a?.night ? "1" : "0"}>
          {aedCards.length > 0 && refusedCount === aedCards.length && <p className="m-0 rounded border border-[#F2B8B5] bg-[#FDECEA] px-3 py-2 text-[14px] font-bold text-[#B3261E]" data-testid="all-refused">{ALL_REFUSED_TEXT}</p>}
          {(a?.cards ?? []).map((c) => <ActionCardView key={c.id} c={c} ctl={ctl} onWhy={() => setWhy(c.id)} next={nextAed?.id === c.id} />)}
        </div>
        <p className="num m-0 shrink-0 border-t border-[var(--cr-line)] pt-1 text-[14px] text-[var(--cr-muted)]" data-testid="tally">
          오늘 채택 <b className="text-[var(--cr-text)]">{ctl.tally.adopt}</b> · 보류 <b className="text-[var(--cr-text)]">{ctl.tally.hold}</b> · 근거 기록 <b className="text-[var(--cr-text)]">{ctl.tally.entries ?? "–"}</b>건{ctl.tally.valid === null ? "" : ` · 변조 탐지 기록 ${ctl.tally.valid ? "정상" : "불일치"}`}
        </p>
      </div>
      {whyCard !== null && (
        <div className="absolute inset-0 z-10 overflow-auto bg-[var(--cr-panel)] p-4" data-testid="why-panel">
          <div className="mb-2 flex items-center justify-between">
            <h3 className="m-0 text-[18px] font-semibold">{whyCard.title} · 근거</h3>
            <Btn onClick={() => setWhy(null)}>닫기</Btn>
          </div>
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {whyCard.evidence.map((r, i) => (
              <li key={`${r.label}-${i}`} className="rounded border border-[var(--cr-line)] bg-[var(--cr-panel2)] px-3 py-2" data-testid="why-row">
                <p className="m-0 text-[14px] font-bold text-[var(--cr-text)]">{r.label}</p>
                <p className="num m-0 break-words text-[15px] text-[var(--cr-text)]">{r.value}</p>
                {r.source !== null && <p className="m-0 break-words text-[13px] text-[var(--cr-muted)]">{r.source}</p>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Zone>
  );
}

// ---------- Zone 3 ----------

/** Lines that mark drill data inside the real bytes (highlighted on screen only; the saved file is untouched). */
const MARK_RE = /"code": "HTEST"|"display": "test health data"|<status>Exercise<\/status>/;

/** 공문 / 문안 미리보기 of the selected output (수신, 제목, 본문, 근거, 생성 시각), read from the API's document_url. */
function Preview({ ctl }: { ctl: TwinCtl }) {
  const a = ctl.actions;
  const card = a?.cards.find((c) => c.id === ctl.selectedCard) ?? a?.cards[0];
  if (a === null || card === undefined) return <p className="m-0 text-[var(--cr-muted)]" data-testid="preview-empty">조치를 고르면 공문·문안 미리보기가 여기에 표시됩니다.</p>;
  const L = letterFor(card.id, ctl.letter);
  const own = L !== null && !LETTER_RECIPIENT_ONLY.has(card.id);
  const to = L?.to ?? card.destination;
  const org = L?.org ?? card.destinationOrg;
  const paragraphs = own && L.paragraphs.length > 0 ? L.paragraphs : card.drafts.length > 0 ? card.drafts.map((x) => `${x.text}  (${x.length}/${x.limit}자 · ${draftTag(x.label, x.limit)})`) : [card.summary];
  const evidence = card.evidenceText !== null ? [card.evidenceText] : own && L.evidence.length > 0 ? L.evidence.slice(0, 5).map((e) => `${e.item} ${e.value}`) : card.evidence.slice(0, 5).map((r) => `${r.label} ${r.value}`);
  return (
    <div data-testid="preview" className="text-[14.5px] leading-[22px]">
      <dl className="m-0 grid grid-cols-[4rem_1fr] gap-x-3 gap-y-0.5">
        <dt className="text-[var(--cr-muted)]">수신</dt><dd className="m-0" data-testid="pv-to">{to ?? "전달 대상 확인 중"}{org !== null && <span className="ml-2 text-[13px] text-[var(--cr-muted)]">{org}</span>}</dd>
        <dt className="text-[var(--cr-muted)]">제목</dt><dd className="m-0 font-semibold" data-testid="pv-title">{own ? L.title ?? card.title : card.title}</dd>
        <dt className="text-[var(--cr-muted)]">본문</dt>
        <dd className="m-0">{paragraphs.map((t, i) => <p key={i} className="m-0 mb-0.5 whitespace-pre-wrap">{t}</p>)}</dd>
        <dt className="text-[var(--cr-muted)]">근거</dt>
        <dd className="m-0 text-[13px] text-[var(--cr-muted)]" data-testid="pv-evidence">{evidence.join(" · ") || "근거 자료 없음"}</dd>
        <dt className="text-[var(--cr-muted)]">생성 시각</dt><dd className="num m-0 text-[var(--cr-muted)]">{L?.createdAt ?? `${a.date} ${a.generatedAt}`}</dd>
      </dl>
      <p className="m-0 mt-1 text-[13px] text-[var(--cr-warn)]">{L?.never ?? `${TRAINING_LABEL} · 주무관 검토용 초안`}</p>
    </div>
  );
}

export function ProtocolZone({ ctl, view }: { ctl: TwinCtl; view: "doc" | "spec" }) {
  const end = useRef<HTMLDivElement>(null);
  const spec = view === "spec";
  useEffect(() => { end.current?.scrollIntoView?.({ block: "end" }); }, [ctl.log.length, ctl.doc?.revealed, spec]);
  const d = ctl.doc;
  const lines = d?.artefact.content.split("\n") ?? [];
  const tone: Record<string, string> = { info: "text-[var(--cr-muted)]", step: "text-[var(--cr-text)]", doc: "text-[var(--cr-accent)]", warn: "text-[var(--cr-warn)]" };
  return (
    <Zone testId="zone-protocol" title={<>공문 · 문안 <span className="ml-2 text-[14px] font-normal text-[var(--cr-muted)]">{TRAINING_LABEL}</span></>}>
      <div className="flex h-full min-h-0 flex-col">
        <div className={`min-h-0 overflow-auto px-4 py-3 ${spec ? "hidden" : "flex-1"}`} data-testid="preview-pane">{!spec && <Preview ctl={ctl} />}</div>
        {spec && (
          <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-[var(--cr-line)] px-2 py-1.5">
            <Btn onClick={() => ctl.generateCapFor(ctl.selectedCard)} testId="gen-cap">CAP 1.2 경보 생성 (훈련)</Btn>
            <Btn onClick={ctl.generateFhir} testId="gen-fhir">FHIR 구급 정보 생성 (훈련)</Btn>
            <Btn disabled={d === null} onClick={() => d && saveArtefact(d.artefact)} testId="save-file">파일로 저장</Btn>
          </div>
        )}
        {spec && (
          <div className="min-h-0 flex-1 overflow-auto bg-[#F3F6FA] px-4 py-3 font-mono text-[13.5px] leading-[1.55]" data-testid="terminal">
            {ctl.log.length === 0 && <p className="m-0 text-[var(--cr-muted)]">대기 중. 조치를 채택하거나 사고 대응을 실행하면 생성 기록이 여기에 쌓입니다.</p>}
            {ctl.log.map((l) => (<div key={l.id} className={tone[l.tone]}><span className="mr-3 text-[#5B6676]">{l.t}</span>{l.text}</div>))}
            {d !== null && (
              <div className="mt-2 border-l-2 border-[var(--cr-accent)] pl-3" data-testid="doc-wrap">
                <p className="m-0 mb-1 text-[13px] text-[var(--cr-warn)]" data-testid="doc-note">{d.artefact.kind === "fhir" ? HTEST_NOTE : CAP_NOTE}{d.artefact.kind === "fhir" ? " · 규격 예시(훈련)" : ""}</p>
                <pre className="m-0 whitespace-pre-wrap break-all text-[#1F2937]" data-testid="doc-body">{lines.slice(0, d.revealed).map((l, i) => {
                  const mark = MARK_RE.test(l);
                  return <span key={i} className={mark ? "block bg-[#FFF3CD] text-[var(--cr-warn)]" : "block"} data-mark={mark ? "1" : undefined}>{l}{mark && <span className="ml-3 select-none text-[13px] italic">{"← "}{d.artefact.kind === "fhir" ? "훈련 데이터 표시" : "CAP 표준 훈련 상태"}</span>}{"\n"}</span>;
                })}</pre>
              </div>
            )}
            <div ref={end} />
          </div>
        )}
      </div>
    </Zone>
  );
}

// ---------- Zone 4 ----------

function Stopwatch({ onsetMs, stopMs }: { onsetMs: number | null; stopMs: number | null }) {
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (stopMs !== null) { if (ref.current) ref.current.textContent = stopMs.toFixed(0); return; }
    if (onsetMs === null) { if (ref.current) ref.current.textContent = "—"; return; }
    let raf = 0;
    const loop = (): void => { if (ref.current) ref.current.textContent = Math.max(0, performance.now() - onsetMs).toFixed(0); raf = requestAnimationFrame(loop); };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [onsetMs, stopMs]);
  const idle = stopMs === null && onsetMs === null;
  return <span className="num" data-testid="stopwatch"><span ref={ref}>—</span>{idle ? "" : " ms"}</span>;
}

export function CameraZone({ ctl }: { ctl: TwinCtl }) {
  const model = useApi(() => loadFallModel(FALL_MODEL_URL));
  const [tele, setTele] = useState<CamTelemetry>(CAM_OFF);
  const [onset, setOnset] = useState<number | null>(null);
  const snapshotRef = useRef<(() => string | null) | null>(null);
  const f = ctl.fall;
  const alert = f.status !== "idle";
  const p0 = tele.persons[0];
  const fromCam = (info: FallInfo): void => ctl.triggerFall("camera", info.onsetMs != null ? info.perfMs - info.onsetMs : null);
  return (
    <Zone
      testId="zone-camera"
      title={<>시연 카메라 <span className="ml-2 text-[15px] font-normal text-[var(--cr-muted)]" data-testid="cam-place">{ctl.place?.label ?? "배치 위치 확인 중"}</span></>}
      right={<><button type="button" onClick={() => ctl.triggerFall("button", null)} data-testid="fall-button-demo" title="웹캠과 공개 영상이 모두 안 될 때만" className="rounded-[5px] border border-[#F2D58A] px-2 py-0.5 text-[13px] text-[var(--cr-warn)] hover:bg-[#FFF3CD]">버튼 시연</button><span className="text-[13px] text-[var(--cr-muted)]">영상 저장·전송 안 함</span></>}
      className={alert && f.status === "running" ? "twin-alert" : ""}
    >
      <div className="flex h-full flex-col gap-2 overflow-auto p-3">
        <div className="twin-cam relative flex shrink-0 items-center overflow-hidden rounded bg-[#F3F6FA]">
          <FieldCamera
            compact placeholder={<div data-testid="cam-placeholder"><p className="m-0 text-[16px] font-semibold text-[var(--cr-text)]">{ctl.place?.label ?? "배치 위치 확인 중"}</p><p className="m-0 text-[13px] text-[var(--cr-muted)]">카메라를 켜면 기기 안에서만 자세를 계산합니다</p></div>} maskLabel={FACE_MASK_LABEL} files={URFALL_FILES} fileBadge={FILE_BADGE} fallModel={model.state.status === "ready" ? model.state.data : null} requiredMps={null}
            snapshotRef={snapshotRef} telemetry={tele} onTelemetry={setTele} onFall={fromCam} onOnset={setOnset}
          />
        </div>
        <p className="m-0 text-[13px] leading-5 text-[var(--cr-muted)]" data-testid="urfall-credit">공개 영상: {URFALL_CREDIT}</p>
        <div className="flex flex-col gap-1.5">
          <div className={`shrink-0 rounded border px-2 py-1.5 ${alert ? "border-[#F2B8B5] bg-[#FDECEA]" : "border-[var(--cr-line)] bg-[var(--cr-panel2)]"}`} data-testid="alert-panel" data-status={f.status}>
            {f.status === "idle" ? (
              <p className="m-0 flex items-baseline gap-2"><span className="text-[19px] font-bold text-[var(--cr-ok)]">정상 보행</span><span className="text-[13px] text-[var(--cr-muted)]">감시 중</span></p>
            ) : (
              <>
                <div className="flex items-center justify-between gap-2">
                  <p className="m-0 min-w-0 truncate text-[16px] font-bold text-[var(--cr-bad)]">{f.status === "pending" ? "관제요원 확인 필요" : f.status === "running" ? "119 연계(훈련) 진행" : "연계 완료(훈련)"}</p>
                  {f.status === "pending" && <Btn small onClick={ctl.confirmFall} testId="confirm-fall" tone="bad">확인</Btn>}
                </div>
                <p className="m-0 text-[14px] font-semibold leading-5 text-[var(--cr-text)]" data-testid="fall-alert-text">{f.source === "button" && <span className="mr-1.5 rounded-[5px] bg-[#F2D58A] px-1.5 text-[13px] font-semibold text-[var(--cr-warn)]" data-testid="button-demo">버튼 시연</span>}{f.alertText ?? `의심 사건 · ${f.where ?? ctl.place?.label ?? ""}`}</p>
                {f.cameraLabel !== null && <p className="m-0 text-[13px] text-[var(--cr-muted)]" data-testid="camera-label">{f.cameraLabel}</p>}
                <p className="m-0 truncate text-[13px] text-[var(--cr-muted)]" title={f.routeText ?? undefined}>가장 가까운 열린 AED · {f.routeText ?? "경로 계산 중"}</p>
              </>
            )}
          </div>
          <div className="shrink-0 rounded border border-[var(--cr-line)] bg-[var(--cr-panel2)] px-3 py-1">
            <p className="m-0 text-[13px] leading-4 text-[var(--cr-muted)]">{f.source === "button" ? "버튼 → 화면 표시" : "낙상 시작 → 감지"}</p>
            <p className="m-0 text-[26px] font-semibold leading-8 text-[var(--cr-text)]">
              {f.source === "button" ? (f.paintMs === null ? "—" : <><span className="num">{f.paintMs.toFixed(0)}</span> ms</>) : f.status !== "idle" && f.detectMs === null ? "측정 없음" : <Stopwatch onsetMs={f.status === "idle" ? onset : null} stopMs={f.detectMs} />}
            </p>
          </div>
          <div className="min-h-0 shrink-0 overflow-hidden rounded border border-[var(--cr-line)] bg-[var(--cr-panel2)] px-3 py-1 text-[13px] leading-[18px]" data-testid="live-fps">
            <p className="m-0 truncate">{tele.on ? <>실시간 계산 <b className="num text-[var(--cr-text)]">{tele.fps.toFixed(0)}</b> fps{tele.inferP50 !== null && <> · 추론 <b className="num text-[var(--cr-text)]">{tele.inferP50.toFixed(0)}</b> ms</>}</> : <span className="text-[var(--cr-muted)]">카메라 꺼짐</span>}</p>
            <p className="m-0 truncate" data-testid="speed-overlay">{p0?.speedMps != null ? <span className="num">{p0.speedMps.toFixed(2)} m/s{tele.calibrated ? "" : " · 보정 전 · 상대 속도"}</span> : <span className="text-[var(--cr-muted)]">보행 속도 측정 전</span>}</p>
            {f.publishText !== null && <p className="m-0 truncate text-[var(--cr-muted)]">{f.publishText}</p>}
          </div>
        </div>
      </div>
    </Zone>
  );
}
