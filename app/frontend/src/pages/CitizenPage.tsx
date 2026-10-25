import { useId, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { describeHeatError, heatApi, HEAT_MOCK } from "../api/heat";
import type { HeatDay, HeatMeta, HeatRegion, HeatRegionDetail } from "../api/heatTypes";
import { useApi } from "../api/useApi";
import { PageHeader } from "../components/AppShell";
import { AsyncBoundary } from "../components/AsyncBoundary";
import { Card } from "../components/Card";
import { EmptyState, Notice } from "../components/Notice";
import { SkeletonText } from "../components/Skeleton";
import { describeZpError, zpApi } from "../api/zp";
import { CROSSWALK_MAX_LENGTH_M, IS_WALK_FRAMING, ZP_TEAMS_DEFAULT } from "../config";
import { normalizeBriefing } from "../lib/zpBriefing";
import { formatDecimal, formatInt, formatPercent } from "../lib/format";
import { dayLabel, sortByGap } from "../lib/heatDays";
import { flagState, shortSido } from "../lib/heatMap";
import type { FlagState } from "../lib/heatMap";
import { RISK_STYLE, riskLevel } from "../lib/heatRisk";

const BIG_CONTROL = "block min-h-14 w-full border-2 border-ink bg-surface px-4 text-[1.125rem] text-ink";
const FLAG_WORD: Record<FlagState, string> = { open: "열림", closed: "닫힘", unknown: "정보 없음" };

function BigSelect({ label, value, onChange, children, disabled = false }: { label: string; value: string; onChange: (v: string) => void; children: ReactNode; disabled?: boolean }) {
  const id = useId();
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-[1.125rem] font-semibold text-ink">{label}</label>
      <select id={id} className={BIG_CONTROL} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
        {children}
      </select>
    </div>
  );
}

function Flag({ label, value }: { label: string; value: HeatRegionDetail["shelters"]["nearby"][number]["night_open"] }) {
  const s = flagState(value);
  const mark = s === "open" ? "✓" : s === "closed" ? "✕" : "?";
  return (
    <span className={`inline-flex min-h-9 items-center gap-1 border-2 px-3 text-[1rem] font-semibold ${s === "open" ? "border-[#1d6b3a] bg-[#e8f3ec] text-[#14502a]" : "border-line-strong bg-surface-sunken text-ink-muted"}`}>
      <span aria-hidden="true">{mark}</span>
      {label} {FLAG_WORD[s]}
    </span>
  );
}

const BIG_CARD = "border-2 border-ink bg-surface p-4";
const SIGNAL_MAX = 3;
const FALL_STEPS: readonly string[] = ["쓰러진 분을 보면 먼저 119에 전화합니다.", "주변 사람에게 큰 소리로 도움을 부탁합니다.", "가까운 자동심장충격기(AED)를 가져옵니다."];

/** 길: the first thing a resident sees. Real values from /briefing and /actions; "서울만 분석" where the analysis does not reach. */
function Road({ code, date, regionName }: { code: string; date: string; regionName: string }) {
  const b = useApi(() => zpApi.briefing(code, date, ZP_TEAMS_DEFAULT), { key: `cb|${code}|${date}`, describe: describeZpError });
  const a = useApi(() => zpApi.actions(code, date, ZP_TEAMS_DEFAULT), { key: `ca|${code}|${date}`, describe: describeZpError });
  const signals = a.state.status === "ready" && Array.isArray(a.state.data.signals) ? a.state.data.signals.slice(0, SIGNAL_MAX) : null;
  const signalNote = a.state.status === "ready" ? (Array.isArray(a.state.data.signals) ? a.state.data.signals_coverage.note : a.state.data.signals.note) : null;
  return (
    <section aria-labelledby="road-title" className="space-y-5 border-4 border-ink p-6">
      <h2 id="road-title" className="text-[1.375rem] font-bold">{regionName}, 고령자가 건너는 길</h2>
      <AsyncBoundary state={b.state} onRetry={b.reload} errorTitle="실버존 정보를 불러오지 못했습니다" skeleton={<SkeletonText lines={3} label="실버존 정보를 불러오는 중" />}>
        {(raw) => {
          const m = normalizeBriefing(raw);
          return m.silver.available ? (
            <div className={BIG_CARD}>
              <p className="text-[1.25rem] font-bold leading-8">작년 고령자 사고 다발 지점 <span className="num">{m.silver.hotspots}</span>곳</p>
              <p className="num mt-2 text-[1.125rem] leading-8">현행 실버존 {m.silver.currentZones}곳 → 에이전트 추천 {m.silver.ourPicks}곳</p>
              {m.silver.hotspotsCovered !== null && (
                <p className="num text-[1.125rem] leading-8">다발 지점을 지키는 구역: 현행 {m.silver.hotspotsCovered.current}곳 → 에이전트 {m.silver.hotspotsCovered.ours}곳</p>
              )}
            </div>
          ) : (
            <p className={`${BIG_CARD} text-[1.125rem] leading-8`}>실버존 비교는 서울만 분석했습니다.</p>
          );
        }}
      </AsyncBoundary>
      <div className="space-y-3">
        <h3 className="text-[1.25rem] font-bold">녹색 신호가 짧은 횡단보도</h3>
        <AsyncBoundary state={a.state} onRetry={a.reload} errorTitle="횡단보도 정보를 불러오지 못했습니다" skeleton={<SkeletonText lines={3} label="횡단보도 정보를 불러오는 중" />}>
          {() =>
            signals !== null && signals.length > 0 ? (
              <ul className="m-0 list-none space-y-3 p-0">
                {signals.map((g) => (
                  <li key={g.crosswalk_id} className={BIG_CARD}>
                    <p className="text-[1.125rem] font-bold leading-7">{g.address}</p>
                    <p className="num mt-1 text-[1.125rem] leading-8">
                      {g.length_m > CROSSWALK_MAX_LENGTH_M ? "자료 확인 필요: 길이가 횡단보도로 보기 어렵습니다" : `길이 ${g.length_m}m · 녹색 ${g.green_s}초 · 천천히 걸으면 ${g.required_green_s}초 필요`}
                    </p>
                  </li>
                ))}
              </ul>
            ) : (
              <p className={`${BIG_CARD} text-[1.125rem] leading-8`}>{signalNote ?? "이 동네에서 신호가 모자란다고 확인된 횡단보도가 없습니다."}</p>
            )
          }
        </AsyncBoundary>
      </div>
      <div className="space-y-3">
        <h3 className="text-[1.25rem] font-bold">쓰러진 분을 보면</h3>
        <ol className={`${BIG_CARD} m-0 list-decimal space-y-2 pl-9 text-[1.125rem] leading-8`}>
          {FALL_STEPS.map((t) => <li key={t}>{t}</li>)}
        </ol>
        <p className="text-[1rem] leading-7 text-ink-muted">실버존 에이전트는 카메라가 달린 횡단보도에서 쓰러짐을 감지하면 119 신고 초안과 가까운 AED 안내를 만듭니다. 지금은 시연 단계라 실제로 설치된 곳은 없습니다.</p>
      </div>
    </section>
  );
}

function Aed({ code, date }: { code: string; date: string }) {
  const b = useApi(() => zpApi.briefing(code, date, ZP_TEAMS_DEFAULT), { key: `cb|${code}|${date}`, describe: describeZpError });
  return (
    <AsyncBoundary state={b.state} onRetry={b.reload} errorTitle="AED 정보를 불러오지 못했습니다" skeleton={<SkeletonText lines={2} label="AED 정보를 불러오는 중" />}>
      {(raw) => {
        const m = normalizeBriefing(raw);
        return m.aed === null || m.aed.nightOpen === null || m.aed.dayOpen === null ? (
          <p className="text-[1.125rem] leading-8">AED 밤 개방 분석은 서울만 합니다.</p>
        ) : (
          <p className="num text-[1.125rem] leading-8">이 구의 AED 중 낮에 열린 곳 {Math.round(m.aed.dayOpen * 100)}% · 밤(22~06시)에 열린 곳 {Math.round(m.aed.nightOpen * 100)}%</p>
        );
      }}
    </AsyncBoundary>
  );
}

function Fold({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details open={!IS_WALK_FRAMING} className="border-2 border-line-strong bg-surface">
      <summary className="min-h-14 cursor-pointer px-4 py-3 text-[1.25rem] font-bold">{title}</summary>
      <div className="space-y-6 p-4">{children}</div>
    </details>
  );
}

function Result({ detail, k, date }: { detail: HeatRegionDetail; k: number; date: string }) {
  const level = riskLevel(detail.scores.rank, k);
  const st = RISK_STYLE[level];
  const { shelters, drivers } = detail;
  return (
    <div className="space-y-8">
      <Road code={detail.region.code} date={date} regionName={detail.region.name} />
      <Fold title={IS_WALK_FRAMING ? "더위 (확장 모듈)" : "더위"}>
      <section aria-labelledby="risk-title" className={`border-4 p-6 ${st.box}`}>
        <h2 id="risk-title" className="text-[1.125rem] font-semibold !text-current">오늘 {detail.region.name}의 더위 위험</h2>
        <p className="mt-2 flex items-center gap-3 text-[3.5rem] font-bold leading-none">
          <span aria-hidden="true">{st.glyph}</span>
          <span>{st.word}</span>
        </p>
        <p className="mt-4 text-[1.25rem] font-medium leading-8">{st.advice}</p>
        <p className="num mt-4 text-[1.125rem] leading-8">
          {drivers.apparent_max != null ? `체감 최고 ${formatDecimal(drivers.apparent_max, 1)}℃ · ` : ""}최고기온 {drivers.tmax === null ? "자료 없음" : `${formatDecimal(drivers.tmax, 1)}℃`} · 65세 이상 {formatPercent(drivers.old_share, 0)}
        </p>
      </section>

      <section aria-labelledby="shelter-title" className="space-y-4">
        <h2 id="shelter-title" className="text-[1.375rem] font-bold">가까운 무더위쉼터</h2>
        {shelters.coverage === "none" ? (
          <p className="border-2 border-line-strong bg-surface-sunken p-4 text-[1.125rem] leading-8">이 지역은 쉼터 위치 자료가 아직 없습니다.</p>
        ) : shelters.nearby.length === 0 ? (
          <p className="border-2 border-line-strong bg-surface-sunken p-4 text-[1.125rem] leading-8">가까운 쉼터가 확인되지 않았습니다.</p>
        ) : (
          <ul className="m-0 list-none space-y-3 p-0">
            {shelters.nearby.map((s) => (
              <li key={`${s.name}-${s.dist_km}`} className="space-y-2 border-2 border-ink bg-surface p-4">
                <p className="flex items-baseline justify-between gap-3 text-[1.25rem] font-bold leading-7">
                  <span className="min-w-0 break-words">{s.name}</span>
                  <span className="num shrink-0">{formatDecimal(s.dist_km, 1)}km</span>
                </p>
                <p className="flex flex-wrap gap-2">
                  <Flag label="야간" value={s.night_open} />
                  <Flag label="주말" value={s.weekend_open} />
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>
      </Fold>
      <Fold title={IS_WALK_FRAMING ? "응급: AED (확장 모듈)" : "응급: AED"}>
        <Aed code={detail.region.code} date={date} />
      </Fold>
    </div>
  );
}

function Picker({ meta, regions, days }: { meta: HeatMeta; regions: readonly HeatRegion[]; days: readonly HeatDay[] }) {
  const sidos = useMemo(() => [...new Set(regions.map((r) => r.sido))], [regions]);
  const sorted = useMemo(() => sortByGap(days), [days]);
  const [sido, setSido] = useState("");
  const [code, setCode] = useState("");
  const [date, setDate] = useState("");
  const activeSido = sido === "" ? (sidos[0] ?? "") : sido;
  const inSido = regions.filter((r) => r.sido === activeSido);
  const activeDate = sorted.find((d) => d.date === date)?.date ?? sorted[0]?.date ?? "";
  const day = sorted.find((d) => d.date === activeDate);
  const region = inSido.find((r) => r.code === code);
  const detail = useApi(() => (region === undefined || activeDate === "" ? Promise.reject(new Error("no selection")) : heatApi.region(region.code, activeDate, "F")), {
    key: `${region?.code ?? ""}|${activeDate}`,
    describe: describeHeatError,
  });
  const year = meta.years[meta.years.length - 1];

  return (
    <div className="space-y-8">
      <div className="space-y-4">
        <BigSelect label="시도" value={activeSido} onChange={(v) => { setSido(v); setCode(""); }}>
          {sidos.map((s) => <option key={s} value={s}>{shortSido(s)}</option>)}
        </BigSelect>
        <BigSelect label="시군구" value={region?.code ?? ""} onChange={setCode}>
          <option value="">시군구를 고르세요</option>
          {inSido.map((r) => <option key={r.code} value={r.code}>{r.name}</option>)}
        </BigSelect>
        <BigSelect label="날짜" value={activeDate} onChange={setDate}>
          {sorted.map((d) => <option key={d.date} value={d.date}>{year}-{dayLabel(d.date)}</option>)}
        </BigSelect>
        <p className="text-[1rem] leading-7 text-ink-muted">시연용입니다. 실제 오늘이 아니라 {year}년 시험 시즌 중 하루를 보여줍니다.</p>
      </div>
      {region === undefined ? (
        <EmptyState title="시군구를 고르면 위험을 알려드립니다">위에서 시도와 시군구를 차례로 고르세요.</EmptyState>
      ) : (
        <AsyncBoundary state={detail.state} onRetry={detail.reload} errorTitle="정보를 불러오지 못했습니다" skeleton={<SkeletonText lines={6} label="정보를 불러오는 중" />}>
          {(d) => <Result detail={d} k={day?.k_budget ?? 0} date={activeDate} />}
        </AsyncBoundary>
      )}
      <p className="num text-[1rem] text-ink-muted">그날 경보 기준 {formatInt(day?.k_budget ?? 0)}곳 안에 들면 높음입니다.</p>
    </div>
  );
}

function Loader({ meta }: { meta: HeatMeta }) {
  const year = meta.years[meta.years.length - 1] ?? 0;
  const regions = useApi(heatApi.regions, { describe: describeHeatError });
  const days = useApi(() => heatApi.days(year, "F", "gap"), { key: String(year), describe: describeHeatError });
  return (
    <AsyncBoundary state={regions.state} onRetry={regions.reload} errorTitle="시군구 목록을 불러오지 못했습니다">
      {(r) => (
        <AsyncBoundary state={days.state} onRetry={days.reload} errorTitle="날짜를 불러오지 못했습니다">
          {(d) => (d.length === 0 ? <EmptyState title="표시할 날짜가 없습니다">평가 결과가 아직 없습니다.</EmptyState> : <Picker meta={meta} regions={r} days={d} />)}
        </AsyncBoundary>
      )}
    </AsyncBoundary>
  );
}

/** Large-type, high-contrast view for residents: one district, one word for today's risk, nearest shelters. */
export function CitizenPage() {
  const meta = useApi(heatApi.meta, { describe: describeHeatError });
  return (
    <div className="mx-auto max-w-xl text-[1.125rem] leading-8">
      <PageHeader eyebrow="고령자·보호자" title={IS_WALK_FRAMING ? "우리 동네 보행 안전" : "우리 동네 생활 안전"} lead={IS_WALK_FRAMING ? "사는 곳을 고르면 고령자가 건너는 길의 위험과 쓰러진 분을 봤을 때 할 일을 알려드립니다." : "사는 곳을 고르면 길, 더위, 응급 세 가지 안전 정보를 알려드립니다. 길이 가장 먼저 나옵니다."} />
      {HEAT_MOCK && <div className="mb-6"><Notice>모의 응답으로 표시 중입니다. 합성 데이터입니다.</Notice></div>}
      <Card>
        <AsyncBoundary state={meta.state} onRetry={meta.reload} errorTitle="정보를 불러오지 못했습니다" skeleton={<SkeletonText lines={5} />}>
          {(m) => <Loader meta={m} />}
        </AsyncBoundary>
      </Card>
    </div>
  );
}
