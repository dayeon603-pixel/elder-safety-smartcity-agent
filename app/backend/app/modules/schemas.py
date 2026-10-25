"""Response models for /silver, /aed and /briefing (read-only, nothing here is accepted as input)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class SourceFile(BaseModel):
    file: str
    sha256: str
    loaded: bool


class SilverMethod(BaseModel):
    """Coverage = hotspots with a zone within 300 m / all hotspots; ci = 95% bootstrap interval."""

    method: str
    label: str
    coverage: float
    ci_lo: float
    ci_hi: float


class SilverDelta(BaseModel):
    """Ours minus `vs`, in percentage points (%p), with the paired 95% interval."""

    vs: str
    label: str
    delta_pp: float
    lo_pp: float
    hi_pp: float


class SilverStratum(BaseModel):
    stratum: Literal["all", "new"]
    n_hot: int
    casualties: float
    methods: list[SilverMethod]
    ours_minus: list[SilverDelta]


class SilverSummary(BaseModel):
    k: int
    variant: str
    pool: str  # pool of the headline (ours) row: OPT for the promoted run, C for v1
    ours_method: str  # ours_opt (promoted) | ours_full (v1)
    comparison_pool: str  # the baselines' pool (C)
    source: str  # promoted | v1 (KCF_SILVER_SOURCE)
    definitions: dict[str, str]
    strata: list[SilverStratum]
    sources: list[SourceFile]


class SilverSite(BaseModel):
    lat: float
    lon: float
    name: str | None
    sgg_code: str | None
    near_current: bool
    near_ours: bool


class SilverHotspot(BaseModel):
    lat: float
    lon: float
    casualties: float
    sgg_code: str | None
    covered_current: bool
    covered_ours: bool


class SilverMap(BaseModel):
    city: Literal["seoul"]
    k: int
    current: list[SilverSite]
    ours: list[SilverSite]
    hotspots_2024: list[SilverHotspot]
    sources: list[SourceFile]


class OpenShare(BaseModel):
    day: float
    night: float
    day_arrest_weighted: float
    night_arrest_weighted: float


class Interval(BaseModel):
    value: float
    ci_lo: float | None
    ci_hi: float | None
    n: int | None


class WorstGu(BaseModel):
    gu: str
    sgg_code: str | None
    night_open_share: float
    day_open_share: float
    gap_score: float


class AedSummary(BaseModel):
    scope: str
    definitions: dict[str, str]
    weekday: OpenShare
    weekend: OpenShare
    public_arrests_total: int
    night_share_of_public_arrests: Interval
    share_arrests_open_lt_50pct: Interval
    worst_gu: list[WorstGu]
    sources: list[SourceFile]


class AedHour(BaseModel):
    hour: int
    public_arrests: int
    open_share_weekday: float
    open_share_weekend: float


class AedGu(WorstGu):
    public_arrests_night: float
    n_aed: int
    n_aed_24h_weekday: int
    rank_interval: list[int] | None = None  # 95% Poisson-bootstrap interval of the gap rank
    top5_probability: float | None = None
    rank_text: str | None = None


class AedCandidate(BaseModel):
    gu: str
    rank: int
    org: str
    address: str
    place: str
    weekday_hours: str | None
    night_open_weekday_share: float
    gain_score: float
    lat: float
    lon: float


class BriefRegion(BaseModel):
    code: str
    name: str
    sido: str
    pop65: int


class PlanAllocation(BaseModel):
    teams: int
    teams_total: int
    expected_cases: float | None
    reason: str | None


class NearbyShelter(BaseModel):
    name: str
    dist_km: float
    night_open: bool | None
    weekend_open: bool | None


class BriefShelters(BaseModel):
    coverage: Literal["data", "none"]
    items: list[NearbyShelter]


class BriefHeat(BaseModel):
    rank: int | None
    rank_text: dict[str, Any]  # mode exact|band|insufficient|none, text, basis_cases (EDGE_CASES A17)
    evidence: dict[str, Any]
    alerted_b1: bool | None
    alerted_ours: bool | None
    cases_actual: int
    plan_allocation_for_region: PlanAllocation
    nearby_open_shelters: BriefShelters


class BriefSilver(BaseModel):
    current_zones_in_region: int | None
    our_picks_in_region: int | None
    hotspots_2024_in_region: int | None
    hotspots_covered_current: int | None
    hotspots_covered_ours: int | None
    crosswalk_green_deficit_count: int | None
    note: str | None


class BriefAed(BaseModel):
    night_open_share: float
    day_open_share: float
    gap_rank: int  # point rank; 12–33 events/yr make it noisy, so use gap_rank_uncertainty / gap_rank_text
    gap_score: float
    gap_rank_uncertainty: dict[str, Any]  # rank_point, rank_interval [lo, hi], top_k, top_k_probability, events_per_year
    gap_rank_text: str
    candidates_top5: list[AedCandidate]


class Action(BaseModel):
    action_id: str  # stable key for POST /feedback: heat.alert | heat.teams | heat.shelters | silver.check | aed.gap | aed.convert
    module: Literal["heat", "silver", "aed", "medical"]
    text: str
    reason: str
    evidence: dict[str, Any] | None  # the inputs that drove the line (heat: λ, 65+, 체감 최고, 순위, 데이터 sha256)


class Briefing(BaseModel):
    date: str
    teams: int
    region: BriefRegion
    heat: BriefHeat
    silver: BriefSilver
    aed: BriefAed | None
    aed_note: str | None
    medical: dict[str, Any] | None  # 밤 의료 공백 (NMC); None when neither live data nor a snapshot exists
    actions: list[Action]
