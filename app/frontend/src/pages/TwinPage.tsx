import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { TwinEngine } from "../twin/engine";
import { PerfProbe } from "../twin/perf";
import { ActionsZone, BareCtx, Breadcrumb, CameraZone, EffectChipView, Legend, LayerPanel, ProtocolZone, Scoreboard, StepList, TimeBar, Toasts, TopBar } from "../twin/TwinPanels";
import { useTwin } from "../twin/useTwin";
import { QrOverlay } from "../twin/QrOverlay";
import { StoryOverlay, useStoryController } from "../twin/story/StoryOverlay";

type Tab = "action" | "doc" | "camera" | "spec";
const TABS: readonly { id: Tab; label: string }[] = [
  { id: "action", label: "조치" }, { id: "doc", label: "공문" }, { id: "camera", label: "현장 카메라" }, { id: "spec", label: "연계 규격" },
];
/** Right panel follows the step: 2 → 조치, 3 → 공문, 5 → 현장 카메라, 6 → 조치. */
const STEP_TAB: Readonly<Record<string, Tab>> = { s1: "action", s2: "action", s3: "doc", silver: "action", s4: "camera", night: "action", rain: "action" };

export function TwinPage() {
  const host = useRef<HTMLDivElement>(null);
  const rain = useRef<HTMLCanvasElement>(null);
  const engineRef = useRef<TwinEngine | null>(null);
  const ctl = useTwin(engineRef);
  const story = useStoryController(engineRef, ctl);
  const [tab, setTab] = useState<Tab>("action");
  const [qrOpen, setQrOpen] = useState(false);
  const closeQr = useCallback(() => setQrOpen(false), []);
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set());

  useEffect(() => {
    if (host.current === null || rain.current === null) return;
    const eng = new TwinEngine(host.current, rain.current);
    engineRef.current = eng;
    (window as unknown as { __twin?: PerfProbe }).__twin = new PerfProbe(eng);
    return () => { eng.destroy(); engineRef.current = null; };
  }, []);

  useEffect(() => { engineRef.current?.setHiddenGroups(hidden); }, [hidden]);
  useEffect(() => { if (ctl.scenario !== null) setTab(STEP_TAB[ctl.scenario] ?? "action"); }, [ctl.scenario]);
  // a fall that waits for the 관제요원 shows the camera tab
  useEffect(() => { if (ctl.fall.status === "pending") setTab("camera"); }, [ctl.fall.status]);

  useEffect(() => {
    document.title = "고령자 안전 스마트도시 에이전트 · 관악구 디지털 트윈";
    const onKey = (e: KeyboardEvent): void => {
      const t = e.target as HTMLElement | null;
      if (t !== null && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
      if (e.key === "1") ctl.runScenario("s1");
      else if (e.key === "2") ctl.runScenario("s2");
      else if (e.key === "3") ctl.runScenario("s3");
      else if (e.key === "4") ctl.runScenario("silver");
      else if (e.key === "5") ctl.runScenario("s4");
      else if (e.key === "6") ctl.runScenario("night");
      else if (e.key === "7") ctl.runScenario("rain");
      else if (e.key === "q" || e.key === "Q") setQrOpen(true);
      else if (e.key === " ") { e.preventDefault(); ctl.togglePlay(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ctl]);

  const toggleLayer = (id: string): void => setHidden((h) => { const n = new Set(h); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const allLayers = (off: boolean): void => setHidden(off ? new Set(["aed", "stop", "signal", "silver", "accident", "cctv"]) : new Set());
  const sc = ctl.scene?.scene;
  const tabBtn = (t: { id: Tab; label: string }): JSX.Element => (
    <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} data-testid={`tab-${t.id}`} onClick={() => setTab(t.id)}
      className={`relative flex-1 border-b-2 px-2 py-3 text-[16px] font-semibold ${tab === t.id ? "border-[var(--cr-accent)] text-[var(--cr-accent)]" : "border-transparent text-[var(--cr-muted)] hover:text-[var(--cr-text)]"}`}>
      {t.label}{t.id === "camera" && ctl.fall.status === "pending" && <span className="absolute right-2 top-2 h-2.5 w-2.5 rounded-full bg-[var(--cr-bad)]" />}
    </button>
  );
  return (
    <div className="twin fixed inset-0 z-40 flex flex-col overflow-hidden" data-testid="twin-root">
      {qrOpen && <QrOverlay onClose={closeQr} />}
      <TopBar ctl={ctl} onQr={() => setQrOpen(true)} onJump={(t) => { if (t === "camera") setTab("camera"); else { setTab("action"); ctl.selectCard(t.card); } }}>
        {ctl.stateError !== null && <span className="text-[12px] text-[var(--cr-warn)]" data-testid="state-error" title={ctl.stateError}>상태 자료 재시도 중</span>}
        <details className="relative" data-testid="more-menu">
          <summary className="flex h-9 w-9 cursor-pointer list-none items-center justify-center rounded border border-[var(--cr-line)] text-[20px] leading-none hover:bg-[#E8EEF6]" aria-label="더보기">⋯</summary>
          <div className="absolute right-0 z-50 mt-1 flex w-40 flex-col rounded border border-[var(--cr-line)] bg-white py-1 shadow">
            <Link to="/" className="px-3 py-1.5 text-[14px]">개요</Link>
            <Link to="/field" className="px-3 py-1.5 text-[14px]">현장 에이전트</Link>
            <Link to="/app" className="px-3 py-1.5 text-[14px]">도시 운영 지도</Link>
          </div>
        </details>
      </TopBar>
      <Breadcrumb ctl={ctl} />
      <div className="flex min-h-0 flex-1">
        <aside className="flex w-[320px] shrink-0 flex-col border-r border-[var(--cr-line)] bg-white max-[1500px]:w-[272px]" data-testid="left-panel">
          <div className="min-h-0 flex-1 overflow-auto">
            <StepList ctl={ctl} />
            <LayerPanel ctl={ctl} hidden={hidden} onToggle={toggleLayer} onAll={allLayers} />
          </div>
          <p className="m-0 border-t border-[var(--cr-line)] px-4 py-2 text-[13px] text-[var(--cr-muted)]">
            건물 <b className="num text-[var(--cr-text)]" data-testid="building-count">{sc?.buildingCount ?? "…"}</b>동 · 층수 실측 <b className="num">{sc?.measuredPct ?? "…"}</b>%{sc != null && (sc.measuredPct ?? 100) < 100 ? " · 나머지 면적 기반 추정" : ""}
          </p>
          <Legend />
        </aside>
        <main className="relative min-w-0 flex-1" data-testid="zone-city">
          <Toasts ctl={ctl} />
          <div ref={host} className="absolute inset-0" style={{ background: "linear-gradient(var(--sky-top, #0b1018), var(--sky-bottom, #1a2330))" }} data-testid="deck-host" />
          <canvas ref={rain} className="pointer-events-none absolute inset-0 h-full w-full" aria-hidden="true" />
          <div className="pointer-events-none absolute right-3 top-3"><Scoreboard ctl={ctl} /></div>
          <div className="pointer-events-none absolute left-1/2 top-[64px] -translate-x-1/2"><EffectChipView ctl={ctl} /></div>
          <StoryOverlay controller={story} ctl={ctl} />
          <div className="absolute right-3 top-1/2 flex -translate-y-1/2 flex-col overflow-hidden rounded border border-[var(--cr-line)] bg-white shadow" data-testid="map-toolbar" role="toolbar" aria-label="지도 도구">
            <button type="button" aria-label="확대" onClick={() => engineRef.current?.zoomBy(0.6)} className="h-9 w-9 text-[20px] hover:bg-[#E8EEF6]">+</button>
            <button type="button" aria-label="축소" onClick={() => engineRef.current?.zoomBy(-0.6)} className="h-9 w-9 border-t border-[var(--cr-line)] text-[20px] hover:bg-[#E8EEF6]">−</button>
            <button type="button" aria-label="전체 보기" onClick={() => engineRef.current?.flyTo("overview")} className="h-9 w-9 border-t border-[var(--cr-line)] text-[13px] hover:bg-[#E8EEF6]">전체</button>
          </div>
          {sc?.credit != null && <p className="pointer-events-none absolute bottom-2 left-3 m-0 max-w-[70%] truncate rounded-sm bg-white/90 px-2 py-1 text-[12px] text-[var(--cr-muted)]" data-testid="credit">ⓒ {sc.credit}</p>}
          {ctl.scene === null && (
            <p className="absolute inset-0 m-0 flex items-center justify-center bg-[#0b1018] text-[22px] text-[#aab6c4]" role="status">
              {ctl.sceneError === null ? "도시 모델을 만드는 중…" : `도시 모델을 불러오지 못했습니다: ${ctl.sceneError}`}
            </p>
          )}
        </main>
        <aside className="flex w-[400px] shrink-0 flex-col border-l border-[var(--cr-line)] bg-white max-[1500px]:w-[372px]" data-testid="right-panel">
          <div role="tablist" className="flex shrink-0 border-b border-[var(--cr-line)]">{TABS.map(tabBtn)}</div>
          <BareCtx.Provider value={true}>
            <div className={`min-h-0 flex-1 ${tab === "action" ? "" : "hidden"}`}><ActionsZone ctl={ctl} /></div>
            <div className={`min-h-0 flex-1 ${tab === "doc" ? "" : "hidden"}`}><ProtocolZone ctl={ctl} view="doc" /></div>
            <div className={`min-h-0 flex-1 ${tab === "camera" ? "" : "hidden"}`}><CameraZone ctl={ctl} /></div>
            <div className={`min-h-0 flex-1 ${tab === "spec" ? "" : "hidden"}`}><ProtocolZone ctl={ctl} view="spec" /></div>
          </BareCtx.Provider>
        </aside>
      </div>
      <TimeBar ctl={ctl} />
    </div>
  );
}
