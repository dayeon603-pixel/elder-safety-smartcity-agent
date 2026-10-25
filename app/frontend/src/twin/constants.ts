/**
 * /twin presentation constants. Everything shown as a data value comes from the API; these are selection keys,
 * visual tuning and protocol limits only.
 */
import type { Color } from "@deck.gl/core";

/** 관악구 (current 5-digit 시군구 code). */
export const TWIN_SGG_CODE = "11620";
/** Demo day agreed in TWIN_CONTRACT.md. */
export const TWIN_DEMO_DATE = "2025-07-22";
export const TWIN_FIRST_HOUR = 6;
export const TWIN_LAST_HOUR = 24;
/** Scenario 2 hour ("밤 23시"). */
export const TWIN_NIGHT_HOUR = 23;
/** Real seconds per simulated hour while the day plays. */
export const TWIN_PLAY_SEC_PER_HOUR = 1.6;
export const TWIN_DEMO_TOKEN_HEADER = "X-Demo-Token";

export const API_STATE_DEBOUNCE_MS = 180;
export const SILVER_RADIUS_FALLBACK_M = 300;

/** Camera presets (pitch/bearing); zoom comes from fitting the bbox, so no per-district number is hardcoded. */
export const CAMERA = {
  overview: { pitch: 50, bearing: -18, zoomOffset: 0.8 },
  night: { pitch: 52, bearing: 10, zoomOffset: 2.25 },
  rain: { pitch: 60, bearing: -32, zoomOffset: 1.1 },
  fall: { pitch: 62, bearing: -12, zoom: 15.9 },
  flyMs: 2200,
  fitPadding: 40,
} as const;

/** Rendering. */
export const RENDER = {
  dprCap: 1.5,
  elevationScale: 1,
  material: { ambient: 0.55, diffuse: 0.7, shininess: 24, specularColor: [70, 80, 96] as [number, number, number] },
  buildingLow: [58, 74, 96] as [number, number, number],
  buildingHigh: [150, 176, 204] as [number, number, number],
  /** Heights are normalised against this percentile so a few towers do not wash the gradient out. */
  heightPercentile: 0.99,
  shadows: false,
} as const;

export const PALETTE: Readonly<Record<string, Color>> = {
  ground: [14, 19, 26, 255],
  groundHeat: [96, 44, 36, 255],
  groundCalm: [20, 30, 40, 255],
  road: [58, 74, 92, 190],
  boundary: [120, 150, 180, 255],
  aedOpen: [61, 220, 151, 255],
  aedClosed: [96, 106, 118, 255],
  aedNew: [76, 194, 255, 255],
  shelter: [255, 200, 87, 255],
  silverCurrent: [140, 150, 162, 70],
  silverOurs: [76, 160, 255, 64],
  crosswalkShort: [255, 82, 82, 255],
  crosswalkOk: [150, 160, 172, 255],
  stop: [255, 176, 64, 255],
  accidentOut: [255, 70, 70, 255],
  accidentIn: [76, 160, 255, 255],
  fallPulse: [255, 60, 60, 255],
  route: [61, 220, 151, 255],
};

/** Disaster-text limit comes from the API (`cbs.limit`); this is only a UI fallback label when the API omits it. */
export const TRAINING_LABEL = "훈련 · 발송 안 함";
export const EDGE_TAGLINE = "기존 CCTV·안내단말에 소프트웨어로 탑재";
export const FACE_MASK_LABEL = "얼굴 가림 · 영상은 기기 밖으로 안 나감";
/** Walking speed the contract uses for /twin/route seconds (only for the straight-line fallback). */
export const ROUTE_WALK_MPS = 0.8;

export const FILE_BADGE = "공개 데이터 영상 · 실시간 계산";
export const URFALL_CREDIT = "UR Fall Detection Dataset (Kwolek & Kepski, 제슈프대) · CC BY-NC-SA 4.0 · 비영리 시연";
export const URFALL_FILES = [
  { id: "fall", label: "공개 낙상 영상", url: `${import.meta.env.BASE_URL}media/urfall/fall-01-cam0.mp4` },
  { id: "adl", label: "공개 일상 영상", url: `${import.meta.env.BASE_URL}media/urfall/adl-01-cam0.mp4` },
] as const;
export const HTEST_NOTE = "훈련 데이터 표시: HL7 표준 보안 태그 HTEST (실제 환자 기록 아님)";
export const CAP_NOTE = "훈련 상태 표시: CAP 표준 status=Exercise (실제 경보 아님)";
/** Strings that must never reach the screen (internal experiment / run identifiers). */
export const FORBIDDEN_SOURCE_RE = /experiments\/|promoted|_k\d+|fold/i;

/** 공무원의 하루: hour of each step on the demo day (TWIN_DEMO_DATE). */
export const STEP_HOURS = { brief: 9, heat: 17, deliver: 17.5, silver: 17.5, incident: 17 + 40 / 60, night: 23 } as const;
/** ★ 한눈에 표의 행 순서 그대로: 언제 · 어디에 · 무엇을 · 밤. `effectKeys` = names the effects API may use for the step. */
export const STEPS: readonly { no: 1 | 2 | 3 | 4 | 5 | 6; id: "s1" | "s2" | "s3" | "silver" | "s4" | "night"; label: string; hint: string; effectKeys: readonly string[] }[] = [
  { no: 1, id: "s1", label: "브리핑", hint: "오전 브리핑", effectKeys: [] },
  { no: 2, id: "s2", label: "폭염 대응", hint: "특보 전일 대응 검토", effectKeys: ["heat", "when", "폭염", "언제"] },
  { no: 3, id: "s3", label: "현장 전달", hint: "안내단말 전달", effectKeys: [] },
  { no: 4, id: "silver", label: "실버존 재배치", hint: "현행 기준 → 에이전트", effectKeys: ["silver", "where", "실버존", "어디에"] },
  { no: 5, id: "s4", label: "낙상 대응", hint: "낙상 의심 사건", effectKeys: ["fall", "what", "낙상", "무엇을"] },
  { no: 6, id: "night", label: "야간 AED", hint: "야간 AED 공백", effectKeys: ["aed", "night", "밤", "AED"] },
];
/** Step 3's chip is text only (no figures). */
export const DELIVER_CHIP_TEXT = "휴대폰 없이도 · 정류장 단말 · 마을방송";
/** Delay before the silver step flips [현행 기준] → [에이전트], so the viewer sees the starting state first. */
export const SILVER_FLIP_DELAY_MS = 2200;
export const SHELTER_NOTE = "관악구 무더위쉼터 공개자료 없음 → 구청 보유 목록 연계 필요(파일럿 필수 데이터)";
export const AED_NOTE = "공공데이터 운영시간 기준 · 현장 확인 필요";
export const STAGES = ["초안", "주무관 채택", "과장 결재(시연)"] as const;
export const HOLD_REASON_MAX = 200;

/** Quick reason for an AED 24h-opening request the installing organisation declines. */
/** Adjacent 구 whose crosswalk data has short green times (관악구 has none): used for the 보행신호 연장 협의 card. */
export const SIGNAL_NEIGHBOR_CODE = "11590";
export const REFUSE_REASON = "설치기관 거절";
export const ALL_REFUSED_TEXT = "야외 보관함 신설 검토 · 그 사이 열린 AED 경로 안내";
