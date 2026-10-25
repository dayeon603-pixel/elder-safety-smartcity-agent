import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { describeHeatError, heatApi } from "../api/heat";
import { useApi } from "../api/useApi";
import type { AsyncState } from "../api/useApi";
import { ZP_MOCK, describeZpError, zpApi } from "../api/zp";
import { PageHeader } from "../components/AppShell";
import { Notice } from "../components/Notice";
import { Skeleton } from "../components/Skeleton";
import { BUILD_COLUMNS, IS_WALK_FRAMING, DAILY_PROBLEMS, SERVICE_FULL_NAME, TOPIC_SENTENCE, SERVICE_SUBTITLE, SERVICE_TAGLINE, ZP_FACT_HEAT_DEATH_60, ZP_FACT_WALK_DEATH_65, ZP_MOCK_NOTICE } from "../config";
import { ApiError, request } from "../api/client";
import { loadFallEvidence } from "../lib/fallEvidence";
import { heroPair } from "../lib/heatEval";
import { ZP_GLOSS } from "../lib/zpGloss";
import { pct0, silverHeadline } from "../lib/zpSilver";

const RULES: readonly { place: string; rule: string }[] = [
  { place: "현행 실버존", rule: "시설 둘레 300m" },
  { place: "에이전트", rule: "사고가 난 곳과 고령자 보행 기준으로 같은 개수를 다시 배치" },
];

function Fact({ big, text, source }: { big: ReactNode; text: ReactNode; source: string }) {
  return (
    <li className="border border-line bg-surface px-4 py-4 sm:px-6">
      <p className="num text-display font-semibold text-ink">{big}</p>
      <p className="mt-1 text-body">{text}</p>
      <p className="mt-2 text-caption text-ink-faint">{source}</p>
    </li>
  );
}

function ProblemStrip() {
  return (
    <section aria-labelledby="problem-title" className="space-y-3">
      <h2 id="problem-title" className="text-h2">{IS_WALK_FRAMING ? "고령자가 가장 많이 다치는 곳은 길 위입니다" : "고령자가 가장 많이 다치는 세 곳: 길, 폭염, 밤"}</h2>
      <ul className="m-0 grid list-none gap-4 p-0 md:grid-cols-2">
        <Fact big={`${ZP_FACT_WALK_DEATH_65}%`} text="보행 사망자 중 65세 이상 (3명 중 2명)" source="도로교통공단" />
        <Fact big={`${ZP_FACT_HEAT_DEATH_60}%`} text={IS_WALK_FRAMING ? "폭염 사망자 중 60세 이상 (확장 모듈의 근거)" : "폭염 사망자 중 60세 이상"} source="질병관리청 온열질환 감시" />
      </ul>
    </section>
  );
}

function SharedFailure() {
  return (
    <section aria-labelledby="fail-title" className="space-y-3">
      <h2 id="fail-title" className="text-h2">실버존은 시설 둘레에 그어져 있습니다</h2>
      <ul className="m-0 grid list-none gap-4 p-0 md:grid-cols-2">
        {RULES.map((r) => (
          <li key={r.place} className="border border-line bg-surface px-4 py-3 sm:px-6">
            <p className="text-small font-medium text-ink-muted">{r.place}</p>
            <p className="text-body font-semibold">{r.rule}</p>
          </li>
        ))}
      </ul>
      <p className="max-w-prose text-body text-ink-muted">규칙이 닿지 않는 곳에 고령자가 있습니다. 에이전트는 같은 개수의 구역으로 그곳을 먼저 지킵니다.</p>
    </section>
  );
}

function BuildStrip() {
  return (
    <section aria-labelledby="build-title" className="space-y-3">
      <h2 id="build-title" className="text-h2">어떻게 만들었나</h2>
      <div className="grid gap-4 md:grid-cols-3">
        {BUILD_COLUMNS.map((c) => (
          <div key={c.title} className="border border-line bg-surface px-4 py-4 sm:px-6">
            <h3 className="text-body font-semibold">{c.title}</h3>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-small text-ink-muted">
              {c.items.map((i) => <li key={i}>{i}</li>)}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}

function ModuleCard({
  title, label, before, after, beforeLabel, afterLabel, note, to, cta, state, reload,
}: {
  title: string;
  label: ReactNode;
  before?: string;
  after?: string;
  beforeLabel: string;
  afterLabel: string;
  note?: ReactNode;
  to: string;
  cta: string;
  state: AsyncState<unknown>;
  reload: () => void;
}) {
  return (
    <li className="flex flex-col border border-line bg-surface">
      <div className="flex-1 space-y-3 px-4 py-4 sm:px-6">
        <h3 className="text-h3">{title}</h3>
        {state.status === "ready" ? (
          <>
            <p className="text-small text-ink-muted">{label}</p>
            <p className="num flex items-baseline gap-2 text-display font-semibold">
              <span className="text-ink-muted"><span className="sr-only">{beforeLabel} </span>{before}</span>
              <span aria-hidden="true" className="text-ink-faint">→</span>
              <span className="text-accent"><span className="sr-only">{afterLabel} </span>{after}</span>
            </p>
            <p className="text-caption text-ink-faint" aria-hidden="true">{beforeLabel} → {afterLabel}</p>
            {note !== undefined && <p className="text-caption text-ink-faint">{note}</p>}
          </>
        ) : state.status === "error" ? (
          <div role="alert">
            <p className="text-body font-semibold text-danger">불러오지 못했습니다</p>
            <p className="mt-1 text-small text-ink-muted">{state.message}</p>
            <button type="button" onClick={reload} className="mt-2 text-small font-medium text-accent underline">다시 시도</button>
          </div>
        ) : (
          <div role="status">
            <Skeleton className="h-12 w-40" />
            <span className="sr-only">{title} 결과를 불러오는 중</span>
          </div>
        )}
      </div>
      <Link to={to} className="flex min-h-12 items-center justify-between border-t border-line px-4 py-3 font-semibold no-underline hover:bg-accent-wash sm:px-6">
        {cta}
        <span aria-hidden="true">→</span>
      </Link>
    </li>
  );
}

/** Silver, heat and AED headline cards (exported for the /evidence hub). */
export function ModuleCards({ collapseExtensions = IS_WALK_FRAMING }: { collapseExtensions?: boolean }) {
  const heat = useApi(() => heatApi.eval("F"), { describe: describeHeatError });
  const silver = useApi(zpApi.silverSummary, { describe: describeZpError });
  const aed = useApi(zpApi.aedSummary, { describe: describeZpError });
  const hp = heat.state.status === "ready" ? heroPair(heat.state.data) : null;
  const sh = silver.state.status === "ready" ? silverHeadline(silver.state.data.all) : null;
  const ad = aed.state.status === "ready" ? aed.state.data : null;
  return (
    <section aria-labelledby="mod-title" className="space-y-3">
      <h2 id="mod-title" className="text-h2">같은 예산, 다른 결과</h2>
      <ul className="m-0 grid list-none gap-4 p-0">
        <ModuleCard
          title="실버존"
          label={<>커버율<span className="text-ink-faint"> ({ZP_GLOSS.coverage})</span></>}
          before={sh === null ? undefined : pct0(sh.current.coverage)}
          after={sh === null ? undefined : pct0(sh.ours.coverage)}
          beforeLabel="현행 실버존"
          afterLabel="에이전트"
          note="서울 · 같은 개수의 구역으로"
          to="/silver"
          cta="실버존 지도 보기"
          state={silver.state}
          reload={silver.reload}
        />
      </ul>
      <details open={!collapseExtensions} className="border border-line bg-surface">
        <summary className="min-h-12 cursor-pointer px-4 py-3 text-body font-semibold sm:px-6">{collapseExtensions ? "확장 모듈: 폭염 · AED 밤 공백" : "더위와 응급: 폭염 · AED 밤 공백"}</summary>
        <ul className="m-0 grid list-none gap-4 p-4 sm:p-6 lg:grid-cols-2">
          <ModuleCard
            title="폭염"
            label={<>환자 포착률<span className="text-ink-faint"> ({ZP_GLOSS.capture})</span></>}
            before={hp === null ? undefined : pct0(hp.b1.point)}
            after={hp === null ? undefined : pct0(hp.ours.point)}
            beforeLabel="특보 규칙"
            afterLabel="에이전트"
            note={hp?.delta != null ? `같은 경보 수로 +${Math.round(hp.delta.point * 100)}%p` : undefined}
            to="/demo"
            cta="폭염 데모 보기"
            state={heat.state}
            reload={heat.reload}
          />
          <ModuleCard
            title="AED 밤 공백"
            label="열려 있는 AED 비율 (평일)"
            before={ad === null ? undefined : pct0(ad.day_open_share)}
            after={ad === null ? undefined : pct0(ad.night_open_share)}
            beforeLabel="낮"
            afterLabel="밤"
            note="서울 2024 · 24시간 전환 후보를 구별로 제시"
            to="/aed"
            cta="밤 공백 보기"
            state={aed.state}
            reload={aed.reload}
          />
        </ul>
      </details>
    </section>
  );
}

const ENTRY_LINK =
  "inline-flex min-h-12 items-center justify-center border px-6 text-body font-semibold no-underline";

const PARTS: readonly { to: string; title: string; text: string }[] = [
  { to: "/app", title: "도시 운영 지도", text: "길, 더위, 응급 데이터를 한 지도에서 보고 조치를 정합니다" },
  { to: "/field", title: "현장 에이전트", text: "횡단보도 카메라가 보행 속도와 쓰러짐을 보고 경보와 119 초안을 만듭니다" },
  { to: "/evidence", title: "에이전트 분석", text: "실버존 위치, 폭염 경보, AED 밤 공백을 공식 기준과 같은 예산으로 비교합니다" },
  { to: "/citizen", title: "시민 화면", text: "동네를 고르면 큰 글씨로 길, 더위, 응급 정보를 보여 줍니다" },
];

function Parts() {
  return (
    <section aria-labelledby="parts-title" className="space-y-3">
      <h2 id="parts-title" className="text-h2">네 부분으로 이루어져 있습니다</h2>
      <ul className="m-0 grid list-none gap-4 p-0 sm:grid-cols-2 lg:grid-cols-4">
        {PARTS.map((p) => (
          <li key={p.to} className="flex flex-col border border-line bg-surface">
            <div className="flex-1 px-4 py-4">
              <h3 className="text-h3">{p.title}</h3>
              <p className="mt-2 text-small text-ink-muted">{p.text}</p>
            </div>
            <Link to={p.to} className="flex min-h-12 items-center justify-between border-t border-line px-4 py-3 font-semibold no-underline hover:bg-accent-wash">
              열기<span aria-hidden="true">→</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

function EvidenceTile({ title, before, after, beforeLabel, afterLabel, note, state, reload, to }: {
  title: string; before?: string; after?: string; beforeLabel: string; afterLabel: string; note: string;
  state: AsyncState<unknown>; reload: () => void; to: string;
}) {
  return (
    <li className="border border-line bg-surface px-4 py-4">
      <h3 className="text-small font-medium text-ink-muted">{title}</h3>
      {state.status === "ready" ? (
        <>
          <p className="num flex items-baseline gap-2 text-display font-semibold">
            <span className="text-ink-muted"><span className="sr-only">{beforeLabel} </span>{before}</span>
            <span aria-hidden="true" className="text-ink-faint">→</span>
            <span className="text-accent"><span className="sr-only">{afterLabel} </span>{after}</span>
          </p>
          <p className="text-caption text-ink-faint">{beforeLabel} → {afterLabel}</p>
          <p className="mt-1 text-caption text-ink-faint">{note}</p>
        </>
      ) : state.status === "error" ? (
        <div role="alert">
          <p className="text-small text-danger">불러오지 못했습니다: {state.message}</p>
          <button type="button" onClick={reload} className="mt-1 text-small font-medium text-accent underline">다시 시도</button>
        </div>
      ) : (
        <div role="status"><Skeleton className="h-12 w-40" /><span className="sr-only">{title} 결과를 불러오는 중</span></div>
      )}
      <Link to={to} className="mt-2 inline-block text-small font-medium">근거 보기 →</Link>
    </li>
  );
}

interface HeatHeadline {
  b1: number;
  ours: number;
  label: string;
}

/** Pooled 2022-25 capture from /heat/eval/pooled; if that route is missing, the latest year from /heat/eval. */
async function loadHeatHeadline(): Promise<HeatHeadline> {
  try {
    const pooled = await request<{ test_years: number[]; rows: { method: string; metric: string; value: number }[] }>("/heat/eval/pooled?variant=F");
    const pick = (m: string): number | undefined => pooled.rows.find((r) => r.method === m && r.metric === "capture")?.value;
    const b1 = pick("B1");
    const ours = pick("OURS");
    if (b1 !== undefined && ours !== undefined) {
      const years = pooled.test_years;
      return { b1, ours, label: `시험 ${years[0]}–${years[years.length - 1]}년 합산` };
    }
  } catch (err) {
    if (!(err instanceof ApiError)) throw err;
  }
  const pair = heroPair(await heatApi.eval("F"));
  if (pair === null) throw new Error("heat evaluation has no capture rows");
  return { b1: pair.b1.point, ours: pair.ours.point, label: "가장 최근 시험 연도" };
}

/** One row, three numbers, all read from the APIs (and the bundled cross-validation file for the fall model). */
function EvidenceRow() {
  const heat = useApi(loadHeatHeadline, { describe: describeHeatError });
  const silver = useApi(zpApi.silverSummary, { describe: describeZpError });
  const fall = useApi(loadFallEvidence);
  const hp = heat.state.status === "ready" ? heat.state.data : null;
  const sh = silver.state.status === "ready" ? silverHeadline(silver.state.data.all) : null;
  const fe = fall.state.status === "ready" ? fall.state.data : null;
  return (
    <section aria-labelledby="ev-title" className="space-y-3">
      <h2 id="ev-title" className="text-h2">같은 예산, 다른 결과</h2>
      <ul className="m-0 grid list-none gap-4 p-0 md:grid-cols-3">
        <EvidenceTile
          title="실버존 커버율" before={sh === null ? undefined : pct0(sh.current.coverage)} after={sh === null ? undefined : pct0(sh.ours.coverage)}
          beforeLabel="현행 실버존" afterLabel="에이전트" note="서울 · 같은 개수의 구역으로 다음 해 사상자 포함" state={silver.state} reload={silver.reload} to="/silver"
        />
        <EvidenceTile
          title="폭염 환자 포착률" before={hp === null ? undefined : pct0(hp.b1)} after={hp === null ? undefined : pct0(hp.ours)}
          beforeLabel="특보 규칙" afterLabel="에이전트" note={hp === null ? "" : `${hp.label} · 같은 경보 수로 (경보 낸 지역의 환자 ÷ 전체 환자)`} state={heat.state} reload={heat.reload} to="/demo"
        />
        <EvidenceTile
          title="낙상 감지 지연(중앙값)" before={fe === null ? undefined : `${fe.rule.latMed.est.toFixed(2)}초`} after={fe === null ? undefined : `${fe.ai.latMed.est.toFixed(2)}초`}
          beforeLabel="규칙" afterLabel="AI 모델" note="UR Fall 공개 데이터, 교차검증 · 실제 횡단보도 영상은 아님" state={fall.state} reload={fall.reload} to="/field"
        />
      </ul>
    </section>
  );
}

/** Product overview. Walk framing keeps the walking story; combined framing shows the four parts and one evidence row. */
export function HomePage() {
  if (!IS_WALK_FRAMING) {
    return (
      <>
        <PageHeader eyebrow={TOPIC_SENTENCE} title={SERVICE_FULL_NAME} lead={SERVICE_TAGLINE} />
        {ZP_MOCK && <div className="mb-6"><Notice>{ZP_MOCK_NOTICE}</Notice></div>}
        <p className="max-w-prose text-lead">{SERVICE_SUBTITLE}</p>
        <div className="mb-10 mt-6 flex flex-wrap gap-3">
          <Link to="/twin" className={`${ENTRY_LINK} border-accent bg-accent text-white hover:bg-accent-strong hover:text-white`}>관악구 디지털 트윈 열기</Link>
          <Link to="/app" className={`${ENTRY_LINK} border-line-strong bg-surface text-ink hover:bg-surface-sunken`}>도시 운영 지도 열기</Link>
          <Link to="/field" className={`${ENTRY_LINK} border-line-strong bg-surface text-ink hover:bg-surface-sunken`}>현장 에이전트 데모</Link>
          <Link to="/citizen" className={`${ENTRY_LINK} border-line-strong bg-surface text-ink hover:bg-surface-sunken`}>시민 화면</Link>
        </div>
        <div className="space-y-12">
          <Parts />
          <EvidenceRow />
          <ProblemStrip />
          <SharedFailure />
          <BuildStrip />
          <section aria-labelledby="cta-title" className="flex flex-wrap items-center justify-between gap-4 border border-accent bg-accent-wash px-4 py-6 sm:px-8">
            <div>
              <h2 id="cta-title" className="text-h2">오늘의 에이전트 브리핑</h2>
              <p className="mt-1 text-body text-ink-muted">시군구 하나를 고르면 길(실버존·신호), 더위, 응급(AED) 세 칸과 오늘 할 일이 한 장으로 나옵니다.</p>
            </div>
            <Link to="/briefing" className="inline-flex min-h-12 items-center border border-accent bg-accent px-6 text-body font-semibold text-white no-underline hover:bg-accent-strong hover:text-white">브리핑 열기</Link>
          </section>
        </div>
      </>
    );
  }
  return (
    <>
      <PageHeader eyebrow={TOPIC_SENTENCE} title={SERVICE_FULL_NAME} lead={SERVICE_TAGLINE} />
      {ZP_MOCK && <div className="mb-6"><Notice>{ZP_MOCK_NOTICE}</Notice></div>}
      <p className="max-w-prose text-lead">{SERVICE_SUBTITLE}</p>
      <p className="mt-2 text-small text-ink-muted">{DAILY_PROBLEMS.join(" · ")}. 재난이 아니라 매일의 생활입니다.</p>
      <div className="mb-10 mt-6 flex flex-wrap gap-3">
        <Link to="/twin" className={`${ENTRY_LINK} border-accent bg-accent text-white hover:bg-accent-strong hover:text-white`}>관악구 디지털 트윈 열기</Link>
        <Link to="/field" className={`${ENTRY_LINK} border-line-strong bg-surface text-ink hover:bg-surface-sunken`}>실버존 에이전트 데모 보기</Link>
        <Link to="/citizen" className={`${ENTRY_LINK} border-line-strong bg-surface text-ink hover:bg-surface-sunken`}>고령자·보호자</Link>
        <Link to="/briefing" className={`${ENTRY_LINK} border-line-strong bg-surface text-ink hover:bg-surface-sunken`}>지자체 담당자</Link>
      </div>
      <div className="space-y-12">
        <ProblemStrip />
        <SharedFailure />
        <ModuleCards />
        <BuildStrip />
        <section aria-labelledby="cta-title" className="flex flex-wrap items-center justify-between gap-4 border border-accent bg-accent-wash px-4 py-6 sm:px-8">
          <div>
            <h2 id="cta-title" className="text-h2">오늘의 에이전트 브리핑</h2>
            <p className="mt-1 text-body text-ink-muted">{IS_WALK_FRAMING ? "시군구 하나를 고르면 실버존 추천, 신호 연장, 오늘 할 일이 한 장으로 나옵니다." : "시군구 하나를 고르면 길·더위·응급 세 칸과 오늘 할 일이 한 장으로 나옵니다."}</p>
          </div>
          <Link to="/briefing" className="inline-flex min-h-12 items-center border border-accent bg-accent px-6 text-body font-semibold text-white no-underline hover:bg-accent-strong hover:text-white">
            브리핑 열기
          </Link>
        </section>
      </div>
    </>
  );
}
