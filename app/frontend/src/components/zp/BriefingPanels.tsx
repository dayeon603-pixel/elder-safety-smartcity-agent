import type { ReactNode } from "react";
import { feedbackAvailable, getFeedbackSummary } from "../../api/feedback";
import { useApi } from "../../api/useApi";
import { CROSSWALK_MAX_LENGTH_M } from "../../config";
import { heatRankBand } from "../../lib/rankBand";
import { apiEvidence, evidenceFor } from "../../lib/zpEvidence";
import { ActionFeedback } from "./ActionFeedback";
import { DocumentLink } from "./DocumentLink";
import { Badge } from "../Badge";
import { Card } from "../Card";
import { InfoTip } from "../InfoTip";
import { EmptyState } from "../Notice";
import { COLOR_BASELINE, COLOR_OURS } from "../ComparisonChart";
import type { ActionsResponse } from "../../api/zpTypes";
import type { AsyncState } from "../../api/useApi";
import type { AedPanelModel, BriefingModel, HeatPanelModel, SilverPanelModel } from "../../lib/zpBriefing";
import { ALERT_CLASS_LABEL } from "../../lib/zpBriefing";
import { ZP_GLOSS, moduleLabel } from "../../lib/zpGloss";
import { pct0 } from "../../lib/zpSilver";
import { Fact } from "./Fact";
import { TopicBadges } from "./TopicBadges";

const SHELTER_LIST_MAX = 3;
/** Walking safety first: silver-zone actions lead the list, then heat, then AED. */
const MODULE_ORDER: Readonly<Record<string, number>> = { silver: 0, heat: 1, aed: 2 };
const MODULE_ORDER_DEFAULT = 3;
const SIGNAL_LIST_MAX = 5;

function AlertBadge({ label, on }: { label: string; on: boolean | null }) {
  return <Badge tone={on === true ? "accent" : "neutral"}>{label} {on === null ? "평가 안 함" : on ? "있음" : "없음"}</Badge>;
}

export function HeatPanel({ h, regionName }: { h: HeatPanelModel; regionName: string }) {
  return (
    <Card title="폭염" caption={`${regionName}의 오늘 위험`} id="bp-heat" actions={<TopicBadges tags={["ai_predict", "public_data"]} />}>
      <dl className="m-0">
        <Fact
          label={<>우리 모형 위험 순위<InfoTip label="위험 순위">{ZP_GLOSS.rank}</InfoTip></>}
          value={h.rank === null ? "–" : <>{heatRankBand(h.rank, h.nRegions)}{h.nRegions !== null && <span className="text-body font-normal text-ink-muted"> (전국 {h.nRegions}곳 중)</span>}</>}
          tone="accent"
        />
        <div className="border-t border-line py-3">
          <dt className="text-small text-ink-muted">특보와 우리 경보</dt>
          <dd className="m-0 mt-1 flex flex-wrap gap-2">
            <AlertBadge label="특보" on={h.alertedB1} />
            <AlertBadge label="우리 경보" on={h.alertedOurs} />
          </dd>
          <dd className="m-0 mt-1 text-small text-ink-muted">{ALERT_CLASS_LABEL[h.alertClass]}</dd>
        </div>
        <Fact label="실제 온열질환자" value={`${h.casesActual}명`} />
        <Fact label="배정된 방문팀" value={`${h.teams}팀`} />
        <Fact
          label="가까운 열린 쉼터"
          value={h.shelterData ? `${h.shelterCount}곳` : "자료 없음"}
          note={h.shelterData ? (h.shelters.length > 0 ? h.shelters.slice(0, SHELTER_LIST_MAX).map((s) => s.name).join(" · ") : "야간·주말 운영 쉼터 없음") : "이 지역은 쉼터 좌표 자료가 없습니다"}
        />
      </dl>
    </Card>
  );
}

export function SilverPanel({ s }: { s: SilverPanelModel }) {
  if (!s.available) {
    return (
      <Card title="노인 보행" caption={s.note ?? "서울 지도만 분석"} id="bp-silver">
        <p className="text-body font-semibold">서울 지도만 분석했습니다.</p>
        <p className="mt-1 text-small text-ink-muted">실버존 비교는 서울 2024 자료로만 계산해, 이 시군구의 구역 수는 아직 없습니다.</p>
      </Card>
    );
  }
  return (
    <Card title="노인 보행" caption="구역과 사고다발지" id="bp-silver" actions={<TopicBadges tags={["ai_optimize", "public_data"]} />}>
      <dl className="m-0">
        <Fact label="현행 실버존 → 에이전트 추천" value={<><span className="text-ink-muted">{s.currentZones}곳</span> → <span className="text-accent">{s.ourPicks}곳</span></>} />
        <Fact label="2024 사고다발지" value={`${s.hotspots}곳`} />
        <Fact
          label={<>신호 시간이 모자라는 횡단보도<InfoTip label="신호 부족">{ZP_GLOSS.greenDeficit}</InfoTip></>}
          value={s.greenDeficit === null ? "자료 없음" : `${s.greenDeficit}곳`}
          note={s.greenDeficit === null ? "이 구의 신호 시간 자료가 아직 연결되지 않았습니다" : undefined}
        />
      </dl>
    </Card>
  );
}

export function AedPanel({ a, note }: { a: AedPanelModel | null; note: string | null }) {
  if (a === null) {
    return (
      <Card title="AED 밤 공백" caption="서울만 분석" id="bp-aed">
        <p className="text-body font-semibold">{note === null || note.includes("서울만") ? "서울만 분석했습니다." : note}</p>
        <p className="mt-1 text-small text-ink-muted">이 시군구는 AED 밤 공백 분석 범위(서울 2024)에 들어 있지 않습니다. 폭염과 노인 보행 칸은 전국에서 볼 수 있습니다.</p>
      </Card>
    );
  }
  const rows: { label: string; v: number | null; ours: boolean }[] = [
    { label: "낮", v: a.dayOpen, ours: false },
    { label: "밤", v: a.nightOpen, ours: true },
  ];
  return (
    <Card title="AED 밤 공백" caption="열려 있는 AED 비율 (평일)" id="bp-aed" actions={<TopicBadges tags={["ai_predict", "public_data"]} />}>
      <ul className="m-0 list-none space-y-3 p-0" aria-label="낮과 밤에 열린 AED 비율">
        {rows.map((r) => (
          <li key={r.label} className="grid grid-cols-[2rem_1fr_3rem] items-center gap-3">
            <span className="text-small text-ink-muted">{r.label}</span>
            <span aria-hidden="true" className="block h-4 bg-surface-inset">
              <span className="block h-full" style={{ width: r.v === null ? "0%" : pct0(r.v), backgroundColor: r.ours ? COLOR_OURS : COLOR_BASELINE }} />
            </span>
            <span className={`num text-right text-body font-semibold ${r.ours ? "text-accent" : "text-ink-muted"}`}>{r.v === null ? "–" : pct0(r.v)}</span>
          </li>
        ))}
      </ul>
      <h3 className="mt-5 text-body font-semibold">24시간 전환 후보 {a.candidates.length}곳</h3>
      {a.candidates.length === 0 ? (
        <p className="mt-1 text-small text-ink-muted">후보가 없습니다.</p>
      ) : (
        <ol className="mt-2 list-decimal space-y-2 pl-5 text-small">
          {a.candidates.map((c) => (
            <li key={`${c.org}|${c.address}`}>
              <span className="font-medium">{c.org}</span>
              <span className="block text-ink-muted">{c.address}{c.place !== null && ` · ${c.place}`}</span>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

interface ActionListProps {
  model: BriefingModel;
  code: string;
  date: string;
  /** 오늘 최고기온 from /actions, when it has arrived. */
  tmaxC: number | null;
  /** Called after the server accepted a 담당자 decision, so the 길 panel can refresh its 현장 기록. */
  onSaved: () => void;
}

/** The one prioritized list: API order is priority order. Check boxes are local, for use on screen; they print as empty boxes. */
export function ActionList({ model, code, date, tmaxC, onSaved }: ActionListProps): ReactNode {
  const available = useApi(feedbackAvailable);
  const open = available.state.status === "loading" ? null : available.state.status === "ready" && available.state.data;
  const ordered = model.actions.map((a, i) => ({ a, i })).sort((x, y) => (MODULE_ORDER[x.a.module] ?? MODULE_ORDER_DEFAULT) - (MODULE_ORDER[y.a.module] ?? MODULE_ORDER_DEFAULT) || x.i - y.i);
  return (
    <Card title="오늘의 행동" caption="우선순위 순입니다. 각 행동의 이유가 바로 아래에 있습니다." id="bp-actions">
      <p className="mb-4 border border-line bg-surface-sunken px-3 py-2 text-small text-ink-muted print:hidden">AI는 추천, 결정은 담당자. 채택·보류·부적합을 누르면 현장 판단이 기록됩니다.</p>
      {model.actions.length === 0 ? (
        <EmptyState title="오늘 제안할 행동이 없습니다">이 시군구는 오늘 세 모듈 모두에서 새로 제안할 항목이 없습니다.</EmptyState>
      ) : (
        <ol className="m-0 list-none space-y-4 p-0">
          {ordered.map(({ a, i }, pos) => {
            const ev = apiEvidence(a) ?? evidenceFor(a.module, model, tmaxC);
            return (
              <li key={`${a.module}-${i}`} className="grid grid-cols-[auto_1fr] gap-x-3 break-inside-avoid">
                <input type="checkbox" id={`act-${i}`} aria-describedby={`act-${i}-why`} className="mt-1.5 h-5 w-5 accent-[#0072B2]" />
                <div>
                  <label htmlFor={`act-${i}`} className="block text-body font-semibold">
                    <span className="num mr-2 text-ink-faint">{pos + 1}.</span>
                    <span className="mr-2 align-middle"><Badge tone="accent">{moduleLabel(a.module)}</Badge></span>
                    {a.text}
                  </label>
                  <p id={`act-${i}-why`} className="mt-1 text-small text-ink-muted">이유: {a.reason}</p>
                  <details className="mt-1 text-small">
                    <summary className="inline-block min-h-8 cursor-pointer font-medium text-accent">왜?</summary>
                    <dl className="m-0 mt-1 grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1 border-l-2 border-line-strong pl-3">
                      {ev.rows.map((r) => (
                        <div key={r.label} className="contents">
                          <dt className="text-ink-muted">{r.label}</dt>
                          <dd className="num m-0 text-right font-medium">{r.value}</dd>
                        </div>
                      ))}
                    </dl>
                    <p className="mt-1 border-l-2 border-line-strong pl-3 text-caption text-ink-faint">출처: {ev.source}</p>
                  </details>
                  <DocumentLink sggCode={code} date={date} actionId={a.action_id ?? null} />
                  <ActionFeedback sggCode={code} date={date} actionId={a.action_id ?? `${a.module}.${i}`} available={open} onSaved={onSaved} />
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </Card>
  );
}

function Big({ label, children, note }: { label: ReactNode; children: ReactNode; note?: string }) {
  return (
    <div className="border-t border-line py-3 first:border-t-0 first:pt-0">
      <dt className="text-small text-ink-muted">{label}</dt>
      <dd className="num m-0 mt-1 text-h2 font-semibold">{children}</dd>
      {note !== undefined && <dd className="m-0 mt-1 text-small text-ink-muted">{note}</dd>}
    </div>
  );
}

/** 길: the first and largest panel. Silver-zone recommendation, signal extension, and the 담당자 decision record. */
export function RoadPanel({ s, regionName, code, actions, savedTick }: {
  s: SilverPanelModel;
  regionName: string;
  code: string;
  actions: AsyncState<ActionsResponse>;
  savedTick: number;
}): ReactNode {
  const summary = useApi(() => getFeedbackSummary(code), { key: `${code}|${savedTick}` });
  const counts = summary.state.status === "ready" ? summary.state.data : null;
  const signals = actions.status === "ready" && Array.isArray(actions.data.signals) ? actions.data.signals : null;
  const signalNote = actions.status === "ready" ? (Array.isArray(actions.data.signals) ? actions.data.signals_coverage.note : actions.data.signals.note) : null;
  return (
    <Card title="길" caption={`${regionName}: 실버존 추천 · 신호 연장 · 현장 기록`} id="bp-road" actions={<TopicBadges tags={["ai_optimize", "public_data"]} />}>
      <div className="grid gap-6 lg:grid-cols-2">
        <dl className="m-0">
          {s.available ? (
            <>
              <Big label="현행 실버존 → 에이전트 추천">
                <span className="text-ink-muted">{s.currentZones}곳</span> → <span className="text-accent">{s.ourPicks}곳</span>
              </Big>
              <Big label="2024 사고다발지" note={s.hotspotsCovered === null ? undefined : `현행 구역이 덮는 곳 ${s.hotspotsCovered.current}곳 → 에이전트 추천이 덮는 곳 ${s.hotspotsCovered.ours}곳`}>{s.hotspots}곳</Big>
            </>
          ) : (
            <Big label="실버존 비교" note={s.note ?? "실버존 비교는 서울 2024 자료로만 계산합니다."}>서울만 분석</Big>
          )}
          <Big
            label={<>신호 시간이 모자라는 횡단보도<InfoTip label="신호 부족">{ZP_GLOSS.greenDeficit}</InfoTip></>}
            note={s.greenDeficit === null ? "이 시군구의 신호 시간 자료가 아직 연결되지 않았습니다" : undefined}
          >
            {s.greenDeficit === null ? "자료 없음" : `${s.greenDeficit}곳`}
          </Big>
        </dl>
        <div>
          <h3 className="text-body font-semibold">신호 연장 권고 (초안)</h3>
          {actions.status === "loading" && <p className="mt-2 text-small text-ink-muted" role="status">불러오는 중</p>}
          {actions.status === "error" && <p className="mt-2 text-small text-danger" role="alert">불러오지 못했습니다: {actions.message}</p>}
          {signals !== null && signals.length > 0 && (
            <ul className="m-0 mt-2 list-none space-y-2 p-0 text-small">
              {signals.slice(0, SIGNAL_LIST_MAX).map((g) => (
                <li key={g.crosswalk_id}>
                  <span className="font-medium">{g.address}</span>
                  {g.length_m > CROSSWALK_MAX_LENGTH_M ? (
                    <span className="num block text-ink-muted">자료 확인 필요: 길이 {g.length_m}m는 횡단보도로 보기 어렵습니다</span>
                  ) : (
                    <span className="num block text-ink-muted">횡단 {g.length_m}m · 녹색 {g.green_s}초 → 필요 {g.required_green_s}초 · <span className="text-accent">+{g.extend_s}초</span></span>
                  )}
                </li>
              ))}
            </ul>
          )}
          {signals !== null && signals.length === 0 && <p className="mt-2 text-small text-ink-muted">연장이 필요한 횡단보도가 없습니다.</p>}
          {signalNote != null && <p className="mt-2 text-caption text-ink-muted">{signalNote}</p>}
          <h3 className="mt-5 text-body font-semibold">현장 기록</h3>
          {counts === null ? (
            <p className="mt-1 text-small text-ink-muted">아직 기록이 없거나 기록 창구가 준비 중입니다.</p>
          ) : (
            <p className="mt-1 text-small" data-testid="feedback-summary">
              {regionName} 담당자 판단: 채택 <span className="num font-semibold">{counts.채택}</span> · 보류 <span className="num font-semibold">{counts.보류}</span> · 부적합 <span className="num font-semibold">{counts.부적합}</span>
            </p>
          )}
        </div>
      </div>
    </Card>
  );
}
