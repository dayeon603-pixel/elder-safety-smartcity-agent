"""Medical module facade: ER beds, open-now pharmacies/hospitals, and the "밤 의료 공백" evidence.

One `MedicalService` per app (app.state.medical). It owns the cache, the quota and the HTTP client factory;
tests inject a mock transport via `app.state.medical_http`. Every answer carries `fetched` (source, age,
stale, error) so a snapshot is always labelled as such; no items -> `note: 자료 없음`.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..config import Settings
from ..heat.plan import haversine_km
from . import constants as C
from . import hours
from .cache import Fetched, MedicalCache
from .client import DailyQuota, NmcClient, resolve_key

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))


@dataclass(frozen=True, slots=True)
class Region:
    code: str
    sido: str
    name: str
    lat: float
    lon: float
    pop65: int


def region_of(conn: sqlite3.Connection, sgg_code: str) -> Region | None:
    r = conn.execute("SELECT code, sido, name, lat, lon, pop65 FROM region WHERE code = ?", (sgg_code,)).fetchone()
    return Region(str(r["code"]), str(r["sido"]), str(r["name"]), float(r["lat"]), float(r["lon"]), int(r["pop65"])) if r else None


def _float(v: str | None) -> float | None:
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _int(v: str | None) -> int | None:
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        return None


def _hvidate(raw: str | None) -> str | None:
    """'20261024162011' (KST) -> ISO 8601 with +09:00."""
    if not raw or len(raw) != 14 or not raw.isdigit():
        return None
    try:
        return datetime.strptime(raw, "%Y%m%d%H%M%S").replace(tzinfo=KST).isoformat()
    except ValueError:
        return None


def _place(it: dict[str, str], origin: tuple[float, float] | None) -> dict[str, Any]:
    lat, lon = _float(it.get("wgs84Lat")), _float(it.get("wgs84Lon"))
    out: dict[str, Any] = {"hpid": it.get("hpid"), "name": it.get("dutyName"), "address": it.get("dutyAddr"),
                           "tel": it.get("dutyTel1") or it.get("dutyTel3"), "lat": lat, "lon": lon}
    if origin is not None and lat is not None and lon is not None:
        out["distance_km"] = round(haversine_km(origin[0], origin[1], lat, lon), 2)
    return out


class MedicalService:
    def __init__(self, settings: Settings, http_factory: Callable[[], httpx.Client] | None = None) -> None:
        self._settings = settings
        self._http_factory = http_factory or (lambda: httpx.Client())
        self._http: httpx.Client | None = None
        self._key = resolve_key(settings.data_go_kr_key)
        self._quota = DailyQuota(settings.medical_cache_dir, settings.medical_daily_quota)
        seed = settings.medical_snapshot_dir if settings.medical_snapshot_dir.is_dir() else None
        self.cache = MedicalCache(settings.medical_cache_dir, seed)
        if self._key is None:
            logger.warning("no data.go.kr key (KCF_DATA_GO_KR_KEY / keychain): medical module serves snapshots only")

    @property
    def live_possible(self) -> bool:
        return self._key is not None

    def _client(self) -> NmcClient | None:
        if self._key is None:
            return None
        if self._http is None:
            self._http = self._http_factory()
        return NmcClient(self._http, self._key, self._quota)

    def fetched(self, service: str, region: Region) -> Fetched | None:
        client = self._client()
        fetcher = (lambda: client.fetch(service, region.sido, region.name)) if client is not None else None
        return self.cache.get(service, region.sido, region.name, fetcher)

    def status(self) -> dict[str, Any]:
        return {"live_possible": self.live_possible, "quota_used_today": self._quota.used(),
                "quota_limit": self._settings.medical_daily_quota, "cache_dir": str(self._settings.medical_cache_dir),
                "seed_dir": str(self._settings.medical_snapshot_dir)}

    # ------------------------------------------------------------------ ER
    def er(self, region: Region, origin: tuple[float, float] | None = None) -> dict[str, Any]:
        """응급의료기관 (with coordinates) joined to 실시간 가용병상 by hpid; nearest first when `origin` is given."""
        listing = self.fetched("er_list", region)
        beds = self.fetched("er_beds", region)
        by_hpid = {b.get("hpid"): b for b in (beds.items if beds else [])}
        items: list[dict[str, Any]] = []
        for it in listing.items if listing else []:
            b = by_hpid.get(it.get("hpid"), {})
            rec = _place(it, origin)
            rec["kind"] = it.get("dutyEmclsName")
            for key, field in C.BED_FIELDS.items():
                rec[key] = _int(b.get(field))
            rec["beds_updated_at"] = _hvidate(b.get("hvidate"))
            rec["beds_reported"] = bool(b)
            items.append(rec)
        items.sort(key=lambda r: (r.get("distance_km") is None, r.get("distance_km") or 0.0, r["name"] or ""))
        with_beds = [r for r in items if r["er_beds_available"] is not None]
        latest = max((r["beds_updated_at"] for r in with_beds if r["beds_updated_at"]), default=None)
        return {
            "sgg_code": region.code, "region": f"{region.sido} {region.name}", "count": len(items),
            "reporting_count": len(with_beds),
            "er_beds_available_total": sum(int(r["er_beds_available"]) for r in with_beds) if with_beds else None,
            "beds_updated_at": latest, "items": items[: C.LIST_LIMIT],
            "note": None if items else C.NO_DATA,
            "fetched": {"er_list": listing.label() if listing else None, "er_beds": beds.label() if beds else None},
            "source": C.SOURCE_LABEL,
        }

    # ------------------------------------------------------------------ open now
    def open_now(self, region: Region, at: datetime, kind: str = "all",
                 origin: tuple[float, float] | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "sgg_code": region.code, "region": f"{region.sido} {region.name}", "at": at.isoformat(timespec="minutes"),
            "weekday_index": at.weekday() + 1, "is_holiday": hours.is_holiday(at.date()),
            "day_index": hours.day_index(at.date()), "is_night": hours.is_night(at),
            "holiday_table_covered": hours.holiday_table_covers(at.date()), "fetched": {}, "source": C.SOURCE_LABEL,
        }
        for svc, label in (("pharmacy", "pharmacies"), ("hospital", "hospitals")):
            if kind not in ("all", svc):
                continue
            f = self.fetched(svc, region)
            out["fetched"][svc] = f.label() if f else None
            items = f.items if f else []
            open_items, closed, unknown = hours.split_open(items, at)
            block: dict[str, Any] = {"open": len(open_items), "closed": closed, "unknown_hours": unknown, "total": len(items),
                                     "note": None if items else C.NO_DATA}
            places = [_place(it, origin) for it in open_items]
            if svc == "hospital":
                # dutyTime* of the hospital directory are 외래 진료시간; an 응급실 (dutyEryn=1) runs regardless, so
                # ER hospitals are counted over the whole directory, not only over the ones open for outpatients.
                for it, p in zip(open_items, places, strict=True):
                    p["kind"] = it.get("dutyDivNam")
                    p["has_er"] = it.get("dutyEryn") == C.ER_OPEN_FLAG
                block["er_hospitals"] = sum(1 for it in items if it.get("dutyEryn") == C.ER_OPEN_FLAG)
                block["hours_note"] = C.HOSPITAL_HOURS_NOTE
            idx = hours.day_index(at.date())
            for it, p in zip(open_items, places, strict=True):
                span = hours.hours_for(it, idx)
                p["hours_today"] = f"{it.get(f'dutyTime{idx}s')}-{it.get(f'dutyTime{idx}c')}" if span else None
            places.sort(key=lambda r: (r.get("distance_km") is None, r.get("distance_km") or 0.0, r["name"] or ""))
            block["items"] = places[: C.LIST_LIMIT]
            out[label] = block
        return out

    # ------------------------------------------------------------------ evidence
    def night_gap(self, region: Region, day: str) -> dict[str, Any]:
        """밤 의료 공백 on `day` at NIGHT_PROBE_TIME: open pharmacies/hospitals per 65+ population, ER beds."""
        at = datetime.fromisoformat(f"{day}T{C.NIGHT_PROBE_TIME}").replace(tzinfo=KST)
        now = self.open_now(region, at)
        er = self.er(region)
        ph, hp = now.get("pharmacies") or {}, now.get("hospitals") or {}
        per = (C.PER_POP65 / region.pop65) if region.pop65 else None
        return {
            "probe_at": now["at"], "is_holiday": now["is_holiday"],
            "pharmacies_open_night": ph.get("open"), "pharmacies_total": ph.get("total"),
            "pharmacies_open_per_10k_pop65": round(ph["open"] * per, 2) if per is not None and ph.get("total") else None,
            "hospitals_open_night": hp.get("open"), "er_hospitals": hp.get("er_hospitals"),
            "er_count": er["count"], "er_beds_available_total": er["er_beds_available_total"],
            "beds_updated_at": er["beds_updated_at"], "pop65": region.pop65,
            "fetched": {**now["fetched"], **er["fetched"]}, "source": C.SOURCE_LABEL,
            "note": C.NO_DATA if not ph.get("total") and not er["count"] else None,
        }


__all__ = ["KST", "MedicalService", "Region", "region_of"]
