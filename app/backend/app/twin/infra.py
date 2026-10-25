"""City-infrastructure inventory of a 구 for the twin (GET /twin/{sgg}/infra): the real devices the agent reasons about.

Types (real data only): aed (NMC AED register), signal_crosswalk (standard crosswalk rows with a traffic light inside the
구), bus_stop (every nationwide-register stop inside the 구; `recommended_bit` marks the agent's 안내단말 연계 후보, which does
NOT claim a display is installed), silver_zone (current zones + agent picks). cctv / fire_station are reported as null with a
note when no public dataset is cached (never invented). control_center = 구청 location taken from the AED register record
installed at the 구청 ("관제(시연 위치)" label, source shown).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from typing import Any

import numpy as np

from ..actions import bit, dongs
from ..actions.geometry import haversine_m, points_in_rings
from ..config import Settings
from . import state as twin_state

logger = logging.getLogger("twin.infra")
_LOCK = threading.Lock()
_CACHE: dict[str, dict[str, Any]] = {}
BIT_LABEL = "안내단말 연계 후보"
STOP_MATCH_M = 40.0  # a register stop within this distance of a recommended stop carries its flag (ids differ between sources)
CCTV_NOTE = ("공개 CCTV 위치 데이터 미확보: 전국CCTV표준데이터(data.go.kr 15013094)·서울 열린데이터광장 파일은 로그인/키 없이 내려받을 수 없어 "
             "표시하지 않는다 (지어내지 않음)")
FIRE_NOTE = "관할 소방서 좌표 공개 데이터가 캐시에 없어 표시하지 않는다"
SRC_AED = "국립중앙의료원 AED 등록정보 (data.go.kr 15000652)"
SRC_CROSSWALK = "전국 횡단보도 표준데이터 (신호등 있음)"
SRC_STOPS = "전국 버스정류장 위치정보 (data.go.kr 15067528, 2025)"
SRC_SILVER = "노인보호구역 현황(현행) · 에이전트 제안 지점"
SRC_CONTROL = "NMC AED 등록정보의 구청 설치 기록 좌표"


class InfraUnavailable(RuntimeError):
    """A source cache the inventory needs is missing."""


def _stops_in_region(settings: Settings, region: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        ds = dongs.dongs_of_region(region["sido"], region["name"], settings.data_dir)
        df = bit.load_stops(settings.data_dir)
    except (dongs.DongDataError, FileNotFoundError) as exc:
        raise InfraUnavailable(f"bus stop / 행정동 source missing: {exc}") from exc
    lat = df["위도"].to_numpy(dtype=float)
    lon = df["경도"].to_numpy(dtype=float)
    ok = np.isfinite(lat) & np.isfinite(lon)
    inside = np.zeros(len(df), dtype=bool)
    rings = [r for d in ds for r in d.rings]
    if rings and ok.any():
        inside[ok] = points_in_rings(rings, lon[ok], lat[ok])
    sub = df[inside]
    return [{"id": str(r["정류장번호"]), "name": str(r["정류장명"]), "lat": float(r["위도"]), "lon": float(r["경도"])} for r in sub.to_dict("records")]


def control_center(settings: Settings, region: dict[str, Any]) -> dict[str, Any] | None:
    """The 구청 as the demo control room: the AED register's record installed at '<구>청' (first by address), else None."""
    df = twin_state._aed_frame(settings, region)
    hall = f"{region['name']}청"
    rows = df[df["org"].astype(str) == hall]
    if rows.empty:
        rows = df[df["buildAddress"].astype(str).str.contains(hall, na=False)]
    if rows.empty:
        return None
    r = rows.iloc[0]
    return {"name": f"{region['name']} 관제(시연 위치)", "lat": float(r["lat"]), "lon": float(r["lon"]),
            "address": str(r["buildAddress"] or ""), "source": SRC_CONTROL, "display_source": f"{hall} AED 설치 기록 ({r['buildAddress']})",
            "note": "실제 관제센터 좌표가 아니라 구청 위치를 시연 관제 지점으로 쓴다"}


def inventory(conn: sqlite3.Connection, settings: Settings, region: dict[str, Any]) -> dict[str, Any]:
    """Devices, counts, control centre and sources of one 구 (cached per process).

    Raises:
        InfraUnavailable: AED / crosswalk / bus-stop caches missing on this host.
    """
    code = region["code"]
    with _LOCK:
        hit = _CACHE.get(code)
    if hit is not None:
        return hit
    devices: list[dict[str, Any]] = []
    aed_df = twin_state._aed_frame(settings, region)
    for r in aed_df.to_dict("records"):
        devices.append({"type": "aed", "id": str(r["serialSeq"]), "name": str(r["org"] or ""), "lat": float(r["lat"]), "lon": float(r["lon"]),
                        "place": r["buildPlace"] or None, "source": SRC_AED})
    for c in twin_state.crosswalk_items(settings, region):
        if c["has_signal"]:
            devices.append({"type": "signal_crosswalk", "id": str(c["id"]), "name": f"신호 횡단보도 {c['id']}", "lat": c["lat"], "lon": c["lon"],
                            "green_s": c["green_s"], "length_m": c["length_m"], "short": c["short"], "source": SRC_CROSSWALK})
    recommended = twin_state.stop_items(conn, code)
    rec_ids = {s["stop_id"] for s in recommended}
    stops = _stops_in_region(settings, region)
    for s in stops:
        flag = s["id"] in rec_ids or any(haversine_m(s["lat"], s["lon"], r["lat"], r["lon"]) <= STOP_MATCH_M for r in recommended)
        devices.append({"type": "bus_stop", "id": s["id"], "name": s["name"], "lat": s["lat"], "lon": s["lon"], "recommended_bit": flag,
                        "bit_label": BIT_LABEL if flag else None, "source": SRC_STOPS})
    silver = twin_state.silver_block(conn, code)
    for kind, label in (("current", "현행 노인보호구역"), ("ours", "에이전트 제안 지점")):
        for i, z in enumerate(silver[kind]):
            devices.append({"type": "silver_zone", "id": f"{kind}:{i}", "name": z["name"] or label, "lat": z["lat"], "lon": z["lon"],
                            "kind": kind, "label": label, "source": SRC_SILVER})
    counts: dict[str, int | None] = {t: sum(1 for d in devices if d["type"] == t) for t in ("aed", "signal_crosswalk", "bus_stop", "silver_zone")}
    counts["bus_stop_recommended_bit"] = sum(1 for d in devices if d["type"] == "bus_stop" and d["recommended_bit"])
    counts["cctv"] = None
    counts["fire_station"] = None
    doc = {
        "sgg_code": code, "region": f"{region['sido']} {region['name']}", "devices": devices, "counts": counts,
        "control_center": control_center(settings, region),
        "notes": [CCTV_NOTE, FIRE_NOTE, f"bus_stop.recommended_bit = {BIT_LABEL} (안내단말 설치를 뜻하지 않음)"],
        "sources": [{"type": "aed", "what": SRC_AED, "display_source": "국립중앙의료원 AED 등록정보"},
                    {"type": "signal_crosswalk", "what": SRC_CROSSWALK, "display_source": "전국 횡단보도 표준데이터"},
                    {"type": "bus_stop", "what": SRC_STOPS, "display_source": "전국 버스정류장 위치정보 + 추천 정류장 분석 (에이전트)"},
                    {"type": "silver_zone", "what": SRC_SILVER, "display_source": "현행 노인보호구역 · 에이전트 제안"},
                    {"type": "control_center", "what": SRC_CONTROL, "display_source": "구청 위치 (AED 등록정보)"}],
        "fetched_at": twin_state.fetched_at(conn),
    }
    with _LOCK:
        _CACHE[code] = doc
    return doc


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


__all__ = ["BIT_LABEL", "InfraUnavailable", "clear_cache", "control_center", "inventory"]
