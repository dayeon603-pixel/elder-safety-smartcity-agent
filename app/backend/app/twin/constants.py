"""Numbers, paths and labels the digital-twin endpoints agree on (see app/TWIN_CONTRACT.md)."""

from __future__ import annotations

from typing import Final

DEFAULT_SGG: Final[str] = "11620"  # 관악구
SEOUL: Final[str] = "서울특별시"
SGG_CODE_RE: Final[str] = r"^\d{5}$"
DATE_RE: Final[str] = r"^\d{4}-\d{2}-\d{2}$"
HOUR_MIN, HOUR_MAX = 0, 24  # 24 = midnight at the end of the day (slider end); mapped to hour 0 of D+1
TIMELINE_START_HOUR: Final[int] = 6
TIMELINE_END_HOUR: Final[int] = 24
DAYTYPES: Final[tuple[str, ...]] = ("weekday", "sat", "sun", "holiday")

# --- buildings -------------------------------------------------------------------------------
LEVEL_HEIGHT_M: Final[float] = 3.2  # one storey
MIN_LEVELS: Final[float] = 1.0
MAX_LEVELS: Final[float] = 130.0  # taller than anything in Seoul (롯데월드타워 123층); guards bad tags
HEIGHT_SOURCE_LABELS: Final[dict[int, str]] = {
    0: "floors: 기록된 층수 × 3.2 m (국토부 지상층수 / num_floors / OSM building:levels)",
    1: "source_height: 기록된 높이(m) 그대로",
    2: "imputed_area: 층수·높이 없음 → 이 구에서 층수가 기록된 건물의 바닥면적 10분위별 층수 중앙값 × 3.2 m",
    3: "imputed_global: 층수 기록 건물이 너무 적어 전체 중앙값 × 3.2 m",
}
HEIGHT_SOURCE_NAMES: Final[dict[int, str]] = {0: "floors", 1: "source_height", 2: "imputed_area", 3: "imputed_global"}
AREA_BANDS: Final[int] = 10  # deciles of footprint area among buildings with recorded floors
MIN_KNOWN_FOR_BANDS: Final[int] = 30  # fewer recorded floors than this -> one global median
SCENE_VERSION: Final[int] = 5  # bump when the npz / json layout changes
# Building footprints: the first existing file wins (official 국토부 footprints when fetched, else OSM).
BUILDINGS_PARQUET: Final[str] = "extra/osm/seoul_buildings.parquet"
BUILDINGS_OFFICIAL_GLOB: Final[str] = "extra/buildings/*.parquet"
# 국토부 GIS건물통합정보 (V-World) arrives as a zipped shapefile in a sub-directory; read with geopandas (geo group) at build time.
BUILDINGS_SHP_GLOBS: Final[tuple[str, ...]] = ("extra/buildings/*/*.zip", "extra/buildings/*/*.shp", "extra/buildings/*.zip")
SHP_LEVELS_COLUMNS: Final[tuple[str, ...]] = ("GRO_FLO_CO", "GRND_FLR_CO", "GRND_FLR", "지상층수", "GRO_FLO", "FLR_CO")
SHP_HEIGHT_COLUMNS: Final[tuple[str, ...]] = ("HEIGHT", "BLD_HGT", "높이")
SHP_TAG_COLUMNS: Final[tuple[str, ...]] = ("BDTYP_CD", "MAIN_PURPS", "USABILITY", "주용도")
BUILDINGS_SOURCE_ENV: Final[str] = "KCF_TWIN_BUILDINGS"  # explicit parquet path (relative to the data dir or absolute)
BUILDING_GEOMETRY_COLUMNS: Final[tuple[str, ...]] = ("geometry", "geom", "wkb")
BUILDING_LEVELS_COLUMNS: Final[tuple[str, ...]] = ("num_floors", "building:levels", "levels", "GRND_FLR_CO", "grnd_flr_co", "floors", "층수", "지상층수")
BUILDING_HEIGHT_COLUMNS: Final[tuple[str, ...]] = ("height_m", "height", "HEIGHT", "높이", "건물높이")
BUILDING_TAG_COLUMNS: Final[tuple[str, ...]] = ("building", "MAIN_PURPS_CD_NM", "주용도", "use", "A9", "BDTYP_NM", "용도명")
# building use categories (buildings.use, Uint8-friendly): matched by substring of the Korean use name (V-World A9) or OSM tag
USE_NAMES: Final[dict[int, str]] = {0: "단독·다가구주택", 1: "공동주택(아파트·다세대·연립)", 2: "근린생활·상업·업무", 3: "교육·연구·공공·의료",
                                    4: "종교·문화·기타", 5: "미상"}
USE_UNKNOWN: Final[int] = 5
USE_PATTERNS: Final[tuple[tuple[int, tuple[str, ...]], ...]] = (
    (1, ("아파트", "다세대", "연립", "공동주택", "기숙사", "apartments", "dormitory")),
    (0, ("단독", "다가구", "주택", "house", "detached", "semidetached_house", "terrace", "bungalow", "residential", "hut", "cabin")),
    (3, ("교육", "연구", "공공", "의료", "학교", "병원", "의원", "관공서", "청사", "도서관", "유치원", "어린이집", "복지", "소방", "경찰", "우체국", "school",
         "university", "college", "hospital", "kindergarten", "public", "government", "civic", "fire_station", "police", "clinic", "library")),
    (2, ("근린", "상업", "업무", "판매", "숙박", "공장", "창고", "위락", "운수", "자동차", "오피스텔", "시장", "commercial", "retail", "office", "hotel",
         "supermarket", "warehouse", "industrial", "shop", "kiosk", "service", "parking", "garage", "garages", "carport", "roof")),
    (4, ("종교", "문화", "교회", "사찰", "성당", "체육", "집회", "관람", "장례", "church", "temple", "cathedral", "mosque", "chapel", "stadium",
         "sports", "museum", "cultural", "shrine", "grandstand", "pavilion", "greenhouse", "shed", "barn", "기타", "construction", "ruins")),
)
WALK_GRAPHML: Final[str] = "extra/osm/seoul_walk.graphml"
TWIN_CACHE_SUBDIR: Final[str] = "extra/twin"
CACHE_DIR_ENV: Final[str] = "KCF_TWIN_CACHE_DIR"
GRAPH_PAD_M: Final[float] = 600.0  # walk network kept this far outside the 구 (routes may cross the border)
MIN_EDGE_M: Final[float] = 0.5
OSM_LICENSE: Final[str] = "(c) OpenStreetMap contributors, ODbL 1.0"
OSM_URL: Final[str] = "https://www.openstreetmap.org/copyright"
ROUNDING_M: Final[int] = 2  # cm precision in the JSON (positions are metres from the scene centre)
EARTH_RADIUS_M: Final[float] = 6371008.8

# --- routing ----------------------------------------------------------------------------------
WALK_SPEED_M_S: Final[float] = 0.8  # contract: seconds @ 0.8 m/s (고령자 보행)
ROUTE_MAX_SNAP_M: Final[float] = 400.0  # a start point farther than this from any walk node is rejected (422)
AED_SNAP_MAX_M: Final[float] = 300.0  # AEDs farther than this from the network are not routing targets
ROUTE_BOUNDS_PAD_DEG: Final[float] = 0.0005

# --- crosswalks --------------------------------------------------------------------------------
CROSSWALK_SPEED_M_S: Final[float] = 0.7  # need_s@0.7 (contract)

# --- weather ----------------------------------------------------------------------------------
HOURLY_RAIN_DIR: Final[str] = "extra/rain_kma/hub_sfctm2"  # all 24 h, 2025-06..09
HOURLY_HEAT_DIR: Final[str] = "extra/kma_hub/sfctm2"  # KST 10-19 h only, 2019-2025 seasons
METEOSTAT_DIR: Final[str] = "extra/meteostat_daily"
KMA_TOKEN_TM, KMA_TOKEN_STN, KMA_TOKEN_TA, KMA_TOKEN_HM, KMA_TOKEN_RN = 0, 1, 11, 13, 15
KMA_TM_LEN: Final[int] = 12
KMA_MISSING_SENTINELS: Final[tuple[float, ...]] = (-9.0, -99.0)  # hub fill values
KMA_TA_RANGE: Final[tuple[float, float]] = (-40.0, 50.0)
KMA_RH_RANGE: Final[tuple[float, float]] = (1.0, 100.0)
RAIN_NIGHT_HOURS: Final[tuple[int, ...]] = (20, 21, 22, 23, 0, 1, 2, 3, 4, 5)
RAIN_THRESHOLD_MM_H: Final[float] = 10.0
RAIN_YEARS: Final[tuple[int, ...]] = (2023, 2024, 2025)
RAIN_SUMMER_MONTHS: Final[tuple[int, ...]] = (6, 7, 8, 9)
RAIN_DAILY_FALLBACK_MM: Final[float] = 30.0  # daily total used only to list *unverified* candidates
METEOSTAT_WMO_PREFIX: Final[str] = "47"  # KMA station 108 == Meteostat 47108

# --- terminal ---------------------------------------------------------------------------------
TERMINAL_KINDS: Final[tuple[str, ...]] = ("heat", "fall", "rain", "clear")
TERMINAL_TITLE_MAX: Final[int] = 40
TERMINAL_BODY_MAX: Final[int] = 200
TERMINAL_STOP_ID_MAX: Final[int] = 32
TERMINAL_KEEPALIVE_S: Final[float] = 10.0
TERMINAL_HISTORY: Final[int] = 50
TERMINAL_AUDIO_PREFIX: Final[str] = "terminal-"
TERMINAL_DRILL_LABEL: Final[str] = "훈련 · 발송 안 함"
