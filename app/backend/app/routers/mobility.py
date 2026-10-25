"""GET /mobility/stops?gu=&sgg_code=&limit= : recommended stops (교통약자 이동 모듈), Seoul subset, read-only.

Data: `mobility_stop` loaded by `python -m app.modules.mobility` from experiments/mobility/out/recommended_top2000.csv.
409 `no_module_data` before the load; 404 unknown 구; items ordered by greedy_rank (best first).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import repo
from ..deps import get_conn
from ..modules.mobility import DATASET_NAME

router = APIRouter(prefix="/mobility", tags=["mobility"])
SEOUL = "서울특별시"
GU_NAME_MAX = 16
LIMIT_DEFAULT, LIMIT_MAX = 2000, 5000
SGG_RE = r"^[0-9]{5}$"
NO_DATA_DETAIL = {"code": "no_module_data", "message": "mobility_stop is empty. Run `python -m app.modules.mobility`."}
DEFINITIONS = {
    "score": "추천 점수 (0~100): 실험의 탐욕(greedy) 선택 순서에서 얻은 상대 점수, 1위 = 100",
    "p65_within_walk": "정류장 도보권 안의 65세 이상 인구 (명, 실험의 보행 반경 기준)",
    "greedy_rank": "탐욕 선택 순위 (1 = 가장 먼저 뽑힌 정류장)",
    "marginal_gain": "그 정류장을 추가했을 때의 폭염 가중 한계이득 (실험 산출값 그대로)",
    "recommended": "상위 2,000곳 추천 목록에 든 정류장",
}


@router.get("/stops")
def stops(gu: str | None = Query(None, max_length=GU_NAME_MAX), sgg_code: str | None = Query(None, pattern=SGG_RE),
          limit: int = Query(LIMIT_DEFAULT, ge=1, le=LIMIT_MAX), conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    """Seoul recommended stops, optionally one 구 (`gu` name or `sgg_code`), best greedy_rank first."""
    if conn.execute("SELECT 1 FROM mobility_stop LIMIT 1").fetchone() is None:
        raise HTTPException(status.HTTP_409_CONFLICT, NO_DATA_DETAIL)
    where, args = ["sido = ?"], [SEOUL]
    if sgg_code is not None:
        region = conn.execute("SELECT name FROM region WHERE code = ? AND sido = ?", (sgg_code, SEOUL)).fetchone()
        if region is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "sgg_code is not a Seoul 구")
        gu = str(region["name"])
    if gu is not None:
        if conn.execute("SELECT 1 FROM region WHERE sido = ? AND name = ?", (SEOUL, gu)).fetchone() is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "gu not found")
        where.append("sgg = ?")
        args.append(gu)
    sql = ("SELECT stop_id, name, lat, lon, sgg, sgg_code, dong, score, p65_within_walk, greedy_rank, recommended, "
           f"marginal_gain FROM mobility_stop WHERE {' AND '.join(where)} ORDER BY greedy_rank, stop_id LIMIT ?")
    rows = conn.execute(sql, (*args, limit)).fetchall()
    total = int(conn.execute(f"SELECT COUNT(*) FROM mobility_stop WHERE {' AND '.join(where)}", args).fetchone()[0])
    items = [{**{k: r[k] for k in r.keys()}, "recommended": bool(r["recommended"])} for r in rows]
    ds = repo.get_dataset_by_name(conn, DATASET_NAME)
    return {
        "city": SEOUL, "gu": gu, "total": total, "returned": len(items), "items": items,
        "sources": [{"file": ds["file_name"], "sha256": ds["sha256"], "fetched_at": ds["fetched_at"]}] if ds else [],
        "definitions": DEFINITIONS,
        "scope": "서울 25개 구; 전국 상위 2,000곳 중 서울에 있는 정류장만 제공",
    }
