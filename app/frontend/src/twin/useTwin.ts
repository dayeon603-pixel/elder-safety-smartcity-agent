/** Control logic of the /twin screen: time, mode, scenarios, state loading, actions, fall flow and the protocol log. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MutableRefObject } from "react";
import { apiUrl, runAgent } from "../api/agent";
import type { AgentResult, AgentStep } from "../api/agent";
import {
  daytypeOf, loadActions, loadEffects, loadNeighborSignals, loadFallSummary, loadLetter, publishFall, postJson, rec, str, loadAuditHead, loadRainDays, loadRoute, loadTwinState, postDecision, publishTerminal,
} from "./api";
import type { AuditHead, FeedbackRecord } from "./api";
import { SIGNAL_NEIGHBOR_CODE, SILVER_FLIP_DELAY_MS, STEP_HOURS, TWIN_DEMO_DATE, TWIN_FIRST_HOUR, TWIN_LAST_HOUR, TWIN_NIGHT_HOUR, TWIN_PLAY_SEC_PER_HOUR, TWIN_SGG_CODE, TRAINING_LABEL } from "./constants";
import type { TwinEngine } from "./engine";
import { nearestStop, pickCameraPlace, scoreboard } from "./place";
import type { CameraPlace } from "./place";
import { clock, generateActionCap, generateCap, readCap, readFhir, short } from "./protocol";
import type { Artefact } from "./protocol";
import { infraFromState, infraInsideZones, loadInfra } from "./infra";
import type { Infra } from "./infra";
import { frameHotspots, loadSilverContext } from "./silverStep";
import type { SilverContext } from "./silverStep";
import { loadScene } from "./sceneLoader";
import { TOAST_MS, hhmmKst, loadInbox } from "./work";
import type { CardTimes, InboxCounts, Toast } from "./work";
import { storyBridge } from "./story/host";
import type { LoadedScene } from "./sceneLoader";
import type { ActionsView, CardLetter, EffectChip, Mode, RainDay, ScenarioId, StepNo, TwinState } from "./types";

const SILVER_PITCH = 20;
const SILVER_MORPH_MS = 2500;
const SIGNAL_ZOOM = 16.6;
const SIGNAL_PITCH = 38;
const FALL_MINUTE = Math.round((STEP_HOURS.incident % 1) * 60);
const ROUTE_ANIM_SPEEDUP = 40;
const ROUTE_ANIM_MIN_MS = 2500;
const ROUTE_ANIM_MAX_MS = 9000;
const UI_HOUR_TICK_MS = 100;
const ACTIONS_DEBOUNE_MS = 250;
const MS_PER_S = 1000;

export interface LogLine { id: number; t: string; text: string; tone: "info" | "step" | "doc" | "warn" }
export interface DocView { artefact: Artefact; revealed: number }
export interface FallUi {
  /** pending = 관제요원 확인 대기, running = 통합플랫폼 119 연계(훈련) 진행 */
  status: "idle" | "pending" | "running" | "done";
  source: "camera" | "button" | null;
  /** measured ms from fall onset to the alarm (camera) */
  detectMs: number | null;
  /** measured ms from the trigger to the first painted frame of the pulse */
  paintMs: number | null;
  routeText: string | null;
  publishText: string | null;
  where: string | null;
  /** control-room alert text and camera honesty label, from GET fall-summary */
  alertText: string | null;
  cameraLabel: string | null;
}
export interface DecisionUi { rec: FeedbackRecord; audit: AuditHead | null }

let lineSeq = 0;

export interface TwinCtl {
  scene: LoadedScene | null;
  sceneError: string | null;
  state: TwinState | null;
  stateError: string | null;
  actions: ActionsView | null;
  actionsError: string | null;
  rainDays: RainDay[] | null;
  rainChecked: boolean;
  date: string;
  hour: number;
  playing: boolean;
  mode: Mode;
  scenario: ScenarioId | null;
  effects: Record<string, EffectChip> | null;
  infra: Infra;
  toasts: Toast[];
  times: Record<string, CardTimes>;
  inbox: InboxCounts | null;
  silverInside: number | null;
  stepsDone: Record<StepNo, boolean>;
  stages: Record<string, 1 | 2 | 3>;
  holds: Record<string, string>;
  tally: { adopt: number; hold: number; entries: number | null; valid: boolean | null };
  place: CameraPlace | null;
  log: LogLine[];
  doc: DocView | null;
  decisions: Record<string, DecisionUi>;
  letter: CardLetter | null;
  listen: (cardId: string) => void;
  selectedCard: string | null;
  fall: FallUi;
  board: ReturnType<typeof scoreboard>;
  rainMm: number | null;
  setMode: (m: Mode) => void;
  setHour: (h: number) => void;
  togglePlay: () => void;
  runScenario: (s: ScenarioId) => void;
  decide: (cardId: string, d: "채택" | "보류") => void;
  selectCard: (id: string | null) => void;
  generateCapFor: (cardId: string | null) => void;
  generateFhir: () => void;
  approve: (cardId: string) => void;
  hold: (cardId: string, reason: string) => void;
  confirmFall: () => void;
  triggerFall: (src: "camera" | "button", detectMs: number | null) => void;
}

export function useTwin(engineRef: MutableRefObject<TwinEngine | null>): TwinCtl {
  const [scene, setScene] = useState<LoadedScene | null>(null);
  const [sceneError, setSceneError] = useState<string | null>(null);
  const [state, setState] = useState<TwinState | null>(null);
  const [stateError, setStateError] = useState<string | null>(null);
  const [actions, setActions] = useState<ActionsView | null>(null);
  const [actionsError, setActionsError] = useState<string | null>(null);
  const [rainDays, setRainDays] = useState<RainDay[] | null>(null);
  const [rainChecked, setRainChecked] = useState(false);
  const [date, setDate] = useState(TWIN_DEMO_DATE);
  const [hour, setHourState] = useState(TWIN_FIRST_HOUR + 8);
  const [playing, setPlaying] = useState(false);
  const [mode, setModeState] = useState<Mode>("asis");
  const [scenario, setScenario] = useState<ScenarioId | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [times, setTimes] = useState<Record<string, CardTimes>>({});
  const [inbox, setInbox] = useState<InboxCounts | null>(null);
  const toastSeq = useRef(0);
  const toast = useCallback((text: string): void => {
    const id = ++toastSeq.current;
    setToasts((t) => [...t.slice(-2), { id, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), TOAST_MS);
  }, []);
  const [apiInfra, setApiInfra] = useState<Infra | null>(null);
  const silverCtx = useRef<SilverContext | null>(null);
  const [silverInside, setSilverInside] = useState<number | null>(null);
  const [effects, setEffects] = useState<Record<string, EffectChip> | null>(null);
  const [stepsDone, setStepsDone] = useState<Record<StepNo, boolean>>({ 1: false, 2: false, 3: false, 4: false, 5: false, 6: false });
  const [stages, setStages] = useState<Record<string, 1 | 2 | 3>>({});
  const [holds, setHolds] = useState<Record<string, string>>({});
  const [entries, setEntries] = useState<{ n: number; valid: boolean } | null>(null);
  const [log, setLog] = useState<LogLine[]>([]);
  const [doc, setDoc] = useState<DocView | null>(null);
  const [decisions, setDecisions] = useState<Record<string, DecisionUi>>({});
  const [selectedCard, setSelectedCard] = useState<string | null>(null);
  const [letter, setLetter] = useState<CardLetter | null>(null);
  const [fall, setFall] = useState<FallUi>({ status: "idle", source: null, detectMs: null, paintMs: null, routeText: null, publishText: null, where: null, alertText: null, cameraLabel: null });
  const hourRef = useRef(hour);
  const markDone = useCallback((n: StepNo): void => setStepsDone((d) => (d[n] ? d : { ...d, [n]: true })), []);
  const dateRef = useRef(date);
  const stateRef = useRef<TwinState | null>(null);
  const cache = useRef(new Map<string, TwinState>());
  const lastRainState = useRef<number | null>(null);
  const [rainMm, setRainMm] = useState<number | null>(null);

  const push = useCallback((text: string, tone: LogLine["tone"] = "info"): void => {
    setLog((l) => [...l.slice(-200), { id: ++lineSeq, t: clock(), text, tone }]);
  }, []);

  // scene (worker) + rain days + actions
  useEffect(() => {
    let alive = true;
    loadScene().then((s) => { if (alive) setScene(s); }).catch((e: unknown) => { if (alive) setSceneError(e instanceof Error ? e.message : String(e)); });
    loadInbox().then((i) => { if (alive) setInbox(i); }).catch(() => undefined);
    loadInfra(TWIN_SGG_CODE).then((i) => { if (alive) setApiInfra(i); }).catch(() => undefined);
    loadEffects().then((e) => { if (alive) setEffects(e); }).catch(() => undefined);
    loadRainDays().then((r) => { if (alive) { setRainDays(r); setRainChecked(true); } }).catch(() => { if (alive) setRainChecked(true); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    const eng = engineRef.current;
    if (scene !== null && eng !== null) eng.setScene(scene.scene);
    if (scene === null) return;
    let alive = true;
    loadSilverContext(TWIN_SGG_CODE, scene.scene.bbox, 300).then((c) => { if (alive) { silverCtx.current = c; engineRef.current?.setSilverContext(c); } }).catch(() => undefined);
    return () => { alive = false; };
  }, [scene, engineRef]);

  // state for (date, integer hour): cached
  const hourKey = Math.floor(hour);
  useEffect(() => {
    let alive = true;
    const key = `${date}|${hourKey}`;
    const hit = cache.current.get(key);
    const done = (s: TwinState): void => { if (!alive) return; stateRef.current = s; setState(s); setStateError(null); };
    if (hit !== undefined) { done(hit); return () => { alive = false; }; }
    const timer = setTimeout(() => {
      loadTwinState(date, hourKey, daytypeOf(date)).then((s) => { cache.current.set(key, s); done(s); }).catch((e: unknown) => { if (alive) setStateError(e instanceof Error ? e.message : String(e)); });
    }, 120);
    return () => { alive = false; clearTimeout(timer); };
  }, [date, hourKey]);

  // hour-aware actions: debounced re-fetch on hour change, previous cards stay until the new ones arrive
  useEffect(() => {
    let alive = true;
    const timer = setTimeout(() => {
      loadActions(date, hourKey).then(async (a) => {
        // 관악구 has no crosswalk with a short green time on the demo day: append the adjacent 구's 신호 연장 협의 card (labelled)
        const extra = a.night || a.cards.some((c) => c.id === "signals") ? null : await loadNeighborSignals(date, hourKey, SIGNAL_NEIGHBOR_CODE);
        if (alive) { setActions(extra === null ? a : { ...a, cards: [...a.cards, extra] }); setActionsError(null); }
      }).catch((e: unknown) => { if (alive) setActionsError(e instanceof Error ? e.message : String(e)); });
    }, ACTIONS_DEBOUNE_MS);
    return () => { alive = false; clearTimeout(timer); };
  }, [date, hourKey]);

  // 공문 of the selected (default: first) action, from the API's document_url
  const letterCard = actions?.cards.find((c) => c.id === selectedCard) ?? actions?.cards[0];
  const letterUrl = letterCard?.documentUrl ?? null;
  const letterCardId = letterCard?.id ?? null;
  useEffect(() => {
    // one request per selected card; an aborted/stale response can never overwrite a newer one
    const ctrl = new AbortController();
    setLetter(null);
    if (letterUrl === null || letterCardId === null) return () => ctrl.abort();
    loadLetter(letterUrl, ctrl.signal).then((l) => { if (!ctrl.signal.aborted && l !== null) setLetter({ cardId: letterCardId, letter: l }); }).catch(() => { if (!ctrl.signal.aborted) setLetter(null); });
    return () => ctrl.abort();
  }, [letterUrl, letterCardId]);

  // push to engine
  useEffect(() => {
    const eng = engineRef.current;
    if (state === null || eng === null) return;
    eng.setState(state);
    const mm = state.weather?.rainMm ?? null;
    setRainMm(mm);
    lastRainState.current = mm;
  }, [state, engineRef]);

  const infra = useMemo(() => infraFromState(apiInfra, state), [apiInfra, state]);
  const infraRef = useRef(infra);
  infraRef.current = infra;
  useEffect(() => { engineRef.current?.setInfra(infra); }, [infra, engineRef, scene]);

  const place = useMemo<CameraPlace | null>(() => (state === null ? null : pickCameraPlace(state)), [state]);
  const placeRef = useRef(place);
  useEffect(() => {
    if (place !== null && placeRef.current === null) placeRef.current = place;
    const eng = engineRef.current;
    const p = placeRef.current;
    if (eng !== null && p !== null) eng.setCamera({ lat: p.lat, lon: p.lon, label: "시연 카메라 배치 위치" });
  }, [place, engineRef, scene]);
  const stablePlace = placeRef.current ?? place;

  const applyTime = useCallback((h: number, d: string): void => {
    const eng = engineRef.current;
    const sc = scene?.scene;
    if (eng === null || sc === undefined) return;
    eng.setTime(d, h, sc.center[1], sc.center[0]);
  }, [engineRef, scene]);

  const hourQuarter = Math.floor(hour * 4);
  const hourRefForTime = useRef(hour);
  hourRefForTime.current = hour;
  useEffect(() => { applyTime(hourRefForTime.current, date); }, [applyTime, date, hourQuarter]);

  // rain intensity from the API (rain_mm relative to the heaviest observed rain day)
  useEffect(() => {
    const eng = engineRef.current;
    if (eng === null) return;
    const max = Math.max(0, ...(rainDays ?? []).map((r) => r.mm ?? 0));
    eng.setRain(rainMm !== null && max > 0 ? rainMm / max : 0);
  }, [rainMm, rainDays, engineRef]);

  const setHour = useCallback((h: number): void => {
    hourRef.current = h;
    setHourState(h);
    applyTime(h, dateRef.current);
  }, [applyTime]);

  const setMode = useCallback((m: Mode): void => { setModeState(m); engineRef.current?.setMode(m); }, [engineRef]);

  // auto play
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let last = performance.now();
    let lastUi = 0;
    const step = (now: number): void => {
      const dt = (now - last) / MS_PER_S;
      last = now;
      let h = hourRef.current + dt / TWIN_PLAY_SEC_PER_HOUR;
      if (h >= TWIN_LAST_HOUR) { h = TWIN_LAST_HOUR; setPlaying(false); }
      hourRef.current = h;
      applyTime(h, dateRef.current);
      if (now - lastUi > UI_HOUR_TICK_MS) { lastUi = now; setHourState(h); }
      if (h < TWIN_LAST_HOUR) raf = requestAnimationFrame(step);
      else setHourState(h);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [playing, applyTime]);

  const togglePlay = useCallback((): void => {
    setPlaying((p) => {
      if (!p && hourRef.current >= TWIN_LAST_HOUR) { hourRef.current = TWIN_FIRST_HOUR; setHourState(TWIN_FIRST_HOUR); }
      return !p;
    });
  }, []);

  // ---------- fall flow: 1) 관제요원 확인 대기  2) 통합플랫폼 119 연계(훈련) ----------
  const fallBusy = useRef(false);
  const routeRef = useRef<{ text: string; bodyText: string } | null>(null);
  const setDay = useCallback((d: string): void => { dateRef.current = d; setDate(d); }, []);

  const pt = (p: { lat: number; lon: number } | null): { lon: number; lat: number } | undefined => (p === null ? undefined : { lon: p.lon, lat: p.lat });
  const triggerFall = useCallback((src: "camera" | "button", detectMs: number | null): void => {
    const eng = engineRef.current;
    const pl = placeRef.current;
    if (fallBusy.current || eng === null || pl === null) return;
    fallBusy.current = true;
    storyBridge.onFall?.(src);
    const t0 = performance.now();
    setScenario("s4");
    setPlaying(false);
    if (dateRef.current !== TWIN_DEMO_DATE) setDay(TWIN_DEMO_DATE);
    if (Math.abs(hourRef.current - STEP_HOURS.incident) > 0.01) setHour(STEP_HOURS.incident);
    setFall({ status: "pending", source: src, detectMs, paintMs: null, routeText: null, publishText: null, where: pl.label, alertText: null, cameraLabel: null });
    void loadFallSummary(pl.lat, pl.lon, dateRef.current, Math.floor(STEP_HOURS.incident), FALL_MINUTE).then((fs) => {
      if (fs === null) return;
      setFall((f) => ({ ...f, alertText: fs.alertText, cameraLabel: fs.cameraLabel }));
      if (fs.cameraId !== null) eng.setCamera({ lat: pl.lat, lon: pl.lon, label: fs.cameraId });
      push(fs.alertText, "warn");
    });
    toast("낙상 의심 감지 · 관제요원 확인 대기");
    push(`의심 사건 · ${pl.label}${detectMs === null ? "" : ` · 감지까지 ${detectMs.toFixed(0)} ms`}${src === "button" ? " · 버튼 시연" : ""}`, "warn");
    eng.clearRoute();
    eng.setHighlight(null);
    eng.flyTo("fall");
    eng.startPulse();
    eng.playback("fallAlert", undefined, pt(pl));
    requestAnimationFrame(() => requestAnimationFrame(() => setFall((f) => ({ ...f, paintMs: performance.now() - t0 }))));
    void loadRoute(pl.lat, pl.lon, dateRef.current, Math.floor(STEP_HOURS.incident), daytypeOf(dateRef.current)).then((r) => {
      if (r === null) { push("열린 AED 경로를 찾지 못함", "warn"); return; }
      const ms = Math.min(ROUTE_ANIM_MAX_MS, Math.max(ROUTE_ANIM_MIN_MS, (r.route.seconds / ROUTE_ANIM_SPEEDUP) * MS_PER_S));
      eng.startRoute(r.route.path, ms, r.route.aed);
      const text = `${r.route.aed.name} · ${r.route.meters.toFixed(0)} m · ${Math.round(r.route.seconds)} 초`;
      routeRef.current = { text, bodyText: `가장 가까운 열린 AED ${r.route.aed.name} ${r.route.meters.toFixed(0)} m` };
      setFall((f) => ({ ...f, routeText: text }));
      push(`가장 가까운 열린 AED ${text}`, "step");
    });
  }, [engineRef, push, setDay, setHour, toast]);

  const reveal = useCallback((a: Artefact): void => {
    const lines = a.content.split("\n").length;
    const parts = [a.kind === "cap" ? "CAP 1.2 생성" : "FHIR R4 Bundle 규격 예시(훈련)", a.generationMs === null ? null : `${a.generationMs.toFixed(1)} ms`, a.kind === "cap" && a.validated === true ? "XSD 통과" : a.kind === "cap" && a.validated === false ? "XSD 실패" : null, short(a.sha256) || null].filter((x): x is string => x !== null);
    push(parts.join("  "), "doc");
    setDoc({ artefact: a, revealed: 0 });
    let n = 0;
    const per = Math.max(1, Math.ceil(lines / 40));
    const iv = setInterval(() => { n += per; setDoc((d) => (d === null || d.artefact !== a ? d : { ...d, revealed: Math.min(lines, n) })); if (n >= lines) clearInterval(iv); }, 30);
  }, [push]);

  const showAgentArtefacts = useCallback(async (result: AgentResult): Promise<void> => {
    const rcpt = result.receipts.find((r) => r.target.includes("119") || r.id?.startsWith("emg"))?.id ?? result.receipts[1]?.id ?? null;
    try {
      if (result.cap?.xmlUrl) reveal(await readCap(result.cap.id, result.cap.xmlUrl, result.cap.xsdValid));
      if (rcpt !== null) { const f = await readFhir(rcpt); if (f !== null) reveal(f); }
    } catch (e) {
      if (e instanceof Error) push(`문서를 읽지 못함: ${e.message}`, "warn");
      else throw e;
    }
  }, [reveal, push]);

  const confirmFall = useCallback((): void => {
    const st = stateRef.current;
    const pl = placeRef.current;
    if (pl === null) return;
    setFall((f) => (f.status === "pending" ? { ...f, status: "running" } : f));
    engineRef.current?.playback("fallConfirmed", undefined, pt(pl));
    push("관제요원 확인 · 통합플랫폼 119 연계(훈련) 시작", "step");
    const onStep = (s: AgentStep): void => push(`${s.name}${s.ms === null ? "" : `  ${s.ms.toFixed(1)} ms`}  ${s.summary}`, "step");
    void runAgent({ code: TWIN_SGG_CODE, date: dateRef.current, lat: pl.lat, lon: pl.lon, est_speed: null, torso_angle: null }, { onStep }).then(async (o) => {
      if (o.kind !== "ok") { push(`에이전트 연결 실패: ${o.reason}`, "warn"); return; }
      push(`에이전트 완료  ${(o.result.totalMs ?? o.clientMs).toFixed(0)} ms`, "info");
      await showAgentArtefacts(o.result);
    }).then(async () => {
      const stop = st === null ? null : nearestStop(st, pl.lat, pl.lon);
      const body = routeRef.current === null ? `${pl.label} 낙상 의심` : `${pl.label} 낙상 의심 · ${routeRef.current.bodyText}`;
      void body;
      const pub = await publishFall(pl.lat, pl.lon, dateRef.current, Math.floor(STEP_HOURS.incident), FALL_MINUTE, stop?.id ?? null);
      const publishText = pub === "ok" ? "안내단말에 게시됨(전달 대기)" : pub === "missing" ? "단말 채널 준비 중" : "단말 게시 실패";
      push(`${publishText} · ${TRAINING_LABEL}`, pub === "ok" ? "step" : "warn");
      setFall((f) => ({ ...f, status: "done", publishText }));
      toast(`${publishText} · ${TRAINING_LABEL}`);
      markDone(5);
    }).catch((e: unknown) => {
      push(`낙상 연계 실패: ${e instanceof Error ? e.message : String(e)}`, "warn");
    }).finally(() => {
      fallBusy.current = false;
    });
  }, [push, showAgentArtefacts, markDone, engineRef, toast]);

  // ---------- actions ----------
  const selectCard = useCallback((id: string | null): void => {
    setSelectedCard(id);
    const card = actions?.cards.find((c) => c.id === id);
    const eng = engineRef.current;
    if (eng === null) return;
    if (card !== undefined && card.items.length > 0) {
      const worst = card.items.reduce((m, x) => (x.required - x.green > m.required - m.green ? x : m), card.items[0] as (typeof card.items)[number]);
      eng.setSignalShort(card.items.map((x) => ({ lon: x.lon, lat: x.lat, text: `녹색 ${x.green}초 < 필요 ${x.required}초` })));
      eng.setViewState({ longitude: worst.lon, latitude: worst.lat, zoom: SIGNAL_ZOOM, pitch: SIGNAL_PITCH, bearing: 0 }, 1600, true);
    } else eng.setSignalShort([]);
  }, [actions, engineRef]);

  const generateCapFor = useCallback((cardId: string | null): void => {
    const pl = placeRef.current;
    const st = stateRef.current;
    const card = actions?.cards.find((c) => c.id === (cardId ?? selectedCard)) ?? actions?.cards[0];
    if (card === undefined) return;
    const where = card.lat !== null && card.lon !== null ? { lat: card.lat, lon: card.lon } : pl;
    if (where !== null) engineRef.current?.setHighlight({ lat: where.lat, lon: where.lon });
    const made = card.capUrl !== null
      ? generateActionCap(card.capUrl, card.id)
      : pl === null ? Promise.reject(new Error("위치 없음")) : generateCap({ identifier: `twin-${card.id}-${Date.now()}`, event: card.title, headline: `${card.title} (훈련)`, description: card.summary || card.title, instruction: st?.heat.rankText ?? "담당자 검토용 훈련 문서", areaDesc: pl.label, lat: pl.lat, lon: pl.lon });
    made.then(reveal).catch((e: unknown) => push(`CAP 생성 실패: ${e instanceof Error ? e.message : String(e)}`, "warn"));
  }, [actions, selectedCard, reveal, push, engineRef]);

  const refreshAudit = useCallback(async (): Promise<AuditHead | null> => {
    const a = await loadAuditHead();
    if (a !== null) setEntries({ n: a.entries, valid: a.valid });
    return a;
  }, []);

  const auditOnce = useRef(false);
  useEffect(() => { if (auditOnce.current) return; auditOnce.current = true; void refreshAudit(); }, [refreshAudit]);

  const decide = useCallback((cardId: string, d: "채택" | "보류", reason: string = ""): void => {
    setSelectedCard(cardId);
    const card = actions?.cards.find((c) => c.id === cardId);
    void postDecision(TWIN_SGG_CODE, dateRef.current, card?.feedbackId ?? cardId, d, reason).then(async (r) => {
      if ("error" in r) { push(`판단 기록 실패: ${r.error}`, "warn"); return; }
      const audit = await refreshAudit();
      setDecisions((m) => ({ ...m, [cardId]: { rec: r, audit } }));
      setTimes((m) => ({ ...m, [cardId]: { ...(m[cardId] ?? { draft: actions?.generatedAt.slice(0, 5) ?? hhmmKst() }), sent: hhmmKst(r.createdAt) } }));
      toast(`${d === "채택" ? "재난 대응 초안 상신됨" : "보류 기록됨"} · 감사 기록 #${r.id}`);
      void loadInbox().then((i) => setInbox(i)).catch(() => undefined);
      push(`${d === "채택" ? "주무관 채택(상신)" : "보류(사유 기록)"} · 판단 기록 #${r.id}${audit?.head ? `  변조 탐지 기록 ${audit.valid ? "정상" : "불일치"} · ${audit.head.slice(0, 12)}` : ""}`, "step");
      if (d === "채택") {
        setStages((m) => ({ ...m, [cardId]: 2 }));
        markDone(2);
        generateCapFor(cardId);
      } else {
        setHolds((m) => ({ ...m, [cardId]: reason }));
      }
    });
  }, [push, generateCapFor, actions, markDone, refreshAudit, toast]);

  const hold = useCallback((cardId: string, reason: string): void => decide(cardId, "보류", reason), [decide]);

  /** 과장 결재(시연): marks the demo approval and puts the heat message on the 안내단말 channel (훈련). */
  const approve = useCallback((cardId: string): void => {
    const card = actions?.cards.find((c) => c.id === cardId);
    if (card === undefined) return;
    setStages((m) => ({ ...m, [cardId]: 3 }));
    setTimes((m) => ({ ...m, [cardId]: { ...(m[cardId] ?? { draft: hhmmKst() }), approved: hhmmKst() } }));
    toast(`과장 결재(시연) · ${card.title} · 전달 대기`);
    markDone(3);
    push(`과장 결재(시연) · ${card.title} · 전달 대기`, "step");
    if (card.kind === "heat") {
      engineRef.current?.playback("broadcast", infraRef.current.stop.filter((s) => s.bit !== false).map((s) => ({ lon: s.lon, lat: s.lat })));
      const stop = stateRef.current?.stops[0];
      void publishTerminal("heat", card.title, card.summary, stop?.id ?? null).then((pub) => {
        push(`${pub === "ok" ? "안내단말에 게시됨(전달 대기)" : pub === "missing" ? "단말 채널 준비 중" : "단말 게시 실패"} · ${TRAINING_LABEL}`, pub === "ok" ? "step" : "warn");
      });
    }
  }, [actions, markDone, push, engineRef, toast]);

  /** ▶ 듣기: plays the 마을방송 WAV the backend generated for the card (existing audio endpoint). */
  const listen = useCallback((cardId: string): void => {
    const card = actions?.cards.find((c) => c.id === cardId);
    if (card === undefined) return;
    const play = (url: string): void => { void new Audio(apiUrl(url)).play().catch((e: unknown) => push(`음성 재생 실패: ${e instanceof Error ? e.message : String(e)}`, "warn")); };
    if (card.audioUrl !== null) { play(card.audioUrl); return; }
    if (card.capUrl === null) return;
    void postJson(card.capUrl, {}).then((r) => {
      const url = str(rec(r.json)?.audio_url);
      if (url === null) push("음성 파일 없음", "warn"); else play(url);
    });
  }, [actions, push]);

  const generateFhir = useCallback((): void => { triggerFall("button", null); }, [triggerFall]);

  // ---------- workday steps ----------
  const rainBest = useMemo<RainDay | null>(() => (rainDays === null || rainDays.length === 0 ? null : [...rainDays].sort((a, b) => (b.mm ?? 0) - (a.mm ?? 0))[0] ?? null), [rainDays]);

  const runScenario = useCallback((s: ScenarioId): void => {
    const eng = engineRef.current;
    setScenario(s);
    eng?.stopPulse();
    eng?.clearRoute();
    eng?.setHighlight(null);
    eng?.setSignalShort([]);
    setPlaying(false);
    eng?.setSilverStep(s === "silver");
    const asis = (): void => { setModeState("asis"); eng?.setMode("asis"); };
    if (s === "s1") {
      asis(); setDay(TWIN_DEMO_DATE); setHour(STEP_HOURS.brief); eng?.flyTo("overview"); markDone(1);
    } else if (s === "s2") {
      asis(); setDay(TWIN_DEMO_DATE); setHour(STEP_HOURS.heat); eng?.flyTo("overview");
    } else if (s === "s3") {
      asis(); setDay(TWIN_DEMO_DATE); setHour(STEP_HOURS.deliver); eng?.flyTo("overview"); markDone(3);
    } else if (s === "silver") {
      // 어디에: start from [현행 기준], then flip to [에이전트] so the zones morph and the hotspot dots recolour
      asis(); setDay(TWIN_DEMO_DATE); setHour(STEP_HOURS.silver);
      eng?.setSilverStep(true);
      const frame = silverCtx.current === null ? null : frameHotspots(silverCtx.current);
      if (frame !== null) eng?.setViewState({ longitude: frame.lon, latitude: frame.lat, zoom: frame.zoom, pitch: SILVER_PITCH, bearing: 0 }, 1800, true);
      else eng?.flyTo("overview");
      setTimeout(() => {
        setModeState("ours"); eng?.setMode("ours", SILVER_MORPH_MS); markDone(4);
        const st = stateRef.current;
        const inside = st === null ? [] : infraInsideZones(infraRef.current, st.silverOurs, st.silverRadiusM);
        setSilverInside(inside.length);
        eng?.playback("silverInfra", inside);
      }, SILVER_FLIP_DELAY_MS);
    } else if (s === "s4") {
      // 무엇을: arm the incident step (17:40, camera on the crosswalk); the fall itself comes from the camera / public clip
      asis(); setDay(TWIN_DEMO_DATE); setHour(STEP_HOURS.incident); eng?.flyTo("fall");
    } else if (s === "night") {
      asis(); setDay(TWIN_DEMO_DATE); setHour(STEP_HOURS.night); eng?.flyTo("night"); markDone(6);
      setTimeout(() => eng?.playback("nightTargets", (stateRef.current?.aedCandidates ?? []).map((a) => ({ lon: a.lon, lat: a.lat }))), 1200);
    } else if (s === "rain") {
      if (rainBest === null) return;
      setDay(rainBest.date); setHour(rainBest.hour ?? TWIN_NIGHT_HOUR); eng?.flyTo("rain");
    }
  }, [engineRef, setHour, rainBest, setDay, markDone]);

  // 근거 기록 is counted for this session only: audit entries now minus the count when the page loaded
  const entriesBaseline = useRef<number | null>(null);
  if (entries !== null && entriesBaseline.current === null) entriesBaseline.current = entries.n;
  const tally = useMemo(() => {
    const vals = Object.values(decisions);
    return { adopt: vals.filter((d) => d.rec.decision === "채택").length, hold: vals.filter((d) => d.rec.decision === "보류").length, entries: entries === null || entriesBaseline.current === null ? null : Math.max(0, entries.n - entriesBaseline.current), valid: entries?.valid ?? null };
  }, [decisions, entries]);

  return {
    scene, sceneError, state, stateError, actions, actionsError, rainDays, rainChecked, date, hour, playing, mode, scenario, effects, infra, toasts, times, inbox, silverInside, stepsDone, stages, holds, tally, place: stablePlace, log, doc, decisions, letter, listen,
    selectedCard, fall, board: state === null ? null : scoreboard(state), rainMm, setMode, setHour, togglePlay, runScenario, decide, selectCard, generateCapFor, generateFhir, approve, hold, confirmFall, triggerFall,
  };
}
