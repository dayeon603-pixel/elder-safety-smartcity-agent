/** Wire and view types for /twin. Wire shapes follow TWIN_CONTRACT.md; fields the contract leaves open are optional. */

export type DayType = "weekday" | "sat" | "sun" | "holiday";
export type LonLat = readonly [number, number];

/** GET /twin/{sgg}/scene. Coordinates are metres east/north of `center` (deck.gl METER_OFFSETS); rings are closed. */
export interface SceneWire {
  sgg_code?: string;
  center: LonLat;
  /** [minLon, minLat, maxLon, maxLat] */
  bbox: readonly [number, number, number, number];
  coordinate_system?: string;
  buildings: {
    count: number;
    positions: number[];
    start_indices: number[];
    heights: number[];
    height_source?: unknown;
    /** per polygon use code (0 단독·다가구 .. 5 미상), names in `use_names` */
    use?: number[];
    use_names?: Record<string, string>;
    level_height_m?: number;
    /** Share (%) of heights that come from a recorded floor count or height; the rest are imputed by the backend. */
    measured_pct?: number;
    height_source_counts?: Record<string, number>;
    /** Inner rings: same flat layout, parent[i] = polygon index. */
    holes?: { count: number; positions: number[]; start_indices: number[]; parent: number[] };
  };
  roads?: { count: number; positions: number[]; start_indices: number[] };
  boundary: { rings: number; positions: number[]; start_indices: number[] };
  sources?: { file: string; sha256?: string; display_source?: string; sources_json?: { dataset?: string; license?: string; data_reference_date?: string } }[];
  fetched_at?: string;
}

/** Typed arrays ready for deck.gl binary attributes (built in the worker). */
export interface SceneBuffers {
  buildingCount: number;
  vertexCount: number;
  triangleIndexCount: number;
  positions: Float32Array;
  startIndices: Uint32Array;
  indices: Uint32Array;
  vertexValid: Uint16Array;
  elevations: Float32Array;
  colors: Uint8Array;
  roadCount: number;
  roadPositions: Float32Array;
  boundaryPositions: Float32Array;
  roadStartIndices: Uint32Array;
  center: LonLat;
  bbox: readonly [number, number, number, number];
  sources: string[];
  heightSource: string | null;
  measuredPct: number | null;
  /** per building use code (null when the scene has none) */
  buildingUse: Uint8Array | null;
  useNames: Record<string, string> | null;
  /** dataset + licence + reference date as the API states them */
  credit: string | null;
  fetchedAt: string | null;
  parseMs: number;
}

export interface AedPoint { id: string; lat: number; lon: number; name: string; open: boolean | null; openAfter: boolean | null; recommended24h: boolean; hours: string | null }
export interface ShelterPoint { id: string; lat: number; lon: number; name: string; open: boolean | null }
export interface CrosswalkPoint { id: string; lat: number; lon: number; address: string; greenS: number | null; needS: number | null; short: boolean }
export interface SilverSite { lat: number; lon: number; name: string | null }
export interface AccidentPoint { lat: number; lon: number; inCurrent: boolean; inOurs: boolean; casualties: number | null }
export interface SilverEval { year: number | null; n: number; coveredCurrent: number; coveredOurs: number; source: string | null }
export interface StopPoint { id: string; lat: number; lon: number; name: string; recommended: boolean }
export interface AedCandidate { lat: number; lon: number; name: string; rank: number }

export interface HeatSummary {
  apparentMaxC: number | null;
  tmaxC: number | null;
  b1Alert: boolean | null;
  oursAlert: boolean | null;
  rank: number | null;
  nCells: number | null;
  expectedCases: number | null;
  rankText: string | null;
  expectedCasesText: string | null;
}

export interface WeatherNow { temp: number | null; feels: number | null; rainMm: number | null }

/** View model of GET /twin/{sgg}/state (or the dev composition of existing endpoints). */
export interface TwinState {
  date: string;
  hour: number;
  daytype: DayType;
  aed: AedPoint[];
  aedCandidates: AedCandidate[];
  shelters: ShelterPoint[];
  crosswalks: CrosswalkPoint[];
  silverCurrent: SilverSite[];
  silverOurs: SilverSite[];
  silverRadiusM: number;
  accidents: AccidentPoint[];
  silverEval: SilverEval | null;
  /** Seoul-wide zone counts (current vs agent), when the API gives them */
  seoulZones: { current: number; ours: number } | null;
  aedOpenPctAfter: number | null;
  stops: StopPoint[];
  heat: HeatSummary;
  weather: WeatherNow | null;
  aedOpenPct: number | null;
  aedOpenCount: number | null;
  aedTotal: number | null;
  sources: string[];
}

export interface RainDay { date: string; hour: number | null; mm: number | null }

export interface TwinRoute {
  aed: { id: string; name: string; lat: number; lon: number };
  path: LonLat[];
  meters: number;
  seconds: number;
}

export interface EvidenceRow { label: string; value: string; source: string | null }
export interface ActionCard {
  id: string;
  kind: string;
  title: string;
  summary: string;
  evidence: EvidenceRow[];
  /** action_id for POST /feedback (feedback_ref.action_id) */
  feedbackId: string;
  capUrl: string | null;
  lat: number | null;
  lon: number | null;
  /** "149/157" style counter from the API, if any */
  chars: string | null;
  /** 전달 대상 as the API states it (null until the API provides it) */
  destination: string | null;
  /** receiving organisation under `destination` */
  destinationOrg: string | null;
  /** GET -> structured 공문 (수신, 제목, 본문, 근거, 생성시각) */
  documentUrl: string | null;
  /** one-line approval flow (flow_note) */
  flowNote: string | null;
  /** Korean one-line evidence from the API (evidence_text), when provided */
  evidenceText: string | null;
  /** Existing WAV for 마을방송 / voice drafts */
  audioUrl: string | null;
  /** Several drafts with their own limit (e.g. 90자 현행 / 157자 시범운영) */
  drafts: { label: string | null; limit: number; length: number; text: string }[];
}

export interface ActionsView { cards: ActionCard[]; night: boolean; notes: string[]; disclaimer: string | null; date: string; hour: number; generatedAt: string }

/** 공문 as returned by the API's document_url. */
export interface Letter { to: string | null; org: string | null; title: string | null; paragraphs: string[]; evidence: { item: string; value: string }[]; createdAt: string | null; footer: string | null; never: string | null }

/** A 공문 together with the card it was fetched for: a preview only ever uses a matching pair. */
export interface CardLetter { cardId: string; letter: Letter }

export type Mode = "asis" | "ours";
/** Workday steps (①브리핑 ②폭염 대응 ③현장 전달 ④사고 대응) plus two secondary views. */
export type ScenarioId = "s1" | "s2" | "s3" | "silver" | "s4" | "night" | "rain";
export type StepNo = 1 | 2 | 3 | 4 | 5 | 6;

/** One headline effect from GET /twin/effects (strings exactly as the API gives them). */
export interface EffectChip { ours: string; baseline: string | null; scope: string | null; source: string | null }
