import { useId, useState } from "react";
import { useApi } from "../api/useApi";
import { ZP_MOCK, describeZpError, zpApi } from "../api/zp";
import type { SilverMap, SilverNote, SilverStratum } from "../api/zpTypes";
import { PageHeader } from "../components/AppShell";
import { AsyncBoundary } from "../components/AsyncBoundary";
import { Card } from "../components/Card";
import { InfoTip } from "../components/InfoTip";
import { Notice } from "../components/Notice";
import { SkeletonText } from "../components/Skeleton";
import { TopicBadges } from "../components/zp/TopicBadges";
import { MethodBars } from "../components/zp/MethodBars";
import { SilverMap as SilverMapView } from "../components/zp/SilverMap";
import { BOUNDARY_URL } from "../lib/geo";
import { formatInt } from "../lib/format";
import { loadBoundary } from "../lib/zpMap";
import { ZP_GLOSS } from "../lib/zpGloss";
import { pct0, silverBars, silverHeadline } from "../lib/zpSilver";
import { ZP_MOCK_NOTICE, ZP_SEOUL_NOTE, ZP_SILVER_CITY } from "../config";
import type { BoundaryCollection } from "../lib/geo";

function Big({ label, value, tone, ci }: { label: string; value: number; tone: "base" | "ours"; ci: string }) {
  return (
    <div className={`border bg-surface px-4 py-5 sm:px-8 sm:py-6 ${tone === "ours" ? "border-accent" : "border-line"}`}>
      <dt className="text-small font-medium text-ink-muted">{label}</dt>
      <dd className="m-0">
        <span className={`num block text-5xl font-semibold leading-tight tracking-tight sm:text-7xl ${tone === "ours" ? "text-accent" : "text-baseline-strong"}`} style={tone === "base" ? { color: "#4a515c" } : undefined}>
          {pct0(value)}
        </span>
        <span className="num mt-1 block text-caption text-ink-faint">{ci}</span>
      </dd>
    </div>
  );
}

function Headline({ all, newSites, k, radius }: { all: SilverStratum; newSites: SilverStratum; k: number; radius: number }) {
  const h = silverHeadline(all);
  const n = silverHeadline(newSites);
  if (h === null) return <Notice tone="danger">현행·에이전트 분석 결과가 응답에 없습니다.</Notice>;
  return (
    <section aria-label="핵심 비교: 현행 노인보호구역과 에이전트 분석" className="space-y-3">
      <h2 className="text-h2 sm:text-h1">
        같은 {formatInt(k)}곳으로 다음 해 사상자의 <span className="num text-accent">{pct0(h.ours.coverage)}</span>를 구역 안에
      </h2>
      <p className="text-small text-ink-muted">
        커버율<InfoTip label="커버율">{ZP_GLOSS.coverage}</InfoTip>
        · 시험 2022–2024년, 다발지 {formatInt(all.n_hotspots)}곳 · 사상자 {formatInt(all.casualties)}명 · 반경 {radius}m
      </p>
      <dl className="m-0 grid grid-cols-2 gap-3 sm:gap-6">
        <Big label="현행 노인보호구역" value={h.current.coverage} tone="base" ci={`95% 신뢰구간 ${pct0(h.current.lo)}–${pct0(h.current.hi)}`} />
        <Big label="에이전트 분석" value={h.ours.coverage} tone="ours" ci={`95% 신뢰구간 ${pct0(h.ours.lo)}–${pct0(h.ours.hi)}`} />
      </dl>
      <p className="num text-small text-ink-muted">
        차이 +{h.gainPp.toFixed(1)}%p<InfoTip label="%p">{ZP_GLOSS.pp}</InfoTip>
        · 95% 신뢰구간<InfoTip label="95% 신뢰구간">{ZP_GLOSS.ci}</InfoTip>
      </p>
      {n !== null && (
        <p className="max-w-prose border-l-2 border-accent pl-3 text-body">
          신규 다발지<InfoTip label="신규 다발지">{ZP_GLOSS.newSite}</InfoTip>
          {formatInt(newSites.n_hotspots)}곳에서는{" "}
          <span className="num font-semibold">{pct0(n.current.coverage)} → <span className="text-accent-strong">{pct0(n.ours.coverage)}</span></span>
          <span className="num text-ink-muted"> (95% 신뢰구간 {pct0(n.ours.lo)}–{pct0(n.ours.hi)})</span>
        </p>
      )}
    </section>
  );
}

function Notes({ notes }: { notes: readonly SilverNote[] }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <section className="border border-line bg-surface">
      <h2 className="m-0 text-h3">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={id}
          onClick={() => setOpen((o) => !o)}
          className="flex min-h-12 w-full items-center justify-between gap-4 px-4 py-3 text-left text-h3 font-semibold hover:bg-surface-sunken sm:px-6"
        >
          방법과 한계 (질문이 있을 때)
          <span aria-hidden="true" className="text-ink-muted">{open ? "닫기 −" : "열기 +"}</span>
        </button>
      </h2>
      {open && (
        <div id={id} className="space-y-4 border-t border-line px-4 py-4 sm:px-6">
          {notes.map((n) => (
            <div key={n.title}>
              <h3 className="text-body font-semibold">{n.title}</h3>
              <p className="mt-1 max-w-prose text-body text-ink-muted">{n.text}</p>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function MapSection({ radius }: { radius: number }) {
  const map = useApi<SilverMap>(() => zpApi.silverMap(ZP_SILVER_CITY), { describe: describeZpError });
  const boundary = useApi<BoundaryCollection | null>(() => loadBoundary(BOUNDARY_URL).catch(() => null));
  return (
    <Card title="서울 지도" caption={`${ZP_SEOUL_NOTE}. 2024년 시험 폴드 기준입니다.`}>
      <AsyncBoundary state={map.state} onRetry={map.reload} skeleton={<SkeletonText lines={8} label="지도를 불러오는 중" />} errorTitle="지도를 불러오지 못했습니다">
        {(m) => (
          <>
            <SilverMapView points={m.points} boundary={boundary.state.status === "ready" ? boundary.state.data : null} radiusM={m.radius_m || radius} />
            {boundary.state.status === "ready" && boundary.state.data === null && <p className="mt-2 text-caption text-ink-faint">배경 경계를 불러오지 못해 점만 표시합니다.</p>}
          </>
        )}
      </AsyncBoundary>
    </Card>
  );
}

/** /silver: 노인 보행. Same number of zones, picked by risk instead of by the current rule. */
export function SilverPage() {
  const summary = useApi(zpApi.silverSummary, { describe: describeZpError });
  return (
    <>
      <PageHeader eyebrow="노인 보행" title="실버존을 다시 긋습니다" lead="같은 개수의 구역을, 다음 해 사고가 날 곳에 더 가깝게 고릅니다." actions={<TopicBadges tags={["ai_optimize", "public_data"]} />} />
      {ZP_MOCK && <div className="mb-6"><Notice>{ZP_MOCK_NOTICE}</Notice></div>}
      <div className="space-y-10">
        <AsyncBoundary state={summary.state} onRetry={summary.reload} skeleton={<SkeletonText lines={6} label="핵심 비교를 불러오는 중" />} errorTitle="노인 보행 결과를 불러오지 못했습니다">
          {(s) => (
            <>
              <Headline all={s.all} newSites={s.new_sites} k={s.k} radius={s.radius_m} />
              <Card title="방법별 커버율" caption={`같은 ${formatInt(s.k)}곳을 서로 다른 규칙으로 골랐을 때, 다음 해 사상자 중 구역 ${s.radius_m}m 안에 든 비율입니다. 회색은 비교 기준입니다.`}>
                <MethodBars bars={silverBars(s.all.methods)} label="방법별 커버율" />
              </Card>
              <MapSection radius={s.radius_m} />
              <Notes notes={s.notes} />
            </>
          )}
        </AsyncBoundary>
      </div>
    </>
  );
}
