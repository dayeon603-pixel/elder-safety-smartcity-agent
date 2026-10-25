"""Tunables for the NMC (국립중앙의료원) medical module. No magic numbers elsewhere in the package."""

from __future__ import annotations

from typing import Final

BASE_URL: Final = "https://apis.data.go.kr/B552657"
# service key -> (path, region parameter names (sido, sgg), rows per page)
SERVICES: Final[dict[str, tuple[str, tuple[str, str], int]]] = {
    "er_beds": ("ErmctInfoInqireService/getEmrrmRltmUsefulSckbdInfoInqire", ("STAGE1", "STAGE2"), 100),
    "er_list": ("ErmctInfoInqireService/getEgytListInfoInqire", ("Q0", "Q1"), 100),
    "pharmacy": ("ErmctInsttInfoInqireService/getParmacyListInfoInqire", ("Q0", "Q1"), 1000),
    "hospital": ("HsptlAsembySearchService/getHsptlMdcncListInfoInqire", ("Q0", "Q1"), 1000),
}
SOURCE_LABEL: Final = "국립중앙의료원 응급의료포털(E-Gen) 공공데이터 (data.go.kr B552657)"
# Freshness: beds change by the minute; directories change daily.
TTL_S: Final[dict[str, float]] = {"er_beds": 300.0, "er_list": 86400.0, "pharmacy": 86400.0, "hospital": 86400.0}
MAX_PAGES: Final = 5
CONNECT_TIMEOUT_S: Final = 3.0
READ_TIMEOUT_S: Final = 8.0
KEYCHAIN_ACCOUNT, KEYCHAIN_SERVICE = "kcf", "DATA_GO_KR_KEY"
KEYCHAIN_TIMEOUT_S: Final = 3.0
RESULT_OK: Final = "00"
QUOTA_FILE: Final = "quota.json"
SNAPSHOT_SUFFIX: Final = ".json"
NIGHT_START_H, NIGHT_END_H = 22, 6  # 밤 = 22시~06시 (same definition as the AED module)
NIGHT_PROBE_TIME: Final = "22:30"  # the clock used for "밤 의료 공백" evidence
HOLIDAY_INDEX: Final = 8  # dutyTime8* = 공휴일
LIST_LIMIT: Final = 20
PER_POP65: Final = 10_000
NO_DATA: Final = "자료 없음"
ER_OPEN_FLAG: Final = "1"  # dutyEryn: 1 = 응급실 운영
HOSPITAL_HOURS_NOTE: Final = "병의원 운영시간은 외래 진료시간 기준; 응급실(dutyEryn=1)은 별도로 24시간 운영을 전제로 집계"
# Bed fields (실시간 가용병상): hvec 응급실 일반, hvicc 일반중환자, hvoc 수술실, hvgc 입원실
BED_FIELDS: Final[dict[str, str]] = {"er_beds_available": "hvec", "icu_available": "hvicc",
                                     "operating_rooms_available": "hvoc", "general_beds_available": "hvgc"}
