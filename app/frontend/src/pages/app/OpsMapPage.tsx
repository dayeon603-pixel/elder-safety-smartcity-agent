import { useId, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { loadRegionSilver } from "../../api/opsApi";
import type { OpsAction, OpsBit } from "../../api/opsApi";
import { API_BASE } from "../../api/client";
import { feedbackAvailable } from "../../api/feedback";
import { describeHeatError, HEAT_MOCK, heatApi } from "../../api/heat";
import type { HeatRegion } from "../../api/heatTypes";
import { useApi } from "../../api/useApi";
import { ZP_MOCK, describeZpError, zpApi } from "../../api/zp";
import { PageHeader } from "../../components/AppShell";
import { AsyncBoundary } from "../../components/AsyncBoundary";
import { Badge } from "../../components/Badge";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { SelectField, TextField } from "../../components/Field";
import { Notice } from "../../components/Notice";
import { SkeletonText } from "../../components/Skeleton";
import { ActionQueue } from "../../components/app/ActionQueue";
import { heatRankBand } from "../../lib/rankBand";
import { OpsMap } from "../../components/app/OpsMap";
import type { Scenario } from "../../components/app/OpsMap";
import {
  FIELD_SPEED_DEFAULT, FIELD_SPEED_MAX, FIELD_SPEED_MIN, FIELD_SPEED_STEP, OPS_CAP_FEED_PATH, OPS_LIST_TOP, ZP_DEFAULT_DATE, ZP_DEFAULT_SIDO, ZP_MOCK_NOTICE,
} from "../../config";
import { assessAll, clampSpeed, shortestFirst } from "../../lib/app/crossing";
import { DAY_CHOICES, DAY_CHOICE_LABEL, OPEN_STATE_LABEL, kstNow } from "../../lib/app/hours";
import type { DayChoice } from "../../lib/app/hours";
import { loadRegionLayers } from "../../lib/app/opsData";
import { LAYER_IDS, LAYER_LABEL, aedStates, buildQueue, countStates, openShare, shelterStates } from "../../lib/app/opsModel";
import type { Focus, LayerId } from "../../lib/app/opsModel";
import { haversineM } from "../../lib/field";
import { BOUNDARY_URL } from "../../lib/geo";
import { loadBoundary } from "../../lib/zpMap";
import { ALERT_CLASS_LABEL, isIsoDate, normalizeBriefing } from "../../lib/zpBriefing";
import { regionOptions, resolvePick } from "../../lib/zpRegions";
import { silverHeadline } from "../../lib/zpSilver";
import { formatMeters } from "../../lib/field";

const TEAMS = 10;
const ALL_LAYERS: ReadonlySet<LayerId> = new Set(LAYER_IDS);
const HEAT_COLOR = { alerted: "#b3261e", calm: "#1d6b3a", unknown: "#626a76" } as const;
const hh = (h: number): string => `${String(h).padStart(2, "0")}시`;
const RANGE = "mt-1 block h-11 w-full accent-[#0072B2]";

function parseLayers(raw: string | null): ReadonlySet<LayerId> {
  if (raw === null) return ALL_LAYERS;
  const set = new Set(raw.split(",").filter((x): x is LayerId => (LAYER_IDS as readonly string[]).includes(x)));
  return set;
}

function parseHour(raw: string | null, fallback: number): number {
  if (raw === null || raw.trim() === "") return fallback;
  const n = Number(raw);
  return Number.isInteger(n) && n >= 0 && n <= 23 ? n : fallback;
}

function Legend({ layers, scenario }: { layers: ReadonlySet<LayerId>; scenario: Scenario }) {
  const item = (key: string, shape: ReactNode, text: string) => (
    <li key={key} className="flex items-center gap-2">
      <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true" className="shrink-0">{shape}</svg>
      {text}
    </li>
  );
  const rows: ReactNode[] = [];
  if (layers.has("silver")) {
    rows.push(item("z", scenario === "current" ? <circle cx="11" cy="11" r="6" fill="none" stroke="#858c98" strokeWidth="2" /> : <circle cx="11" cy="11" r="3.5" fill="#0072B2" fillOpacity="0.6" />, scenario === "current" ? "현행 실버존" : "에이전트 추천 위치"));
    rows.push(item("h1", <circle cx="11" cy="11" r="7" fill="#14171c" fillOpacity="0.85" />, "사고다발지, 구역 안"));
    rows.push(item("h2", <circle cx="11" cy="11" r="7" fill="#fff" stroke="#b3261e" strokeWidth="2.4" />, "사고다발지, 구역 밖"));
  }
  if (layers.has("crosswalk")) rows.push(item("c", <polygon points="11,3 19,11 11,19 3,11" fill="#b3261e" stroke="#fff" strokeWidth="1.4" />, "신호가 모자란 횡단보도"));
  if (layers.has("time")) {
    rows.push(item("a1", <rect x="4" y="4" width="14" height="14" fill="#1d6b3a" stroke="#fff" strokeWidth="1.4" />, "AED 열림 (사각형)"));
    rows.push(item("s1", <polygon points="11,3 19,17 3,17" fill="#1d6b3a" stroke="#fff" strokeWidth="1.4" />, "쉼터 열림 (삼각형)"));
    rows.push(item("a2", <circle cx="11" cy="11" r="3" fill="#626a76" fillOpacity="0.7" />, "닫힘 (작은 점)"));
    rows.push(item("a3", <circle cx="11" cy="11" r="4" fill="#fff" stroke="#8a5a00" strokeWidth="1.4" strokeDasharray="2 2" />, "시간 자료 없음 (점선 점)"));
  }
  if (layers.has("bit")) rows.push(item("b", <rect x="2" y="2" width="18" height="18" rx="4" fill="#0072B2" />, "안내단말 정류장 (번호 = 순위)"));
  return <ul className="m-0 flex list-none flex-wrap gap-x-5 gap-y-2 p-0 text-small" aria-label="범례">{rows}</ul>;
}

interface BodyProps {
  region: HeatRegion;
  date: string;
  speed: number;
  hour: number;
  day: DayChoice;
  layers: ReadonlySet<LayerId>;
}

function Body({ region, date, speed, hour, day, layers }: BodyProps) {
  const [scenario, setScenario] = useState<Scenario>("ours");
  const [focus, setFocus] = useState<{ focus: Focus; id: string } | null>(null);
  const [, setSaved] = useState(0);
  const scenarioName = useId();
  const key = `${region.code}|${date}`;
  const data = useApi(() => loadRegionLayers(region.sido, region.code), { key: region.code });
  const silver = useApi(() => loadRegionSilver(region), { key: region.code, describe: describeZpError });
  const boundary = useApi(() => loadBoundary(BOUNDARY_URL), {});
  const briefing = useApi(() => zpApi.briefing(region.code, date, TEAMS), { key: `b|${key}`, describe: describeZpError });
  const actions = useApi(() => zpApi.actions(region.code, date, TEAMS), { key: `a|${key}`, describe: describeZpError });
  const summary = useApi(zpApi.silverSummary, { describe: describeZpError });
  const fb = useApi(feedbackAvailable);
  const feedbackOpen = fb.state.status === "loading" ? null : fb.state.status === "ready" && fb.state.data;

  const layersData = data.state.status === "ready" ? data.state.data : null;
  const model = briefing.state.status === "ready" ? normalizeBriefing(briefing.state.data) : null;
  const acts = actions.state.status === "ready" ? actions.state.data : null;

  const derived = useMemo(() => {
    const cw = layersData?.crosswalks ?? [];
    const verdicts = assessAll(cw, speed);
    const baseline = assessAll(cw, FIELD_SPEED_DEFAULT).filter((v) => v.short).length;
    const aeds = aedStates(layersData?.aeds ?? [], day, hour);
    const shelters = shelterStates(layersData?.shelters ?? [], day, hour);
    return { verdicts, short: shortestFirst(verdicts), baseline, aeds, shelters, aedCounts: countStates(aeds.map((a) => a.state)), shCounts: countStates(shelters.map((s) => s.state)) };
  }, [layersData, speed, day, hour]);

  const silverPts = silver.state.status === "ready" ? silver.state.data : [];
  const bit = acts === null ? [] : (acts.bit as OpsBit[]);
  const rawActions = briefing.state.status === "ready" ? (briefing.state.data.actions as OpsAction[]) : [];
  const head = summary.state.status === "ready" ? silverHeadline(summary.state.data.all) : null;
  const headline = head === null || summary.state.status !== "ready" ? null : `전국 평가(같은 ${summary.state.data.k.toLocaleString("ko-KR")}곳): 사고다발지가 구역 300m 안에 든 비율 현행 ${Math.round(head.current.coverage * 100)}% → 에이전트 ${Math.round(head.ours.coverage * 100)}%`;
  const tmax = acts?.context?.tmax_c ?? null;

  const queue = useMemo(
    () =>
      buildQueue({
        regionName: region.name,
        speedMps: speed,
        crosswalks: layersData === null || layersData.crosswalks.length === 0 ? null : { evaluated: derived.verdicts.length, short: derived.short, shortAtBaseline: derived.baseline },
        briefing: model,
        actions: acts,
        rawActions,
        tmaxC: tmax,
        aed: layersData === null || layersData.aeds.length === 0 ? null : { counts: derived.aedCounts, hourLabel: `${DAY_CHOICE_LABEL[day]} ${hh(hour)}` },
        capFeedHref: `${API_BASE}${OPS_CAP_FEED_PATH}`,
        silverHeadline: headline,
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [region.name, speed, layersData, derived, model, acts, briefing.state, tmax, day, hour, headline],
  );

  const heat = model?.heat ?? null;
  const heatColor = heat === null ? null : heat.alertedOurs === null ? HEAT_COLOR.unknown : heat.alertedOurs ? HEAT_COLOR.alerted : HEAT_COLOR.calm;
  const mapSummary = [
    layers.has("crosswalk") ? `횡단보도 ${derived.verdicts.length}곳 중 ${derived.short.length}곳 신호 부족` : null,
    layers.has("time") ? `${DAY_CHOICE_LABEL[day]} ${hh(hour)} AED ${derived.aedCounts.total}대 중 ${derived.aedCounts.open}대 열림` : null,
  ].filter((x): x is string => x !== null).join(" · ");
  const center = { lat: region.lat, lon: region.lon };
  const nearest = <T extends { row: { lat: number; lon: number } }>(rows: readonly T[]): (T & { d: number })[] => rows.map((r) => ({ ...r, d: haversineM(center.lat, center.lon, r.row.lat, r.row.lon) })).sort((a, b) => a.d - b.d);

  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_29rem]">
      <div className="min-w-0 space-y-6">
        <Card
          title={`${region.sido} ${region.name}`}
          caption="지도는 선택한 시군구의 공개 자료를 겹쳐 그립니다. 켜고 끌 수 있는 다섯 겹입니다."
          id="ops-map"
        >
          <fieldset className="m-0 flex flex-wrap gap-x-5 gap-y-1 border-0 p-0">
            <legend className="mb-1 text-small font-medium">보이는 겹</legend>
            {LAYER_IDS.map((l) => (
              <LayerToggle key={l} id={l} on={layers.has(l)} />
            ))}
          </fieldset>

          {layers.has("heat") && (
            <p className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1 border border-line bg-surface-sunken px-3 py-2 text-body" data-testid="heat-strip">
              <Badge tone={heat?.alertedOurs === true ? "danger" : heat?.alertedOurs === false ? "success" : "neutral"}>
                {heat === null ? (briefing.state.status === "error" ? "불러오지 못함" : "불러오는 중") : heat.alertedOurs === null ? "평가 대상 아님" : heat.alertedOurs ? "▲ 경보 대상" : "● 경보 대상 아님"}
              </Badge>
              {heat !== null && (
                <span className="num">
                  {date} · {ALERT_CLASS_LABEL[heat.alertClass]}
                  {heat.rank !== null && ` · 에이전트 순위 ${heatRankBand(heat.rank, heat.nRegions)}${heat.nRegions !== null ? ` (${heat.nRegions}곳 중)` : ""}`}
                  {tmax !== null && ` · 최고 ${tmax.toFixed(1)}℃`}
                </span>
              )}
            </p>
          )}

          {layers.has("silver") && (
            <fieldset className="m-0 mt-4 flex flex-wrap items-center gap-x-5 gap-y-1 border-0 p-0">
              <legend className="mb-1 text-small font-medium">사고다발지를 어느 구역 기준으로 칠할까요</legend>
              {(["current", "ours"] as const).map((s) => (
                <label key={s} className="flex min-h-11 items-center gap-2 text-body">
                  <input type="radio" name={scenarioName} checked={scenario === s} onChange={() => setScenario(s)} className="h-4 w-4 accent-[#0072B2]" />
                  {s === "current" ? "현행 실버존" : "에이전트 추천"}
                </label>
              ))}
            </fieldset>
          )}

          <div className="mt-4 border border-line">
            {data.state.status === "loading" ? (
              <div className="p-6"><SkeletonText lines={6} label="지도 자료를 불러오는 중" /></div>
            ) : data.state.status === "error" ? (
              <div className="p-6"><Notice tone="danger">지도 자료를 불러오지 못했습니다: {data.state.message}</Notice></div>
            ) : (
              <OpsMap
                regionName={region.name}
                center={center}
                boundary={boundary.state.status === "ready" ? boundary.state.data : null}
                layers={layers}
                scenario={scenario}
                silver={silverPts}
                crosswalks={derived.verdicts}
                aeds={derived.aeds}
                shelters={derived.shelters}
                bit={bit}
                heatColor={layers.has("heat") ? heatColor : null}
                focus={focus?.focus ?? null}
                summary={mapSummary}
              />
            )}
          </div>

          <p aria-live="polite" className="num mt-3 text-body font-semibold" data-testid="map-summary">{mapSummary}</p>
          <div className="mt-3"><Legend layers={layers} scenario={scenario} /></div>
          <ul className="m-0 mt-3 list-none space-y-1 p-0 text-caption text-ink-muted" aria-label="자료 상태">
            {layers.has("crosswalk") && layersData !== null && layersData.crosswalks.length === 0 && <li data-testid="no-crosswalk">횡단보도: 이 시군구는 표준데이터에 신호 기록이 없어 표시하지 않습니다.</li>}
            {layers.has("time") && layersData !== null && layersData.aeds.length === 0 && <li>AED: 이 시군구의 개방시간 자료가 없습니다.</li>}
            {layers.has("time") && layersData !== null && layersData.shelters.length === 0 && <li data-testid="no-shelter">무더위쉼터: 위치 자료 없음 (자료가 있는 시도만 표시합니다).</li>}
            {layers.has("time") && layersData !== null && layersData.aeds.length > 0 && <li>AED {derived.aedCounts.total}대 중 개방시간 자료가 없는 곳 {derived.aedCounts.unknown}대는 점선 점입니다. 닫힘으로 세지 않았습니다.</li>}
            {layers.has("time") && layersData !== null && layersData.shelters.length > 0 && day === "weekday" && hour >= 6 && hour < 22 && <li>쉼터는 평일 낮 개방 시간 자료가 없어 모두 점선 점입니다. 22–06시와 주말·공휴일은 자료의 야간·주말 표시를 따릅니다.</li>}
            {layers.has("silver") && silver.state.status === "error" && <li role="alert">실버존: {silver.state.message}</li>}
            {layers.has("bit") && acts !== null && bit.every((b) => b.lat === undefined) && bit.length > 0 && <li>안내단말: 서버가 정류장 좌표를 보내지 않아 지도에는 표시하지 않고 오른쪽 조치에 적었습니다.</li>}
            <li>배경 경계는 2013년 기준입니다. 점 위치는 공공데이터 좌표입니다.</li>
          </ul>
        </Card>

        <Card title="지도와 같은 내용을 목록으로" caption="지점 이름과 숫자를 읽을 수 있는 목록입니다. 시군구 중심에서 가까운 순입니다.">
          <div className="space-y-3">
            <details className="border border-line" open>
              <summary className="min-h-12 cursor-pointer px-4 py-3 text-body font-semibold">신호가 모자란 횡단보도 ({derived.short.length}곳)</summary>
              {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- a scrollable region must be keyboard-focusable (axe scrollable-region-focusable) */}
              <div className="overflow-x-auto px-4 pb-4" tabIndex={0} role="region" aria-label="신호가 모자란 횡단보도 표">
                {derived.short.length === 0 ? <p className="text-small text-ink-muted">해당 없음 또는 자료 없음.</p> : (
                  <table className="w-full min-w-[32rem] text-left text-small">
                    <caption className="sr-only">걸음 {speed} m/s 기준 신호가 모자란 횡단보도</caption>
                    <thead><tr className="border-b border-line text-ink-muted"><th scope="col" className="py-2 pr-3 font-medium">주소</th><th scope="col" className="py-2 pr-3 font-medium">길이</th><th scope="col" className="py-2 pr-3 font-medium">녹색</th><th scope="col" className="py-2 pr-3 font-medium">필요</th><th scope="col" className="py-2 font-medium">연장</th></tr></thead>
                    <tbody>
                      {derived.short.slice(0, OPS_LIST_TOP).map((v) => (
                        <tr key={v.id} className="border-b border-line last:border-0">
                          <td className="py-2 pr-3">{v.address}</td>
                          <td className="num py-2 pr-3">{v.lengthM}m</td>
                          <td className="num py-2 pr-3">{v.greenSec}초</td>
                          <td className="num py-2 pr-3">{v.requiredSec.toFixed(1)}초</td>
                          <td className="num py-2 font-semibold text-danger">+{v.extensionSec}초</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </details>
            <details className="border border-line">
              <summary className="min-h-12 cursor-pointer px-4 py-3 text-body font-semibold">
                {DAY_CHOICE_LABEL[day]} {hh(hour)} AED ({derived.aedCounts.open}대 열림
                {openShare(derived.aedCounts) !== null && `, 시간 자료 있는 ${derived.aedCounts.open + derived.aedCounts.closed}대 중 ${Math.round((openShare(derived.aedCounts) ?? 0) * 100)}%`})
              </summary>
              <ul className="m-0 list-none space-y-1 px-4 pb-4 text-small">
                {nearest(derived.aeds).slice(0, OPS_LIST_TOP).map(({ row, state, d }) => (
                  <li key={row.id} className="flex flex-wrap justify-between gap-x-3 border-b border-line py-1 last:border-0">
                    <span className="min-w-0 break-words">{row.org} <span className="text-ink-muted">{row.place}</span></span>
                    <span className="num shrink-0"><Badge tone={state === "open" ? "success" : state === "closed" ? "neutral" : "danger"}>{OPEN_STATE_LABEL[state]}</Badge> {formatMeters(d)}</span>
                  </li>
                ))}
                {derived.aeds.length === 0 && <li className="text-ink-muted">자료 없음.</li>}
              </ul>
            </details>
            <details className="border border-line">
              <summary className="min-h-12 cursor-pointer px-4 py-3 text-body font-semibold">무더위쉼터 ({derived.shCounts.total}곳 중 {derived.shCounts.open}곳 열림)</summary>
              <ul className="m-0 list-none space-y-1 px-4 pb-4 text-small">
                {nearest(derived.shelters).slice(0, OPS_LIST_TOP).map(({ row, state, d }) => (
                  <li key={row.id} className="flex flex-wrap justify-between gap-x-3 border-b border-line py-1 last:border-0">
                    <span className="min-w-0 break-words">{row.name}</span>
                    <span className="num shrink-0"><Badge tone={state === "open" ? "success" : state === "closed" ? "neutral" : "danger"}>{OPEN_STATE_LABEL[state]}</Badge> {formatMeters(d)}</span>
                  </li>
                ))}
                {derived.shelters.length === 0 && <li className="text-ink-muted">쉼터 위치 자료 없음.</li>}
              </ul>
            </details>
          </div>
        </Card>
      </div>

      <div className="min-w-0">
        {briefing.state.status === "error" && <div className="mb-4"><Notice tone="danger">브리핑을 불러오지 못했습니다: {briefing.state.message}</Notice></div>}
        {actions.state.status === "error" && <div className="mb-4"><Notice tone="danger">행동 채널을 불러오지 못했습니다: {actions.state.message}</Notice></div>}
        <ActionQueue
          items={queue}
          sggCode={region.code}
          date={date}
          feedbackOpen={feedbackOpen}
          focusLabel={focus?.id ?? null}
          onFocus={(f, id) => setFocus(f === null ? null : { focus: f, id })}
          onSaved={() => setSaved((n) => n + 1)}
          audio={acts === null ? null : { regionName: region.name, lat: region.lat, lon: region.lon, text: acts.cbs.easy_text, date }}
        />
      </div>
    </div>
  );
}

/** Checkbox bound to the ?layers= URL param through a custom event-free approach: see Console. */
function LayerToggle({ id, on }: { id: LayerId; on: boolean }) {
  const [params, setParams] = useSearchParams();
  const toggle = (): void => {
    const cur = new Set(parseLayers(params.get("layers")));
    if (cur.has(id)) cur.delete(id);
    else cur.add(id);
    const next = new URLSearchParams(params);
    next.set("layers", [...LAYER_IDS.filter((l) => cur.has(l))].join(",") || "none");
    setParams(next, { replace: true });
  };
  return (
    <label className="flex min-h-11 items-center gap-2 text-body">
      <input type="checkbox" checked={on} onChange={toggle} className="h-5 w-5 accent-[#0072B2]" />
      {LAYER_LABEL[id]}
    </label>
  );
}

function Console({ regions }: { regions: readonly HeatRegion[] }) {
  const [params, setParams] = useSearchParams();
  const pick = resolvePick(regions, params.get("sgg"));
  const date = params.get("date") ?? ZP_DEFAULT_DATE;
  const now = useMemo(() => kstNow(new Date()), []);
  const speed = clampSpeed(params.get("speed") === null ? FIELD_SPEED_DEFAULT : Number(params.get("speed")));
  const hour = parseHour(params.get("h"), now.hour);
  const dayParam = params.get("day");
  const day: DayChoice = (DAY_CHOICES as readonly string[]).includes(dayParam ?? "") ? (dayParam as DayChoice) : now.choice;
  const layers = parseLayers(params.get("layers"));
  const { sidos, sigungu } = useMemo(() => regionOptions(regions, pick?.sido ?? ZP_DEFAULT_SIDO), [regions, pick?.sido]);
  const region = regions.find((r) => r.code === pick?.code);
  const dateOk = isIsoDate(date);

  const update = (patch: Record<string, string>): void => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) next.set(k, v);
    setParams(next, { replace: true });
  };

  if (pick === null || region === undefined) return <Notice tone="danger">시군구 목록이 비어 있습니다.</Notice>;
  return (
    <div className="space-y-6">
      <Card title="보는 조건" id="ops-controls">
        <form className="grid gap-x-6 gap-y-4 md:grid-cols-2 xl:grid-cols-4" onSubmit={(e) => e.preventDefault()} aria-label="운영 지도 조건">
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
            label="폭염 분석 날짜"
            type="date"
            value={date}
            onChange={(e) => update({ date: e.target.value })}
            error={dateOk ? null : "YYYY-MM-DD 형식의 날짜를 입력해 주세요."}
            hint="폭염은 2023–2025년 5–9월 날짜만 분석합니다."
          />
          <div className="space-y-1">
            <label htmlFor="ops-speed" className="block text-small font-medium">
              걸음 속도 <span className="num font-semibold" data-testid="speed-value">{speed.toFixed(2)} m/s</span>
            </label>
            <input id="ops-speed" type="range" min={FIELD_SPEED_MIN} max={FIELD_SPEED_MAX} step={FIELD_SPEED_STEP} value={speed} onChange={(e) => update({ speed: e.target.value })} className={RANGE} aria-describedby="ops-speed-hint" />
            <p id="ops-speed-hint" className="text-caption text-ink-faint">기본 {FIELD_SPEED_DEFAULT} m/s는 문헌의 노인 보행속도 가정값입니다.</p>
          </div>
          <div className="space-y-1 md:col-span-2">
            <label htmlFor="ops-hour" className="block text-small font-medium">
              AED · 쉼터를 볼 시각 <span className="num font-semibold" data-testid="hour-value">{hh(hour)}</span>
            </label>
            <input id="ops-hour" type="range" min={0} max={23} step={1} value={hour} onChange={(e) => update({ h: e.target.value })} className={RANGE} />
          </div>
          <fieldset className="m-0 border-0 p-0 md:col-span-2">
            <legend className="mb-1 text-small font-medium">요일</legend>
            <div className="flex flex-wrap items-center gap-2">
              {DAY_CHOICES.map((d) => (
                <label key={d} className={`flex min-h-11 cursor-pointer items-center border px-4 text-body ${day === d ? "border-accent bg-accent-wash font-semibold text-accent-strong" : "border-line-strong bg-surface"}`}>
                  <input type="radio" name="ops-day" value={d} checked={day === d} onChange={() => update({ day: d })} className="sr-only" />
                  {DAY_CHOICE_LABEL[d]}
                </label>
              ))}
              <Button variant="secondary" onClick={() => update({ day: now.choice, h: String(now.hour) })}>지금 ({DAY_CHOICE_LABEL[now.choice]} {hh(now.hour)})</Button>
            </div>
            <p className="mt-1 text-caption text-ink-faint">평일은 수요일 칸을 읽습니다. 공휴일은 2025–2027년 달력 기준입니다.</p>
          </fieldset>
        </form>
      </Card>
      {dateOk ? <Body region={region} date={date} speed={speed} hour={hour} day={day} layers={layers} /> : <Notice tone="danger">날짜 형식이 올바르지 않아 지도를 만들지 않았습니다.</Notice>}
    </div>
  );
}

/** /app: 도시 운영 지도. One screen for a 시군구 담당자: every older-resident infrastructure action on one map. */
export function OpsMapPage() {
  const regions = useApi(heatApi.regions, { describe: describeHeatError });
  return (
    <>
      <PageHeader
        eyebrow="담당자"
        title="도시 운영 지도"
        lead="시군구 하나의 실버존, 횡단보도 신호, AED와 쉼터, 폭염 위험, 안내단말을 한 지도에 겹쳐 보고 오늘 할 인프라 조치를 정합니다."
      />
      {(ZP_MOCK || HEAT_MOCK) && <div className="mb-6"><Notice>{ZP_MOCK_NOTICE}</Notice></div>}
      <AsyncBoundary state={regions.state} onRetry={regions.reload} skeleton={<SkeletonText lines={4} label="시군구 목록을 불러오는 중" />} errorTitle="시군구 목록을 불러오지 못했습니다">
        {(r) => <Console regions={r} />}
      </AsyncBoundary>
    </>
  );
}
