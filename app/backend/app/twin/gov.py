"""Who each agent recommendation is handed to, and how its status is worded (gov-operations frame).

Every label lives here so the authority names can be corrected in one place. Nothing the agent produces is ever "sent":
statuses mean "전달 대기" (waiting for the human in charge). Source notes: [1차] = statute / official page checked by the
research agent, [2차] = press or secondary report.
"""

from __future__ import annotations

from typing import Final

# --- status vocabulary (never "sent") -----------------------------------------------------------------------
STATUS_DRAFT: Final[str] = "draft"  # 초안 · 전달 대기
STATUS_ADOPTED: Final[str] = "adopted"  # 담당자 채택 (POST /feedback 채택)
STATUS_APPROVED_DEMO: Final[str] = "approved_demo"  # 과장 결재 시연 (POST /feedback stage=approve_demo)
STATUS_HELD: Final[str] = "held"  # 보류 (사유 필수)
STATUSES: Final[tuple[str, ...]] = (STATUS_DRAFT, STATUS_ADOPTED, STATUS_APPROVED_DEMO, STATUS_HELD)
STATUS_LABEL: Final[dict[str, str]] = {
    STATUS_DRAFT: "초안 · 전달 대기", STATUS_ADOPTED: "채택 · 결재 대기", STATUS_APPROVED_DEMO: "결재 완료(시연) · 전달 대기",
    STATUS_HELD: "보류",
}
NEVER_SENT: Final[str] = "훈련 · 발송 안 함 (모든 출력은 담당자에게 전달 대기 중인 초안)"

# --- our-side wording (no 발령/미발령, no 발송/전송) -----------------------------------------------------------
OURS_ALERT_TEXT: Final[str] = "에이전트 권고: 사전 조치"
OURS_NO_ALERT_TEXT: Final[str] = "에이전트 권고: 대상 아님"
B1_ALERT_TEXT: Final[str] = "현행 특보 기준: 대상"
B1_NO_ALERT_TEXT: Final[str] = "현행 특보 기준: 대상 아님"

# --- CBS limits ---------------------------------------------------------------------------------------------
# 재난문자방송 기준 및 운영규정 (행정안전부 고시, law.go.kr) [1차]: 시·군·구 입력자가 시스템에 입력해 송출 요청 → 같은 시·군·구
# 재난상황 담당 부서장 승인 (제7조①3, 제10조①, 제11조①); 시스템 운영 = 행안부, 송출 = 통신사·방송사. 별표 2: 90자형 표준문안이
# 기본, 157자형은 폭염 등 일부 경보 표준문안. 별표 1: 야간(21~06시)에는 폭염 등 일부 항목을 보내지 않는다.
CBS_STANDARD_LIMIT: Final[int] = 90
CBS_PILOT_LIMIT: Final[int] = 157  # name kept for callers; it is the 157자형 폭염 경보 표준문안 length, not a pilot
CBS_STANDARD_LABEL: Final[str] = "90자형(표준)"
CBS_PILOT_LABEL: Final[str] = "157자형(폭염 경보 문안)"
CBS_LIMIT_NOTE: Final[str] = ("90자형 표준문안이 기본, 157자형은 폭염 등 일부 경보 표준문안 (재난문자방송 기준 및 운영규정 별표 2) [1차]. "
                              "에이전트의 역할 = 우선순위 + 초안 + '보내지 않아도 되는 지역' 표시(피로도·중복 방지)")
CBS_NIGHT_START_H: Final[int] = 21
CBS_NIGHT_END_H: Final[int] = 6
CBS_NIGHT_EXCLUDED_TEXT: Final[str] = "야간 발송 기준 제외 항목(운영규정 별표 1) → 다음 날 06시 이후 검토"
CBS_FLOW_NOTE: Final[str] = "시·군·구 입력자 송출 요청 → 재난 담당 부서장 승인 → 행안부 시스템 → 통신사·방송사 송출 (운영규정 제7·10·11조) [1차]"
SILVER_DESIGNATION_NOTE: Final[str] = "노인보호구역은 시장등이 경찰과 협의해 지정한다 (도로교통법 제12조의2) [2차: 규칙 사본]"
SIGNALS_SPEED_NOTE: Final[str] = "보호구역 보행속도 최대 0.7 m/s 기준 (서울시 2024-10 발표) [2차: 공공기관 발표]"

# --- destinations per action kind ----------------------------------------------------------------------------
DESTINATIONS: Final[dict[str, dict[str, str]]] = {
    "cbs": {"label": "재난문자 송출 요청 승인권자(구청 재난 담당 부서장)", "org": "구청 재난상황 담당 부서 (입력자 → 부서장 승인)",
            "channel": "재난문자(CBS) 송출 요청 초안 (행안부 시스템 입력 → 통신사·방송사 송출)",
            "source_note": "[1차] 재난문자방송 기준 및 운영규정 제7·10·11조"},
    "bit": {"label": "연계 제안 (교통정보센터 협의 필요)", "org": "교통정보센터 (BIT 운영)", "channel": "정류장 안내단말 문구 연계 제안",
            "source_note": "BIT에 재난 문구를 표출한 확인된 선례 없음 → 연계 제안으로만 표기"},
    "voice": {"label": "경로당·마을방송·AI 스피커 음성 문안", "org": "주민센터 · 경로당 · AI 스피커 운영 부서", "channel": "음성 안내 문안 + 마을방송 WAV",
              "source_note": "제주 AI 스피커 폭염 안내 선례 [2차]"},
    "route": {"label": "복지 부서 방문팀", "org": "구청 복지 부서 · 노인맞춤돌봄 수행기관", "channel": "방문 점검 동선 초안",
              "source_note": "노인맞춤돌봄 수행기관 방문 체계 [1차 확인 중]"},
    "signals": {"label": "시·도경찰청 협의 (협의 요청서 초안)", "org": "시·도경찰청 교통안전시설심의위원회", "channel": "보행신호 연장 협의 요청 초안",
                "source_note": "신호 운영은 경찰 권한; 에이전트는 신호를 바꾸지 않는다. 보호구역 보행속도 최대 0.7 m/s (서울시 2024-10 발표) [2차: 공공기관 발표]"},
    "aed.convert": {"label": "설치기관 관리책임자 (개방 요청 공문 초안) · 관할 보건소 참조", "org": "AED 설치기관 관리책임자 · 관할 보건소",
                    "channel": "AED 24시간 개방 요청 공문 초안", "source_note": "응급의료법상 AED 관리책임자 = 설치기관 [1차 확인 중]"},
    "shelter.night": {"label": "무더위쉼터 지정권자", "org": "구청 (시군구 지정)", "channel": "야간 연장 쉼터 후보",
                      "source_note": "무더위쉼터는 시군구가 지정; 관악구 목록 미연계 → 후보는 구청 목록 연계 시 제공"},
    "fall": {"label": "CCTV 통합관제센터 관제요원 확인 → 스마트도시 통합플랫폼 119 긴급출동 연계", "org": "CCTV 통합관제센터 · 관할 소방서(119)",
             "channel": "관제요원 알림 (의심 사건 · 위치 · 가장 가까운 열린 AED); FHIR 번들 = 연계 데이터 규격 예시(훈련)",
             "source_note": "스마트도시 통합플랫폼 5대 연계서비스 중 긴급출동 지원 [1차 확인 중]"},
}
SHELTER_ABSENT_TEXT: Final[str] = "야간 연장 쉼터 후보: 구청 목록 연계 시 제공"
SIGNALS_TITLE: Final[str] = "보행신호 연장 협의 요청 초안"
BIT_TITLE: Final[str] = "정류장 안내단말 문구 · 연계 제안 (교통정보센터 협의 필요)"
VOICE_TITLE: Final[str] = "경로당·마을방송·AI 스피커 음성 문안"
FHIR_LABEL: Final[str] = "연계 데이터 규격 예시(훈련)"
WALK_SPEED_FALL_RESPONDER_M_S: Final[float] = 1.2  # 행인(시연) 왕복 속도 (STORY_CONTRACT)


def destination(kind: str) -> dict[str, str]:
    """Destination block for an action kind (falls back to the 재난문자 approver for unknown kinds)."""
    return dict(DESTINATIONS.get(kind, DESTINATIONS["cbs"]))


__all__ = ["B1_ALERT_TEXT", "B1_NO_ALERT_TEXT", "CBS_FLOW_NOTE", "CBS_LIMIT_NOTE", "CBS_NIGHT_END_H", "CBS_NIGHT_EXCLUDED_TEXT",
           "CBS_NIGHT_START_H", "CBS_PILOT_LABEL", "CBS_PILOT_LIMIT", "CBS_STANDARD_LABEL", "CBS_STANDARD_LIMIT", "SIGNALS_SPEED_NOTE",
           "SILVER_DESIGNATION_NOTE",
           "DESTINATIONS", "FHIR_LABEL", "NEVER_SENT", "OURS_ALERT_TEXT", "OURS_NO_ALERT_TEXT", "SHELTER_ABSENT_TEXT",
           "SIGNALS_TITLE", "STATUSES", "STATUS_ADOPTED", "STATUS_APPROVED_DEMO", "STATUS_DRAFT", "STATUS_HELD", "STATUS_LABEL",
           "VOICE_TITLE", "WALK_SPEED_FALL_RESPONDER_M_S", "destination"]
