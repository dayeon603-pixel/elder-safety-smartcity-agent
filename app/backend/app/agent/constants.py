"""Tunables for the action agent. No magic numbers elsewhere in the package."""

from __future__ import annotations

import os
import re
from typing import Final

PROMPT_VERSION: Final = "agent-prompt-v2-structured-plan"

OLLAMA_URL: Final = os.environ.get("KCF_OLLAMA_URL", "http://127.0.0.1:11434")
PLANNER_MODEL: Final = os.environ.get("KCF_AGENT_MODEL", "qwen2.5:3b")
VLM_MODEL: Final = os.environ.get("KCF_AGENT_VLM", "qwen2.5vl:3b")
SELF_URL_ENV: Final = "KCF_AGENT_SELF_URL"
DEMO_TOKEN_HEADER: Final = "X-Demo-Token"

LLM_TEMPERATURE: Final = 0.0
LLM_SEED: Final = 42
LLM_NUM_CTX: Final = 4096
LLM_NUM_PREDICT_PLAN: Final = 320
LLM_NUM_PREDICT_BRIEF: Final = 300
VLM_NUM_PREDICT: Final = 160
# Keep-alive: `make demo` sets a long value so the planner stays resident between runs; the
# default is short because memory is tight when the VLM is also used.
KEEP_ALIVE: Final = os.environ.get("KCF_OLLAMA_KEEP_ALIVE", "2m")
OLLAMA_PROBE_TIMEOUT_S: Final = 2.0
OLLAMA_CONNECT_TIMEOUT_S: Final = 2.0
OLLAMA_CHAT_TIMEOUT_S: Final = 30.0
OLLAMA_WARMUP_TIMEOUT_S: Final = 120.0
SELF_HTTP_TIMEOUT_S: Final = 20.0
# Monotonic budget for one run: once it is spent, no further LLM call is attempted and the
# deterministic planner / template briefing finish the run.
RUN_BUDGET_S: Final = float(os.environ.get("KCF_AGENT_RUN_BUDGET_S", "40"))
MIN_LLM_CALL_S: Final = 3.0  # do not start an LLM call with less budget than this
MAX_CONCURRENT_RUNS: Final = int(os.environ.get("KCF_AGENT_MAX_CONCURRENT", "3"))
TOOL_WORKERS: Final = 4
STREAM_KEEPALIVE_S: Final = 5.0

MAX_PLAN_STEPS: Final = 8
MAX_IMAGE_BYTES: Final = 4 * 1024 * 1024
MAX_IMAGE_B64_CHARS: Final = MAX_IMAGE_BYTES * 4 // 3 + 16
MAX_BRIEFING_CHARS: Final = 600
LLM_VIEW_MAX_CHARS: Final = 1800
DEFAULT_TEAMS: Final = 10
PLAN_TOP_N: Final = 5
SNAPSHOT_PLACEHOLDER: Final = "@snapshot"
INBOX_LIST_MAX: Final = 100
HEAT_CAP_THRESHOLD_C: Final = 33.0  # heat_daily: CAP only when alerted or Tmax >= this (actions.HEAT_THRESHOLD_C)

# Standards-derived constants (see standards.py for which fields mirror what).
CAP_NAMESPACE: Final = "urn:oasis:names:tc:emergency:cap:1.2"
CAP_SENDER: Final = "zero-point.demo"
CAP_LANGUAGE: Final = "ko-KR"
CAP_RADIUS_KM: Final = 0.3
KST_OFFSET_MIN: Final = 9 * 60
DENM_CAUSE_HUMAN_PRESENCE_ON_ROAD: Final = 12
DENM_CAUSE_EXTREME_WEATHER: Final = 17
DENM_STATION_TYPE_RSU: Final = 15
DENM_STATION_ID: Final = 900001
DENM_VALIDITY_S: Final = 600
DENM_INFO_QUALITY: Final = 2
TIM_DURATION_MIN: Final = 10
TIM_PRIORITY: Final = 5
MINUTES_PER_YEAR_MAX: Final = 527040
RSU_TEXT_MAX: Final = 80
RSU_TEXT_FALL: Final = "전방 횡단보도 보행자 낙상 의심, 서행"
RSU_TEXT_HEAT: Final = "폭염 주의, 노인 보행자 주의"
BIT_FALL_LINE1: Final = "횡단보도 낙상 의심"  # <= 15 chars each (actions.constants.BIT_LINE_CHARS)
BIT_FALL_LINE2: Final = "차량 서행 바랍니다"

NO_DATA_LABEL: Final = "자료 없음"
SIM_RSU_LABEL: Final = "RSU 시뮬레이터"
SIM_119_LABEL: Final = "119 시뮬레이터"
VLM_LABEL: Final = "VLM 장면 설명(참고용)"

# Fixed public constants that are not data claims; allowed in a briefing without evidence.
# They are matched with digit boundaries ("119" never swallows the digits of "1195").
ALLOWED_LITERALS: Final = ("CAP 1.2", "FHIR R4", "65세 이상", "119", "ETSI", "J2735", "DENM", "TIM")
CAP_FEED_PATH: Final = "/cap/feed.atom"
BANNED_BRIEFING_TERMS: Final = ("진단", "뇌졸중", "골절", "심근경색", "치매", "사망",
                                "적합", "준수", "인증", "None", "nan", "null")
VLM_BANNED_TERMS: Final = BANNED_BRIEFING_TERMS + ("이름", "신원", "얼굴로", "나이", "성별", "남성", "여성", "환자")
VLM_MAX_SENTENCES: Final = 2

# Identifiers and timestamps this system itself emits; their digits are not data claims.
# Each pattern is anchored to the exact shape the code produces.
NOISE_RES: Final = (
    re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})"),
    re.compile(r"zp-run-[0-9a-f]{12}-[0-9]{5}"),
    re.compile(r"run-[0-9a-f]{12}"),
    re.compile(r"(?:rsu|emg)-[0-9a-f]{12}"),
    re.compile(r"urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"),
    re.compile(r"(?<![0-9])0[0-9]{1,2}-[0-9]{3,4}-[0-9]{4}(?![0-9])"),  # Korean phone numbers (tool output, not data)
    re.compile(r"(?<![0-9])1[0-9]{3}-[0-9]{4}(?![0-9])"),  # 15xx/16xx/18xx service numbers
)
