"""GET /medical/er?sgg_code=  ·  GET /medical/open-now?sgg_code=&at=YYYY-MM-DDTHH:MM[&kind=pharmacy|hospital|all]

Data: 국립중앙의료원 E-Gen (data.go.kr B552657). Live when a key and quota exist, else the newest snapshot
with its age (`fetched.*.stale`, `age_s`); no record for the 시군구 -> `note: 자료 없음`; API down and no
snapshot at all -> 503 `medical_unavailable`.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from ..deps import get_conn
from ..medical import MedicalService, region_of
from ..medical.service import KST

router = APIRouter(prefix="/medical", tags=["medical"])
SGG_RE = r"^[0-9]{5}$"
AT_RE = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}$"
UNAVAILABLE = {"code": "medical_unavailable", "message": "NMC API unreachable and no snapshot for this 시군구"}


def get_medical(request: Request) -> MedicalService:
    svc: MedicalService = request.app.state.medical
    return svc


def _region(conn: sqlite3.Connection, sgg_code: str) -> Any:
    region = region_of(conn, sgg_code)
    if region is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "region not found")
    return region


def _parse_at(raw: str | None) -> datetime:
    if raw is None:
        return datetime.now(KST)
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=KST)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "at must be YYYY-MM-DDTHH:MM (KST)") from exc


@router.get("/er")
def er(sgg_code: str = Query(..., pattern=SGG_RE), lat: float | None = Query(None, ge=-90, le=90),
       lon: float | None = Query(None, ge=-180, le=180), conn: sqlite3.Connection = Depends(get_conn),
       svc: MedicalService = Depends(get_medical)) -> dict[str, Any]:
    """응급의료기관 + 실시간 가용 병상 (hvec 응급실 일반) with the last update time; nearest first when lat/lon given."""
    region = _region(conn, sgg_code)
    out = svc.er(region, (lat, lon) if lat is not None and lon is not None else (region.lat, region.lon))
    if out["fetched"]["er_list"] is None and out["fetched"]["er_beds"] is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE)
    return out


@router.get("/open-now")
def open_now(sgg_code: str = Query(..., pattern=SGG_RE), at: str | None = Query(None, pattern=AT_RE),
             kind: Literal["pharmacy", "hospital", "all"] = "all", lat: float | None = Query(None, ge=-90, le=90),
             lon: float | None = Query(None, ge=-180, le=180), conn: sqlite3.Connection = Depends(get_conn),
             svc: MedicalService = Depends(get_medical)) -> dict[str, Any]:
    """지금(또는 `at`, KST) 열린 약국·병의원, 야간·공휴일 포함 (dutyTime8 = 공휴일, 자정 넘김 처리)."""
    region = _region(conn, sgg_code)
    out = svc.open_now(region, _parse_at(at), kind, (lat, lon) if lat is not None and lon is not None else (region.lat, region.lon))
    if all(v is None for v in out["fetched"].values()):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE)
    return out


@router.get("/status")
def medical_status(svc: MedicalService = Depends(get_medical)) -> dict[str, Any]:
    """Key present, quota used today, cache/seed directories."""
    return svc.status()
