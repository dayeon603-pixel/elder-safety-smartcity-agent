/** 시민 시점 UI: view toggle, 생활인구 legend, step HUD and the in-scene panels (signal head, 정류장 안내단말) anchored to 3D positions. */
import { useEffect, useMemo, useRef, useSyncExternalStore } from "react";
import type { MutableRefObject } from "react";
import type { TwinEngine } from "../engine";
import type { TwinCtl } from "../useTwin";
import { bindStory } from "./bind";
import { StoryController } from "./controller";
import type { PosterCfg } from "./poster";
import type { StorySnapshot } from "./controller";
import { ATTRIBUTION, basemapStatus } from "../basemap/store";
import { BUTTON_DEMO_LABEL, CAMERA_LABEL, MODEL_CREDIT, PASSERBY_LABEL, PERSON_LABEL, POP_LEGEND_SOURCE, POP_PENDING_TEXT, RUN_BASIS, SCALE_NOTE, TRAINING, WALK_BASIS } from "./constants";

const CLAMP_X = 150;
const CLAMP_T = 60;
const CLAMP_B = 40;
const fmtMin = (sec: number): string => `${Math.floor(sec / 60)}분 ${Math.round(sec % 60)}초`;
const SIGNAL_TONE: Record<"ok" | "short" | "none", string> = { ok: "text-[#1E8449]", short: "text-[#C62828]", none: "text-[#5B6676]" };

export function useStoryController(engineRef: MutableRefObject<TwinEngine | null>, ctl: TwinCtl): StoryController {
  const controller = useMemo(() => new StoryController(), []);
  const hasScene = ctl.scene !== null;
  useEffect(() => {
    const eng = engineRef.current;
    if (eng === null || !hasScene) return;
    return bindStory(eng, controller);
  }, [engineRef, controller, hasScene]);
  useEffect(() => { controller.activate(); return () => controller.destroy(); }, [controller]);
  const prevScenario = useRef(ctl.scenario);
  useEffect(() => {
    const prev = prevScenario.current;
    prevScenario.current = ctl.scenario;
    if (ctl.scenario === prev) return;
    if (ctl.scenario === "s3") controller.start();
    else if (ctl.scenario !== "s4" && ctl.scenario !== null) controller.stop();
  }, [controller, ctl.scenario]);
  const triggerFall = ctl.triggerFall;
  useEffect(() => {
    if (ctl.scene === null || ctl.state === null) return;
    const p = ctl.place;
    controller.setInputs({ scene: ctl.scene.scene, state: ctl.state, date: ctl.date, hour: ctl.hour, place: p === null ? null : { lat: p.lat, lon: p.lon, label: p.label, crosswalkId: p.crosswalkId }, triggerFall });
  }, [controller, ctl.scene, ctl.state, ctl.date, ctl.hour, ctl.place, triggerFall]);
  useEffect(() => {
    (window as unknown as { __poster?: unknown }).__poster = { set: (cfg: PosterCfg | null) => controller.setPoster(cfg) };
    (window as unknown as { __basemap?: unknown }).__basemap = { status: () => basemapStatus.get() };
    (window as unknown as { __story?: unknown }).__story = {
      start: () => controller.start(), stop: () => controller.stop(), fall: () => controller.fallByButton(), snapshot: () => controller.getSnapshot(), frontCam: (on: boolean) => { controller.frontCam = on; },
    };
    return () => { delete (window as unknown as { __story?: unknown }).__story; };
  }, [controller]);
  return controller;
}

export function StoryOverlay({ controller, ctl }: { controller: StoryController; ctl: TwinCtl }) {
  const s = useSyncExternalStore(controller.subscribe, controller.getSnapshot);
  const ground = useSyncExternalStore(basemapStatus.subscribe, basemapStatus.get);
  const personRef = useRef<HTMLDivElement>(null);
  const passerRef = useRef<HTMLDivElement>(null);
  const signalRef = useRef<HTMLDivElement>(null);
  const stopRef = useRef<HTMLDivElement>(null);
  const aedRef = useRef<HTMLDivElement>(null);
  const clockRef = useRef<HTMLSpanElement>(null);

  // keyboard: S toggles, Esc leaves
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      const t = e.target as HTMLElement | null;
      if (t !== null && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
      if (e.key === "s" || e.key === "S") { if (controller.getSnapshot().active) controller.stop(); else controller.start(); }
      else if (e.key === "3") controller.start();
      else if (e.key === "Escape") controller.stop();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [controller]);

  // anchored panels follow their 3D positions without React re-renders
  useEffect(() => {
    if (!s.active) return;
    let raf = 0;
    const place = (el: HTMLDivElement | null, p: [number, number] | null, side = false, clamp = false): void => {
      if (el === null) return;
      if (p === null) { el.style.visibility = "hidden"; return; }
      el.style.visibility = "visible";
      const host = el.parentElement;
      const w = host?.clientWidth ?? 0;
      const h = host?.clientHeight ?? 0;
      if (clamp && w > 0) { p = [Math.min(w - CLAMP_X, Math.max(CLAMP_X, p[0])), Math.min(h - CLAMP_B, Math.max(CLAMP_T, p[1]))]; }
      el.style.transform = `translate(${p[0].toFixed(1)}px, ${p[1].toFixed(1)}px) translate(${side ? "64px, -30%" : "-50%, -100%"})`;
    };
    const loop = (): void => {
      const a = controller.anchors;
      place(personRef.current, a.person);
      place(passerRef.current, a.passerby);
      place(signalRef.current, a.signal, true);
      place(stopRef.current, a.stop);
      place(aedRef.current, a.aed, false, true);
      const m = controller.machine;
      if (clockRef.current !== null && m.rescue !== null) clockRef.current.textContent = `${m.runRealSec.toFixed(0)} / ${m.runTotalRealSec.toFixed(0)}초`;
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [controller, s.active]);

  const ready = ctl.scene !== null && ctl.state !== null;
  const fallen = s.phase === "fall" || s.phase === "rescue" || s.phase === "done";
  const stopAnchored = s.phase === "arrived" && s.terminal !== null;
  const fallDocked = fallen && s.terminal !== null;

  return (
    <div className="pointer-events-none absolute inset-0" data-testid="story-root" data-phase={s.phase}>
      <div className="absolute left-1/2 top-3 -translate-x-1/2">
        <div role="group" aria-label="시점 전환" className="pointer-events-auto inline-flex rounded border border-[#D9DEE5] bg-white/95 p-1 shadow backdrop-blur" data-testid="view-switch">
          <button type="button" aria-pressed={!s.active} onClick={() => controller.stop()} data-testid="view-control" className={`h-10 rounded px-5 text-[18px] font-semibold ${!s.active ? "bg-[#0F4C9A] text-white" : "text-[#5B6676] hover:text-[#1F2937]"}`}>관제 시점</button>
          <button type="button" aria-pressed={s.active} disabled={!ready} onClick={() => controller.start()} data-testid="view-citizen" title="S" className={`h-10 rounded px-5 text-[18px] font-semibold disabled:opacity-40 ${s.active ? "bg-[#0F4C9A] text-white" : "text-[#5B6676] hover:text-[#1F2937]"}`}>시민 시점</button>
        </div>
      </div>

      <Legend s={s} controller={controller} />
      <p className="pointer-events-none absolute bottom-1 right-3 m-0 rounded bg-white/80 px-1.5 text-[13px] text-[#3a4452]" data-testid="map-attribution" data-ground={ground.mode}>{ATTRIBUTION[ground.mode]}</p>

      {s.active && (
        <div className="absolute bottom-9 left-3 max-w-[600px]">
          <div className="group pointer-events-auto rounded border border-[#D9DEE5] bg-white/95 px-3 py-1.5 backdrop-blur" data-testid="story-hud">
            <p className="m-0 flex items-center gap-2 text-[15px] font-semibold leading-5 text-[#1F2937]" data-testid="story-title">
              <span className="truncate">
                {s.phase === "enter" || s.phase === "walk" ? <>③ 현장 전달 · {PERSON_LABEL} → «{s.stopName}»</> : null}
                {s.phase === "arrived" ? <>③ 정류장 도착 · 안내단말 확인</> : null}
                {fallen ? <>④ 사고 대응 · 횡단보도 낙상 · {s.fallSource === "button" ? BUTTON_DEMO_LABEL : CAMERA_LABEL}</> : null}
              </span>
              {!fallen && <button type="button" onClick={() => controller.fallByButton()} data-testid="story-fall-btn" className="h-6 shrink-0 rounded border border-[#E8A5A5] bg-[#FDECEC] px-2 text-[13px] font-semibold text-[#C62828]">{BUTTON_DEMO_LABEL}: 낙상</button>}
              {fallen && ctl.fall.status === "pending" && <button type="button" onClick={() => ctl.confirmFall()} data-testid="story-confirm-btn" className="h-6 shrink-0 rounded border border-[#D9DEE5] bg-[#EEF3FA] px-2 text-[13px] font-semibold text-[#1F2937]">관제요원 확인(훈련)</button>}
              <button type="button" onClick={() => controller.stop()} data-testid="story-exit" className="h-6 shrink-0 rounded border border-[#D9DEE5] px-2 text-[13px] font-normal text-[#5B6676]">관제 시점 (Esc)</button>
            </p>
            <p className="m-0 text-[13px] leading-[18px] text-[#5B6676]">
              {s.speedup !== null && !fallen ? <>×{s.speedup} 배속 · 실제 0.7 m/s로는 {s.walkRealSec === null ? "" : fmtMin(s.walkRealSec)} · </> : null}
              {s.run !== null ? <span data-testid="story-run" className="text-[#1F2937]">{PASSERBY_LABEL} 왕복 {(2 * s.run.meters).toFixed(0)} m ÷ 1.2 m/s = <b className="num">{s.run.totalRealSec.toFixed(0)}</b>초 · ×{s.speedup} 배속 · <span className="num" ref={clockRef}>0 / {s.run.totalRealSec.toFixed(0)}초</span> · </span> : null}
              {PERSON_LABEL}은 가상 · {SCALE_NOTE}
            </p>
            <div className="hidden text-[13px] leading-[18px] text-[#5B6676] group-hover:block" data-testid="story-hud-detail">
              <p className="m-0">{WALK_BASIS}{s.lengthM === null ? "" : ` · 경로 ${s.lengthM.toFixed(0)} m`} · {MODEL_CREDIT}</p>
              {s.run !== null && <p className="m-0">{RUN_BASIS} · 열린 AED {s.run.aedName}</p>}
              {fallen && <p className="m-0" data-testid="story-fall-status">{ctl.fall.routeText !== null ? `열린 AED 경로 ${ctl.fall.routeText} · ` : ""}{ctl.fall.status === "pending" ? "관제요원 확인 대기" : ctl.fall.status === "running" ? "119 연계 진행(시뮬레이터)" : ctl.fall.publishText ?? ""}{` · ${TRAINING}`}</p>}
              {s.phase === "arrived" && <p className="m-0">카메라 낙상 감지 대기 · 또는 버튼 시연</p>}
              {s.error !== null && <p className="m-0 text-[#ff9a5a]">{s.error}</p>}
            </div>
          </div>
        </div>
      )}

      {s.active && (
        <>
          <div ref={personRef} className="absolute left-0 top-0 invisible" data-testid="label-person"><span className="rounded bg-white/95 px-2 py-0.5 text-[14px] font-semibold text-[#1F2937] shadow">{PERSON_LABEL}</span></div>
          {s.run !== null && <div ref={passerRef} className="absolute left-0 top-0 invisible" data-testid="label-passerby"><span className="rounded bg-[#FFF3DC] px-2 py-0.5 text-[14px] font-semibold text-[#8A5A00] shadow">{PASSERBY_LABEL}</span></div>}
          {fallen && s.run !== null && <div ref={aedRef} className="absolute left-0 top-0 invisible" data-testid="label-aed"><span className="rounded bg-[#E6F6EE] px-2 py-0.5 text-[14px] font-semibold text-[#17794A] shadow">열린 AED · {s.run.aedName}</span></div>}
          {fallen && s.signal !== null && (
            <div ref={signalRef} className="absolute left-0 top-0 invisible" data-testid="signal-board">
              <div className="w-[300px] rounded border border-[#D9DEE5] bg-white/95 px-3 py-2 shadow-lg">
                <p className="m-0 text-[13px] text-[#5B6676]">보행 신호 · 횡단보도 {s.signal.id}</p>
                <p className={`m-0 text-[17px] font-semibold leading-6 ${SIGNAL_TONE[s.signal.tone]}`} data-testid="signal-text">{s.signal.line}</p>
              </div>
            </div>
          )}
          {s.phase === "arrived" && (
            <div ref={stopRef} className="absolute left-0 top-0 invisible" data-testid="stop-anchor">
              {stopAnchored && s.terminal !== null && <TerminalCard stopName={s.stopName} title={s.terminal.title} body={s.terminal.body} />}
            </div>
          )}
          {fallDocked && s.terminal !== null && (
            <div className="absolute right-3 top-[110px]"><TerminalCard stopName={s.stopName} title={s.terminal.title} body={s.terminal.body} docked /></div>
          )}
          {s.phase === "arrived" && s.terminal === null && <p className="absolute left-1/2 top-20 m-0 -translate-x-1/2 rounded bg-white/95 px-3 py-1 text-[15px] text-[#5B6676]">안내단말 메시지를 불러오는 중…</p>}
        </>
      )}
    </div>
  );
}

function TerminalCard({ stopName, title, body, docked = false }: { stopName: string | null; title: string; body: string; docked?: boolean }) {
  return (
    <div className={`${docked ? "w-[300px]" : "mb-3 w-[380px] shadow-[0_0_36px_rgba(255,138,31,0.45)]"} overflow-hidden rounded-md border-[3px] border-[#2a2a2a] bg-black`} data-testid="story-terminal">
      <div className="flex items-center gap-2 bg-[#a00018] px-3 py-1 text-[13px] font-bold text-white"><span className="shrink-0 rounded-sm bg-white px-1 text-[#a00018]">[훈련]</span><span className="truncate">정류장 안내단말{stopName === null ? "" : ` · ${stopName}`}</span><span className="ml-auto shrink-0 pl-2 font-normal">{docked ? "정류장(화면 밖)" : "장면 안"}</span></div>
      <div className="px-3 py-2 [background-image:radial-gradient(rgba(255,255,255,0.05)_30%,transparent_33%)] [background-size:4px_4px]">
        <p className={`m-0 font-extrabold leading-6 text-[#ff8a1f] [text-shadow:0_0_8px_rgba(255,138,31,0.6)] ${docked ? "text-[17px]" : "text-[22px]"}`} data-testid="story-terminal-title">{title}</p>
        <p className={`m-0 mt-1 font-semibold leading-[22px] text-[#ffc23d] [text-shadow:0_0_6px_rgba(255,194,61,0.45)] ${docked ? "text-[13.5px]" : "text-[16px]"}`} data-testid="story-terminal-body">{body}</p>
      </div>
      <p className="m-0 border-t border-[#3a2a00] px-3 py-0.5 text-[13px] text-[#d9d9d9]">휴대폰 화면과 같은 안내 · {TRAINING}</p>
    </div>
  );
}

function Legend({ s, controller }: { s: StorySnapshot; controller: StoryController }) {
  const p = s.pop;
  return (
    <div className="pointer-events-auto absolute bottom-9 right-3 w-[330px]" data-testid="pop-legend">
      <div className="rounded border border-[#D9DEE5] bg-white/95 px-3 py-1.5 backdrop-blur">
        <label className="flex cursor-pointer items-center gap-2 text-[14px] text-[#1F2937]">
          <input type="checkbox" checked={s.motionOn} onChange={(e) => controller.setMotion(e.target.checked)} data-testid="motion-toggle" />
          도시의 움직임
          {p.status === "ok" && <span className="num text-[#5B6676]">{p.dots?.toLocaleString("ko-KR")}점</span>}
        </label>
        {p.status === "ok" && s.motionOn && (
          <p className="m-0 text-[13px] leading-5 text-[#5B6676]" data-testid="pop-legend-text">
            <span className="text-[#1E7BD6]">●</span> 1점 = {p.perDot}명 · <span className="text-[#ff9e40]">●</span> 65세 이상 · {POP_LEGEND_SOURCE}
          </p>
        )}
        {p.status === "pending" && <p className="m-0 text-[13px] text-[#5B6676]" data-testid="pop-pending">{POP_PENDING_TEXT} <button type="button" className="ml-2 underline" onClick={() => controller.retryPopulation()}>다시 시도</button></p>}
        {p.status === "loading" && <p className="m-0 text-[13px] text-[#5B6676]">생활인구 불러오는 중…</p>}
        {p.status === "error" && <p className="m-0 text-[13px] text-[#ff9a5a]">생활인구 불러오기 실패: {p.message}</p>}
      </div>
    </div>
  );
}
