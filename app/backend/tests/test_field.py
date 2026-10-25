"""Ops-map layers: /crosswalks, /aed, /shelters, /integrations/status, the disk cache and the warm-up."""

from __future__ import annotations

import gzip
import json
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import db
from app.actions import aed_points, bit, dongs, signals, warm
from app.actions.diskcache import cached
from app.config import Settings
from app.main import create_app
from app.medical.service import KST
from app.routers.field import shelter_open
from tests import heat_fixture as fx
from tests.test_actions_api import CODE, write_sources

AED_RECORDS = [
    {"serialSeq": "s1", "sido": "서울특별시", "gugun": "종로구", "org": "종로경로당", "buildPlace": "1층", "buildAddress": "a",
     "wgs84Lat": 37.566, "wgs84Lon": 126.976, "monSttTme": "0900", "monEndTme": 1800, "satSttTme": "0000", "satEndTme": 2400,
     "holSttTme": "0000", "holEndTme": 2400, "manager": "김**", "managerTel": "010-0000-0000"},
    {"serialSeq": "s2", "sido": "서울특별시", "gugun": "종로구", "org": "24시편의점", "buildPlace": "카운터", "buildAddress": "b",
     "wgs84Lat": 37.567, "wgs84Lon": 126.977, "monSttTme": "0000", "monEndTme": 2400, "satSttTme": "0000", "satEndTme": 2400,
     "sunSttTme": "0000", "sunEndTme": 2400, "holSttTme": "0000", "holEndTme": 2400},
    {"serialSeq": "s3", "sido": "서울특별시", "gugun": "종로구", "org": "시간미상", "buildPlace": "", "buildAddress": "c",
     "wgs84Lat": 37.568, "wgs84Lon": 126.978},
    {"serialSeq": "s4", "sido": "부산광역시", "gugun": "중구", "org": "부산AED", "buildPlace": "", "buildAddress": "d",
     "wgs84Lat": 35.1, "wgs84Lon": 129.03, "monSttTme": "0900", "monEndTme": 1800},
]


def _write_aeds(data: Path) -> None:
    d = data / aed_points.AED_DIR
    d.mkdir(parents=True, exist_ok=True)
    with gzip.open(d / "full_001.json.gz", "wt", encoding="utf-8") as fh:
        json.dump({"response": {"body": {"items": {"item": AED_RECORDS}}}}, fh, ensure_ascii=False)


@pytest.fixture
def fclient(settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(dongs, "EXPERIMENT_DIR", tmp_path / "no_rules")
    for clear in (dongs.clear_cache, bit.clear_cache, signals.clear_cache):
        clear()
    write_sources(settings.data_dir)
    _write_aeds(settings.data_dir)
    c = db.connect(settings.db_path)
    db.migrate(c)
    fx.build_heat_db(c, tmp_path / "out")
    ds = int(c.execute("SELECT id FROM datasets LIMIT 1").fetchone()[0])
    c.executemany(
        "INSERT INTO shelter (region_code, name, lat, lon, capacity, night_open, weekend_open, dataset_id) VALUES (?,?,?,?,?,?,?,?)",
        [(CODE, "밤쉼터", 37.566, 126.976, 10, 1, 1, ds), (CODE, "낮쉼터", 37.567, 126.977, 20, 0, 0, ds),
         (CODE, "미상쉼터", 37.568, 126.978, None, None, None, ds)],
    )
    c.commit()
    c.close()
    with TestClient(create_app(settings)) as client:
        yield client


def test_disk_cache_hits_and_invalidates(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    src.write_text("v1")
    calls: list[int] = []

    def build() -> dict[str, int]:
        calls.append(1)
        return {"n": len(calls)}

    assert cached("t", 1, [src], build, tmp_path / "c") == {"n": 1}
    assert cached("t", 1, [src], build, tmp_path / "c") == {"n": 1} and len(calls) == 1  # pickle hit
    assert cached("t", 2, [src], build, tmp_path / "c") == {"n": 2}  # code version bump rebuilds
    time.sleep(1.1)
    src.write_text("v2-changed")
    assert cached("t", 2, [src], build, tmp_path / "c") == {"n": 3}  # source change rebuilds
    assert len(list((tmp_path / "c").glob("t-*.pkl"))) == 1  # one version kept per table
    with pytest.raises(FileNotFoundError):
        cached("t", 2, [tmp_path / "missing"], build, tmp_path / "c")


def test_warm_builds_all_tables_and_reports_missing(settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dongs, "EXPERIMENT_DIR", tmp_path / "no_rules")
    for clear in (dongs.clear_cache, bit.clear_cache, signals.clear_cache):
        clear()
    write_sources(settings.data_dir)
    monkeypatch.setattr(warm, "PROBE_REGION", ("서울특별시", "종로구"))
    out = warm.warm(settings.data_dir)
    assert {k for k, v in out.items() if "ms" in v} == {"dongs", "stops", "crosswalks"} and "error" in out["aeds"]
    pkls = {p.name.split("-")[0] for p in (tmp_path / "actions_cache").glob("*.pkl")}
    assert pkls == {"dongs", "bus_stops", "crosswalks"}


def test_crosswalks_layer(fclient: TestClient) -> None:
    r = fclient.get(f"/crosswalks?sgg_code={CODE}")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["total"] == 2 and b["speed_m_s"] == 0.7 and b["with_signal_and_green"] == 2 and b["deficit_count"] == 1
    x1 = next(x for x in b["items"] if x["id"] == "X1")
    assert x1["length_m"] == 21 and x1["green_s"] == 20 and x1["need_s"] == 30.0 and x1["deficit_s"] == 10 and x1["has_signal"]
    assert x1["address"] == "지번1" and b["items"][0]["id"] == "X1"  # largest deficit first
    fast = fclient.get(f"/crosswalks?sgg_code={CODE}&speed=1.2").json()
    assert fast["deficit_count"] == 0
    assert fclient.get(f"/crosswalks?sgg_code={CODE}&speed=9").status_code == 422
    assert fclient.get("/crosswalks?sgg_code=99999").status_code == 404
    assert fclient.get("/crosswalks?sgg_code=26110").status_code == 503  # no 행정동 in the fixture


def test_aed_layer_hours_and_privacy(fclient: TestClient, settings: Settings) -> None:
    b = fclient.get(f"/aed?sgg_code={CODE}&day=weekday&hour=10").json()
    assert b["total"] == 3 and b["open"] == 2 and b["closed"] == 0 and b["unknown_hours"] == 1
    night = fclient.get(f"/aed?sgg_code={CODE}&day=weekday&hour=23").json()
    assert night["open"] == 1 and night["closed"] == 1 and night["items"][0]["org"] == "24시편의점"
    assert night["items"][0]["hours"] == "0000-2400" and night["items"][0]["lat"] == 37.567
    sun = fclient.get(f"/aed?sgg_code={CODE}&day=sun&hour=12").json()
    assert sun["open"] == 1 and sun["unknown_hours"] == 2  # 경로당 lists no Sunday hours
    hol = fclient.get(f"/aed?sgg_code={CODE}&day=holiday&hour=12").json()
    assert hol["open"] == 2
    assert "월요일" in " ".join(hol["rules"]) and fclient.get("/aed?sgg_code=26110&day=sat&hour=1").json()["total"] == 1
    dumped = json.dumps(aed_points.load_aeds(settings.data_dir).to_dict("records"), ensure_ascii=False)
    assert "manager" not in dumped and "010-0000" not in dumped and "김**" not in dumped


def test_shelters_layer_rules() -> None:
    wk_day = datetime(2024, 7, 2, 11, 0, tzinfo=KST)
    wk_night = datetime(2024, 7, 2, 22, 0, tzinfo=KST)
    sat_day = datetime(2024, 7, 6, 11, 0, tzinfo=KST)
    sat_night = datetime(2024, 7, 6, 22, 0, tzinfo=KST)
    hol_day = datetime(2026, 2, 17, 11, 0, tzinfo=KST)
    assert shelter_open(0, 0, wk_day) is True and shelter_open(None, None, wk_day) is True
    assert shelter_open(1, 0, wk_night) is True and shelter_open(0, 1, wk_night) is False and shelter_open(None, 1, wk_night) is None
    assert shelter_open(0, 1, sat_day) is True and shelter_open(1, 0, sat_day) is False and shelter_open(1, None, sat_day) is None
    assert shelter_open(1, 1, sat_night) is True and shelter_open(0, 1, sat_night) is False and shelter_open(None, 1, sat_night) is None
    assert shelter_open(1, 0, hol_day) is False


def test_shelters_endpoint(fclient: TestClient) -> None:
    b = fclient.get(f"/shelters?sgg_code={CODE}&at=2024-07-02T22:00").json()
    assert b["total"] == 3 and b["open"] == 1 and b["unknown"] == 1 and not b["is_holiday"]
    by = {x["name"]: x for x in b["items"]}
    assert by["밤쉼터"]["open_now"] is True and by["낮쉼터"]["open_now"] is False and by["미상쉼터"]["open_now"] is None
    assert by["미상쉼터"]["night_open"] is None and by["밤쉼터"]["lat"] == 37.566 and len(b["rules"]) == 3
    day = fclient.get(f"/shelters?sgg_code={CODE}&at=2024-07-02T11:00").json()
    assert day["open"] == 3
    assert fclient.get("/shelters?sgg_code=26110").json()["note"] is None  # fixture: 26110 has shelters too
    assert fclient.get("/shelters?sgg_code=11120").json()["note"].startswith("자료 없음")
    assert fclient.get("/shelters?sgg_code=11110&at=bad").status_code == 422


def test_integrations_status(fclient: TestClient) -> None:
    b = fclient.get("/integrations/status").json()
    assert b["webhook_configured"] is False and "POST /feedback" in b["trigger"] and "TTA" in b["spec_note"]


def test_briefing_actions_and_candidates_have_ids_and_coordinates(fclient: TestClient) -> None:
    r = fclient.get(f"/actions/{CODE}?date={fx.DATES[0]}&teams=1")
    assert r.status_code == 200
    body: dict[str, Any] = r.json()
    assert all({"lat", "lon"} <= set(s) for s in body["bit"]) and body["action_ids"]["bit"] == "bit"
