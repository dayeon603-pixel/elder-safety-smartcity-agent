import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { describeHeatError, HEAT_MOCK, heatApi } from "../api/heat";
import type { HeatRegion } from "../api/heatTypes";
import { useApi } from "../api/useApi";
import { ZP_MOCK, describeZpError, zpApi } from "../api/zp";
import { PageHeader } from "../components/AppShell";
import { AsyncBoundary } from "../components/AsyncBoundary";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { SelectField, TextField } from "../components/Field";
import { Notice } from "../components/Notice";
import { SkeletonText } from "../components/Skeleton";
import { ActionChannels } from "../components/zp/ActionChannels";
import { ActionList, AedPanel, HeatPanel, RoadPanel } from "../components/zp/BriefingPanels";
import {
  IS_WALK_FRAMING, ZP_DEFAULT_DATE, ZP_DEFAULT_SIDO, ZP_MOCK_NOTICE, ZP_TEAMS_DEFAULT, ZP_TEAMS_MAX, ZP_TEAMS_MIN,
} from "../config";
import { clampTeams, isIsoDate, normalizeBriefing } from "../lib/zpBriefing";
import { regionOptions, resolvePick } from "../lib/zpRegions";

const PRINT_SOURCES = "자료: 질병관리청 온열질환 신고 · 기상 관측 · 행정안전부 주민등록 인구 · 도로교통공단 보행노인 사고다발지 · 전국 횡단보도·보호구역 표준데이터 · 소방청 구급활동정보 · 국립중앙의료원 AED (서울 분석 2024)";

function Body({ code, date, teams }: { code: string; date: string; teams: number }) {
  const b = useApi(() => zpApi.briefing(code, date, teams), { key: `${code}|${date}|${teams}`, describe: describeZpError });
  const act = useApi(() => zpApi.actions(code, date, teams), { key: `act|${code}|${date}|${teams}`, describe: describeZpError });
  const [savedTick, setSavedTick] = useState(0);
  const tmaxC = act.state.status === "ready" ? (act.state.data.context?.tmax_c ?? null) : null;
  return (
    <AsyncBoundary state={b.state} onRetry={b.reload} skeleton={<SkeletonText lines={8} label="브리핑을 만드는 중" />} errorTitle="브리핑을 만들지 못했습니다">
      {(raw) => {
        const m = normalizeBriefing(raw);
        return (
          <div className="space-y-6">
            <div className="hidden print:block">
              <p className="text-h3 font-semibold">{m.region.sido} {m.region.name} · {date} · 방문팀 {teams}팀</p>
            </div>
            <RoadPanel s={m.silver} regionName={m.region.name} code={code} actions={act.state} savedTick={savedTick} />
            <ActionList model={m} code={code} date={date} tmaxC={tmaxC} onSaved={() => setSavedTick((n) => n + 1)} />
            <AsyncBoundary state={act.state} onRetry={act.reload} skeleton={<SkeletonText lines={6} label="행동 채널 초안을 만드는 중" />} errorTitle="행동 채널 초안을 만들지 못했습니다">
              {(data) => <ActionChannels data={data} />}
            </AsyncBoundary>
            <details open={!IS_WALK_FRAMING} className="border border-line bg-surface" id="bp-extension">
              <summary className="min-h-12 cursor-pointer px-4 py-3 text-body font-semibold sm:px-6">{IS_WALK_FRAMING ? "확장 모듈: 폭염 · AED 밤 공백" : "더위와 응급: 폭염 · AED 밤 공백"}</summary>
              <div className="grid gap-6 p-4 sm:p-6 lg:grid-cols-2 print:grid-cols-2">
                <HeatPanel h={m.heat} regionName={m.region.name} />
                <AedPanel a={m.aed} note={m.aedNote} />
              </div>
            </details>
            <p className="hidden text-caption text-ink-muted print:block">{PRINT_SOURCES}</p>
          </div>
        );
      }}
    </AsyncBoundary>
  );
}

function Controls({ regions }: { regions: readonly HeatRegion[] }) {
  const [params, setParams] = useSearchParams();
  const pick = resolvePick(regions, params.get("sgg"));
  const date = params.get("date") ?? ZP_DEFAULT_DATE;
  const teams = clampTeams(params.get("teams"), ZP_TEAMS_MIN, ZP_TEAMS_MAX, ZP_TEAMS_DEFAULT);
  const { sidos, sigungu } = useMemo(() => regionOptions(regions, pick?.sido ?? ZP_DEFAULT_SIDO), [regions, pick?.sido]);
  const dateOk = isIsoDate(date);

  const update = (patch: Record<string, string>): void => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) next.set(k, v);
    setParams(next, { replace: true });
  };

  if (pick === null) return <Notice tone="danger">시군구 목록이 비어 있습니다.</Notice>;
  return (
    <div className="space-y-6">
      <div className="print:hidden">
        <Card title="시군구와 날짜">
          <form className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4" onSubmit={(e) => e.preventDefault()} aria-label="브리핑 조건">
            <SelectField
              label="시도"
              value={pick.sido}
              onChange={(e) => {
                const first = regionOptions(regions, e.target.value).sigungu[0];
                if (first !== undefined) update({ sgg: first.code });
              }}
              options={sidos.map((s) => ({ value: s, label: s }))}
            />
            <SelectField label="시군구" value={pick.code} onChange={(e) => update({ sgg: e.target.value })} options={sigungu.map((r) => ({ value: r.code, label: r.name }))} />
            <TextField
              label="날짜"
              type="date"
              value={date}
              onChange={(e) => update({ date: e.target.value })}
              error={dateOk ? null : "YYYY-MM-DD 형식의 날짜를 입력해 주세요."}
              hint="폭염은 2023–2025년 5–9월 날짜만 분석합니다."
            />
            <TextField
              label="방문팀 수"
              type="number"
              inputMode="numeric"
              min={ZP_TEAMS_MIN}
              max={ZP_TEAMS_MAX}
              value={teams}
              onChange={(e) => update({ teams: String(clampTeams(e.target.value, ZP_TEAMS_MIN, ZP_TEAMS_MAX, ZP_TEAMS_DEFAULT)) })}
              hint={`${ZP_TEAMS_MIN}–${ZP_TEAMS_MAX}팀, 전국 합계`}
            />
          </form>
          <div className="mt-4 flex justify-end">
            <Button variant="secondary" onClick={() => window.print()}>인쇄하기</Button>
          </div>
        </Card>
      </div>
      {dateOk ? <Body code={pick.code} date={date} teams={teams} /> : <Notice tone="danger">날짜 형식이 올바르지 않아 브리핑을 만들지 않았습니다.</Notice>}
    </div>
  );
}

/** /briefing: one 시군구, one day, three modules on one printable page and a single prioritized action list. */
export function BriefingPage() {
  const regions = useApi(heatApi.regions, { describe: describeHeatError });
  return (
    <>
      <PageHeader eyebrow="브리핑" title="오늘의 에이전트 브리핑" lead={IS_WALK_FRAMING ? "시군구 하나를 고르면 실버존 추천, 신호 연장, 오늘 할 일이 한 장으로 나옵니다. 폭염과 AED는 확장 모듈에 접어 두었습니다." : "시군구 하나를 고르면 길(실버존·신호), 더위, 응급(AED) 세 칸과 오늘 할 일이 한 장으로 나옵니다."} />
      {(ZP_MOCK || HEAT_MOCK) && <div className="mb-6"><Notice>{ZP_MOCK_NOTICE}</Notice></div>}
      <AsyncBoundary state={regions.state} onRetry={regions.reload} skeleton={<SkeletonText lines={4} label="시군구 목록을 불러오는 중" />} errorTitle="시군구 목록을 불러오지 못했습니다">
        {(r) => <Controls regions={r} />}
      </AsyncBoundary>
    </>
  );
}
