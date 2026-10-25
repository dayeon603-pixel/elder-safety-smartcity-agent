import { useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { HEAT_MOCK, describeHeatError, heatApi } from "../api/heat";
import type { HeatAlerts, HeatDay, HeatEvalRow, HeatMeta, HeatRegion, HeatVariant } from "../api/heatTypes";
import type { AsyncState } from "../api/useApi";
import { useApi } from "../api/useApi";
import { PageHeader } from "../components/AppShell";
import { TopicBadges } from "../components/zp/TopicBadges";
import { AsyncBoundary } from "../components/AsyncBoundary";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { SelectField } from "../components/Field";
import { DayPicker } from "../components/heat/DayPicker";
import { HeroCompare } from "../components/heat/HeroCompare";
import { EvalStrip } from "../components/heat/EvalStrip";
import { HeatMap, MapLegend } from "../components/heat/HeatMap";
import { PlanStep } from "../components/heat/PlanStep";
import { RegionPanel } from "../components/heat/RegionPanel";
import { VariantToggle } from "../components/heat/VariantToggle";
import { EmptyState, ErrorState, Notice } from "../components/Notice";
import { Skeleton, SkeletonText } from "../components/Skeleton";
import type { StepDef } from "../components/Stepper";
import { Stepper } from "../components/Stepper";
import { HEAT_DEFAULT_VARIANT } from "../config";
import { GLOSS } from "../lib/heatEval";
import { InfoTip } from "../components/InfoTip";
import { formatInt } from "../lib/format";
import { loadOutline, MAP_HEIGHT, MAP_WIDTH } from "../lib/geo";
import { dayLabel, sortByGap } from "../lib/heatDays";
import { buildDots, countByClass, regionTitle, summarizeDay } from "../lib/heatMap";
import type { MapDot } from "../lib/heatMap";

const STEPS: readonly StepDef[] = [
  { id: "day", label: "1. 날짜 고르기", description: "우리가 더 잡은 날이 위" },
  { id: "map", label: "2. 경보 지도", description: "B1과 우리 모형 비교" },
  { id: "region", label: "3. 시군구 근거", description: "점수와 쉼터" },
  { id: "plan", label: "4. 오늘의 대응안", description: "인력 배치와 쉼터" },
];

function isVariant(v: string | undefined): v is HeatVariant {
  return v === "F" || v === "O";
}

function defaultVariant(meta: HeatMeta): HeatVariant {
  return meta.variants.includes(HEAT_DEFAULT_VARIANT) ? HEAT_DEFAULT_VARIANT : (meta.variants.find(isVariant) ?? "F");
}

// ---------- step 1 ----------

interface DayStepProps {
  meta: HeatMeta;
  year: number;
  variant: HeatVariant;
  days: AsyncState<HeatDay[]>;
  onReloadDays: () => void;
  date: string | null;
  onYear: (y: number) => void;
  onVariant: (v: HeatVariant) => void;
  onDate: (d: string) => void;
  onNext: () => void;
}

function DayStep({ meta, year, variant, days, onReloadDays, date, onYear, onVariant, onDate, onNext }: DayStepProps) {
  return (
    <div className="space-y-6">
      <div className="grid gap-6 sm:grid-cols-[12rem_1fr]">
        <SelectField
          label="시험 연도"
          value={String(year)}
          onChange={(e) => onYear(Number(e.target.value))}
          options={meta.years.map((y) => ({ value: String(y), label: `${y}년` }))}
          hint="그 해 이전 자료만으로 학습한 모형입니다."
        />
        <VariantToggle value={variant} onChange={onVariant} variants={meta.variants} />
      </div>
      <AsyncBoundary state={days} onRetry={onReloadDays} errorTitle="날짜 목록을 불러오지 못했습니다" skeleton={<SkeletonText lines={8} label="날짜 목록을 불러오는 중" />}>
        {(list) => {
          if (list.length === 0) {
            return <EmptyState title="날짜 자료가 없습니다">{year}년, 변형 {variant}의 시험 시즌 날짜가 저장되어 있지 않습니다.</EmptyState>;
          }
          const sorted = sortByGap(list);
          const picked = sorted.find((d) => d.date === date) ?? sorted[0];
          return (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="text-body" aria-live="polite">
                  선택한 날짜 <strong className="num font-semibold">{picked === undefined ? "–" : `${year}-${dayLabel(picked.date)}`}</strong>
                  {picked !== undefined && (
                    <span className="text-ink-muted"> · 환자 {formatInt(picked.n_cases)}명 · 경보 {formatInt(picked.k_budget)}곳</span>
                  )}
                </p>
                <Button variant="primary" onClick={onNext} disabled={picked === undefined}>다음: 지도 보기</Button>
              </div>
              <DayPicker days={sorted} selected={picked?.date ?? null} onSelect={onDate} highlight={sorted[0]?.date} />
              <p className="text-caption text-ink-faint">
                포착 = 경보 지역에서 나온 환자 수(%는 그날 전체 환자 중 비율). 두 방법 모두 그날 특보 규칙이 경보한 수 K만큼만 경보합니다. 차이 = 우리 − 특보.
              </p>
            </div>
          );
        }}
      </AsyncBoundary>
    </div>
  );
}

// ---------- steps 2 and 3 ----------

function MapSkeleton() {
  return (
    <div role="status" className="max-w-[34rem]">
      <span className="sr-only">지도를 불러오는 중</span>
      <Skeleton className="w-full" style={{ aspectRatio: `${MAP_WIDTH} / ${MAP_HEIGHT}` }} />
    </div>
  );
}

function CandidateList({ dots, selected, onSelect, missedByBoth }: { dots: readonly MapDot[]; selected: string | null; onSelect: (c: string) => void; missedByBoth: number }) {
  return (
    <section aria-labelledby="blind-title" className="space-y-3">
      <h3 id="blind-title" className="text-h3">B1은 놓치고 우리는 잡은 시군구 <span className="num text-ink-muted">{dots.length}곳</span></h3>
      {dots.length === 0 ? (
        <p className="text-small text-ink-muted">이날은 B1이 놓치고 우리가 잡은 시군구가 없습니다.</p>
      ) : (
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
        <ul className="m-0 flex max-h-64 list-none flex-wrap gap-2 overflow-y-auto p-0" tabIndex={0} aria-label="시군구 목록">
          {dots.map((d) => (
            <li key={d.code}>
              <button
                type="button"
                aria-pressed={d.code === selected}
                onClick={() => onSelect(d.code)}
                className="flex min-h-11 items-center gap-2 border border-line-strong bg-surface px-3 text-small hover:bg-surface-sunken aria-pressed:border-accent aria-pressed:bg-accent-wash sm:min-h-9"
              >
                <span className="font-medium">{regionTitle(d.region)}</span>
                <span className="num text-ink-muted">환자 {d.cases}명</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {missedByBoth > 0 && (
        <p className="text-small text-ink-muted">두 방법 모두 경보하지 않은 지역의 환자 <span className="num font-medium text-ink">{formatInt(missedByBoth)}명</span>은 포착되지 않았습니다.</p>
      )}
    </section>
  );
}

interface MapStepProps {
  year: number;
  date: string;
  variant: HeatVariant;
  meta: HeatMeta;
  onVariant: (v: HeatVariant) => void;
  regions: AsyncState<HeatRegion[]>;
  onReloadRegions: () => void;
  evalRows: AsyncState<HeatEvalRow[]>;
  onReloadEval: () => void;
  outline: string | null;
  region: string | null;
  showPanel: boolean;
  onSelect: (code: string) => void;
}

function MapStep({ year, date, variant, meta, onVariant, regions, onReloadRegions, evalRows, onReloadEval, outline, region, showPanel, onSelect }: MapStepProps) {
  const alerts = useApi(() => heatApi.alerts(date, variant), { key: `${date}|${variant}`, describe: describeHeatError });
  return (
    <div className="space-y-8">
      <AsyncBoundary state={evalRows} onRetry={onReloadEval} errorTitle="포착률을 불러오지 못했습니다" skeleton={<SkeletonText lines={3} label="포착률을 불러오는 중" />}>
        {(rows) => <EvalStrip rows={rows} year={year} variant={variant} compact />}
      </AsyncBoundary>
      {!showPanel && (
        <>
          <hr className="m-0 border-line" />
          <VariantToggle value={variant} onChange={onVariant} variants={meta.variants} />
        </>
      )}
      <AsyncBoundary state={regions} onRetry={onReloadRegions} errorTitle="시군구 목록을 불러오지 못했습니다" skeleton={<MapSkeleton />}>
        {(regionList) => (
          <AsyncBoundary state={alerts.state} onRetry={alerts.reload} errorTitle="이날의 경보를 불러오지 못했습니다" skeleton={<MapSkeleton />}>
            {(a) => <MapBody regions={regionList} alerts={a} year={year} outline={outline} region={region} showPanel={showPanel} variant={variant} date={date} onSelect={onSelect} />}
          </AsyncBoundary>
        )}
      </AsyncBoundary>
    </div>
  );
}

function MapBody({ regions, alerts, year, outline, region, showPanel, variant, date, onSelect }: {
  regions: readonly HeatRegion[];
  alerts: HeatAlerts;
  year: number;
  outline: string | null;
  region: string | null;
  showPanel: boolean;
  variant: HeatVariant;
  date: string;
  onSelect: (code: string) => void;
}) {
  const model = useMemo(() => buildDots(regions, alerts), [regions, alerts]);
  const sum = useMemo(() => summarizeDay(model.dots), [model.dots]);
  const picked = model.dots.find((d) => d.code === region);
  const regionInfo = regions.find((r) => r.code === region);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="inline-flex items-center"><Badge tone="accent">같은 경보 수 K={formatInt(alerts.k)}</Badge><InfoTip label="같은 경보 수">{GLOSS.k}</InfoTip></span>
        <p className="text-small text-ink-muted">
          {year}-{dayLabel(date)} · 환자 <span className="num font-medium text-ink">{formatInt(alerts.total_cases)}명</span> · B1 경보 지역의 환자{" "}
          <span className="num font-medium text-ink">{formatInt(sum.b1Caught)}명</span> · 우리 경보 지역의 환자{" "}
          <span className="num font-medium text-accent-strong">{formatInt(sum.oursCaught)}명</span>
        </p>
      </div>
      {alerts.k === 0 && <Notice>이날은 B1이 경보한 시군구가 없어, 같은 수로 비교할 경보도 없습니다.</Notice>}
      {model.unknownCodes.length > 0 && (
        <Notice tone="danger">경보 자료에 시군구 목록에 없는 코드 {model.unknownCodes.length}개가 있어 지도에서 뺐습니다: {model.unknownCodes.slice(0, 5).join(", ")}</Notice>
      )}
      {outline === null && <p className="text-caption text-ink-faint">배경 경계를 불러오지 못해 점만 표시합니다.</p>}
      <div className="grid gap-8 lg:grid-cols-[minmax(0,34rem)_minmax(0,1fr)]">
        <div className="space-y-4">
          <HeatMap dots={model.dots} outline={outline} selected={region} onSelect={onSelect} />
          <MapLegend counts={countByClass(model.dots)} />
          <p className="text-caption text-ink-faint">배경은 2013년 시군구 경계입니다. 점은 2026년 시군구 중심점입니다.</p>
        </div>
        <div className="space-y-8">
          {showPanel && region !== null && (
            <RegionPanel
              key={region}
              code={region}
              title={picked !== undefined ? regionTitle(picked.region) : regionInfo !== undefined ? regionTitle(regionInfo) : region}
              date={date}
              variant={variant}
              cls={picked?.cls}
              k={alerts.k}
            />
          )}
          <CandidateList dots={sum.onlyOurs} selected={region} onSelect={onSelect} missedByBoth={sum.missedByBoth} />
        </div>
      </div>
    </div>
  );
}

// ---------- flow ----------

function Flow({ meta }: { meta: HeatMeta }) {
  const latest = meta.years[meta.years.length - 1] ?? 0;
  const [year, setYear] = useState(latest);
  const [variant, setVariant] = useState<HeatVariant>(() => defaultVariant(meta));
  const [date, setDate] = useState<string | null>(null);
  const [region, setRegion] = useState<string | null>(null);
  const [step, setStep] = useState(0);
  const [reached, setReached] = useState(0);
  const heading = useRef<HTMLHeadingElement>(null);

  const days = useApi(() => heatApi.days(year, variant, "gap"), { key: `${year}|${variant}`, describe: describeHeatError });
  const regions = useApi(heatApi.regions, { describe: describeHeatError });
  const evalRows = useApi(() => heatApi.eval(variant), { key: variant, describe: describeHeatError });
  const outline = useApi(loadOutline);

  const effectiveDate = useMemo(() => {
    if (days.state.status !== "ready") return date;
    const sorted = sortByGap(days.state.data);
    return (sorted.find((d) => d.date === date) ?? sorted[0])?.date ?? null;
  }, [days.state, date]);

  const go = (i: number): void => {
    setStep(i);
    setReached((r) => Math.max(r, i));
    heading.current?.focus();
  };
  const changeYear = (y: number): void => {
    setYear(y);
    setDate(null);
    setRegion(null);
    setReached(0);
  };
  const changeDate = (d: string): void => {
    setDate(d);
    setRegion(null);
    setReached(0);
  };
  const pickRegion = (code: string): void => {
    setRegion(code);
    setStep(2);
    setReached(2);
  };

  return (
    <div className="space-y-6">
      <AsyncBoundary state={evalRows.state} onRetry={evalRows.reload} skeleton={<SkeletonText lines={3} label="핵심 비교를 불러오는 중" />} errorTitle="핵심 비교를 불러오지 못했습니다">
        {(rows) => <HeroCompare rows={rows} variant={variant} />}
      </AsyncBoundary>
      <Stepper steps={STEPS} current={step} reached={reached} onSelect={go} />
      <Card>
        <h2 ref={heading} tabIndex={-1} className="mb-6 text-h2 outline-none">
          {STEPS[step]?.label.replace(/^\d\.\s*/, "")}
        </h2>
        {step === 0 && (
          <DayStep
            meta={meta}
            year={year}
            variant={variant}
            days={days.state}
            onReloadDays={days.reload}
            date={effectiveDate}
            onYear={changeYear}
            onVariant={setVariant}
            onDate={changeDate}
            onNext={() => {
              setDate(effectiveDate);
              go(1);
            }}
          />
        )}
        {step === 3 && effectiveDate !== null && (
          <>
            <PlanStep date={effectiveDate} variant={variant} />
            <div className="mt-8 flex flex-wrap gap-2">
              <Button onClick={() => go(region === null ? 1 : 2)}>이전</Button>
              <Button onClick={() => go(0)}>다른 날짜 고르기</Button>
            </div>
          </>
        )}
        {step > 0 && step < 3 && effectiveDate !== null && (
          <>
            <MapStep
              year={year}
              date={effectiveDate}
              variant={variant}
              meta={meta}
              onVariant={setVariant}
              regions={regions.state}
              onReloadRegions={regions.reload}
              evalRows={evalRows.state}
              onReloadEval={evalRows.reload}
              outline={outline.state.status === "ready" ? outline.state.data : null}
              region={region}
              showPanel={step === 2}
              onSelect={pickRegion}
            />
            <div className="mt-8 flex flex-wrap gap-2">
              <Button onClick={() => go(step - 1)}>이전</Button>
              {step === 1 && region !== null && <Button variant="primary" onClick={() => go(2)}>다음: 시군구 근거 보기</Button>}
              <Button variant="primary" onClick={() => go(3)}>다음: 오늘의 대응안</Button>
              {step === 2 && <Button onClick={() => go(0)}>다른 날짜 고르기</Button>}
            </div>
          </>
        )}
        {step > 0 && effectiveDate === null && (
          <ErrorState title="날짜를 고르지 못했습니다" message="1단계에서 날짜를 먼저 선택해 주세요." onRetry={() => go(0)} />
        )}
      </Card>
      <p className="text-small text-ink-muted">
        시험 연도 전체의 비교와 95% 신뢰구간은 <Link to="/results">결과</Link>에서 볼 수 있습니다.
      </p>
    </div>
  );
}

export function HeatMapPage() {
  const meta = useApi(heatApi.meta, { describe: describeHeatError });
  return (
    <>
      <PageHeader
        eyebrow="데모"
        title="같은 경보 수로, 어디를 먼저 볼까"
        lead="특보식 규칙(B1)과 같은 수의 시군구만 경보할 때, 우리 모형이 더 많은 온열질환자를 포착한 날을 골라 확인합니다."
        actions={<TopicBadges tags={["ai_predict", "public_data"]} />}
      />
      {HEAT_MOCK && <div className="mb-6"><Notice>모의 응답으로 표시 중입니다. 합성 데이터이며 실제 평가 결과가 아닙니다.</Notice></div>}
      <AsyncBoundary state={meta.state} onRetry={meta.reload} errorTitle="평가 정보를 불러오지 못했습니다" skeleton={<Card><SkeletonText lines={6} /></Card>}>
        {(m) => <Flow meta={m} />}
      </AsyncBoundary>
    </>
  );
}
