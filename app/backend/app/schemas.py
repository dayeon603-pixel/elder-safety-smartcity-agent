"""Strict pydantic v2 request/response models."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

EMAIL_RE = r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,189}\.[A-Za-z]{2,24}$"
CSV_NAME_RE = r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}\.csv$"
DATASET_NAME_RE = r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$"
URL_RE = r"^https?://[^\s]{1,2040}$"
MIN_PASSWORD_LEN = 12
MAX_PASSWORD_LEN = 128

_STRICT = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)
# Credentials must not strip: a password with leading/trailing spaces is a different password.
_STRICT_NO_STRIP = ConfigDict(strict=True, extra="forbid")
Email = Annotated[str, StringConstraints(strip_whitespace=True, pattern=EMAIL_RE, max_length=254)]
MAX_LABEL_VALUES = 20
DEFAULT_LABEL_VALUES = (0, 1)


class Credentials(BaseModel):
    """Register body. Passwords are used byte-for-byte (no whitespace stripping)."""

    model_config = _STRICT_NO_STRIP
    email: Email
    password: str = Field(min_length=MIN_PASSWORD_LEN, max_length=MAX_PASSWORD_LEN)


class LoginIn(BaseModel):
    """Login body: length floor is not enforced so policy changes cannot lock users out."""

    model_config = _STRICT_NO_STRIP
    email: Email
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LEN)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class RegisterOut(BaseModel):
    """Uniform registration reply: identical whether or not the email was already taken."""

    status: str = "accepted"
    detail: str = "If the address is eligible, an account was created. Sign in to continue."


class IngestIn(BaseModel):
    """Request to ingest a CSV already present in the data cache directory."""

    model_config = _STRICT
    file_name: str = Field(pattern=CSV_NAME_RE)
    name: str = Field(pattern=DATASET_NAME_RE)
    source_url: str = Field(pattern=URL_RE)
    license: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=500)
    id_column: str | None = Field(default=None, max_length=128)
    time_column: str | None = Field(default=None, max_length=128)
    label_column: str | None = Field(default=None, max_length=128)
    group_column: str | None = Field(default=None, max_length=128)
    feature_columns: list[str] | None = Field(default=None, max_length=1000)
    label_values: list[int] = Field(
        default_factory=lambda: list(DEFAULT_LABEL_VALUES), min_length=2, max_length=MAX_LABEL_VALUES
    )


class DatasetOut(BaseModel):
    """`source_url` and `license` are declared by the ingesting user and not verified;
    `sha256` is computed by the server over the file bytes; `fetched_at` is the ingest
    time and `file_mtime` the file's modification time."""

    id: int
    name: str
    source_url: str
    license: str
    sha256: str
    fetched_at: str
    file_name: str | None
    row_count: int
    description: str
    column_roles: dict[str, Any] = Field(default_factory=dict)
    status: str = "complete"
    file_mtime: str | None = None


class IngestOut(BaseModel):
    created: bool
    dataset: DatasetOut


class AvailableFile(BaseModel):
    file_name: str
    size_bytes: int


class RunOut(BaseModel):
    id: int
    experiment: str
    dataset_id: int | None
    role: str
    method: str
    params: dict[str, Any]
    metrics: dict[str, Any]
    seed: int
    split_kind: str
    n_train: int
    n_test: int
    git_hash: str
    duration_s: float
    started_at: str


class InfoOut(BaseModel):
    version: str
    schema_version: int
    datasets: list[dict[str, str]]
    last_run: dict[str, Any] | None


class AuditVerifyOut(BaseModel):
    valid: bool
    entries: int
    head: str
    broken_at: int | None
    reason: str | None


# --- /heat (read-only responses) -------------------------------------------------------------


class HeatSeason(BaseModel):
    start: str
    end: str


class HeatSource(BaseModel):
    name: str
    source_url: str
    license: str
    sha256: str
    fetched_at: str
    row_count: int


class HeatMeta(BaseModel):
    years: list[int]
    variants: list[str]
    methods: list[str]
    season: HeatSeason
    sources: list[HeatSource]


class HeatDelta(BaseModel):
    """Method minus B1 for the same year, variant and budget (paired block bootstrap CI)."""

    value: float
    ci_lo: float | None
    ci_hi: float | None


class HeatEvalRow(BaseModel):
    year: int
    method: str
    metric: str
    value: float
    ci_lo: float | None
    ci_hi: float | None
    delta_vs_b1: HeatDelta | None


class HeatCurvePoint(BaseModel):
    x: float
    capture: float


class HeatCurve(BaseModel):
    b1_point: HeatCurvePoint
    series: dict[str, list[HeatCurvePoint]]


class HeatDay(BaseModel):
    date: str
    n_cases: int
    k_budget: int
    b1_captured: int
    ours_captured: int


class HeatRegionBrief(BaseModel):
    code: str
    sido: str
    name: str
    lat: float
    lon: float
    pop65: int


class HeatAlertCell(BaseModel):
    code: str
    score: float
    cases: int


class HeatCaseCell(BaseModel):
    code: str
    n: int


class HeatAlerts(BaseModel):
    date: str
    k: int
    total_cases: int
    b1: list[HeatAlertCell]
    ours: list[HeatAlertCell]
    cases: list[HeatCaseCell]


class HeatRegionFull(BaseModel):
    code: str
    sido: str
    name: str
    lat: float
    lon: float
    pop: int
    pop65: int


class HeatScores(BaseModel):
    b1: float | None
    ours: float | None
    rank: int | None


class HeatStation(BaseModel):
    id: str
    name: str | None
    source: str | None
    dist_km: float


class HeatDrivers(BaseModel):
    tmax: float | None
    apparent_max: float | None  # KMA 여름철 체감온도 daily max (KST 10-19h); the 폭염특보 quantity
    station: HeatStation | None
    hist_rate: float | None
    old_share: float


class HeatShelterNear(BaseModel):
    name: str
    dist_km: float
    night_open: bool | None
    weekend_open: bool | None


class HeatShelters(BaseModel):
    coverage: Literal["data", "none"]
    nearby: list[HeatShelterNear]


class HeatRegionDetail(BaseModel):
    region: HeatRegionFull
    cases_today: int
    scores: HeatScores
    drivers: HeatDrivers
    shelters: HeatShelters


class HeatAllocation(BaseModel):
    code: str
    name: str
    teams: int
    expected_cases: float
    actual_cases: int
    reason: str


class HeatShelterExtension(BaseModel):
    shelter_id: int
    name: str
    region_code: str
    reason: str


class HeatPlanCompare(BaseModel):
    ours_reached: int
    b1_reached: int


class HeatPlan(BaseModel):
    date: str
    teams: int
    mode: str
    allocations: list[HeatAllocation]
    shelter_extensions: list[HeatShelterExtension]
    unknown_hours_count: int
    compare: HeatPlanCompare


class HeatPlanDay(BaseModel):
    date: str
    ours_reached: int
    b1_reached: int


class HeatPlanEval(BaseModel):
    """Same teams, B1 targeting vs ours; ci is a paired 7-day block bootstrap on the season delta."""

    year: int
    teams: int
    mode: str
    n_days: int
    total_cases: int
    ours_reached: int
    b1_reached: int
    delta: int
    ci_lo: float
    ci_hi: float
    per_day: list[HeatPlanDay]


class HeatPooledRow(BaseModel):
    method: str
    metric: str
    value: float
    ci_lo: float | None
    ci_hi: float | None
    delta_vs_b1: HeatDelta | None


class HeatPooled(BaseModel):
    """All-test-years block of results.json; results_sha256 pins the file the numbers came from."""

    variant: str
    pooled_key: str
    test_years: list[int]
    results_sha256: str
    rows: list[HeatPooledRow]
