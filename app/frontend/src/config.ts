/**
 * Topic-level knobs. Change these (and DomainPage) when the topic is announced;
 * nothing else in the app should need to know the domain.
 */
/**
 * Product framing switch (VITE_ZP_FRAMING). "combined" (default): 생활안전, 보행 leads, 더위·응급 stay in the main nav.
 * "walk": 보행안전 only in the main nav; 폭염 and AED are collapsed extension modules (footer + collapsed sections).
 */
export type ZpFraming = "walk" | "combined";
export const ZP_FRAMING: ZpFraming = import.meta.env.VITE_ZP_FRAMING === "walk" ? "walk" : "combined";
export const IS_WALK_FRAMING: boolean = ZP_FRAMING === "walk";

const FRAMING_TEXT: Readonly<Record<ZpFraming, { fullName: string; tagline: string; subtitle: string; problems: readonly string[] }>> = {
  walk: {
    fullName: "고령자 보행안전 에이전트",
    tagline: "고령자가 건너는 길을 AI가 지킵니다.",
    subtitle: "실버존을 사고가 나는 곳에 다시 긋고, 신호가 모자란 횡단보도를 찾고, 쓰러진 고령자에게 가장 빠른 도움을 연결합니다",
    problems: ["집 앞 횡단보도", "짧은 녹색 신호", "사고가 나는데 비어 있는 실버존"],
  },
  combined: {
    fullName: "고령자 안전 스마트도시 에이전트",
    tagline: "도시의 길과 더위와 응급을 한 지도에 올리고, AI가 고령자 곁의 위험을 먼저 찾습니다.",
    subtitle: "도시 운영 지도, 현장 에이전트, 에이전트 분석, 시민 화면 네 부분이 같은 공공데이터 위에서 움직입니다",
    problems: ["집 앞 횡단보도", "폭염 속 고령자", "밤에 잠긴 AED"],
  },
};
const FT = FRAMING_TEXT[ZP_FRAMING];
export const SERVICE_NAME: string = import.meta.env.VITE_SERVICE_NAME ?? "스마트도시 에이전트";
export const SERVICE_TAGLINE: string = import.meta.env.VITE_SERVICE_TAGLINE ?? FT.tagline;
/** Full product name shown as the page title of the overview. */
export const SERVICE_FULL_NAME = FT.fullName;

/** Headline on the overview page; rewrite once the topic is known. */
export const HOME_HEADLINE: string = import.meta.env.VITE_HOME_HEADLINE ?? "에이전트: 공식 기준이 놓친 사람을 먼저 찾습니다";
export const SERVICE_SUBTITLE = FT.subtitle;

/** Data credits shown in the footer. Required by the contest rules and by each licence (Meteostat is CC BY-NC 4.0). */
export const FOOTER_DATA_CREDITS: readonly { label: string; href: string }[] = [
  { label: "질병관리청 온열질환 신고(2011–2025)", href: "https://www.data.go.kr/data/15149889/fileData.do" },
  { label: "Meteostat 일별 기온 (CC BY-NC 4.0)", href: "https://meteostat.net" },
  { label: "행정안전부 주민등록 인구(2026-09)", href: "https://www.data.go.kr/data/15097972/fileData.do" },
  { label: "vuski/admdongkor 행정동 경계", href: "https://github.com/vuski/admdongkor" },
  { label: "southkorea-maps 시군구 경계 (2013, 배경용)", href: "https://github.com/southkorea/southkorea-maps" },
  { label: "충남·부산 무더위쉼터", href: "https://www.data.go.kr/data/15118638/fileData.do" },
  { label: "도로교통공단 보행노인 사고다발지(TAAS)", href: "https://www.data.go.kr/data/15029185/standard.do" },
  { label: "전국 횡단보도 표준데이터(보행 녹색시간)", href: "https://www.data.go.kr/data/15028201/standard.do" },
  { label: "전국 어린이·노인·장애인 보호구역 표준데이터", href: "https://www.data.go.kr/data/15012891/standard.do" },
  { label: "소방청 구급활동정보 (서울 2024)", href: "https://www.data.go.kr" },
  { label: "국립중앙의료원 전국 AED 정보", href: "https://www.data.go.kr" },
];

/** Extra credits shown in the footer besides datasets read from /api/datasets (open-source, fonts, ...). */
export const FOOTER_CREDITS: readonly { label: string; href: string }[] = [
  { label: "Pretendard (SIL OFL 1.1)", href: "https://github.com/orioncactus/pretendard" },
  { label: "Recharts (MIT)", href: "https://recharts.org" },
];

/** Metric highlighted in StatTile / chart ordering when present in a run. */
export const PRIMARY_METRIC = "auroc";

/** Heat-alert demo knobs. Years and variants come from /heat/meta; these are only the UI defaults. */
export const HEAT_DEFAULT_VARIANT = "F";
export const HEAT_DAY_LIST_MAX_HEIGHT_REM = 24;
export const HEAT_SHELTER_NOTE = "쉼터는 예측 평가에 쓰지 않은 참고 정보입니다. 좌표 자료가 있는 지역만 보여줍니다.";

/** Response-plan step: team slider bounds and how many shelter extensions to ask the API for. */
export const PLAN_TEAMS_MIN = 1;
export const PLAN_TEAMS_MAX = 30;
export const PLAN_TEAMS_DEFAULT = 10;
export const PLAN_SHELTERS = 5;
export const PLAN_DEBOUNCE_MS = 250;

/** Citizen view: "watch" starts after the alert cut-off K and ends at this multiple of K. */
export const CITIZEN_WATCH_MULTIPLE = 2;

/** Template/demo datasets that must not appear in the product UI (they remain in the DB). */
export const HIDDEN_DATASET_NAMES: readonly string[] = ["wdbc-sklearn"];

export const SESSION_STORAGE_KEY = "kcf.session";
export const TOAST_MS = 5000;
export const TOAST_ERROR_MS = 8000;
export const RUNS_FETCH_LIMIT = 200;
export const TABLE_PAGE_SIZE = 10;
export const HASH_SHORT = 12;
export const MIN_PASSWORD_LEN = 12;
export const MAX_PASSWORD_LEN = 128;

/** Field-integration simulation (/field). All numbers here are simulation assumptions, not measurements. */
export const FIELD_BANNER_TEXT = "시뮬레이션입니다 · 실측 성능이 아닙니다 · 입력값은 실제 공공데이터";
export const FIELD_CROSSWALKS_URL = `${import.meta.env.BASE_URL}field_crosswalks.json`;
export const FIELD_AEDS_URL = `${import.meta.env.BASE_URL}field_aeds.json`;
/** Mediapipe wasm files and the pose model are served from /public so the camera path works offline. */
export const FIELD_WASM_BASE = `${import.meta.env.BASE_URL}mediapipe-wasm`;
export const FIELD_POSE_MODEL_URL = `${import.meta.env.BASE_URL}models/pose_landmarker_lite.task`;
/** Walking speed in m/s. Default 0.7 is the elderly walking-speed value from the research literature; the slider is an assumption knob. */
export const FIELD_SPEED_DEFAULT = 0.7;
/** A crosswalk longer than this is a data error (standard-data typo), never a "needs +N s" recommendation. */
export const CROSSWALK_MAX_LENGTH_M = 100;
export const FIELD_SPEED_MIN = 0.5;
export const FIELD_SPEED_MAX = 1.2;
export const FIELD_SPEED_STEP = 0.05;
export const FIELD_PLAYBACK_RATES: readonly number[] = [1, 2, 4];
export const FIELD_PLAYBACK_DEFAULT = 2;
/** Daily max temperature (deg C) at which the demo treats a day as 폭염주의보 / 폭염경보 level. KMA warnings use feels-like temperature; this is a proxy. */
export const FIELD_HEAT_ADVISORY_C = 33;
export const FIELD_HEAT_WARNING_C = 35;
/** Nearest 시군구 centroid farther than this is probably the wrong district: ask the user to check or choose manually. */
export const FIELD_REGION_MAX_KM = 30;
/** An AED farther than this from the crosswalk is reported as "no AED data" (the bundled file keeps AEDs within 3 km). */
export const FIELD_AED_MAX_M = 3000;
export const FIELD_HEAT_VARIANT = "F";
/** Rule baseline (torso angle above FALL_HORIZONTAL_DEG for FALL_HOLD_MS) and the display cut-off for 'upright again'. */
export const FALL_HOLD_MS = 1500;
export const FALL_HORIZONTAL_DEG = 60;
export const FALL_UPRIGHT_DEG = 35;
/** ~30 fps, the frame rate the fall model was trained on. */
export const FALL_DETECT_INTERVAL_MS = 33;
export const FALL_UI_INTERVAL_MS = 250;

/** Gait estimate from the camera. Needs calibration (real-world width of the camera field at the walker's distance). */
export const GAIT_WINDOW_MS = 1000;
export const GAIT_MIN_SPAN_MS = 500;
export const GAIT_FIELD_WIDTH_DEFAULT_M = 3;
export const GAIT_FIELD_WIDTH_MIN_M = 0.5;
export const GAIT_FIELD_WIDTH_MAX_M = 20;
/** Estimates above this are treated as tracking jitter, not walking (a person does not sprint across a crosswalk). */
export const GAIT_MAX_PLAUSIBLE_MPS = 3;
/** Below this the person is treated as standing still: no speed verdict. */
export const GAIT_STILL_MPS = 0.1;

/** CAP 1.2 (OASIS) drill message. */
export const CAP_SENDER = "zero-point.demo";
export const CAP_RADIUS_KM = 0.1;
export const CAP_LANGUAGE = "ko-KR";
export const CAP_STANDARD_NOTE = "표준: OASIS CAP 1.2";
export const CAP_KST_OFFSET_MIN = 540;

/** 에이전트 modules (briefing / silver / aed). */
export const ZP_DEFAULT_SIDO = "서울특별시";
export const ZP_DEFAULT_SIGUNGU = "관악구";
export const ZP_DEFAULT_DATE = "2025-07-22";
export const ZP_TEAMS_MIN = PLAN_TEAMS_MIN;
export const ZP_TEAMS_MAX = PLAN_TEAMS_MAX;
export const ZP_TEAMS_DEFAULT = PLAN_TEAMS_DEFAULT;
/** Silver and AED analyses are Seoul-only; the map and ranking say so plainly. */
export const ZP_SEOUL_NOTE = "서울 2024 · 시군구×시간 단위 분석";
export const ZP_SILVER_CITY = "seoul";
export const ZP_AED_NIGHT_START = 22;
export const ZP_AED_NIGHT_END = 6;
export const ZP_AED_GU_TOP = 10;
/** 행동 채널: how many bus-stop terminals the briefing lists, and the draft label shown on every channel. */
export const ZP_BIT_TOP = 3;
export const ZP_DRAFT_LABEL = "권고 초안 · 발송 안 함";
/** Published problem-strip facts that come from official statistics, not from this API (질병관리청, 도로교통공단). */
export const ZP_FACT_HEAT_DEATH_60 = 62;
export const ZP_FACT_WALK_DEATH_65 = 67;
export const ZP_MOCK_NOTICE = "모의 응답으로 표시 중입니다. 실제 API 결과가 아닙니다.";

/** Camera pipeline. */
export const FIELD_MAX_PERSONS = 4;
export const TRACK_IOU_MIN = 0.15;
export const TRACK_CENTROID_MAX = 0.3;
export const TRACK_LOST_MS = 1500;
export const POSE_MIN_VISIBILITY = 0.3;
export const PERF_WINDOW = 100;
export const FPS_WINDOW_MS = 1000;
export const FACE_MARGIN = 0.8;
export const FACE_MIN_HALF = 0.04;
export const FACE_BLOCKS_ACROSS = 6;
export const WHOLE_FRAME_BLOCK_PX = 24;
export const FACE_LANDMARK_COUNT = 11;
/** Mirror of the code-path audit in cameraPrivacy.test.ts: camera code contains no storage or upload API, so both counters are structurally zero. */
export const PRIVACY_STORED_FRAMES = 0;
export const PRIVACY_SENT_VIDEO = 0;
export const ZP_SILVER_RADIUS_M = 300;
export const ZP_SILVER_TEST_YEARS: readonly number[] = [2022, 2023, 2024];

/** Contest topic sentence, shown verbatim on the home page. */
export const TOPIC_SENTENCE = "AI와 디지털 기술로 해결하는 우리 주변의 생활문제";
export const DAILY_PROBLEMS: readonly string[] = FT.problems;
export const BUILD_COLUMNS: readonly { title: string; items: readonly string[] }[] = [
  { title: "생활문제", items: DAILY_PROBLEMS },
  { title: "디지털 기술", items: ["공공데이터 API 연동: 기상청 · 소방청 · 국립중앙의료원", "국제 표준 CAP 1.2 · FHIR", "웹 · 모바일", "보안: Argon2id, HMAC 감사로그"] },
  { title: "AI", items: ["실버존 위치 최적화(최대 커버리지)", "낙상 감지 모델 · 자세 추정", "로컬 LLM 행동 에이전트 · VLM", IS_WALK_FRAMING ? "확장: 폭염 위험 예측 · AED 밤 공백" : "폭염 위험 예측 · AED 밤 공백 예측"] },
];

export const FALL_MODEL_URL = `${import.meta.env.BASE_URL}models/fall_model.json`;
export const FALL_CV_URL = `${import.meta.env.BASE_URL}models/fall_cv_summary.json`;
/** A fired detector chip stays lit until the person is upright again or this long has passed. */
export const FALL_CHIP_HOLD_MS = 15_000;

/** Extension modules, reachable from the footer and from the collapsed sections (not in the main nav). */
export const EXTENSION_LINKS: readonly { to: string; label: string }[] = [
  { to: "/demo", label: "폭염" },
  { to: "/aed", label: "AED 밤 공백" },
];

/** 도시 운영 지도 (/app). Static shards built by scripts/gen_ops_data.py from real public data. */
export const OPS_DATA_BASE = `${import.meta.env.BASE_URL}ops`;
export const OPS_INDEX_URL = `${OPS_DATA_BASE}/index.json`;
/** Silver-zone records and hotspots of a 시군구 farther than this from its centroid are dropped when the API gives no sgg_code (mock only). */
export const OPS_FALLBACK_RADIUS_KM = 6;
/** How many rows the map labels and the queue show per list. */
export const OPS_LABEL_TOP = 5;
export const OPS_LIST_TOP = 10;
export const OPS_CAP_FEED_PATH = "/cap/feed.atom";
/** 전달 경로 endpoints (backend routes under /api). null = route not live: the button stays disabled ("준비 중"). */
export const OPS_CHANNEL_ENDPOINTS: Readonly<{ gongmun: string | null; webhook: string | null; audio: string | null }> = {
  gongmun: "/documents",
  webhook: null,
  audio: "/cap/alerts",
};
export const OPS_DRAFT_LABEL = "초안 · 발송 안 함";
export const OPS_CAP_ID_PREFIX = "ops";

/** At or above this 체감 최고(℃) the heat model extrapolates beyond its training range: expected cases are a lower bound. */
export const HEAT_LOWER_BOUND_APPARENT_C = 36;
