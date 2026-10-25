/**
 * The fall-response workflow. Every step is timed with the injected clock (performance.now in the app), so the
 * latencies shown in the UI are measured, not assumed. t0 = the moment the detection fired (or the button was pressed).
 * The hold time of the fall rule (1.5 s) happens BEFORE t0 and is not part of these numbers.
 */
import { buildCapJson, buildCapXml } from "./cap";
import type { CapJson } from "./cap";
import { aedSpeech, buildPayload, formatMeters, HEAT_STAGE_LABEL, nearestBy } from "./field";
import type { Aed, Crosswalk, EmergencyPayload, HeatInfo, Nearest } from "./field";
import { buildFhirBundle } from "./fhir";
import { buildIncident } from "./fieldIncident";
import type { Incident } from "./fieldIncident";
import type { SpeechResult } from "./speech";
import { FIELD_AED_MAX_M } from "../config";

export type AedStatus = "ok" | "none_nearby" | "unavailable";

export interface EmergencyStep {
  id: string;
  title: string;
  /** ms since t0, measured right after the step finished */
  atMs: number;
  lines: string[];
}

export interface EmergencyEvent {
  id: string;
  trigger: "button" | "camera";
  detectedAt: Date;
  steps: EmergencyStep[];
  speechText: string;
  speechResult: SpeechResult;
  aed: Nearest<Aed> | null;
  aedStatus: AedStatus;
  payload: EmergencyPayload;
  capXml: string;
  capJson: CapJson;
  incident: Incident;
  fhir: Record<string, unknown>;
  trackId: number | null;
  /** t0 -> alert artifacts built (ms) */
  buildMs: number;
  /** t0 -> next animation frame after the alert state was committed (ms); filled in after paint */
  paintMs: number | null;
  torsoAngleDeg: number | null;
  estSpeedMps: number | null;
  aiProb: number | null;
  ruleFiredAlready: boolean | null;
}

export interface EmergencyInput {
  trigger: "button" | "camera";
  t0: number;
  now: () => number;
  at: Date;
  crosswalk: Crosswalk;
  heat: HeatInfo;
  /** null while not loaded / failed */
  aeds: readonly Aed[] | null;
  trackId: number | null;
  aiProb?: number | null;
  ruleFiredAlready?: boolean | null;
  uuid: () => string;
  torsoAngleDeg: number | null;
  estSpeedMps: number | null;
  muted: boolean;
  speakFn: (text: string, muted: boolean) => SpeechResult;
}

const pad = (n: number): string => String(n).padStart(2, "0");

export function incidentId(at: Date, crosswalkId: string): string {
  const k = new Date(at.getTime() + 9 * 3_600_000);
  return `zp-${k.getUTCFullYear()}${pad(k.getUTCMonth() + 1)}${pad(k.getUTCDate())}${pad(k.getUTCHours())}${pad(k.getUTCMinutes())}${pad(k.getUTCSeconds())}-${crosswalkId}`;
}

export function runEmergency(i: EmergencyInput): EmergencyEvent {
  const steps: EmergencyStep[] = [];
  const mark = (id: string, title: string, lines: string[]): void => {
    steps.push({ id, title, atMs: i.now() - i.t0, lines });
  };
  const cw = i.crosswalk;

  mark("locate", "위치 확정", [`${cw.address}`, `위도 ${cw.lat.toFixed(6)} · 경도 ${cw.lon.toFixed(6)} (횡단보도 좌표 자료)`]);

  const heatLine =
    i.heat.source === "api" && i.heat.tmaxC !== null
      ? `${i.heat.date} ${i.heat.regionName ?? ""} 일 최고기온 ${i.heat.tmaxC.toFixed(1)}℃ → ${HEAT_STAGE_LABEL[i.heat.stage]}`
      : `직접 선택: ${HEAT_STAGE_LABEL[i.heat.stage]}`;
  mark("heat", "폭염 단계 확인", [heatLine]);

  let aed: Nearest<Aed> | null = null;
  let aedStatus: AedStatus = "unavailable";
  if (i.aeds !== null) {
    const n = nearestBy(i.aeds, cw.lat, cw.lon);
    if (n !== null && n.distanceM <= FIELD_AED_MAX_M) {
      aed = n;
      aedStatus = "ok";
    } else {
      aedStatus = "none_nearby";
    }
  }
  mark(
    "aed",
    "가장 가까운 AED 검색",
    aed === null
      ? [aedStatus === "unavailable" ? "AED 자료 없음 (자료를 불러오지 못함)" : `AED 자료 없음 (${formatMeters(FIELD_AED_MAX_M)} 이내 기록 없음)`]
      : [`${aed.item.org} ${aed.item.place}`.trim(), `${aed.item.address} · 거리 ${formatMeters(aed.distanceM)}`],
  );

  const speechText = aedSpeech(aed);
  const speechResult = i.speakFn(speechText, i.muted);
  mark("speech", "스피커 안내", [`"${speechText}"`, speechResult === "spoken" ? "음성 재생 요청함 (브라우저 음성 합성)" : speechResult === "muted" ? "음소거 상태: 재생하지 않음" : "이 브라우저는 음성 합성 미지원: 문구만 표시"]);

  const payload = buildPayload({
    trigger: i.trigger, at: i.at, crosswalk: cw, aed,
    heat: { date: i.heat.date, stage: i.heat.stage, stage_label: HEAT_STAGE_LABEL[i.heat.stage], tmax_c: i.heat.tmaxC, source: i.heat.source },
  });
  mark("payload", "119 전송 payload 생성", ["생성됨 · 전송 안 함(훈련)"]);

  const id = incidentId(i.at, cw.id);
  const capIn = {
    identifier: id,
    sent: i.at,
    event: "보행자 쓰러짐 의심(훈련)",
    headline: `[훈련] ${cw.sigungu} 횡단보도 보행자 쓰러짐 의심`,
    description: `${cw.address} 횡단보도에서 보행자 쓰러짐 의심 신호. 폭염 단계: ${HEAT_STAGE_LABEL[i.heat.stage]}${i.heat.tmaxC !== null ? ` (일 최고기온 ${i.heat.tmaxC.toFixed(1)}℃)` : ""}. ${aed === null ? "AED 자료 없음." : `가장 가까운 AED: ${aed.item.org} ${aed.item.place}, ${formatMeters(aed.distanceM)}.`}`,
    instruction: "119에 신고하고 가장 가까운 자동심장충격기를 가져오십시오. 이 메시지는 훈련(Exercise)이며 전송되지 않았습니다.",
    areaDesc: cw.address,
    lat: cw.lat,
    lon: cw.lon,
  };
  const capJson = buildCapJson(capIn);
  const capXml = buildCapXml(capIn);
  mark("cap", "CAP 1.2 경보 생성", ["status=Exercise · scope=Restricted", "생성됨 · 전송 안 함(훈련)"]);

  const incident = buildIncident({
    incidentId: id, at: i.at, crosswalk: cw,
    heat: { stage: i.heat.stage, tmaxC: i.heat.tmaxC, date: i.heat.date, source: i.heat.source },
    torsoAngleDeg: i.torsoAngleDeg, estSpeedMps: i.estSpeedMps, fallDetected: true, trackId: i.trackId, aiProb: i.aiProb ?? null,
  });
  mark("incident", "인시던트 JSON 생성", [`${id}`, "생성됨 · 전송 안 함(훈련)"]);

  const fhir = buildFhirBundle({
    incidentId: id, at: i.at, trackId: i.trackId, crosswalk: cw, stage: i.heat.stage, tmaxC: i.heat.tmaxC,
    torsoAngleDeg: i.torsoAngleDeg, estSpeedMps: i.estSpeedMps, uuid: i.uuid,
  });
  mark("fhir", "FHIR R4 Bundle 생성", ["Bundle(collection) · HTEST 표시", "생성됨 · 전송 안 함(훈련)"]);

  return {
    id, trigger: i.trigger, detectedAt: i.at, steps, speechText, speechResult, aed, aedStatus, payload, capXml, capJson, incident, fhir, trackId: i.trackId,
    buildMs: i.now() - i.t0, paintMs: null, torsoAngleDeg: i.torsoAngleDeg, estSpeedMps: i.estSpeedMps,
    aiProb: i.aiProb ?? null, ruleFiredAlready: i.ruleFiredAlready ?? null,
  };
}
