import { useCallback, useMemo, useRef, useState } from "react";
import { runAgent } from "../api/agent";
import type { AgentRunRequest, AgentStep } from "../api/agent";
import { useApi } from "../api/useApi";
import { PageHeader } from "../components/AppShell";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { SelectField } from "../components/Field";
import { ErrorState, Notice } from "../components/Notice";
import { SkeletonText } from "../components/Skeleton";
import { ControlPanel } from "../components/field/ControlPanel";
import type { AgentUi } from "../components/field/ControlPanel";
import { CAM_OFF, FieldCamera } from "../components/field/FieldCamera";
import type { CamTelemetry, FallInfo } from "../components/field/FieldCamera";
import { HeatPicker } from "../components/field/HeatPicker";
import { PipelineStrip } from "../components/field/PipelineStrip";
import { ScenarioA } from "../components/field/ScenarioA";
import { useFieldHeat } from "../components/field/useFieldHeat";
import { FALL_MODEL_URL, FIELD_BANNER_TEXT } from "../config";
import type { Crosswalk } from "../lib/field";
import { evidenceDetail, evidenceText, loadFallEvidence } from "../lib/fallEvidence";
import { loadAeds, loadCrosswalks, loadFallModel } from "../lib/fieldData";
import { runEmergency } from "../lib/fieldEmergency";
import type { EmergencyEvent } from "../lib/fieldEmergency";
import type { PipelineStageId } from "../lib/fieldIncident";
import { speak, stopSpeaking } from "../lib/speech";

const cwLabel = (c: Crosswalk): string => `${c.sido} ${c.sigungu} · ${c.lengthM.toFixed(1)} m · 녹색 ${c.greenSec} s · ${c.lanes}차로`;

function FieldBody({ crosswalks, sources }: { crosswalks: readonly Crosswalk[]; sources: string }) {
  const [cwId, setCwId] = useState<string>(crosswalks[0]?.id ?? "");
  const crosswalk = crosswalks.find((c) => c.id === cwId) ?? (crosswalks[0] as Crosswalk);
  const heat = useFieldHeat(crosswalk);
  const aeds = useApi(loadAeds);
  const model = useApi(() => loadFallModel(FALL_MODEL_URL));
  const evidence = useApi(loadFallEvidence);
  const fallModel = model.state.status === "ready" ? model.state.data : null;
  const [muted, setMuted] = useState(false);
  const [vlm, setVlm] = useState(false);
  const [tele, setTele] = useState<CamTelemetry>(CAM_OFF);
  const [events, setEvents] = useState<EmergencyEvent[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [agents, setAgents] = useState<Record<string, AgentUi>>({});
  const [aLevel, setALevel] = useState<0 | 1 | 2>(0);
  const [snapshots, setSnapshots] = useState(0);
  const snapshotRef = useRef<(() => string | null) | null>(null);
  const latest = useRef({ crosswalk, heat, aeds, muted, vlm, events, tele, regionCode: heat.regionCode });
  latest.current = { crosswalk, heat, aeds, muted, vlm, events, tele, regionCode: heat.regionCode };

  const requiredMps = crosswalk.lengthM / crosswalk.greenSec;

  const trigger = useCallback((kind: "button" | "camera", fall?: FallInfo): void => {
    const L = latest.current;
    if (fall !== undefined && L.events.some((e) => e.trackId === fall.id)) return;
    if (fall === undefined && L.events.some((e) => e.trigger === "button")) return;
    const t0 = fall?.perfMs ?? performance.now();
    const ev = runEmergency({
      trigger: kind, t0, now: () => performance.now(), at: new Date(), crosswalk: L.crosswalk, heat: L.heat.info,
      aeds: L.aeds.state.status === "ready" ? L.aeds.state.data.aeds : null,
      trackId: fall?.id ?? null, uuid: () => crypto.randomUUID(),
      torsoAngleDeg: fall?.torsoDeg ?? null, estSpeedMps: fall?.speedMps ?? null, aiProb: fall?.aiProb ?? null, ruleFiredAlready: fall === undefined ? null : fall.ruleFiredAlready, muted: L.muted, speakFn: speak,
    });
    setEvents((prev) => [ev, ...prev]);
    setSelected(ev.id);
    requestAnimationFrame(() => setTimeout(() => {
      const paintMs = performance.now() - t0;
      setEvents((prev) => prev.map((e) => (e.id === ev.id ? { ...e, paintMs } : e)));
    }, 0));

    const code = L.regionCode;
    const date = L.heat.info.date;
    if (code === null || date === null) {
      setAgents((a) => ({ ...a, [ev.id]: { status: "done", outcome: { kind: "unavailable", reason: "시군구 코드나 날짜를 알 수 없음(폭염 입력을 직접 선택한 경우)" } } }));
      return;
    }
    const body: AgentRunRequest = {
      code, date, lat: L.crosswalk.lat, lon: L.crosswalk.lon,
      est_speed: fall?.speedMps ?? null, torso_angle: fall?.torsoDeg == null ? null : Math.min(180, Math.max(0, fall.torsoDeg)),
    };
    if (L.vlm) {
      const jpeg = snapshotRef.current?.() ?? null;
      if (jpeg !== null) {
        body.image_base64 = jpeg;
        setSnapshots((n) => n + 1);
      }
    }
    setAgents((a) => ({ ...a, [ev.id]: { status: "loading", since: performance.now(), steps: [], briefing: "", notes: [] } }));
    const patch = (f: (cur: Extract<AgentUi, { status: "loading" }>) => Extract<AgentUi, { status: "loading" }>): void =>
      setAgents((a) => {
        const cur = a[ev.id];
        return cur?.status === "loading" ? { ...a, [ev.id]: f(cur) } : a;
      });
    void runAgent(body, {
      onStep: (step: AgentStep) => patch((cur) => ({ ...cur, steps: [...cur.steps, step] })),
      onBriefing: (text) => patch((cur) => ({ ...cur, briefing: text })),
      onNote: (text) => patch((cur) => ({ ...cur, notes: [...cur.notes, text] })),
    }).then((outcome) => setAgents((a) => ({ ...a, [ev.id]: { status: "done", outcome } })));
  }, []);

  const reset = (): void => {
    stopSpeaking();
    setEvents([]);
    setSelected(null);
    setAgents({});
  };

  const cautionAny = tele.persons.some((p) => p.status === "caution");
  const alert = events.length > 0;
  const agentOk = Object.values(agents).filter((a) => a.status === "done" && a.outcome.kind === "ok");
  const sceneOk = agentOk.some((a) => a.status === "done" && a.outcome.kind === "ok" && a.outcome.result.sceneText !== null);
  const active = useMemo<Record<PipelineStageId, boolean>>(() => ({
    camera: tele.on,
    pose: tele.on && tele.persons.length > 0,
    judge: tele.persons.length > 0 || aLevel >= 1 || alert,
    data: aLevel >= 1 || alert || tele.persons.length > 0,
    plan: agentOk.length > 0,
    scene: sceneOk,
    alert: aLevel >= 2 || alert || cautionAny,
  }), [tele, aLevel, alert, cautionAny, agentOk.length, sceneOk]);

  return (
    <div>
      <PipelineStrip active={active} />

      <div className="mt-6">
        <Card title="입력: 실제 공공데이터" caption="횡단보도는 2024 보행노인 사고다발지 300 m 이내의 표준데이터 행입니다.">
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <SelectField label="횡단보도" value={crosswalk.id} onChange={(e) => setCwId(e.target.value)} options={crosswalks.map((c) => ({ value: c.id, label: cwLabel(c) }))} hint={<span className="dev-only">{crosswalk.address}</span>} />
            <HeatPicker heat={heat} />
          </div>
          <p className="dev-only mt-3 text-caption text-ink-faint">{sources}</p>
        </Card>
      </div>

      <div className="mt-6 grid grid-cols-1 items-start gap-6 lg:grid-cols-2">
        <Card title="현장 카메라" caption="브라우저 안에서 자세를 추정합니다." actions={<Badge tone="accent">프로토타입 감지(정확도 미검증)</Badge>}>
          {evidence.state.status === "ready" && (
            <div className="mb-4 border border-accent/40 bg-accent-wash px-3 py-2">
              <p className="text-small font-semibold" data-testid="fall-evidence">{evidenceText(evidence.state.data)}</p>
              <p className="dev-only mt-1 text-caption text-ink-muted">{evidenceDetail(evidence.state.data)}</p>
            </div>
          )}
          {model.state.status === "error" && (
            <div className="mb-4"><Notice tone="danger">AI 낙상 모델을 불러오지 못해 규칙 기준선(60° · 1.5초)으로 대체합니다. {model.state.message}</Notice></div>
          )}
          <FieldCamera
            fallModel={fallModel}
            requiredMps={requiredMps}
            snapshotRef={snapshotRef}
            telemetry={tele}
            onTelemetry={setTele}
            onFall={(f) => trigger("camera", f)}
          />
          <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-line pt-4">
            <Button variant="danger" onClick={() => trigger("button")} disabled={aeds.state.status === "loading"}>쓰러짐 이벤트 발생(시뮬레이션)</Button>
            <Button variant="secondary" aria-pressed={muted} onClick={() => { setMuted(!muted); if (!muted) stopSpeaking(); }}>{muted ? "음성 안내 켜기" : "음성 안내 끄기"}</Button>
          </div>
          <label className="mt-3 flex items-start gap-2 text-small">
            <input type="checkbox" checked={vlm} onChange={(e) => setVlm(e.target.checked)} className="mt-1 h-4 w-4 accent-accent" />
            <span>
              장면 설명(VLM) 사용 · 참고용
              <span className="block text-caption text-ink-faint">기본 꺼짐. 켜면 응답이 약 20초 늦어지고 설명이 틀릴 수 있습니다. 쓰러짐 순간 얼굴을 가린 스냅샷 1장만 로컬 서버로 보냅니다(서버는 저장하지 않음). 지금까지 전송한 스냅샷: <span className="num">{snapshots}</span>장</span>
            </span>
          </label>
        </Card>
        <ControlPanel crosswalk={crosswalk} heat={heat.info} tele={tele} events={events} selectedId={selected} onSelect={setSelected} agents={agents} onReset={reset} threshold={fallModel?.threshold ?? null} />
      </div>

      <div className="mt-6">
        <ScenarioA crosswalk={crosswalk} heat={heat.info} onStage={setALevel} />
      </div>
    </div>
  );
}

export function FieldPage() {
  const data = useApi(loadCrosswalks);
  const [presentation, setPresentation] = useState(false);
  return (
    <div className={presentation ? "field-present" : ""}>
      <PageHeader
        eyebrow="현장 연동 · 시뮬레이션 포함"
        title="실버존 에이전트"
        lead="우리 동네 횡단보도에서, AI와 디지털 기술로"
        actions={<Button variant="secondary" aria-pressed={presentation} onClick={() => setPresentation(!presentation)}>발표 모드 {presentation ? "끄기" : "켜기"}</Button>}
      />
      <p role="note" className="sticky top-[6.5rem] z-20 mb-6 border border-line-strong bg-surface px-3 py-2 text-small font-medium md:top-16">{FIELD_BANNER_TEXT}</p>
      {data.state.status === "loading" && <SkeletonText lines={6} label="횡단보도 자료를 불러오는 중" />}
      {data.state.status === "error" && <ErrorState title="횡단보도 자료를 불러오지 못했습니다" message={data.state.message} onRetry={data.reload} />}
      {data.state.status === "ready" && (
        <FieldBody crosswalks={data.state.data.crosswalks} sources={data.state.data.meta.sources.map((s) => `${s.name} (${s.file}, sha256 ${s.sha256.slice(0, 12)}…)`).join(" · ")} />
      )}
    </div>
  );
}
