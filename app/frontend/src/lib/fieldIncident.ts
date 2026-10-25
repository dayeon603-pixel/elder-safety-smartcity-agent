/** Compact incident JSON shown next to the CAP message. Every field is computed from live inputs; nothing is sent. */
import type { Crosswalk, HeatStage } from "./field";
import { HEAT_STAGE_LABEL } from "./field";

export interface IncidentInput {
  incidentId: string;
  at: Date;
  crosswalk: Crosswalk;
  heat: { stage: HeatStage; tmaxC: number | null; date: string | null; source: "api" | "manual" };
  /** null when the event came from the button (no pose measured). */
  torsoAngleDeg: number | null;
  estSpeedMps: number | null;
  fallDetected: boolean;
  trackId?: number | null;
  aiProb?: number | null;
}

export interface Incident {
  incident_id: string;
  time: string;
  simulated: true;
  location: { lat: number; lon: number; crosswalk_id: string; address: string };
  environment: { date: string | null; tmax_c: number | null; heat_stage: string; source: "api" | "manual" };
  victim_status: { track_id: number | null; ai_fall_probability: number | null; torso_angle_deg: number | null; est_speed_mps: number | null; fall_detected: boolean };
  actions: { vms: string; cap_alert: string };
}

const round = (v: number | null, d: number): number | null => (v === null ? null : Number(v.toFixed(d)));

export function buildIncident(i: IncidentInput): Incident {
  return {
    incident_id: i.incidentId,
    time: i.at.toISOString(),
    simulated: true,
    location: { lat: i.crosswalk.lat, lon: i.crosswalk.lon, crosswalk_id: i.crosswalk.id, address: i.crosswalk.address },
    environment: { date: i.heat.date, tmax_c: i.heat.tmaxC, heat_stage: HEAT_STAGE_LABEL[i.heat.stage], source: i.heat.source },
    victim_status: { track_id: i.trackId ?? null, ai_fall_probability: round(i.aiProb ?? null, 3), torso_angle_deg: round(i.torsoAngleDeg, 1), est_speed_mps: round(i.estSpeedMps, 2), fall_detected: i.fallDetected },
    actions: { vms: "표시(시뮬레이션)", cap_alert: "생성됨·전송 안 함" },
  };
}

export type PipelineStageId = "camera" | "pose" | "judge" | "data" | "plan" | "scene" | "alert";
export const PIPELINE_STAGES: readonly { id: PipelineStageId; label: string; sub: string; badge: string }[] = [
  { id: "camera", label: "카메라(로컬)", sub: "브라우저 안", badge: "로컬 처리" },
  { id: "pose", label: "자세 추정", sub: "MediaPipe", badge: "AI" },
  { id: "judge", label: "보행·낙상 판단", sub: "자세 값 → 학습 모델", badge: "AI 모델(로지스틱 회귀)" },
  { id: "data", label: "기상·횡단보도", sub: "폭염 단계 · 녹색시간", badge: "공공데이터 연동" },
  { id: "plan", label: "행동 계획", sub: "서버 연결 시", badge: "로컬 LLM 에이전트" },
  { id: "scene", label: "장면 설명", sub: "선택 사항", badge: "VLM" },
  { id: "alert", label: "경보", sub: "전광판 · 음성 · 메시지", badge: "국제 표준 CAP·FHIR" },
];
