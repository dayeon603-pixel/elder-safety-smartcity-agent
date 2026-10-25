"""Single place for the numbers and source paths the action channels agree on."""

from __future__ import annotations

from typing import Final

# --- 재난문자(CBS) -------------------------------------------------------------------------
# [1차] 재난문자방송 기준 및 운영규정 (행정안전부 고시, law.go.kr) 별표 2: 90자형 표준문안이 기본, 157자형은 폭염 등 일부 경보
# 표준문안. 글자 수는 한글 기준, 공백 포함으로 센다. 그 외의 시행 시점·확대 보도는 1차 확인이 없어 적지 않는다.
CBS_LIMIT_CURRENT: Final[int] = 157  # 157자형 (폭염 경보 표준문안)
CBS_LIMIT_LEGACY: Final[int] = 90  # 90자형 (표준)
CBS_LIMITS: Final[tuple[int, ...]] = (CBS_LIMIT_LEGACY, CBS_LIMIT_CURRENT)
CBS_LIMIT_SOURCE: Final[str] = (
    "재난문자방송 기준 및 운영규정 별표 2: 157자형 표준문안 (폭염 등 일부 경보; 한글 기준, 공백 포함). 90자형(표준)으로도 생성할 수 있다."
)
CBS_LIMIT_SOURCE_LEGACY: Final[str] = "재난문자방송 기준 및 운영규정 별표 2: 90자형 표준문안 (기본)."
EASY_MAX_SENTENCE_CHARS: Final[int] = 32
SHELTERS_IN_TEXT: Final[int] = 3
SHELTER_SEARCH_KM: Final[float] = 5.0
SHELTER_CANDIDATES: Final[int] = 5
GUIDE_HOURS: Final[str] = "12~17시"
EMERGENCY_NUMBER: Final[str] = "119"

HEAT_THRESHOLD_C: Final[float] = 33.0  # experiments/heat hot_c: 이 이상이거나 경보면 '폭염'으로 쓴다
RANK_TEXT_TOP_SHARE: Final[float] = 0.25  # 위험 순위는 상위 25% 안일 때만 문자에 넣는다

# --- 버스정류장 안내단말(BIT) --------------------------------------------------------------
BIT_RADIUS_M: Final[float] = 300.0
BIT_TOP_N_DEFAULT: Final[int] = 5
BIT_TOP_N_MAX: Final[int] = 30
BIT_LINES: Final[int] = 2
BIT_LINE_CHARS: Final[int] = 15
BIT_LAT_RANGE: Final[tuple[float, float]] = (33.0, 39.0)
BIT_LON_RANGE: Final[tuple[float, float]] = (124.0, 132.0)
BIT_DISTANCE_CHUNK: Final[int] = 400

# --- 보행신호 연장 --------------------------------------------------------------------------
WALK_SPEED_M_S: Final[float] = 0.7  # 고령자·보행약자 기준 보행속도
FIELD_LIST_LIMIT: Final[int] = 500  # /aed default page
FIELD_LIST_MAX: Final[int] = 5000
SIGNALS_LIMIT_DEFAULT: Final[int] = 30
SIGNALS_LIMIT_MAX: Final[int] = 200
CROSSWALK_MAX_LENGTH_M: Final[float] = 100.0  # 표준데이터에 연장 302m 같은 입력 오류가 있어 상한을 둔다
CROSSWALK_MAX_GREEN_S: Final[float] = 120.0
CROSSWALK_ROW_CAP: Final[int] = 50_000  # 내려받은 파일 행 수가 이와 같으면 API 상한에 걸렸을 수 있다

# --- 방문팀 동선 ----------------------------------------------------------------------------
ROUTE_STOPS_DEFAULT: Final[int] = 6
ROUTE_STOPS_MIN: Final[int] = 2
ROUTE_STOPS_MAX: Final[int] = 15
TWO_OPT_MAX_PASSES: Final[int] = 100

# --- 요청 ------------------------------------------------------------------------------------
TEAMS_DEFAULT: Final[int] = 10
TEAMS_MIN: Final[int] = 1
TEAMS_MAX: Final[int] = 100
SGG_CODE_RE: Final[str] = r"^\d{5}$"
DATE_RE: Final[str] = r"^\d{4}-\d{2}-\d{2}$"

# --- 원자료 (KCF_DATA_DIR 기준) ------------------------------------------------------------
BUS_STOPS_CSV: Final[str] = "g_transport_mobility/kr_15067528_bus_stops_nationwide_2025.csv"
BUS_STOPS_ENCODING: Final[str] = "cp949"
CROSSWALK_CSV: Final[str] = "extra/silverzone/kr_15028201_crosswalk_std.csv"
CROSSWALK_ENCODING: Final[str] = "utf-8-sig"
SOURCES: Final[dict[str, str]] = {
    "cbs": "재난문자방송 기준 및 운영규정 별표 2(표준문안); 기상청 ASOS 관측; 질병관리청 온열질환 감시 기반 폭염 위험 모델",
    "bit": "공공데이터포털 15067528 전국 버스정류장 위치정보(2025); 행정동 인구 15097972(2026-09); HangJeongDong 경계(2026-10)",
    "signals": "공공데이터포털 15028201 전국 횡단보도 표준데이터",
    "route": "행정동 인구 15097972(2026-09); HangJeongDong 경계 중심점; 직선거리(haversine)",
}
SOURCE_SHELTER: Final[str] = "무더위쉼터 지자체 공개자료"  # appended to the cbs source only when the 구 itself has shelter rows
# stable keys a reviewer's decision (POST /feedback) refers to, one per channel
ACTION_IDS: Final[dict[str, str]] = {"cbs": "cbs", "bit": "bit", "signals": "signals", "route": "route"}
DISCLAIMER: Final[str] = "모든 출력은 담당자가 검토할 권고 초안이며 발송·시행된 것이 아니다."
