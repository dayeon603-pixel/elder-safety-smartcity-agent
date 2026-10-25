/** Pure model for the 도시 운영 지도: layer ids, open-now counts, and the 오늘의 인프라 조치 queue. */
import type { ActionsResponse, BriefingAedCandidate } from "../../api/zpTypes";
import type { OpsAction, OpsBit } from "../../api/opsApi";
import { OPS_CHANNEL_ENDPOINTS, OPS_LIST_TOP, ZP_BIT_TOP } from "../../config";
import type { BriefingModel } from "../zpBriefing";
import { evidenceFor } from "../zpEvidence";
import type { EvidenceRow } from "../zpEvidence";
import type { CrosswalkVerdict } from "./crossing";
import { aedStateAt, shelterStateAt, slotOfChoice } from "./hours";
import type { DayChoice, OpenState } from "./hours";
import type { AedRow, ShelterRow } from "./opsData";

export type LayerId = "silver" | "crosswalk" | "time" | "heat" | "bit";
export const LAYER_IDS: readonly LayerId[] = ["silver", "crosswalk", "time", "heat", "bit"];
export const LAYER_LABEL: Readonly<Record<LayerId, string>> = {
  silver: "실버존과 사고다발 지점",
  crosswalk: "신호가 모자란 횡단보도",
  time: "열린 AED · 무더위쉼터",
  heat: "오늘의 폭염 위험",
  bit: "안내단말 송출 대상",
};

export interface OpenCounts {
  open: number;
  closed: number;
  unknown: number;
  total: number;
}

export function countStates(states: readonly OpenState[]): OpenCounts {
  const c: OpenCounts = { open: 0, closed: 0, unknown: 0, total: states.length };
  for (const s of states) c[s] += 1;
  return c;
}

export interface WithState<T> {
  row: T;
  state: OpenState;
}

export function aedStates(rows: readonly AedRow[], choice: DayChoice, hour: number): WithState<AedRow>[] {
  const slot = slotOfChoice(choice);
  return rows.map((row) => ({ row, state: aedStateAt(row.hours, slot, hour) }));
}

export function shelterStates(rows: readonly ShelterRow[], choice: DayChoice, hour: number): WithState<ShelterRow>[] {
  return rows.map((row) => ({ row, state: shelterStateAt(row.nightOpen, row.weekendOpen, choice, hour) }));
}

/** Share of AEDs with known hours that are open; null when none has usable hours (never 0 %). */
export function openShare(c: OpenCounts): number | null {
  const known = c.open + c.closed;
  return known === 0 ? null : c.open / known;
}

export interface Focus {
  lat: number;
  lon: number;
  label: string;
}

export interface EvidenceLink {
  to: string;
  label: string;
  /** true: a plain <a href> (feed, file); false: an in-app route */
  external?: boolean;
}

export type ChannelKind = "gongmun" | "webhook" | "bit" | "audio" | "cbs";

/** One 전달 경로 of an action. `href` is set only when the backend route is live; otherwise the UI shows 준비 중. */
export interface Channel {
  kind: ChannelKind;
  label: string;
  /** e.g. "147/157자" or "3곳" */
  detail: string | null;
  /** false: the backend route is not live (button disabled, "준비 중") */
  available: boolean;
  /** backend document template id for gongmun channels (GET /documents/{docId}.docx) */
  docId: string | null;
}

/** Who has to do something for the action to work. */
export type Actor = "none" | "phone";
export const ACTOR_LABEL: Readonly<Record<Actor, string>> = { none: "고령자 조작 불필요", phone: "휴대폰 기본 수신" };

export interface QueueItem {
  /** none: runs on infrastructure or staff (신호, 실버존, 안내단말, 마을방송, AED, 방문팀); phone: arrives as a 재난문자, no app needed */
  actor: Actor;
  channels: Channel[];
  /** action id for POST /feedback */
  id: string;
  tag: string;
  title: string;
  /** extra lines under the title (drafts, stop list) */
  lines: string[];
  evidence: EvidenceRow[];
  source: string;
  evidenceLink: EvidenceLink;
  focus: Focus | null;
  /** false for items that are a link, not a decision (CAP feed) */
  decidable: boolean;
}

export interface QueueInput {
  regionName: string;
  speedMps: number;
  /** null: this 시군구 has no crosswalk rows in the data */
  crosswalks: { evaluated: number; short: CrosswalkVerdict[]; shortAtBaseline: number } | null;
  briefing: BriefingModel | null;
  actions: ActionsResponse | null;
  rawActions: readonly OpsAction[];
  tmaxC: number | null;
  /** AED open counts at the slider hour */
  aed: { counts: OpenCounts; hourLabel: string } | null;
  capFeedHref: string;
  /** e.g. "전국 평가에서 현행 15% → 에이전트 93%", from /silver/summary when it has loaded */
  silverHeadline: string | null;
}

const E = OPS_CHANNEL_ENDPOINTS;
const gongmun = (docId: string): Channel => ({ kind: "gongmun", label: "공문 초안 다운로드", detail: null, available: E.gongmun !== null, docId });
const webhook = (): Channel => ({ kind: "webhook", label: "웹훅 (기존 시스템)", detail: null, available: E.webhook !== null, docId: null });
const audio = (): Channel => ({ kind: "audio", label: "마을방송 음성 듣기", detail: null, available: E.audio !== null, docId: null });
const cbsChannel = (a: ActionsResponse | null): Channel[] => (a === null ? [] : [{ kind: "cbs", label: "재난문자 초안", detail: `${a.cbs.length}/${a.cbs.limit}자`, available: true, docId: null }]);

const SOURCE_SIGNAL = "전국 횡단보도 표준데이터(보행 녹색시간·횡단 길이) · 노인 보행속도 가정값";
const SOURCE_CBS = "행정안전부 재난문자 개선안(157자) · 기상청 · 질병관리청";
const SOURCE_BIT = "버스정류장 위치 · 행정안전부 주민등록 인구(65세 이상)";
const SOURCE_CAP = "OASIS CAP 1.2 Atom 피드 (Exercise 상태, 외부 발송 없음)";

function signalItem(i: QueueInput): QueueItem {
  const base = { actor: "none" as const, channels: [gongmun("signals"), webhook()], id: "signal.extend", tag: "신호", source: SOURCE_SIGNAL, evidenceLink: { to: "/field", label: "횡단 시뮬레이션" }, decidable: true };
  const speed = i.speedMps.toFixed(2).replace(/0$/, "");
  if (i.crosswalks === null) {
    return { ...base, title: `${i.regionName}: 횡단보도 신호 자료가 없어 판정하지 않았습니다`, lines: [], evidence: [{ label: "자료", value: "표준데이터에 이 시군구 기록 없음" }], focus: null, decidable: false };
  }
  const { short, evaluated, shortAtBaseline } = i.crosswalks;
  const top = short.slice(0, OPS_LIST_TOP);
  const first = short[0];
  return {
    ...base,
    title: short.length === 0 ? `걸음 ${speed} m/s 기준, 신호가 모자란 횡단보도가 없습니다` : `걸음 ${speed} m/s 기준, 녹색 신호가 모자란 횡단보도 ${short.length}곳`,
    lines: top.map((v) => `${v.address} · 길이 ${v.lengthM}m · 녹색 ${v.greenSec}초 → 필요 ${v.requiredSec.toFixed(1)}초 · +${v.extensionSec}초 연장`),
    evidence: [
      { label: "판정한 횡단보도", value: `${evaluated}곳` },
      { label: `부족 (${speed} m/s)`, value: `${short.length}곳` },
      { label: "부족 (0.7 m/s 기준)", value: `${shortAtBaseline}곳` },
      ...(first === undefined ? [] : [{ label: "가장 큰 부족", value: `+${first.extensionSec}초` }]),
    ],
    focus: first === undefined ? null : { lat: first.lat, lon: first.lon, label: `+${first.extensionSec}초` },
  };
}

function silverItem(b: BriefingModel | null, headline: string | null): QueueItem | null {
  if (b === null) return null;
  const s = b.silver;
  const ev = evidenceFor("silver", b, null);
  if (!s.available) {
    return { actor: "none", channels: [], id: "silver.check", tag: "실버존", title: "실버존 비교는 이 시군구에서 분석하지 않았습니다", lines: [], evidence: [], source: ev.source, evidenceLink: { to: "/silver", label: "실버존 지도" }, focus: null, decidable: false };
  }
  return {
    channels: [gongmun("silver.check"), webhook()],
    actor: "none",
    id: "silver.check",
    tag: "실버존",
    title: `실버존 후보 ${s.ourPicks}곳을 현장 확인 (현행 ${s.currentZones}곳, 2024 다발지 ${s.hotspots}곳)`,
    lines: [
      ...(s.hotspotsCovered === null ? [] : [`다발지를 지키는 구역: 현행 ${s.hotspotsCovered.current}곳 → 에이전트 ${s.hotspotsCovered.ours}곳`]),
      ...(headline === null ? [] : [headline]),
    ],
    evidence: ev.rows,
    source: ev.source,
    evidenceLink: { to: "/silver", label: "실버존 지도" },
    focus: null,
    decidable: true,
  };
}

function aedItem(i: QueueInput): QueueItem | null {
  const b = i.briefing;
  if (b === null || b.aed === null) return null;
  const ev = evidenceFor("aed", b, null);
  const first = b.aed.candidates[0] as (BriefingAedCandidate & { lat?: number; lon?: number }) | undefined;
  const lines = b.aed.candidates.slice(0, 3).map((c) => `${c.org} · ${c.address}`);
  const rows: EvidenceRow[] = [...ev.rows];
  if (i.aed !== null) rows.push({ label: `${i.aed.hourLabel} 열린 AED`, value: `${i.aed.counts.open}대 / 시간 자료 있는 ${i.aed.counts.open + i.aed.counts.closed}대` });
  return {
    channels: [gongmun("aed.convert"), webhook()],
    actor: "none",
    id: "aed.convert",
    tag: "응급",
    title: first === undefined ? "이 시군구에는 24시간 전환 후보가 없습니다" : `24시간 전환 1순위: ${first.org}`,
    lines,
    evidence: rows,
    source: ev.source,
    evidenceLink: { to: "/aed", label: "AED 밤 공백" },
    focus: first?.lat !== undefined && first.lon !== undefined ? { lat: first.lat, lon: first.lon, label: "전환 후보" } : null,
    decidable: first !== undefined,
  };
}

function heatItem(i: QueueInput): QueueItem | null {
  const b = i.briefing;
  if (b === null) return null;
  const a = i.rawActions.find((x) => x.module === "heat");
  if (a === undefined) return null;
  const ev = evidenceFor("heat", b, i.tmaxC);
  return {
    channels: [gongmun("heat.alert"), ...cbsChannel(i.actions), audio(), webhook()],
    actor: "none",
    id: a.action_id ?? "heat.alert",
    tag: "폭염",
    title: a.text,
    lines: [`이유: ${a.reason}`],
    evidence: ev.rows,
    source: ev.source,
    evidenceLink: { to: "/demo", label: "폭염 경보 평가" },
    focus: null,
    decidable: true,
  };
}

function bitItem(a: ActionsResponse | null): QueueItem | null {
  if (a === null) return null;
  const stops = (a.bit as OpsBit[]).slice(0, ZP_BIT_TOP);
  const first = stops[0];
  return {
    channels: [{ kind: "bit", label: "버스정류장 안내단말 문구", detail: `${stops.length}곳`, available: true, docId: null }, gongmun("bit"), webhook()],
    actor: "none",
    id: "bit",
    tag: "안내단말",
    title: stops.length === 0 ? "이 시군구에서 고른 정류장이 없습니다" : `안내단말 문구를 보낼 정류장 상위 ${stops.length}곳`,
    lines: stops.map((s) => `${s.rank}. ${s.name} (${s.dong}) · 65세 이상 약 ${s.covered_pop65_est.toLocaleString("ko-KR")}명 · “${s.message.lines.join(" ")}”`),
    evidence: stops.map((s) => ({ label: `${s.name} 반경 300m 65세 이상(추정)`, value: `${s.covered_pop65_est.toLocaleString("ko-KR")}명` })),
    source: SOURCE_BIT,
    evidenceLink: { to: "/briefing", label: "담당자 브리핑" },
    focus: first?.lat !== undefined && first.lon !== undefined ? { lat: first.lat, lon: first.lon, label: "1순위 정류장" } : null,
    decidable: stops.length > 0,
  };
}

function cbsItem(a: ActionsResponse | null): QueueItem | null {
  if (a === null) return null;
  return {
    channels: [gongmun("cbs"), ...cbsChannel(a), audio(), webhook()],
    actor: "phone",
    id: "cbs",
    tag: "재난문자",
    title: "재난문자 초안 (발송 안 함)",
    lines: [a.cbs.text, `쉬운 말: ${a.cbs.easy_text}`],
    evidence: [
      { label: "문안 글자 수", value: `${a.cbs.length} / ${a.cbs.limit}자` },
      { label: "쉬운 말 글자 수", value: `${a.cbs.easy_length} / ${a.cbs.limit}자` },
    ],
    source: SOURCE_CBS,
    evidenceLink: { to: "/briefing", label: "담당자 브리핑" },
    focus: null,
    decidable: true,
  };
}

/** The queue, in the order a 담당자 should look at it. 폭염 goes first only when our model alerts the district today. */
export function buildQueue(i: QueueInput): QueueItem[] {
  const heat = heatItem(i);
  const alerted = i.briefing?.heat.alertedOurs === true;
  const rest: (QueueItem | null)[] = [signalItem(i), silverItem(i.briefing, i.silverHeadline), aedItem(i)];
  const tail: (QueueItem | null)[] = [bitItem(i.actions), cbsItem(i.actions)];
  const cap: QueueItem = {
    channels: [],
    actor: "none",
    id: "cap.feed",
    tag: "CAP",
    title: "CAP 1.2 피드로 공유 (Exercise 상태)",
    lines: [],
    evidence: [{ label: "형식", value: "OASIS CAP 1.2 Atom" }],
    source: SOURCE_CAP,
    evidenceLink: { to: i.capFeedHref, label: "CAP 피드 열기", external: true },
    focus: null,
    decidable: false,
  };
  const ordered = alerted ? [heat, ...rest, ...tail] : [...rest, heat, ...tail];
  return [...ordered, cap].filter((x): x is QueueItem => x !== null);
}

export interface ActorSummary {
  total: number;
  none: number;
  phone: number;
}

/** Counts over the decidable actions only (a "자료 없음" row or the CAP link is not an action). */
export function actorSummary(items: readonly QueueItem[]): ActorSummary {
  const acts = items.filter((i) => i.decidable);
  return { total: acts.length, none: acts.filter((i) => i.actor === "none").length, phone: acts.filter((i) => i.actor === "phone").length };
}
