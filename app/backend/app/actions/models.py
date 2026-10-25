"""Pydantic v2 response models of the action channels."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .constants import ACTION_IDS


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ShelterRef(_Model):
    name: str
    region_code: str | None
    distance_km: float
    night_open: bool | None
    weekend_open: bool | None


class CbsDraft(_Model):
    text: str
    easy_text: str
    length: int
    easy_length: int
    limit: int
    limit_source: str
    limit_status: Literal["확인 필요"] = "확인 필요"  # the 157-char rule is cited from a press release, not a spec
    shelters: list[ShelterRef]
    shelter_note: str | None = None
    status: Literal["draft"] = "draft"


class BitMessage(_Model):
    lines: list[str]


class BitStop(_Model):
    rank: int
    stop_id: str
    name: str
    lat: float
    lon: float
    dong: str
    covered_pop65_est: int
    message: BitMessage


class BitInfo(_Model):
    radius_m: float
    covered_pop65_est: int
    region_pop65_with_stops: int
    n_candidate_stops: int
    method: str
    note: str


class SignalItem(_Model):
    crosswalk_id: str
    address: str
    lat: float
    lon: float
    length_m: float
    green_s: float
    required_green_s: float
    extend_s: int


class SignalsNote(_Model):
    note: str


class SignalCoverage(_Model):
    dataset_rows: int
    dataset_sido: list[str]
    n_crosswalks_in_region: int
    n_evaluable: int
    n_short: int
    note: str


class RouteStop(_Model):
    order: int
    dong_code: str
    dong: str
    lat: float
    lon: float
    pop65: int
    leg_km: float


class RouteResult(_Model):
    stops: list[RouteStop]
    km: float
    km_pop65_order: float
    distance_basis: str
    method: str
    allocated: bool
    teams_in_region: int
    note: str


class ActionContext(_Model):
    date: str
    variant: str
    alerted: bool | None
    score: float | None
    rank: int | None
    n_cells: int
    tmax_c: float | None
    apparent_max_c: float | None = None  # 체감 최고 (KMA 여름철 체감온도 daily max); drives `hot` when present
    tmax_day: str
    pop65: int
    team_allocated: bool
    teams_total: int


class ActionsResponse(_Model):
    sgg_code: str
    region_name: str
    context: ActionContext
    cbs: CbsDraft
    bit: list[BitStop]
    bit_info: BitInfo
    signals: list[SignalItem] | SignalsNote
    signals_coverage: SignalCoverage
    route: RouteResult
    sources: dict[str, str] = Field(default_factory=dict)
    # 근거 shared with /briefing (modules/evidence.py): λ, 65+, 체감 최고/최고기온 + station, rank, data sha256
    evidence: dict[str, Any] = Field(default_factory=dict)
    # stable keys for POST /feedback, one per channel
    action_ids: dict[str, str] = Field(default_factory=lambda: dict(ACTION_IDS))
    disclaimer: str
