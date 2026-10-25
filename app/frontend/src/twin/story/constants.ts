/**
 * 시민 시점 constants. Values that appear on screen as data come from the API; the numbers here are the contract's
 * stated assumptions (walking speeds with their basis), visualisation tuning, and protocol keys.
 */
import type { Color } from "@deck.gl/core";

/** STORY_CONTRACT: 시연 인물 걸음 0.7 m/s (횡단보도 표준데이터의 필요 시간 계산과 같은 속도). */
export const WALK_MPS = 0.7;
export const WALK_BASIS = "보행 0.7 m/s (횡단보도 필요시간 기준 속도)";
/** STORY_CONTRACT: 행인(시연) 달리기 1.2 m/s. */
export const RUN_MPS = 1.2;
export const RUN_BASIS = "행인 1.2 m/s 가정";

export const PERSON_LABEL = "시연 인물";
export const PASSERBY_LABEL = "행인(시연)";
export const MODEL_CREDIT = "3D 모델 Cesium Man · CC-BY 4.0";
export const MODEL_URL = `${import.meta.env.BASE_URL}models/CesiumMan.glb`;
export const DONG_URL = `${import.meta.env.BASE_URL}story/gwanak_dong.json`;
export const TRAINING = "훈련 · 발송 안 함";
export const BUTTON_DEMO_LABEL = "버튼 시연";
export const CAMERA_LABEL = "카메라 감지";

/** Population layer. */
export const POP_PENDING_TEXT = "생활인구 자료 준비 중";
export const POP_LEGEND_SOURCE = "인원 분포: 서울 생활인구 실측 · 이동 경로: 시각화";
export const PEOPLE_PER_DOT = 100;
export const MAX_DOTS = 4500;
export const SAMPLE_SEED = 20251024;
export const TRIP_LEN_MIN_M = 220;
export const TRIP_LEN_MAX_M = 600;
/** Visual speed of dots in the control view (metres per loop-second). A visualisation rate, not a measured walking speed. */
export const TRIP_VIS_MPS = 90;
export const TRIP_LOOP_S = 9;
export const TRIP_COLOR: Color = [120, 205, 255, 255];
export const TRIP_COLOR_65: Color = [255, 158, 64, 255];
/** Control view (subtle) and street view (people walk at ~1.3 m/s, short trails). */
export const TRIP_VIEW = {
  overview: { rate: 1, trail: 3, widthM: 9, opacity: 0.8 },
  street: { rate: 1.3 / TRIP_VIS_MPS, trail: 0.4, widthM: 0.9, opacity: 0.8 },
} as const;
export const MAX_HOUR = 23;
export const POP_DEBOUNCE_MS = 220;

/** Story pacing: the walk is time-lapsed so step ③ stays ~25-30 s; the factor is derived and shown. */
export const TARGET_WALK_SEC = 11;
export const HOME_MIN_M = 60;
export const HOME_MAX_M = 80;
export const HOME_SNAP_MAX_M = 60;
/** the person stops this far before the stop post, beside it */
export const STAND_OFF_M = 1.8;
export const ENTER_MS = 2200;
export const EXIT_MS = 1800;
export const ARRIVE_DWELL_MS = 4500;
export const FALL_ANIM_MS = 1100;
/** close-up on the fallen person before the rescue run starts */
export const FALL_HOLD_MS = 2400;
export const MOVE_MARGIN_M = 0.5;

/** Camera. */
export const CAM = {
  /** chase camera, distances in metres from the target: ~50 m behind and ~45 m up, looking along the street; the target leads the person so the road ahead and the stop are in frame */
  street: { zoom: 20.3, distM: 68, pitch: 48, targetH: 1.6, lookAheadM: 16, leadM: 20, headingTau: 0.6, followTau: 0.2, zoomTau: 0.7 },
  arrive: { zoom: 20.5, distM: 48, pitch: 50, targetH: 3.8 },
  fall: { zoom: 20.5, distM: 58, pitch: 48, lookAlongM: 30 },
  rescue: { zoomMax: 19.6, zoomMin: 16.8, pitch: 48, fitM: 46 },
  /** occlusion search: pitch reductions (deg) and heading offsets (deg) tried in order */
  pitchSteps: [0, -12, -24, -36],
  headingOffsets: [0, 35, -35, 70, -70, 180],
  evalMs: 220,
  edgeRadiusM: 170,
} as const;

/** the figures and posts are drawn larger than life so they read at chase-camera distance; the HUD says so */
export const DISPLAY_SCALE = 2.2;
export const SCALE_NOTE = `인물·표지 확대 표시 ×${DISPLAY_SCALE}`;
export const PERSON = { sizeM: 1.7, modelHeightM: 1.7, tint: [255, 255, 255, 255] as Color, walkAnim: 1.4 } as const;
export const PASSERBY_TINT: Color = [255, 190, 90, 255];
export const ROUTE_COLOR: Color = [61, 220, 151, 255];
export const PULSE_COLOR: Color = [255, 60, 60, 255];
export const PULSE_RINGS = 3;
export const PULSE_MAX_M = 6;
export const PULSE_PERIOD_MS = 1800;
export const CLUSTER_RADIUS_M = 60;
export const TERMINAL_POLL_MS = 700;
export const TERMINAL_POLL_MAX_MS = 70_000;
