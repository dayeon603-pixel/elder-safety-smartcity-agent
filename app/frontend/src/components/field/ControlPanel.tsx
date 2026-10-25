import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { agentRunUrl, apiUrl } from "../../api/agent";
import type { AgentOutcome, AgentStep } from "../../api/agent";
import { assessCrossing, formatMeters, HEAT_STAGE_LABEL, vmsText } from "../../lib/field";
import type { Crosswalk, HeatInfo } from "../../lib/field";
import type { EmergencyEvent } from "../../lib/fieldEmergency";
import { Badge } from "../Badge";
import { Button } from "../Button";
import { AgentLog } from "./AgentLog";
import type { LogEntry } from "./AgentLog";
import type { CamTelemetry } from "./FieldCamera";
import { VmsMock } from "./VmsMock";

export type AgentUi = { status: "loading"; since: number; steps: readonly AgentStep[]; briefing: string; notes: readonly string[] } | { status: "done"; outcome: AgentOutcome };

interface Props {
  crosswalk: Crosswalk;
  heat: HeatInfo;
  tele: CamTelemetry;
  events: readonly EmergencyEvent[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  agents: Readonly<Record<string, AgentUi>>;
  onReset: () => void;
  threshold: number | null;
}

const ms = (v: number): string => `${v.toFixed(1)} ms`;

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-caption text-ink-faint">{label}</dt>
      <dd className="num text-body font-semibold">{children}</dd>
    </div>
  );
}

function Json({ title, text, open = false, devOnly = false }: { title: string; text: string; open?: boolean; devOnly?: boolean }) {
  return (
    <details open={open} className={`border border-line bg-surface ${devOnly ? "dev-only" : ""}`}>
      <summary className="cursor-pointer px-3 py-2 text-small font-medium">{title}</summary>
      <pre className="m-0 max-h-72 overflow-auto border-t border-line bg-surface-sunken p-3 font-mono text-caption">{text}</pre>
    </details>
  );
}

const ELAPSED_TICK_MS = 250;
const MS_PER_S = 1000;

function Planning({ since, steps, briefing, notes }: { since: number; steps: readonly AgentStep[]; briefing: string; notes: readonly string[] }) {
  const [now, setNow] = useState<number>(() => performance.now());
  useEffect(() => {
    const id = setInterval(() => setNow(performance.now()), ELAPSED_TICK_MS);
    return () => clearInterval(id);
  }, []);
  return (
    <p className="text-small text-ink-muted" role="status" data-testid="agent-planning">
      <span aria-hidden="true" className="mr-2 inline-block h-2 w-2 animate-pulse bg-accent" />
      에이전트 계획 중… <span className="num">{((now - since) / MS_PER_S).toFixed(1)} 초</span>
      {steps.length > 0 && (
        <ol className="m-0 mt-2 list-none space-y-1 p-0" aria-label="도착한 단계">
          {steps.map((st, i) => (
            <li key={i} className="animate-rise">
              <span className="num font-mono text-ink-faint">{st.ms === null ? "–" : ms(st.ms)}</span> <span className="font-medium text-ink">{st.name}</span>
              {st.summary !== "" && <span className="break-words"> · {st.summary}</span>}
            </li>
          ))}
        </ol>
      )}
      {notes.map((n) => <span key={n} className="block text-caption">{n}</span>)}
      {briefing !== "" && (
        <span className="mt-2 block whitespace-pre-line border-l-2 border-line-strong bg-surface-sunken px-3 py-2 text-small text-ink">
          {briefing}
          <span className="block text-caption text-ink-faint">작성 중인 초안입니다. 숫자 검증을 통과한 문장이 최종본으로 바뀝니다.</span>
        </span>
      )}
      <span className="block text-caption text-ink-faint">위 경보는 이미 이 브라우저에서 만들어졌습니다. 서버의 계획·실행 기록은 서버가 단계를 보내 주면 바로 채우고, 아니면 끝난 뒤 한꺼번에 나옵니다.</span>
    </p>
  );
}

/** 마을방송 음성: the audio is generated on first request, so it is only fetched when the user asks to listen. */
function VillageAudio({ src, script }: { src: string; script: string | null }) {
  const [on, setOn] = useState(false);
  const [failed, setFailed] = useState(false);
  return (
    <div>
      {!on ? (
        <Button variant="secondary" onClick={() => setOn(true)} className="!min-h-9 !px-3 !text-small">마을방송 음성 듣기</Button>
      ) : (
        <>
          {/* The spoken text is printed below as the transcript. */}
          {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
          <audio controls autoPlay src={src} onError={() => setFailed(true)} className="w-full max-w-sm" aria-label="마을방송 음성 안내" />
        </>
      )}
      {script !== null && <p className="mt-1 text-caption text-ink-muted">방송 문안: {script}</p>}
      {failed && <p className="mt-1 text-caption text-danger" role="status">음성을 만들지 못했습니다(이 서버에 음성 합성이 없을 수 있습니다).</p>}
    </div>
  );
}

function AgentBlock({ ui }: { ui: AgentUi | undefined }) {
  if (ui === undefined) return null;
  if (ui.status === "loading") return <Planning since={ui.since} steps={ui.steps} briefing={ui.briefing} notes={ui.notes} />;
  const o = ui.outcome;
  if (o.kind === "unavailable") {
    return <p className="text-small text-ink-muted">에이전트 서버를 쓸 수 없어 이 화면의 로컬 흐름만 실행했습니다. ({o.reason})</p>;
  }
  const r = o.result;
  return (
    <section aria-label="로컬 에이전트 결과" className="space-y-3 border border-line bg-surface p-3" data-testid="agent-result">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-small font-semibold">로컬 에이전트</h4>
        {r.model !== null && <Badge>{r.model}</Badge>}
        {r.planner !== null && <Badge>{r.planner === "llm" ? "LLM이 계획" : r.planner === "llm+fallback" ? "LLM 계획 + 결정론 보완" : "결정론적 계획"}</Badge>}
        {r.briefingSource === "llm" && r.grounded === true && <Badge tone="success">숫자 검증 통과</Badge>}
        {r.briefingSource === "template" && <Badge>템플릿 브리핑(LLM 문장은 검증 탈락 또는 없음)</Badge>}
        <span className="num text-caption text-ink-faint">호출~응답 {ms(o.clientMs)}{r.totalMs !== null ? ` · 서버 ${ms(r.totalMs)}` : ""} (측정)</span>
      </div>
      {r.modelDigest !== null && <p className="dev-only num break-all text-caption text-ink-faint">모델 digest {r.modelDigest}</p>}
      {r.plannerReason !== null && <p className="text-caption text-ink-muted">{r.plannerReason}</p>}
      <ol className="m-0 list-none space-y-1 p-0">
        {r.steps.map((s, i) => (
          <li key={i} className="text-small">
            <span className="num font-mono text-ink-faint">{s.ms === null ? "–" : ms(s.ms)}</span> <span className="font-medium">{s.name}</span>
            {!s.ok && <> <Badge tone="danger">실패</Badge></>}
            {s.summary !== "" && <span className="break-words text-ink-muted"> · {s.summary}</span>}
          </li>
        ))}
      </ol>
      {r.receipts.length > 0 && (
        <ul className="m-0 list-none space-y-0.5 p-0 text-small">
          {r.receipts.map((x, i) => (
            <li key={i}>{x.target} 수신 <span className="num font-medium">{x.status ?? "–"}</span>{x.id !== null && <span className="num text-ink-faint"> · {x.id}</span>}</li>
          ))}
        </ul>
      )}
      {r.briefing !== null && (
        <blockquote className="m-0 whitespace-pre-line border-l-2 border-line-strong bg-surface-sunken px-3 py-2 text-small">{r.briefing}</blockquote>
      )}
      {r.sceneText !== null && (
        <p className="text-small"><Badge>참고용</Badge> <span className="text-ink-muted">장면 설명: {r.sceneText}</span></p>
      )}
      {r.cap !== null && (
        <div className="space-y-2 border-t border-line pt-2 text-small" data-testid="cap-channel">
          <p>
            <Badge tone={r.cap.xsdValid === true ? "success" : "neutral"}>{r.cap.xsdValid === true ? "OASIS CAP 1.2 스키마 검증 통과" : "CAP 1.2 (스키마 검증 결과 없음)"}</Badge>{" "}
            <span className="text-ink-muted">훈련(Exercise) · 제한 배포 · 발송 안 함</span>
          </p>
          <p className="flex flex-wrap gap-x-4 gap-y-1">
            {r.cap.xmlUrl !== null && <a href={apiUrl(r.cap.xmlUrl)} target="_blank" rel="noreferrer noopener">CAP XML</a>}
            {r.capFeedUrl !== null && <a href={apiUrl(r.capFeedUrl)} target="_blank" rel="noreferrer noopener">CAP 피드(Atom)</a>}
          </p>
          {r.cap.audioUrl !== null && <VillageAudio src={apiUrl(r.cap.audioUrl)} script={r.cap.audioScript} />}
        </div>
      )}
      {r.runId !== null && (
        <p className="text-caption"><a href={agentRunUrl(r.runId)} target="_blank" rel="noreferrer noopener">에이전트 기록</a> <span className="num text-ink-faint">{r.runId} · JSON 원본</span></p>
      )}
    </section>
  );
}

/** 관제 패널: live facts, VMS, agent log; switches to the red emergency state when a fall event exists. */
export function ControlPanel({ crosswalk, heat, tele, events, selectedId, onSelect, agents, onReset, threshold }: Props) {
  const alert = events.length > 0;
  const selected = events.find((e) => e.id === selectedId) ?? events[0] ?? null;
  const requiredMps = crosswalk.lengthM / crosswalk.greenSec;
  const measured = tele.persons.filter((p) => p.speedMps !== null);
  const cautionIds = tele.persons.filter((p) => p.status === "caution").map((p) => p.id);

  const live: LogEntry[] = [
    {
      id: "heat", tag: "(a)", title: "폭염 단계",
      lines: [heat.source === "api" && heat.tmaxC !== null ? `${heat.date} ${heat.regionName} 최고기온 ${heat.tmaxC.toFixed(1)}℃ → ${HEAT_STAGE_LABEL[heat.stage]}` : `직접 선택: ${HEAT_STAGE_LABEL[heat.stage]}`],
    },
    {
      id: "req", tag: "(b)", title: "필요 보행속도",
      lines: [`길이 ${crosswalk.lengthM.toFixed(1)} m ÷ 녹색 ${crosswalk.greenSec} 초 = ${requiredMps.toFixed(2)} m/s`],
    },
    ...tele.persons.map((p): LogEntry => {
      if (p.speedMps === null) return { id: `p${p.id}`, tag: `ID ${p.id}`, title: "속도 측정 중", lines: ["1초 창이 차면 추정합니다."] };
      const a = assessCrossing(crosswalk.lengthM, p.speedMps, crosswalk.greenSec);
      return {
        id: `p${p.id}`, tag: `ID ${p.id}`, title: a.finishes ? "녹색 안에 건널 수 있음" : "녹색 안에 건너기 어려움",
        lines: [`${crosswalk.lengthM.toFixed(1)} ÷ ${p.speedMps.toFixed(2)} = ${a.requiredSec.toFixed(1)} 초 ${a.finishes ? "≤" : ">"} ${crosswalk.greenSec} 초`, ...(a.finishes ? [] : [`부족 ${a.deficitSec.toFixed(1)} 초 → 신호 연장 +${a.extensionSec}초 권고`])],
        tone: a.finishes ? "success" : "danger",
      };
    }),
  ];

  const eventLog: LogEntry[] = selected === null ? [] : [
    { id: "t0", tag: "+0.0 ms", title: `쓰러짐 감지${selected.trackId !== null ? ` (ID ${selected.trackId})` : ""} · ${selected.trigger === "camera" ? "카메라" : "버튼(시뮬레이션)"}`, lines: [selected.detectedAt.toISOString(), ...(selected.aiProb !== null ? [`AI 낙상 모델 확률 ${selected.aiProb.toFixed(2)}${threshold !== null ? ` ≥ 임계 ${threshold}` : ""}`] : []), ...(selected.ruleFiredAlready === null ? [] : [selected.ruleFiredAlready ? "규칙 기준선(60° · 1.5초): 이미 발동" : "규칙 기준선(60° · 1.5초): 아직 발동 전"])], tone: "danger" },
    ...selected.steps.map((s): LogEntry => ({ id: s.id, tag: `+${s.atMs.toFixed(1)} ms`, title: s.title, lines: s.lines, tone: s.id === "payload" || s.id === "cap" || s.id === "fhir" ? "neutral" : "neutral" })),
  ];

  return (
    <section
      aria-labelledby="panel-title"
      data-alert={alert}
      className={`space-y-4 border p-4 sm:p-6 ${alert ? "border-danger bg-danger-wash" : "border-line bg-surface"}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="panel-title" className="text-h3">관제 패널</h2>
          <p className="text-small text-ink-muted">{alert ? "긴급 대응 상태" : "상시 감시 상태"}</p>
        </div>
        {alert && (
          <p role="alert" className="border-2 border-danger bg-surface px-3 py-1 text-body font-bold text-danger" data-testid="alert-stamp">에이전트 긴급 대응</p>
        )}
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
        <Fact label="폭염 단계">{HEAT_STAGE_LABEL[heat.stage]}</Fact>
        <Fact label="최고기온">{heat.tmaxC === null ? "직접 선택" : `${heat.tmaxC.toFixed(1)}℃`}</Fact>
        <Fact label="횡단보도">{formatMeters(crosswalk.lengthM)} · 녹색 {crosswalk.greenSec}초</Fact>
        <Fact label="필요 속도">{requiredMps.toFixed(2)} m/s</Fact>
        <Fact label="추정 속도">{measured.length === 0 ? "–" : measured.map((p) => `ID${p.id} ${p.speedMps?.toFixed(2)}`).join(" · ")}</Fact>
        <Fact label="판정">{alert ? "위험" : !tele.on ? "카메라 꺼짐" : cautionIds.length > 0 ? `주의 (ID ${cautionIds.join(", ")})` : "정상"}</Fact>
      </dl>

      <VmsMock
        active={alert || cautionIds.length > 0}
        text={alert ? "⚠ 보행자 쓰러짐 · 서행 · 정차 준비" : vmsText(heat.stage)}
        idleText="표시 없음"
      />

      {alert && events.length > 1 && (
        <div className="flex flex-wrap gap-2" role="group" aria-label="이벤트 선택">
          {events.map((e) => (
            <Button key={e.id} variant="secondary" aria-pressed={e.id === selected?.id} onClick={() => onSelect(e.id)} className="!min-h-9 !px-3 !text-small">
              {e.trackId === null ? "버튼" : `ID ${e.trackId}`}
            </Button>
          ))}
        </div>
      )}

      <AgentLog entries={alert ? eventLog : live} emptyText="" />

      {selected !== null && (
        <div className="space-y-3">
          <p className="num text-body font-semibold" data-testid="latency">
            감지→경보 생성 {ms(selected.buildMs)}
            {selected.paintMs !== null && <> · 화면 반영 {ms(selected.paintMs)}</>}
            <span className="block text-caption font-normal text-ink-faint">이 브라우저에서 performance.now()로 측정. 1.5초 지속 조건은 감지 이전이라 포함하지 않음.</span>
          </p>
          <AgentBlock ui={agents[selected.id]} />
          <Json title="CAP 1.2 XML (표준: OASIS CAP 1.2 · status=Exercise)" text={selected.capXml} />
          <Json title="CAP 1.2 JSON" text={JSON.stringify(selected.capJson, null, 2)} />
          <Json title="인시던트 JSON" text={JSON.stringify(selected.incident, null, 2)} />
          <Json title="HL7 FHIR R4 Bundle (HTEST · 훈련)" text={JSON.stringify(selected.fhir, null, 2)} />
          <Json title="119 전송 payload 미리보기 (생성됨 · 전송 안 함(훈련))" text={JSON.stringify(selected.payload, null, 2)} devOnly />
          <div className="rounded-none border border-line bg-surface px-3 py-2 text-small">
            <p className="text-caption text-ink-faint">스피커 안내 문구 (모의)</p>
            <p data-testid="speech-text">{selected.speechText}</p>
          </div>
          <Button variant="secondary" onClick={onReset}>경보 해제(시뮬레이션)</Button>
        </div>
      )}
    </section>
  );
}
