import { useMemo, useState } from "react";
import { useApi } from "../api/useApi";
import { ZP_MOCK, describeZpError, zpApi } from "../api/zp";
import type { AedCandidate, AedGu } from "../api/zpTypes";
import { PageHeader } from "../components/AppShell";
import { AsyncBoundary } from "../components/AsyncBoundary";
import { Card } from "../components/Card";
import type { Column } from "../components/DataTable";
import { DataTable } from "../components/DataTable";
import { InfoTip } from "../components/InfoTip";
import { Notice } from "../components/Notice";
import { SkeletonText } from "../components/Skeleton";
import { TopicBadges } from "../components/zp/TopicBadges";
import { NightGapChart } from "../components/zp/NightGapChart";
import { ZP_AED_GU_TOP, ZP_AED_NIGHT_END, ZP_AED_NIGHT_START, ZP_MOCK_NOTICE, ZP_SEOUL_NOTE } from "../config";
import { formatInt } from "../lib/format";
import { candidatesFor, formatHours, hourData, rankGu } from "../lib/zpAed";
import { ZP_GLOSS } from "../lib/zpGloss";
import { pct0 } from "../lib/zpSilver";

function Summary() {
  const s = useApi(zpApi.aedSummary, { describe: describeZpError });
  return (
    <AsyncBoundary state={s.state} onRetry={s.reload} skeleton={<SkeletonText lines={3} label="요약을 불러오는 중" />} errorTitle="요약을 불러오지 못했습니다">
      {(d) => (
        <section aria-label="핵심 비교: 낮과 밤에 열린 AED" className="space-y-3">
          <h2 className="text-h2 sm:text-h1">
            심정지의 <span className="num text-accent">{pct0(d.night_arrest_share.value)}</span>가 밤에 일어나는데, 그 시간 AED는 {pct0(d.night_open_share)}만 열려 있습니다
          </h2>
          <p className="text-small text-ink-muted">
            공공장소 심정지 {formatInt(d.n_public_arrests)}건 · {d.scope} · 밤 = 22시~06시, 평일 기준
            <InfoTip label="밤에 열린 비율">{ZP_GLOSS.nightOpen}</InfoTip>
          </p>
          <dl className="m-0 grid grid-cols-2 gap-3 sm:gap-6">
            <div className="border border-line bg-surface px-4 py-5 sm:px-8 sm:py-6">
              <dt className="text-small font-medium text-ink-muted">낮에 열린 AED</dt>
              <dd className="m-0"><span className="num block text-5xl font-semibold leading-tight sm:text-7xl" style={{ color: "#4a515c" }}>{pct0(d.day_open_share)}</span></dd>
            </div>
            <div className="border border-accent bg-surface px-4 py-5 sm:px-8 sm:py-6">
              <dt className="text-small font-medium text-ink-muted">밤에 열린 AED</dt>
              <dd className="m-0"><span className="num block text-5xl font-semibold leading-tight text-accent sm:text-7xl">{pct0(d.night_open_share)}</span></dd>
            </div>
          </dl>
          <p className="num text-caption text-ink-faint">
            밤 심정지 비율의 95% 신뢰구간<InfoTip label="95% 신뢰구간">{ZP_GLOSS.ci}</InfoTip> {pct0(d.night_arrest_share.lo)}–{pct0(d.night_arrest_share.hi)}
          </p>
        </section>
      )}
    </AsyncBoundary>
  );
}

function GuRanking({ rows, selected, onSelect }: { rows: readonly AedGu[]; selected: string; onSelect: (gu: string) => void }) {
  const ranked = useMemo(() => rankGu(rows).slice(0, ZP_AED_GU_TOP), [rows]);
  return (
    <ol aria-label="밤에 열린 AED 비율이 낮은 구" className="m-0 list-none space-y-1 p-0">
      {ranked.map((g) => (
        <li key={g.code}>
          <button
            type="button"
            aria-pressed={g.gu === selected}
            onClick={() => onSelect(g.gu)}
            className={`grid min-h-11 w-full grid-cols-[1.5rem_5rem_1fr_3rem] items-center gap-x-3 border px-3 py-2 text-left sm:min-h-10 sm:grid-cols-[1.5rem_6rem_1fr_3rem] ${
              g.gu === selected ? "border-accent bg-accent-wash" : "border-line bg-surface hover:bg-surface-sunken"
            }`}
          >
            <span aria-hidden="true" className="text-small text-ink-faint">·</span>
            <span className="text-body font-medium">{g.gu}</span>
            <span aria-hidden="true" className="block h-3 bg-surface-inset">
              <span className="block h-full bg-accent" style={{ width: pct0(g.night_open_share) }} />
            </span>
            <span className="num text-right text-body font-semibold">{pct0(g.night_open_share)}</span>
          </button>
        </li>
      ))}
    </ol>
  );
}

const CANDIDATE_COLUMNS: readonly Column<AedCandidate>[] = [
  { key: "org", header: "설치 기관", render: (c) => <span className="font-medium">{c.org}</span>, sortValue: (c) => c.org },
  { key: "address", header: "주소", render: (c) => c.address, sortValue: (c) => c.address },
  { key: "place", header: "설치 위치", render: (c) => c.place ?? "–", hideOnMobile: true },
  { key: "hours", header: "현재 운영", render: (c) => formatHours(c.hours), hideOnMobile: true },
  { key: "gain", header: "효과 점수", numeric: true, render: (c) => c.gain_score.toFixed(1), sortValue: (c) => c.gain_score },
];

function Candidates({ gu }: { gu: string }) {
  const list = useApi(() => zpApi.aedCandidates(gu), { key: gu, describe: describeZpError });
  return (
    <AsyncBoundary state={list.state} onRetry={list.reload} skeleton={<SkeletonText lines={5} label="후보를 불러오는 중" />} errorTitle="후보를 불러오지 못했습니다">
      {(rows) => (
        <DataTable
          caption={`${gu} 24시간 전환 후보`}
          columns={CANDIDATE_COLUMNS}
          rows={candidatesFor(rows, gu)}
          rowKey={(c) => `${c.org}|${c.address}`}
          emptyTitle="이 구의 후보가 없습니다"
          initialSort={{ key: "gain", dir: "desc" }}
        />
      )}
    </AsyncBoundary>
  );
}

function Detail({ gus }: { gus: readonly AedGu[] }) {
  const worst = rankGu(gus)[0]?.gu ?? "";
  const [picked, setPicked] = useState<string | null>(null);
  const gu = picked ?? worst;
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      <Card title="구별 순위" caption={`밤에 열린 AED 비율이 낮은 상위 ${ZP_AED_GU_TOP}구입니다(구 사이 순서는 통계적으로 불안정해 번호를 붙이지 않았습니다). 구를 누르면 오른쪽 후보가 바뀝니다.`}>
        <GuRanking rows={gus} selected={gu} onSelect={setPicked} />
      </Card>
      <Card
        title={gu === "" ? "24시간 전환 후보" : `${gu} 24시간 전환 후보`}
        caption={<>밤에 문을 닫는 AED 중 24시간 이용으로 바꾸면 효과가 큰 곳입니다.<InfoTip label="효과 점수">{ZP_GLOSS.gainScore}</InfoTip></>}
        flush
      >
        {gu === "" ? <p className="p-6 text-body text-ink-muted">구 자료가 없습니다.</p> : <Candidates gu={gu} />}
      </Card>
    </div>
  );
}

/** /aed: the night gap. Public cardiac arrests do not stop at night; the AED schedule does. */
export function AedPage() {
  const hourly = useApi(zpApi.aedHourly, { describe: describeZpError });
  const gus = useApi(zpApi.aedGu, { describe: describeZpError });
  return (
    <>
      <PageHeader eyebrow="AED 밤 공백" title="밤에도 열려 있는 AED가 필요합니다" lead={ZP_SEOUL_NOTE} actions={<TopicBadges tags={["ai_predict", "public_data"]} />} />
      {ZP_MOCK && <div className="mb-6"><Notice>{ZP_MOCK_NOTICE}</Notice></div>}
      <div className="space-y-10">
        <Summary />
        <Card title="시간대별 심정지와 열린 AED" caption="막대는 공공장소 심정지 건수, 선은 그 시각에 문이 열려 있는 AED의 비율입니다. 회색 음영이 밤입니다.">
          <AsyncBoundary state={hourly.state} onRetry={hourly.reload} skeleton={<SkeletonText lines={8} label="그래프를 불러오는 중" />} errorTitle="그래프를 불러오지 못했습니다">
            {(rows) => <NightGapChart data={hourData(rows, ZP_AED_NIGHT_START, ZP_AED_NIGHT_END)} nightStart={ZP_AED_NIGHT_START} nightEnd={ZP_AED_NIGHT_END} />}
          </AsyncBoundary>
        </Card>
        <AsyncBoundary state={gus.state} onRetry={gus.reload} skeleton={<SkeletonText lines={8} label="구별 자료를 불러오는 중" />} errorTitle="구별 자료를 불러오지 못했습니다">
          {(rows) => <Detail gus={rows} />}
        </AsyncBoundary>
      </div>
    </>
  );
}
