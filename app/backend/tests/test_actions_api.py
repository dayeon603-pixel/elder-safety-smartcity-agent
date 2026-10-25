"""GET /actions/{sgg_code} on the tiny heat fixture plus a hand-made data dir (one 구, two 행정동)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.actions import bit, dongs, signals
from app.actions import constants as C
from app.config import Settings
from app.heat import common as heat_common
from app.main import create_app
from tests import heat_fixture as fx

D0 = fx.DATES[0]
CODE = "11110"


def _poly(lon0: float, lat0: float, size: float = 0.01) -> list[list[list[float]]]:
    return [[[lon0, lat0], [lon0 + size, lat0], [lon0 + size, lat0 + size], [lon0, lat0 + size], [lon0, lat0]]]


def write_sources(data: Path) -> None:
    def put(rel: str, text: str, enc: str) -> None:
        p = data / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode(enc))

    feats = [
        {"type": "Feature", "properties": {"adm_cd2": f"1111{n}000", "sidonm": "서울특별시", "sggnm": "종로구",
                                           "adm_nm": f"서울특별시 종로구 {name}"},
         "geometry": {"type": "Polygon", "coordinates": _poly(lon, 37.56)}}
        for n, name, lon in (("053", "가동", 126.97), ("054", "나동", 126.98))
    ]
    put(heat_common.GEOJSON, json.dumps({"type": "FeatureCollection", "features": feats}), "utf-8")
    put(heat_common.POP_CSV, "행정기관코드,기준연월,계,0세,70세\n1111053000,202609,1000,400,600\n1111054000,202609,800,500,300\n", "utf-8-sig")
    put(
        C.BUS_STOPS_CSV,
        "정류장번호,정류장명,위도,경도\nS1,가1,37.5650,126.9750\nS2,가2,37.5651,126.9751\n"
        "S3,나1,37.5650,126.9850\nS4,바깥,37.7,126.9\nS5,불량,999,999\n",
        C.BUS_STOPS_ENCODING,
    )
    cols = "CTPRVN_NM,SIGNGU_NM,LNMADR,RDNMADR,CRSLK_MANAGE_NO,LATITUDE,LONGITUDE,ET,TFCLGHT_YN,GREEN_SGNGNR_TIME"
    put(
        C.CROSSWALK_CSV,
        f"{cols}\n서울특별시,종로구,지번1,,X1,37.565,126.975,21,Y,20\n서울특별시,종로구,지번2,,X2,37.565,126.985,7,Y,20\n",
        C.CROSSWALK_ENCODING,
    )


@pytest.fixture
def aclient(settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(dongs, "EXPERIMENT_DIR", tmp_path / "no_rules")  # plain 2026 시군구 units
    for clear in (dongs.clear_cache, bit.clear_cache, signals.clear_cache):
        clear()
    write_sources(settings.data_dir)
    c = db.connect(settings.db_path)
    db.migrate(c)
    fx.build_heat_db(c, tmp_path / "out")
    ds = int(c.execute("SELECT id FROM datasets LIMIT 1").fetchone()[0])
    c.execute(
        "INSERT INTO shelter (region_code, name, lat, lon, capacity, night_open, weekend_open, dataset_id) "
        "VALUES (?, '종로쉼터', 37.566, 126.976, 10, 1, 1, ?)", (CODE, ds),
    )
    c.commit()
    c.close()
    with TestClient(create_app(settings)) as client:
        yield client


def test_shape_and_values(aclient: TestClient) -> None:
    r = aclient.get(f"/actions/{CODE}", params={"date": D0, "teams": 1})
    assert r.status_code == 200, r.text
    b = r.json()
    assert set(b) == {"sgg_code", "region_name", "context", "cbs", "bit", "bit_info", "signals",
                      "signals_coverage", "route", "sources", "disclaimer", "evidence", "action_ids"}
    assert b["action_ids"] == {"cbs": "cbs", "bit": "bit", "signals": "signals", "route": "route"}
    ev = b["evidence"]
    assert ev["apparent_max_c"] == 35.1 and ev["tmax_c"] == 33.5 and ev["station"]["id"] == "47108"
    assert ev["pop65"] == b["context"]["pop65"] and ev["rank"] == b["context"]["rank"]
    assert ev["expected_cases_lambda"] == b["context"]["score"]
    cbs = b["cbs"]
    assert set(cbs) >= {"text", "easy_text", "length", "limit", "limit_source"}
    assert cbs["limit"] == 157 and cbs["length"] == len(cbs["text"]) <= 157 and len(cbs["easy_text"]) <= 157
    assert "종로쉼터(야간·주말 운영)" in cbs["text"] and "119" in cbs["text"] and "℃" in cbs["text"]
    assert b["context"]["tmax_c"] == 33.5 and b["context"]["apparent_max_c"] == 35.1 and b["context"]["date"] == D0
    assert "체감 최고 35℃ 폭염" in cbs["text"] and "체감 온도가 35도까지" in cbs["easy_text"]
    assert b["bit"][0]["message"]["lines"][0] == "오늘 체감 35℃ 폭염"
    # S1+S2 are 14 m apart: the first pick covers both 가동 stops (600 = its whole 65+); 나동 adds 300
    assert [s["stop_id"] for s in b["bit"]] == ["S1", "S3"]
    assert b["bit"][0]["covered_pop65_est"] == 600 and b["bit"][1]["covered_pop65_est"] == 300
    assert all(len(line) <= 15 for s in b["bit"] for line in s["message"]["lines"])
    assert b["signals"][0]["crosswalk_id"] == "X1" and b["signals"][0]["extend_s"] == 10
    assert b["route"]["distance_basis"] == "직선거리 기준" and len(b["route"]["stops"]) == 2
    assert b["disclaimer"].startswith("모든 출력은")


def test_allocation_flag_matches_heat_plan(aclient: TestClient) -> None:
    plan = aclient.get("/heat/plan", params={"date": D0, "teams": 1}).json()
    funded = {a["code"] for a in plan["allocations"]}
    r = aclient.get(f"/actions/{CODE}", params={"date": D0, "teams": 1}).json()
    assert r["route"]["allocated"] is (CODE in funded) and r["context"]["team_allocated"] is (CODE in funded)


def test_legacy_limit_90(aclient: TestClient) -> None:
    b = aclient.get(f"/actions/{CODE}", params={"date": D0, "cbs_limit": 90}).json()["cbs"]
    assert b["limit"] == 90 and b["length"] <= 90 and len(b["easy_text"]) <= 90
    assert "90" in b["limit_source"]


def test_region_without_signal_data_says_so(aclient: TestClient, settings: Settings) -> None:
    (settings.data_dir / C.CROSSWALK_CSV).write_bytes(
        ("CTPRVN_NM,SIGNGU_NM,LNMADR,RDNMADR,CRSLK_MANAGE_NO,LATITUDE,LONGITUDE,ET,TFCLGHT_YN,GREEN_SGNGNR_TIME\n"
         "경기도,수원시,x,,Z,37.2,127.0,10,Y,10\n").encode("utf-8-sig")
    )
    signals.clear_cache()
    b = aclient.get(f"/actions/{CODE}", params={"date": D0}).json()
    assert b["signals"] == {"note": b["signals"]["note"]} and "자료 없음" in b["signals"]["note"]
    assert b["signals_coverage"]["n_crosswalks_in_region"] == 0


def test_errors(aclient: TestClient) -> None:
    assert aclient.get("/actions/99999", params={"date": D0}).status_code == 404
    assert aclient.get("/actions/abc", params={"date": D0}).status_code == 422
    assert aclient.get(f"/actions/{CODE}").status_code == 422  # date required
    for bad in ("2024-13-01", "2024-7-1", "2024-12-25", "x"):
        assert aclient.get(f"/actions/{CODE}", params={"date": bad}).status_code == 422, bad
    assert aclient.get(f"/actions/{CODE}", params={"date": "2023-07-01"}).status_code == 422  # not a test year
    for q in ({"teams": 0}, {"teams": 101}, {"cbs_limit": 100}, {"bit_top_n": 0}, {"route_stops": 1}):
        assert aclient.get(f"/actions/{CODE}", params={"date": D0, **q}).status_code == 422, q


def test_missing_source_is_503(aclient: TestClient, settings: Settings) -> None:
    (settings.data_dir / C.BUS_STOPS_CSV).unlink()
    bit.clear_cache()
    r = aclient.get(f"/actions/{CODE}", params={"date": D0})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "data_unavailable"


def test_no_completed_run_is_409(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        r = client.get(f"/actions/{CODE}", params={"date": D0})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_completed_run"
