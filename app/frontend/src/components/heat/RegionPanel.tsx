import { useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { heatApi, describeHeatError } from "../../api/heat";
import type { HeatFlag, HeatRegionDetail, HeatVariant } from "../../api/heatTypes";
import { useApi } from "../../api/useApi";
import { HEAT_SHELTER_NOTE } from "../../config";
import { formatDecimal, formatInt, formatPercent } from "../../lib/format";
import { dayLabel } from "../../lib/heatDays";
import type { AlertClass, FlagState } from "../../lib/heatMap";
import { classText, flagState } from "../../lib/heatMap";
import { AsyncBoundary } from "../AsyncBoundary";
import { Badge } from "../Badge";
import { Notice } from "../Notice";
import { SkeletonText } from "../Skeleton";

const FLAG_TEXT: Record<FlagState, string> = { open: "열림", closed: "닫힘", unknown: "정보 없음" };

function Flag({ label, value }: { label: string; value: HeatFlag }) {
  const s = flagState(value);
  return (
    <Badge tone={s === "open" ? "success" : "neutral"}>
      {label} {FLAG_TEXT[s]}
    </Badge>
  );
}

function Row({ term, children, hint }: { term: string; children: ReactNode; hint?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-t border-line py-2 first:border-t-0">
      <dt className="text-small text-ink-muted">
        {term}
        {hint !== undefined && <span className="block text-caption text-ink-faint">{hint}</span>}
      </dt>
      <dd className="num m-0 text-right text-body font-medium">{children}</dd>
    </div>
  );
}

function Shelters({ shelters }: { shelters: HeatRegionDetail["shelters"] }) {
  if (shelters.coverage === "none") {
    return (
      <Notice>
        <strong className="font-semibold">쉼터 좌표 자료 없음.</strong> 이 시군구는 쉼터가 0곳이라는 뜻이 아니라, 좌표 자료를 확보하지 못했다는 뜻입니다.
      </Notice>
    );
  }
  if (shelters.nearby.length === 0) {
    return <Notice>이 시군구 안에서 가까운 쉼터가 확인되지 않았습니다.</Notice>;
  }
  return (
    <ul className="m-0 list-none divide-y divide-line border border-line p-0">
      {shelters.nearby.map((s) => (
        <li key={`${s.name}-${s.dist_km}`} className="space-y-1 px-3 py-2">
          <p className="flex items-baseline justify-between gap-3 text-body font-medium">
            <span className="min-w-0 break-words">{s.name}</span>
            <span className="num shrink-0 text-small font-normal text-ink-muted">{formatDecimal(s.dist_km, 1)} km</span>
          </p>
          <p className="flex flex-wrap gap-2">
            <Flag label="야간" value={s.night_open} />
            <Flag label="주말" value={s.weekend_open} />
          </p>
        </li>
      ))}
    </ul>
  );
}

function Detail({ d, cls, k, date }: { d: HeatRegionDetail; cls: AlertClass | undefined; k: number; date: string }) {
  const inBudget = d.scores.rank <= k;
  return (
    <div className="space-y-6">
      <section aria-labelledby="rp-today" className="space-y-2">
        <h4 id="rp-today" className="text-small font-semibold">{dayLabel(date)} 상황</h4>
        <dl className="m-0">
          <Row term="실제 환자">{formatInt(d.cases_today)}명</Row>
          <Row term="우리 점수 위치" hint={`경보는 상위 ${formatInt(k)}곳`}>
            {inBudget ? "경보 안" : "경보 밖"}
          </Row>
          <Row term="우리 점수">{formatDecimal(d.scores.ours, 3)}</Row>
          <Row term="B1 점수" hint="특보 규칙의 점수 (높을수록 경보 쪽)">{formatDecimal(d.scores.b1, 1)}</Row>
        </dl>
        {cls !== undefined && <p className="text-small text-ink-muted">이날 결과: <span className="font-medium text-ink">{classText(cls)}</span></p>}
      </section>

      <section aria-labelledby="rp-drivers" className="space-y-2">
        <h4 id="rp-drivers" className="text-small font-semibold">점수의 근거</h4>
        <dl className="m-0">
          <Row term="최고기온">{d.drivers.tmax === null ? "자료 없음" : `${formatDecimal(d.drivers.tmax, 1)}℃`}</Row>
          {d.drivers.apparent_max != null && <Row term="체감 최고">{formatDecimal(d.drivers.apparent_max, 1)}℃{d.drivers.station != null ? ` (측정소 ${d.drivers.station.name}, ${formatDecimal(d.drivers.station.dist_km, 1)}km)` : ""}</Row>}
          <Row term="과거 발생률" hint="이 시군구의 학습 기간 기록">{formatDecimal(d.drivers.hist_rate, 3)}</Row>
          <Row term="65세 이상 비율" hint="주민등록 인구 기준">{formatPercent(d.drivers.old_share)}</Row>
        </dl>
      </section>

      <section aria-labelledby="rp-shelters" className="space-y-2">
        <h4 id="rp-shelters" className="text-small font-semibold">가까운 무더위쉼터</h4>
        <Shelters shelters={d.shelters} />
        <p className="text-caption text-ink-faint">{HEAT_SHELTER_NOTE}</p>
      </section>
    </div>
  );
}

interface RegionPanelProps {
  code: string;
  title: string;
  date: string;
  variant: HeatVariant;
  cls: AlertClass | undefined;
  k: number;
}

/** Detail for one clicked district. Mounted with key=code, so each selection moves focus to its heading once. */
export function RegionPanel({ code, title, date, variant, cls, k }: RegionPanelProps) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus();
  }, []);
  const detail = useApi(() => heatApi.region(code, date, variant), { key: `${code}|${date}|${variant}`, describe: describeHeatError });
  return (
    <section aria-labelledby="rp-title" className="border border-line bg-surface p-4 sm:p-6">
      <h3 id="rp-title" ref={heading} tabIndex={-1} className="mb-4 text-h3 outline-none">{title}</h3>
      <AsyncBoundary state={detail.state} onRetry={detail.reload} errorTitle="시군구 정보를 불러오지 못했습니다" skeleton={<SkeletonText lines={8} label="시군구 정보를 불러오는 중" />}>
        {(d) => <Detail d={d} cls={cls} k={k} date={date} />}
      </AsyncBoundary>
    </section>
  );
}
