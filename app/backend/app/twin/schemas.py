"""Response models of the twin endpoints (read-only; nothing here is accepted as input)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class AedState(BaseModel):
    id: str
    lat: float
    lon: float
    name: str
    place: str | None
    open: bool | None
    hours: str | None
    recommended_24h: bool
    open_after: bool | None


class ShelterState(BaseModel):
    id: int
    name: str
    lat: float
    lon: float
    capacity: int | None
    night_open: bool | None
    weekend_open: bool | None
    open: bool | None


class CrosswalkState(BaseModel):
    id: str | None
    lat: float
    lon: float
    length_m: float | None
    green_s: float | None
    need_s: float | None
    short: bool | None
    extend_s: int | None
    has_signal: bool
    address: str | None = None
    road: str | None = None
    address_kind: str | None = None


class SilverSite(BaseModel):
    lat: float
    lon: float
    name: str | None


class SilverHotspot(BaseModel):
    lat: float
    lon: float
    casualties: float
    inside_current: bool
    inside_ours: bool


class SilverEval(BaseModel):
    year: int
    scope: str
    n_hotspots: int
    covered_current: int
    covered_ours: int
    pct_current: float | None
    pct_ours: float | None
    rule: str
    source: str
    display_source: str
    designation: str | None = None
    seoul_zones_current: int | None = None
    seoul_zones_ours: int | None = None
    caption_note: str | None = None
    note: str | None


class StopState(BaseModel):
    stop_id: str
    name: str
    lat: float
    lon: float
    score: float
    greedy_rank: int
    p65_within_walk: float | None


class HeatState(BaseModel):
    date: str
    variant: str
    apparent_max_c: float | None
    tmax_c: float | None
    station: dict[str, Any] | None
    b1_alert: bool | None
    ours_alert: bool | None
    rank: int | None
    n_cells: int | None
    rank_text: str | None
    expected_cases_text: str | None
    expected_cases_lambda: float | None
    cases_today: int | None = None
    decision_text: dict[str, str] | None = None
    note: str | None


class WeatherState(BaseModel):
    station: dict[str, Any] | None
    date: str
    hour: int
    temp: float | None
    feels: float | None
    rain_mm: float | None
    rh: float | None = None
    resolution: Literal["hourly", "daily", "none"]
    source: str | None = None
    display_source: str | None = None
    note: str | None = None


class TwinState(BaseModel):
    sgg_code: str
    region: str
    requested: dict[str, Any]
    date: str
    hour: int
    daytype: Literal["weekday", "sat", "sun", "holiday"]
    is_holiday: bool
    aed: list[AedState]
    aed_before_after: dict[str, Any]
    shelters: list[ShelterState]
    shelter_rules: list[str]
    crosswalks: list[CrosswalkState]
    crosswalk_speed_m_s: float
    silver_current: list[SilverSite]
    silver_ours: list[SilverSite]
    silver_hotspots: list[SilverHotspot]
    silver_eval: SilverEval
    silver_eval_seoul: SilverEval
    stops_recommended: list[StopState]
    heat: HeatState
    weather: WeatherState
    summary: dict[str, Any]
    notes: list[str]
    sources: list[dict[str, Any]]
    fetched_at: str | None


class TimelineHour(BaseModel):
    hour: int
    date: str
    clock_hour: int
    daytype: str
    aed_open: int
    aed_open_pct: float | None
    aed_open_pct_after: float | None
    shelters_open: int
    temp: float | None
    feels: float | None
    rain_mm: float | None
    weather_resolution: str
    night: bool


class TwinTimeline(BaseModel):
    sgg_code: str
    region: str
    date: str
    aed_total: int
    shelters_total: int
    hours: list[TimelineHour]
    heat: HeatState
    sources: list[dict[str, Any]]
    fetched_at: str | None


class RouteAed(BaseModel):
    id: str
    name: str
    place: str | None
    address: str | None
    lat: float
    lon: float
    hours: str | None
    open: bool
    gugun: str | None


class RouteOut(BaseModel):
    aed: RouteAed
    path: list[list[float]]
    meters: float
    network_meters: float
    snap_start_m: float
    snap_aed_m: float
    seconds: int
    speed_m_s: float
    straight_line_m: float
    date: str
    hour: int
    daytype: str
    open_aeds_considered: int
    nodes_in_network: int
    sgg_code: str
    compute_ms: float
    sources: list[dict[str, Any]]
    fetched_at: str
    note: str


class RainDays(BaseModel):
    sgg_code: str
    region: str
    station: dict[str, Any]
    criteria: dict[str, Any]
    days: list[dict[str, Any]]
    unverified_candidates: list[dict[str, Any]]
    coverage: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    fetched_at: str | None


__all__ = ["AedState", "CrosswalkState", "HeatState", "RainDays", "RouteAed", "RouteOut", "ShelterState", "SilverEval",
           "SilverHotspot", "SilverSite", "StopState", "TimelineHour", "TwinState", "TwinTimeline", "WeatherState"]
