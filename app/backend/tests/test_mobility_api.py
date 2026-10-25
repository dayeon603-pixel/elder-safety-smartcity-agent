"""Mobility module: idempotent loader with provenance, Seoul-subset endpoint, migration checks."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import Settings
from app.main import create_app
from app.modules import mobility
from tests import heat_fixture as fx

HEADER = ("stop_id,name,lat,lon,sido,sgg,dong,score,p65_within_walk,greedy_rank,recommended,marginal_gain_heat_weighted\n")
ROWS = (
    "S1,종로1정류장,37.571,126.981,서울특별시,종로구,청운동,100.0,1042.6,1,True,1171.97\n"
    "S2,중구정류장,37.561,126.991,서울특별시,중구,명동,90.4,1094.3,2,True,1059.31\n"
    "S3,부산정류장,35.104,129.026,부산광역시,중구,보수동,80.0,900.0,3,True,\n"
    "S4,종로2정류장,37.572,126.982,서울특별시,종로구,사직동,70.0,500.0,4,False,10.0\n"
    "S5,모름,37.50,127.10,서울특별시,없는구,x,60.0,1.0,5,True,1.0\n"
)


def _write(d: Path, body: str = ROWS) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / mobility.FILE
    p.write_text(HEADER + body, encoding="utf-8-sig")
    return p


@pytest.fixture
def mclient(settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    out = tmp_path / "mob"
    _write(out)
    monkeypatch.setenv(mobility.MOBILITY_ENV, str(out))
    c = db.connect(settings.db_path)
    db.migrate(c)
    fx.build_heat_db(c, tmp_path / "out")
    assert mobility.load_mobility(c) == "loaded"
    c.close()
    with TestClient(create_app(settings)) as client:
        yield client


def test_loader_is_idempotent_and_tracks_sha(conn: sqlite3.Connection, tmp_path: Path) -> None:
    out = tmp_path / "mob"
    _write(out)
    with pytest.raises(mobility.MobilityLoadError, match="missing source file"):
        mobility.load_mobility(conn, tmp_path / "nowhere")
    fx.seed_base(conn)
    assert mobility.load_mobility(conn, out) == "loaded"
    rows = {r["stop_id"]: dict(r) for r in conn.execute("SELECT * FROM mobility_stop")}
    assert len(rows) == 5 and rows["S1"]["sgg_code"] == "11110" and rows["S2"]["sgg_code"] == "11120"
    assert rows["S3"]["sgg_code"] == "26110" and rows["S3"]["marginal_gain"] is None and rows["S5"]["sgg_code"] is None
    assert rows["S4"]["recommended"] == 0 and rows["S1"]["recommended"] == 1 and rows["S1"]["greedy_rank"] == 1
    ds = conn.execute("SELECT sha256, row_count, status FROM datasets WHERE name = ?", (mobility.DATASET_NAME,)).fetchone()
    assert len(ds["sha256"]) == 64 and ds["row_count"] == 5 and ds["status"] == "complete"
    assert mobility.load_mobility(conn, out) == "unchanged"
    assert mobility.load_mobility(conn, out, force=True) == "loaded"
    _write(out, ROWS + "S6,추가,37.57,126.98,서울특별시,종로구,y,50.0,2.0,6,True,1.0\n")
    assert mobility.load_mobility(conn, out) == "loaded"
    assert conn.execute("SELECT COUNT(*) FROM mobility_stop").fetchone()[0] == 6
    assert conn.execute("SELECT COUNT(*) FROM datasets WHERE name = ?", (mobility.DATASET_NAME,)).fetchone()[0] == 1


def test_loader_rejects_bad_rows_atomically(conn: sqlite3.Connection, tmp_path: Path) -> None:
    out = tmp_path / "mob"
    fx.seed_base(conn)
    _write(out)
    assert mobility.load_mobility(conn, out) == "loaded"
    _write(out, ROWS + "S7,나쁨,95.0,126.98,서울특별시,종로구,y,50.0,2.0,7,True,1.0\n")  # lat out of range
    with pytest.raises(mobility.MobilityLoadError):
        mobility.load_mobility(conn, out)
    assert conn.execute("SELECT COUNT(*) FROM mobility_stop").fetchone()[0] == 5  # the previous load survived
    _write(out, HEADER.replace("score,", "sc,") + "")
    (out / mobility.FILE).write_text("stop_id,name\n", encoding="utf-8-sig")
    with pytest.raises(mobility.MobilityLoadError, match="missing columns"):
        mobility.load_mobility(conn, out, force=True)
    _write(out, "S8,x,37.5,127.0,서울특별시,종로구,y,nan,2.0,1,True,1.0\n")
    with pytest.raises(mobility.MobilityLoadError, match="not finite"):
        mobility.load_mobility(conn, out, force=True)


def test_stops_endpoint_seoul_subset(mclient: TestClient) -> None:
    b = mclient.get("/mobility/stops").json()
    assert b["city"] == "서울특별시" and b["total"] == 4 and b["returned"] == 4  # the Busan stop is excluded
    assert [x["stop_id"] for x in b["items"]] == ["S1", "S2", "S4", "S5"] and b["items"][0]["recommended"] is True
    assert set(b["items"][0]) == {"stop_id", "name", "lat", "lon", "sgg", "sgg_code", "dong", "score", "p65_within_walk",
                                  "greedy_rank", "recommended", "marginal_gain"}
    assert b["sources"][0]["file"] == mobility.FILE and len(b["sources"][0]["sha256"]) == 64 and "score" in b["definitions"]
    gu = mclient.get("/mobility/stops?gu=종로구").json()
    assert gu["total"] == 2 and [x["stop_id"] for x in gu["items"]] == ["S1", "S4"] and gu["gu"] == "종로구"
    assert mclient.get("/mobility/stops?sgg_code=11120").json()["items"][0]["stop_id"] == "S2"
    assert mclient.get("/mobility/stops?gu=종로구&limit=1").json()["returned"] == 1
    assert mclient.get("/mobility/stops?gu=없는구").status_code == 404
    assert mclient.get("/mobility/stops?sgg_code=26110").status_code == 404  # not a Seoul 구
    assert mclient.get("/mobility/stops?limit=0").status_code == 422


def test_stops_endpoint_409_before_load(client: TestClient) -> None:
    r = client.get("/mobility/stops")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_module_data"
