"""Digital-twin endpoints: scene build/serve, hourly state, timeline, routing, terminal channel, rain days, actions, verify."""

from __future__ import annotations

import gzip
import json
import os
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import shapely
from fastapi.testclient import TestClient
from shapely.geometry import MultiPolygon, Polygon

from app import db
from app.actions import bit, dongs, signals
from app.agent import cap_audio
from app.config import Settings, load_settings
from app.heat import verify as heat_verify
from app.main import create_app
from app.twin import constants as C
from app.twin import actions as twin_actions_module
from app.twin import cache as twin_cache
from app.twin import effects as twin_effects
from app.twin import infra as twin_infra
from app.twin import tiles as twin_tiles
from app.twin import lan as twin_lan
from app.twin import gov as gov_module
from app.twin import population as twin_pop
from app.twin import rain, route as twin_route, scene_build, state as twin_state, store, terminal, weather
from tests import heat_fixture as fx
from tests.conftest import TEST_SECRET
from tests.test_actions_api import CODE, write_sources
from tests.test_field import _write_aeds

DATE = fx.DATES[0]  # 2024-07-01 (a Monday)
TOKEN = "twin-demo-token-0123456789"
STATION = "47108"  # the fixture's station id (real KMA files use the bare WMO suffix, e.g. 108)
# walk chain n1 -> n2 -> n3 -> n4 along the diagonal inside 가동 (126.97..126.98, 37.56..37.57)
NODES = {"n1": (126.9750, 37.5650), "n2": (126.9760, 37.5660), "n3": (126.9770, 37.5670), "n4": (126.9780, 37.5680)}
GRAPHML_HEAD = ('<?xml version="1.0" encoding="utf-8"?><graphml xmlns="http://graphml.graphdrawing.org/xmlns">'
                '<key id="d5" for="node" attr.name="x" attr.type="string"/><key id="d4" for="node" attr.name="y" attr.type="string"/>'
                '<key id="d16" for="edge" attr.name="length" attr.type="string"/><key id="d17" for="edge" attr.name="geometry" attr.type="string"/>'
                '<graph edgedefault="directed">')


def _edge_len(a: str, b: str) -> float:
    from app.actions.geometry import haversine_m

    (x1, y1), (x2, y2) = NODES[a], NODES[b]
    return haversine_m(y1, x1, y2, x2)


def write_graphml(data: Path) -> None:
    parts = [GRAPHML_HEAD]
    for n, (x, y) in NODES.items():
        parts.append(f'<node id="{n}"><data key="d5">{x}</data><data key="d4">{y}</data></node>')
    chain = [("n1", "n2"), ("n2", "n3"), ("n3", "n4")]
    for a, b in chain:
        length = _edge_len(a, b)
        geom = ""
        if (a, b) == ("n2", "n3"):  # one edge with explicit geometry (a bend)
            (x1, y1), (x2, y2) = NODES[a], NODES[b]
            geom = f'<data key="d17">LINESTRING ({x1} {y1}, {x1} {y2}, {x2} {y2})</data>'
        for u, v in ((a, b), (b, a)):  # osmnx stores both directions
            parts.append(f'<edge source="{u}" target="{v}"><data key="d16">{length:.3f}</data>{geom}</edge>')
    parts.append('<edge source="n1" target="n1"><data key="d16">1.0</data></edge>')  # self loop, dropped
    parts.append("</graph></graphml>")
    p = data / C.WALK_GRAPHML
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(parts), encoding="utf-8")


def _square(lon: float, lat: float, size: float = 0.0004) -> Polygon:
    return Polygon([(lon, lat), (lon + size, lat), (lon + size, lat + size), (lon, lat + size), (lon, lat)])


def write_buildings(data: Path, path: Path | None = None, extra_columns: bool = False) -> Path:
    import pyarrow as pa
    import pyarrow.parquet as pq

    donut = Polygon(_square(126.9765, 37.5655, 0.0006).exterior.coords, [list(_square(126.9767, 37.5657, 0.0002).exterior.coords)])
    rows = [
        ("house", "3", _square(126.9752, 37.5652)),  # tagged levels
        ("house", None, _square(126.9756, 37.5652)),  # imputed from the house median (3)
        ("church", None, _square(126.9760, 37.5652)),  # imputed from the global median
        ("yes", "2;3", MultiPolygon([_square(126.9770, 37.5660), _square(126.9775, 37.5660)])),  # split in two
        ("yes", "150", donut),  # implausible levels -> imputed; one hole
        ("house", "4", _square(127.5, 37.5)),  # outside the 구
    ]
    cols: dict[str, Any] = {
        "building": [r[0] for r in rows], "building:levels": [r[1] for r in rows],
        "geometry": [shapely.to_wkb(r[2]) for r in rows],
    }
    if extra_columns:
        cols = {"MAIN_PURPS_CD_NM": cols["building"], "GRND_FLR_CO": cols["building:levels"], "geom": cols["geometry"]}
    out = path or data / C.BUILDINGS_PARQUET
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(cols), out)
    return out


def write_weather(data: Path) -> None:
    def line(stamp: str, ta: float, hm: float, rn: float) -> str:
        toks = [stamp, STATION] + ["-9"] * 9 + [f"{ta}", "-9", f"{hm}", "-9", f"{rn}", "-9", "-9"]
        return " ".join(toks)

    hourly = data / C.HOURLY_RAIN_DIR
    hourly.mkdir(parents=True, exist_ok=True)
    (hourly / "2024070123.txt").write_text("#START\n" + line("202407012300", 27.3, 82.0, -9) + "\n", encoding="euc-kr")
    (hourly / "2025070121.txt").write_text(line("202507012100", 24.0, 95.0, 15.5) + "\n", encoding="euc-kr")
    (hourly / "2025070122.txt").write_text(line("202507012200", 24.0, 95.0, 3.0) + "\n", encoding="euc-kr")
    (hourly / "2025070203.txt").write_text(line("202507020300", 23.0, 90.0, 11.0) + "\n", encoding="euc-kr")
    (hourly / "2025070503.txt").write_text(line("202507050300", 23.0, 90.0, 0.0) + "\n", encoding="euc-kr")
    ms = data / C.METEOSTAT_DIR
    ms.mkdir(parents=True, exist_ok=True)
    with gzip.open(ms / f"{C.METEOSTAT_WMO_PREFIX}{STATION}.csv.gz", "wt", encoding="utf-8") as fh:
        fh.write("date,tavg,tmin,tmax,prcp,snow,wdir,wspd,wpgt,pres,tsun\n2023-07-01,28,24,33,45.5,,,,,,\n2023-07-02,28,24,33,2.0,,,,,,\n"
                 "2024-07-01,29,25,34,0.5,,,,,,\n")


def seed_modules(settings: Settings) -> None:
    c = db.connect(settings.db_path)
    db.migrate(c)
    fx.build_heat_db(c, settings.db_path.parent / "out")
    ds = int(c.execute("SELECT id FROM datasets LIMIT 1").fetchone()[0])
    c.executemany(
        "INSERT INTO shelter (region_code, name, lat, lon, capacity, night_open, weekend_open, dataset_id) VALUES (?,?,?,?,?,?,?,?)",
        [(CODE, "밤쉼터", 37.566, 126.976, 10, 1, 1, ds), (CODE, "낮쉼터", 37.567, 126.977, 20, 0, 0, ds),
         ("11120", "신대방테스트경로당", 37.5661, 126.9761, 30, 1, 1, ds)],  # a 중구 shelter sitting next to 종로's: must never be named for 종로
    )
    c.executemany(
        "INSERT INTO silver_site (kind, lat, lon, sido, name, sgg_code, sgg_method, cur_within_300m, ours_within_300m) VALUES (?,?,?,?,?,?,?,?,?)",
        [("current", 37.5652, 126.9752, "서울특별시", "사직경로당", CODE, "polygon", 1, 0),
         ("ours", 37.5660, 126.9760, "서울특별시", None, CODE, "polygon", 0, 1)],
    )
    c.executemany(
        "INSERT INTO silver_hotspot_2024 (lat, lon, casualties, sgg_code, sgg_method, covered_current, covered_ours) VALUES (?,?,?,?,?,?,?)",
        [(37.5653, 126.9753, 7, CODE, "polygon", 1, 1), (37.5661, 126.9761, 4, CODE, "polygon", 0, 1), (37.56, 126.99, 2, "11120", "polygon", 0, 0)],
    )
    c.execute("INSERT INTO mobility_stop (stop_id, name, lat, lon, sido, sgg, dong, sgg_code, score, p65_within_walk, greedy_rank, "
              "recommended, marginal_gain, dataset_id) VALUES ('S1','가1',37.5650,126.9750,'서울특별시','종로구','가동',?,100,120,1,1,0.5,?)", (CODE, ds))
    c.execute("INSERT INTO aed_gu_gap (gu, sgg_code, source_code, n_aed, n_aed_24h_weekday, night_open_share, day_open_share, gap_score, "
              "public_arrests_night) VALUES ('종로구', ?, '11010', 3, 1, 0.4, 0.7, 9.7, 17.2)", (CODE,))
    c.execute("INSERT INTO aed_candidate (list_kind, serial_seq, gu, rank_in_gu, org, address, place, weekday_hours, night_open_weekday_share, "
              "gain_score, lat, lon) VALUES ('top5_per_gu', 's1', '종로구', 1, '종로경로당', 'a', '1층', '0900-1800', 0.0, 12.5, 37.566, 126.976)")
    c.commit()
    c.close()


@pytest.fixture
def tsettings(tmp_path: Path) -> Settings:
    data = tmp_path / "cache"
    data.mkdir()
    return load_settings({
        "KCF_SECRET_KEY": TEST_SECRET, "KCF_DB_PATH": str(tmp_path / "api.sqlite3"), "KCF_DATA_DIR": str(data),
        "KCF_RATE_CAPACITY": "1000", "KCF_RATE_REFILL_PER_S": "1000", "KCF_DEMO_TOKEN": TOKEN,
        "KCF_SIM_RATE_CAPACITY": "1000", "KCF_SIM_RATE_REFILL_PER_S": "1000",
    })


@pytest.fixture
def tclient(tsettings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(dongs, "EXPERIMENT_DIR", tmp_path / "no_rules")
    monkeypatch.setenv(C.CACHE_DIR_ENV, str(tmp_path / "twin_cache"))
    monkeypatch.setenv("KCF_HEAT_OUT_DIR", str(tsettings.db_path.parent / "out"))
    for clear in (dongs.clear_cache, bit.clear_cache, signals.clear_cache, store.clear_cache, twin_state.clear_cache,
                  twin_route.clear_cache, weather.clear_cache, twin_pop.clear_cache, twin_cache.clear, twin_infra.clear_cache):
        clear()
    write_sources(tsettings.data_dir)
    _write_aeds(tsettings.data_dir)
    write_graphml(tsettings.data_dir)
    write_buildings(tsettings.data_dir)
    write_weather(tsettings.data_dir)
    seed_modules(tsettings)
    with TestClient(create_app(tsettings)) as client:
        yield client


H = {"X-Demo-Token": TOKEN}


# --- scene -----------------------------------------------------------------------------------------------------


def test_scene_builds_once_then_serves_gzip_with_etag(tclient: TestClient, tmp_path: Path) -> None:
    r = tclient.get(f"/twin/{CODE}/scene", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers["x-twin-cache"] == "exact" and r.headers.get("content-encoding") == "gzip"
    doc = r.json()  # httpx decodes gzip
    b = doc["buildings"]
    assert b["count"] == 6  # 5 inside: 3 squares + 2 multipolygon parts + donut = 6 polygons; the 127.5 one is out
    assert len(b["start_indices"]) == b["count"] + 1 and len(b["positions"]) == 2 * b["start_indices"][-1]
    assert b["heights"][0] == pytest.approx(3 * C.LEVEL_HEIGHT_M) and b["height_source"][0] == 0
    # only 3 footprints inside the 구 have floors (3, 2, 2) -> too few for area bands -> global median 2 (code 3)
    assert b["heights"][1] == pytest.approx(2 * C.LEVEL_HEIGHT_M) and b["height_source"][1] == 3
    assert b["heights"][2] == pytest.approx(2 * C.LEVEL_HEIGHT_M) and b["height_source"][2] == 3
    assert b["holes"]["count"] == 1 and b["holes"]["parent"] == [5]
    assert b["height_source_counts"] == {"floors": 3, "source_height": 0, "imputed_area": 0, "imputed_global": 3}
    assert b["use"] == [0, 0, 4, 5, 5, 5] and b["use_names"]["5"] == "미상" and b["use_counts"]["단독·다가구주택"] == 2
    assert b["measured_pct"] == 50.0 and b["imputation"]["n_known"] == 3 and doc["coordinate_system"] == "METER_OFFSETS"
    assert doc["roads"]["count"] == 3 and doc["roads"]["nodes"] == 4  # self loop and reverse duplicates dropped
    assert doc["boundary"]["rings"] >= 1 and doc["sources"][0]["file"] == "seoul_buildings.parquet" and doc["fetched_at"]
    # positions are metres from the centre: the first vertex of the first square sits south-west of the centre
    x0, y0 = b["positions"][0], b["positions"][1]
    assert abs(x0) < 2000 and abs(y0) < 2000
    etag = r.headers["etag"]
    r2 = tclient.get(f"/twin/{CODE}/scene", headers={"If-None-Match": etag})
    assert r2.status_code == 304
    r3 = tclient.get(f"/twin/{CODE}/scene", headers={"Accept-Encoding": "identity"})
    assert r3.status_code == 200 and "content-encoding" not in r3.headers and r3.json()["buildings"]["count"] == 6
    meta = tclient.get(f"/twin/{CODE}/scene?meta=1").json()
    assert meta["buildings"] == 6 and meta["cache"] == "exact"
    assert sorted(p.name[:5] for p in (tmp_path / "twin_cache").iterdir()) == ["graph", "scene"]


def test_scene_unknown_gu_404_and_missing_sources_409(tclient: TestClient, tsettings: Settings) -> None:
    assert tclient.get("/twin/99999/scene").status_code == 404
    assert tclient.get("/twin/11120/scene").status_code == 409  # 중구 has no 행정동 in the fixture -> cannot build
    (tsettings.data_dir / C.WALK_GRAPHML).unlink()
    store.clear_cache()
    r = tclient.get("/twin/11120/scene")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "twin_scene_not_built"


def test_scene_rebuilds_when_an_official_building_file_appears(tclient: TestClient, tsettings: Settings) -> None:
    first = tclient.get(f"/twin/{CODE}/scene?meta=1").json()
    official = write_buildings(tsettings.data_dir, tsettings.data_dir / "extra/buildings/mlit_seoul.parquet", extra_columns=True)
    (official.parent / "sources.json").write_text(json.dumps({"source": "국토교통부 건축물대장 외곽선", "fetched_at": "2026-10-24"}), encoding="utf-8")
    second = tclient.get(f"/twin/{CODE}/scene?meta=1").json()
    assert second["cache_key"] != first["cache_key"] and second["buildings"] == 6
    assert second["sources"][0]["file"] == "mlit_seoul.parquet" and second["sources"][0]["sources_json"]["source"].startswith("국토교통부")


def test_area_bands_fit_and_impute() -> None:
    rng = np.random.default_rng(0)
    areas = np.concatenate([rng.uniform(50, 100, 40), rng.uniform(1000, 2000, 40)])
    levels = np.concatenate([np.full(40, 2.0), np.full(40, 15.0)])
    bands = scene_build.AreaBands.fit(areas.tolist(), levels.tolist())
    assert bands.n_known == 80 and len(bands.edges) == C.AREA_BANDS - 1 and len(bands.medians) == C.AREA_BANDS
    assert bands.impute(60.0) == (2.0, 2) and bands.impute(1500.0) == (15.0, 2) and bands.impute(1e9) == (15.0, 2)
    assert bands.table()[0]["area_m2_from"] == 0.0 and bands.table()[-1]["area_m2_to"] is None
    few = scene_build.AreaBands.fit([10.0, 20.0], [3.0, 5.0])
    assert few.impute(15.0) == (4.0, 3)
    sq = _square(126.97, 37.56, 0.001)  # ~ 88 m x 111 m
    area = scene_build.footprint_area_m2(np.asarray(sq.exterior.coords), 37.56)
    assert 9000 < area < 10500


def test_wkb_and_levels_parsers() -> None:
    poly = _square(126.97, 37.56)
    out = scene_build.parse_wkb_polygons(shapely.to_wkb(poly))
    assert len(out) == 1 and out[0][0].shape == (5, 2) and out[0][1] == ()
    big = scene_build.parse_wkb_polygons(shapely.to_wkb(poly, byte_order=0))  # big-endian
    assert np.allclose(big[0][0], out[0][0])
    with pytest.raises(ValueError):
        scene_build.parse_wkb_polygons(shapely.to_wkb(shapely.Point(1, 2)))
    with pytest.raises(ValueError):
        scene_build.parse_wkb_polygons(b"\x01\x03\x00\x00\x00\x05")
    assert scene_build.parse_levels("3") == 3 and scene_build.parse_levels(" 2;3") == 2 and scene_build.parse_levels("3.5") == 3.5
    assert scene_build.parse_levels(None) is None and scene_build.parse_levels("many") is None and scene_build.parse_levels("0") is None
    assert scene_build.parse_levels("999") is None


# --- state / timeline ---------------------------------------------------------------------------------------------


def test_state_day_and_night(tclient: TestClient) -> None:
    day = tclient.get(f"/twin/{CODE}/state?date={DATE}&hour=10").json()
    assert day["daytype"] == "weekday" and day["summary"]["aed_total"] == 3 and day["summary"]["aed_open"] == 2
    assert {a["id"]: a["open"] for a in day["aed"]} == {"s1": True, "s2": True, "s3": None}
    assert day["aed_before_after"]["with_24h_conversions"]["n_converted"] == 1
    assert [s["open"] for s in day["shelters"]] == [True, True]  # weekday daytime: open by rule
    assert day["summary"]["crosswalks_total"] == 2 and day["summary"]["crosswalks_short"] == 1  # X2: 7 m needs 10 s > 20? no: X1 21 m needs 30 s > 20
    assert day["silver_eval"] == {**day["silver_eval"], "scope": "이 구", "n_hotspots": 2, "covered_current": 1, "covered_ours": 2,
                                  "pct_current": 50.0, "pct_ours": 100.0}
    assert day["silver_eval_seoul"]["scope"] == "서울 전체" and day["silver_eval_seoul"]["n_hotspots"] == 3
    assert day["silver_eval"]["designation"].startswith("노인보호구역은 시장등이 경찰과 협의해 지정")
    assert day["silver_eval_seoul"]["seoul_zones_current"] == 1 and day["silver_eval_seoul"]["seoul_zones_ours"] == 1 and day["silver_eval_seoul"]["caption_note"]
    assert day["silver_eval_seoul"]["pct_ours"] == pytest.approx(66.7)
    assert day["stops_recommended"][0]["stop_id"] == "S1" and day["heat"]["ours_alert"] is False and day["heat"]["b1_alert"] is True
    assert day["heat"]["apparent_max_c"] == 35.1 and day["weather"]["resolution"] == "daily" and day["weather"]["rain_mm"] == 0.5
    assert day["sources"] and day["fetched_at"]
    night = tclient.get(f"/twin/{CODE}/state?date={DATE}&hour=23").json()
    assert night["summary"]["aed_open"] == 1 and night["summary"]["night"] is True
    assert night["aed_before_after"]["open"] == 1 and night["aed_before_after"]["with_24h_conversions"]["open"] == 2
    assert next(a for a in night["aed"] if a["id"] == "s1")["open_after"] is True
    assert night["weather"] == {**night["weather"], "resolution": "hourly", "temp": 27.3, "rh": 82.0, "rain_mm": 0.0}
    assert night["weather"]["feels"] == pytest.approx(float(weather.summer_apparent_temp(27.3, 82.0)), abs=0.1)
    assert [s["open"] for s in night["shelters"]] == [False, True]  # 낮쉼터 (night_open=0), 밤쉼터 (night_open=1)


def test_state_hour_24_rolls_over_and_daytype_overrides(tclient: TestClient) -> None:
    r = tclient.get(f"/twin/{CODE}/state?date={DATE}&hour=24").json()
    assert r["date"] == "2024-07-02" and r["hour"] == 0 and r["requested"] == {"date": DATE, "hour": 24}
    sat = tclient.get(f"/twin/{CODE}/state?date={DATE}&hour=10&daytype=sat").json()
    assert sat["daytype"] == "sat" and {a["id"]: a["open"] for a in sat["aed"]}["s1"] is True  # sat 0000-2400
    hol = tclient.get(f"/twin/{CODE}/state?date=2024-08-15&hour=10").json()
    assert hol["daytype"] == "holiday" and hol["is_holiday"] is True
    assert tclient.get(f"/twin/{CODE}/state?date={DATE}&hour=25").status_code == 422
    assert tclient.get(f"/twin/{CODE}/state?date=2024-02-30&hour=1").status_code == 422
    assert tclient.get(f"/twin/{CODE}/state?date={DATE}&hour=1&daytype=mon").status_code == 422
    assert tclient.get(f"/twin/99999/state?date={DATE}&hour=1").status_code == 404
    assert tclient.get(f"/twin/26110/state?date={DATE}&hour=1").json()["notes"]  # 부산 중구: no AED cache rows


def test_timeline_covers_06_to_24(tclient: TestClient) -> None:
    t = tclient.get(f"/twin/{CODE}/timeline?date={DATE}").json()
    hours = t["hours"]
    assert [h["hour"] for h in hours] == list(range(6, 25)) and hours[-1]["date"] == "2024-07-02" and hours[-1]["clock_hour"] == 0
    assert hours[4]["aed_open"] == 2 and hours[-2]["aed_open"] == 1 and hours[-2]["weather_resolution"] == "hourly"
    assert t["heat"]["b1_alert"] is True and t["aed_total"] == 3


def test_resolve_clock_and_shelter_rules() -> None:
    assert twin_state.resolve_clock("2024-07-06", 10, None) == ("2024-07-06", 10, "sat")
    assert twin_state.resolve_clock("2024-07-07", 24, "sat") == ("2024-07-08", 0, "weekday")
    with pytest.raises(ValueError):
        twin_state.resolve_clock("2024-7-1", 1, None)
    assert twin_state.shelter_open(None, None, False, 12) is True and twin_state.shelter_open(None, None, False, 23) is None
    assert twin_state.shelter_open(1, 0, True, 12) is False and twin_state.shelter_open(1, 1, True, 23) is True
    assert twin_state.default_daytype(date(2024, 10, 3)) == "holiday"


# --- route -----------------------------------------------------------------------------------------------------


def test_route_to_nearest_open_aed_by_hour(tclient: TestClient) -> None:
    q = f"lat={NODES['n1'][1] + 0.00005}&lon={NODES['n1'][0] + 0.00005}&date={DATE}"
    day = tclient.get(f"/twin/route?{q}&hour=10&sgg_code={CODE}").json()
    assert day["aed"]["id"] == "s1" and day["network_meters"] == pytest.approx(_edge_len("n1", "n2"), abs=0.5)
    assert day["path"][0] == [round(NODES["n1"][0] + 0.00005, 6), round(NODES["n1"][1] + 0.00005, 6)] and day["path"][-1] == [126.976, 37.566]
    assert day["seconds"] == round(day["meters"] / C.WALK_SPEED_M_S) and day["open_aeds_considered"] == 2
    night = tclient.get(f"/twin/route?{q}&hour=23&sgg_code={CODE}").json()
    assert night["aed"]["id"] == "s2" and night["network_meters"] > day["network_meters"]
    assert len(night["path"]) == 2 + 4  # start, n1, n2, bend, n3, aed (edge n2->n3 has a 3-point geometry)
    assert night["sources"] and night["fetched_at"]
    far = tclient.get(f"/twin/route?lat=37.7&lon=126.9&date={DATE}&hour=10&sgg_code={CODE}")
    assert far.status_code == 422
    assert tclient.get(f"/twin/route?{q}&hour=10&sgg_code=99999").status_code == 404
    assert tclient.get(f"/twin/route?{q}&hour=10&sgg_code=11120").status_code == 409  # no scene possible


def test_route_409_when_no_open_aed(tclient: TestClient, tsettings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.actions import aed_points

    monkeypatch.setattr(aed_points, "open_state", lambda *_: False)
    twin_route.clear_cache()
    r = tclient.get(f"/twin/route?lat={NODES['n1'][1]}&lon={NODES['n1'][0]}&date={DATE}&hour=10&sgg_code={CODE}")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_open_aed"


def test_walk_index_dijkstra_unreachable() -> None:
    idx = store.WalkIndex(sgg_code="x", node_lon=np.zeros(3), node_lat=np.zeros(3), node_x=np.array([0.0, 10.0, 1e6]),
                          node_y=np.zeros(3), indptr=np.array([0, 1, 2, 2]), nbr=np.array([1, 0]), w=np.array([10.0, 10.0]),
                          eid=np.array([0, 0]), rev=np.array([0, 1]), road_flat=np.array([0.0, 0.0, 10.0, 0.0]),
                          road_start=np.array([0, 2]), center=(127.0, 37.5), bbox=(0, 0, 0, 0), built_at="t")
    assert idx.shortest_to_any(0, {2: 1}) is None
    assert idx.shortest_to_any(0, {1: 1}) == (1, 10.0, [0])
    assert idx.nearest(127.0, 37.5)[0] == 0


# --- terminal ----------------------------------------------------------------------------------------------------


def test_terminal_publish_requires_token_and_replays_last(tclient: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cap_audio, "available", lambda: False)
    body = {"kind": "heat", "title": "폭염 주의", "body": "쉼터에서 쉬세요."}
    assert tclient.post("/terminal/publish", json=body).status_code == 401
    assert tclient.get("/terminal/last").json()["message"] is None
    r = tclient.post("/terminal/publish", json=body, headers=H)
    assert r.status_code == 201 and r.json()["id"] == 1 and r.json()["audio_url"] is None and r.json()["drill"]
    r2 = tclient.post("/terminal/publish", json={**body, "kind": "fall", "stop_id": "S2", "audio_url": "/cap/alerts/x/audio.wav"}, headers=H)
    assert r2.status_code == 201 and r2.json()["audio_url"] == "/cap/alerts/x/audio.wav"
    assert tclient.get("/terminal/last?stop_id=S1").json()["message"]["id"] == 1  # S2's message is not for S1
    assert tclient.get("/terminal/last?stop_id=S2").json()["message"]["id"] == 2
    assert tclient.get("/terminal/last").json()["message"]["id"] == 2
    with tclient.stream("GET", "/terminal/stream?stop_id=S2") as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        lines = []
        for ln in resp.iter_lines():
            lines.append(ln)
            if ln.startswith("data:"):
                break
    assert lines[0] == ": connected" and lines[1] == "retry: 1000" and "id: 2" in lines
    msg = json.loads(lines[-1][len("data: "):])
    assert msg["kind"] == "fall" and msg["replay"] is True and msg["stop_id"] == "S2"
    assert tclient.post("/terminal/publish", json={**body, "kind": "nope"}, headers=H).status_code == 422
    assert tclient.post("/terminal/publish", json={**body, "audio_url": "http://evil/x.wav"}, headers=H).status_code == 422
    assert tclient.get("/terminal/audio/1.wav").status_code == 404


def test_terminal_stream_survives_keepalives_and_delivers_later_messages(tclient: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading
    import time

    monkeypatch.setattr(C, "TERMINAL_KEEPALIVE_S", 0.15)
    hub: terminal.TerminalHub = tclient.app.state.terminal_hub  # type: ignore[attr-defined]
    assert hub.subscriber_count() == 0
    timer = threading.Timer(0.6, lambda: hub.publish({"kind": "rain", "title": "비", "body": "조심", "stop_id": None}))
    t0 = time.perf_counter()
    with tclient.stream("GET", "/terminal/stream?stop_id=S1") as resp:
        lines: list[str] = []
        for ln in resp.iter_lines():
            if not lines:
                timer.start()  # headers + first chunk arrived before anything was published
            lines.append(ln)
            if ln.startswith("data:"):
                break
    elapsed = time.perf_counter() - t0
    assert lines[0] == ": connected" and lines[1] == "retry: 1000"
    assert lines.count(": keep-alive") >= 2 and elapsed >= 0.5  # the stream stayed open across several keep-alives
    msg = json.loads(lines[-1][len("data: "):])
    assert msg["kind"] == "rain" and "replay" not in msg
    assert hub.subscriber_count() == 0  # detached on close


def test_terminal_hub_filters_and_message_builders() -> None:
    hub = terminal.TerminalHub()
    m = hub.publish({"kind": "clear", "title": "t", "body": "b"})
    assert m["id"] == 1 and hub.last_for("S9")["id"] == 1
    rec = {"trigger": "fall", "briefing": "낙상 의심. 119 신고.\n둘째 줄", "context": {"code": CODE, "date": DATE},
           "steps": [{"tool": "make_cap_alert", "ok": True, "result": {"audio_url": "/cap/alerts/z/audio.wav", "headline": "h"}}]}
    msg = terminal.message_from_run(rec)
    assert msg is not None and msg["kind"] == "fall" and msg["audio_url"] == "/cap/alerts/z/audio.wav" and msg["body"] == "낙상 의심. 119 신고."
    assert terminal.message_from_run({**rec, "trigger": "heat_daily"})["kind"] == "heat"
    assert terminal.message_from_run({**rec, "trigger": "heat_daily", "steps": []}) is None
    assert terminal.heat_message("관악구", {"ours_alert": False}, {}, 14) is None
    hm = terminal.heat_message("관악구", {"ours_alert": True, "b1_alert": False, "date": DATE}, {"feels": 33.2}, 14)
    assert hm is not None and "특보 기준에는 못 미치지만" in hm["body"] and "관악구는" in hm["body"] and "에이전트 권고" in hm["title"]
    assert "쉼터" not in hm["body"] and "가까운 열린 AED 위치를 화면에서 확인하세요" in hm["body"]  # no shelter data -> no shelter sentence
    with_sh = terminal.heat_message("관악구", {"ours_alert": True}, {}, 14, has_shelters=True)
    assert with_sh is not None and "무더위쉼터" in with_sh["body"]
    hint = terminal.heat_message("관악구", {"ours_alert": True}, {}, 14, aed_hint="가까운 열린 AED: 관악구청 (도보 2분, 0.8 m/s)")
    assert hint is not None and hint["body"].endswith("가까운 열린 AED: 관악구청 (도보 2분, 0.8 m/s).") and "쉼터" not in hint["body"]
    assert terminal.script_for("제목", "첫 문장. 둘째.").endswith(cap_audio.CLOSING)
    assert len(terminal.script_for("긴" * 60, "x")) <= cap_audio.MAX_SCRIPT_CHARS


def test_publish_heat_from_state(tclient: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cap_audio, "available", lambda: False)
    r = tclient.post(f"/twin/{CODE}/publish-heat?date={DATE}&hour=14", headers=H)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_alert"  # OURS alerted 중구 on D0, not 종로구
    r = tclient.post(f"/twin/11120/publish-heat?date={DATE}&hour=14", headers=H)
    assert r.status_code == 201 and r.json()["kind"] == "heat" and r.json()["heat"]["ours_alert"] is True
    assert "무더위쉼터" in r.json()["body"]  # 중구 has one shelter row in the fixture


def test_publish_fall_alert(tclient: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cap_audio, "available", lambda: False)
    q = f"lat={NODES['n1'][1] + 0.00005}&lon={NODES['n1'][0] + 0.00005}&date={DATE}&hour=15&minute=7"
    assert tclient.post(f"/twin/{CODE}/publish-fall?{q}").status_code == 401
    r = tclient.post(f"/twin/{CODE}/publish-fall?{q}&stop_id=S1", headers=H)
    assert r.status_code == 201
    j = r.json()
    assert j["kind"] == "fall" and j["title"] == "낙상 의심 감지 · 시연 카메라 #1" and j["stop_id"] == "S1"
    assert j["body"].startswith("서울특별시 종로구 지번1 (횡단보도) 시연 카메라 #1에서 낙상 의심 감지 · 15:07 · 관제요원 확인 대기.")
    assert "가까운 열린 AED: 종로경로당" in j["body"] and j["meta"]["camera"]["id_label"] == "시연 카메라 #1"
    assert tclient.get("/terminal/last?stop_id=S1").json()["message"]["id"] == j["id"]
    assert twin_state.road_name("관악로 145, 관악구청 (봉천동)") == "관악로" and twin_state.road_name("서울특별시 관악구 남부순환로 1800") == "남부순환로"
    assert twin_state.road_name("지번1") is None and twin_state.road_name(None) is None


def test_publish_heat_without_shelter_data_names_no_shelter(tclient: TestClient, tsettings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """The 관악구 case: 영점 alerts, the 구 has no shelter data -> only the AED sentence, labelled 에이전트 권고(훈련)."""
    monkeypatch.setattr(cap_audio, "available", lambda: False)
    c = db.connect(tsettings.db_path)
    c.execute("DELETE FROM shelter WHERE region_code = '11120'")
    c.commit()
    c.close()
    r = tclient.post(f"/twin/11120/publish-heat?date={DATE}&hour=14", headers=H)
    assert r.status_code == 201
    body, title = r.json()["body"], r.json()["title"]
    assert "쉼터" not in body and "신대방" not in body and "가까운 열린 AED 위치를 화면에서 확인하세요" in body
    assert "에이전트 권고(훈련)" in title and "(에이전트 권고·훈련)" in body and "발령" not in body and r.json()["meta"]["has_shelters"] is False
    # with a known stop of the 구 the nearest open AED and walking minutes are named instead (종로 fixture stop S1)
    r2 = tclient.post(f"/twin/{CODE}/publish-heat?date={fx.DATES[2]}&hour=14&stop_id=S1", headers=H)
    if r2.status_code == 201:  # 종로 is alerted by OURS on D2 in the fixture
        assert "가까운 열린 AED: 종로경로당" in r2.json()["body"] and "도보" in r2.json()["body"]
    else:
        assert r2.json()["detail"]["code"] == "no_alert"


# --- rain days ------------------------------------------------------------------------------------------------------


def test_rain_days_hourly_verified_and_daily_candidates(tclient: TestClient) -> None:
    r = tclient.get(f"/twin/rain-days?sgg_code={CODE}")
    assert r.status_code == 200
    j = r.json()
    assert j["station"]["id"] == STATION
    assert [(d["date"], d["max_mm_h"], d["max_at"]["hour"]) for d in j["days"]] == [("2025-07-01", 15.5, 21)]
    assert j["days"][0]["hours_ge_threshold"][1]["date"] == "2025-07-02"  # 03h of the next morning belongs to the 07-01 night
    cov = {c["year"]: c for c in j["coverage"]}
    assert cov[2025]["resolution"] == "hourly" and cov[2023]["resolution"] == "daily"
    assert cov[2024]["resolution"] == "hourly" and cov[2024]["n_days"] == 0  # one cached night hour (23h, 0 mm) -> checked, no rain day
    assert [c["date"] for c in j["unverified_candidates"]] == ["2023-07-01"] and j["unverified_candidates"][0]["verified"] is False
    assert tclient.get("/twin/rain-days?sgg_code=99999").status_code == 404


def test_rain_days_409_without_any_data(tclient: TestClient, tsettings: Settings) -> None:
    import shutil

    shutil.rmtree(tsettings.data_dir / C.HOURLY_RAIN_DIR)
    shutil.rmtree(tsettings.data_dir / C.METEOSTAT_DIR)
    weather.clear_cache()
    r = tclient.get(f"/twin/rain-days?sgg_code={CODE}")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_rain_data"
    assert rain.night_hours_of(date(2025, 7, 1))[0] == (date(2025, 7, 1), 20) and rain.night_hours_of(date(2025, 7, 1))[-1] == (date(2025, 7, 2), 5)


def test_kma_hour_line_parser() -> None:
    toks = ["202507012100", "108"] + ["-9"] * 9 + ["30.1", "-9", "60", "-9", "-9", "-9"]
    obs = weather.parse_hour_line(toks, "f")
    assert obs.ta == 30.1 and obs.rh == 60 and obs.rain_mm == 0.0 and obs.feels is not None
    dead = weather.parse_hour_line(["202507012100", "108"] + ["-9"] * 15, "f")
    assert dead.ta is None and dead.rain_mm is None and dead.feels is None


# --- actions / cameras / cap / feedback audit -------------------------------------------------------------------------


def test_actions_panel_changes_with_the_hour(tclient: TestClient) -> None:
    day = tclient.get(f"/twin/{CODE}/actions?date={DATE}&hour=14").json()
    ids = [a["id"] for a in day["actions"]]
    assert ids[:4] == ["cbs", "voice", "bit", "route"] and day["night"] is False
    cbs = day["actions"][0]
    assert cbs["evidence"] and cbs["feedback_ref"] == {"sgg_code": CODE, "date": DATE, "action_id": "cbs"} and cbs["cap_url"].startswith("/twin/")
    assert [d["limit"] for d in cbs["drafts"]] == [90, 157] and [d["label"] for d in cbs["drafts"]] == ["90자형(표준)", "157자형(폭염 경보 문안)"]
    assert cbs["destination"]["label"] == "재난문자 송출 요청 승인권자(구청 재난 담당 부서장)" and cbs["destination"]["source_note"].startswith("[1차]")
    assert cbs["status"] == "draft" and cbs["status_label"] == "초안 · 전달 대기" and cbs["night_excluded"] is False
    # 21h: still "day" for the panel but inside the 운영규정 별표 1 night window -> the card is deferred, not tonight's action
    late = tclient.get(f"/twin/{CODE}/actions?date={DATE}&hour=21").json()
    cbs21 = next(a for a in late["actions"] if a["id"] == "cbs")
    assert cbs21["night_excluded"] is True and cbs21["body"].startswith("야간 발송 기준 제외 항목(운영규정 별표 1) → 다음 날 06시 이후 검토")
    assert any(e["label"] == "야간 발송 기준" for e in cbs21["evidence"])
    night23 = tclient.get(f"/twin/{CODE}/actions?date={DATE}&hour=23").json()
    assert "cbs" not in [a["id"] for a in night23["actions"]] and any("야간 발송 기준 제외" in n for n in night23["notes"])
    assert "시범운영" not in json.dumps(late, ensure_ascii=False) and "0.8" not in json.dumps([a for a in late["actions"] if a["id"] == "signals"], ensure_ascii=False)
    assert day["never_sent"].startswith("훈련 · 발송 안 함") and day["actions"][2]["title"].startswith("정류장 안내단말 문구 · 연계 제안")
    assert day["actions"][1]["id"] == "voice" and day["actions"][1]["destination"]["org"].startswith("주민센터")
    night = tclient.get(f"/twin/{CODE}/actions?date={DATE}&hour=23").json()
    nids = [a["id"] for a in night["actions"]]
    assert "aed.convert:1" in nids and "cbs" not in nids and night["night"] is True
    conv = next(a for a in night["actions"] if a["id"] == "aed.convert:1")
    assert conv["serial"] == "s1" and conv["feedback_ref"]["action_id"] == "aed.convert" and conv["lat"] == 37.566
    assert conv["title"] == "1층 AED 24시간 개방 요청(초안)" and conv["org"] == "종로경로당" and conv["hours_known"] is True  # address 'a' has no site part
    assert "운영시간 0900-1800 → 23시 닫힘" in conv["body"] and conv["gain_p65_2min"] is None
    labels = [e["label"] for e in conv["evidence"]]
    assert "관리 기관" in labels and twin_actions_module.GAIN_SCORE_LABEL in labels and "판정 규칙" not in labels
    assert all(e["display_source"] and "experiments" not in e["display_source"] for e in conv["evidence"])
    assert "shelter.night" not in nids  # 밤쉼터 is open at night
    off = tclient.get(f"/twin/{CODE}/actions?date=2024-03-01&hour=14").json()
    assert off["actions"] == [] and any("폭염 평가 기간" in n for n in off["notes"])
    assert tclient.get(f"/twin/99999/actions?date={DATE}&hour=14").status_code == 404


def test_action_cap_and_feedback_audit_chain(tclient: TestClient) -> None:
    assert tclient.post(f"/twin/{CODE}/actions/cbs/cap?date={DATE}&hour=14").status_code == 401
    r = tclient.post(f"/twin/{CODE}/actions/cbs/cap?date={DATE}&hour=14", headers=H)
    assert r.status_code == 201
    j = r.json()
    assert j["xsd_valid"] is True and j["generation_ms"] >= 0 and j["xml"].startswith("<?xml") and j["action_id"] == "cbs"
    assert tclient.get(j["xml_url"]).status_code == 200
    assert tclient.post(f"/twin/{CODE}/actions/nope/cap?date={DATE}&hour=14", headers=H).status_code == 404
    fb = tclient.post("/feedback", json={**j["feedback_ref"], "decision": "채택"}, headers=H)
    assert fb.status_code == 201 and fb.json()["audit"]["action"] == "feedback.accept" and len(fb.json()["audit"]["hash"]) == 64
    held = tclient.post("/feedback", json={**j["feedback_ref"], "decision": "보류", "note": "현장 확인 필요", "stage": "hold"}, headers=H)
    assert held.status_code == 201 and held.json()["audit"]["action"] == "feedback.hold" and held.json()["status"] == "held"
    assert tclient.post("/feedback", json={**j["feedback_ref"], "decision": "보류", "stage": "hold"}, headers=H).status_code == 422  # reason required
    panel = tclient.get(f"/twin/{CODE}/actions?date={DATE}&hour=14").json()
    assert next(a for a in panel["actions"] if a["id"] == "cbs")["status"] == "held"
    # approve_demo on a fresh recommendation: 409 until adopted; adopt + approve chain is audited twice
    ref = {"sgg_code": CODE, "date": DATE, "action_id": "route"}
    assert tclient.post("/feedback", json={**ref, "decision": "채택", "stage": "approve_demo"}, headers=H).status_code == 201
    ap = tclient.post("/feedback", json={**ref, "decision": "채택", "stage": "approve_demo"}, headers=H).json()
    assert ap["status"] == "approved_demo" and ap["stage"]["stage"] == "approve_demo" and ap["audit"]["action"] in ("feedback.accept", "feedback.approve_demo")
    assert tclient.post("/feedback", json={"sgg_code": CODE, "date": DATE, "action_id": "bit", "decision": "부적합", "stage": "approve_demo"}, headers=H).status_code == 422
    summ = tclient.get("/feedback/summary", headers=H).json()
    today = next(x for x in summ["by_date"] if x["date"] == DATE)
    assert today["adopted"] >= 2 and today["held"] == 1 and today["approved"] >= 1 and summ["status_semantics"].startswith("모든 단계는 전달 대기")
    v = tclient.get("/audit/verify").json()
    assert v["valid"] is True and v["entries"] >= 1


def test_aed_card_helpers(tmp_path: Path) -> None:
    assert twin_actions_module.site_name("국회단지길 14 은천제일교회 지하1층 식당", "국회단지길 14, 은천제일교회 (봉천동)", "관악구청주택과") == "은천제일교회"
    assert twin_actions_module.site_name("체육관 1층", "문성로 79", "서울난곡초등학교") == "체육관 1층"
    assert twin_actions_module.site_name(None, None, "기관") == "기관"
    picks = tmp_path / "convert_picks.csv"
    picks.write_text("site_row,org,gu,addr,gain_p65\n1,기관,관악구,\"길 1, 교회 (동)\",197.69\n", encoding="utf-8")
    assert twin_actions_module.walk4_gains(picks) == {("기관", "길 1, 교회 (동)"): 197.69}
    assert twin_actions_module.walk4_gains(tmp_path / "missing.csv") == {}


def test_population_409_until_file_exists_then_matches_dongs(tclient: TestClient, tsettings: Settings) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    r = tclient.get(f"/twin/{CODE}/population?date={DATE}&hour=14")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_population_data"
    d = tsettings.data_dir / twin_pop.LIVING_POP_SUBDIR
    d.mkdir(parents=True)
    rows = {"date": [DATE] * 4, "hour": [14, 14, 23, 14], "adm_cd": ["11110530", "11110540", "11110530", "99999999"],
            "adm_nm": ["가동", "나동", "가동", "없는동"], "total": [1200.5, 800.0, 400.0, 5.0], "age65p": [300.0, 150.0, 120.0, 1.0]}
    pq.write_table(pa.table(rows), d / f"jongno_{DATE}.parquet")
    (d / "jongno_living_pop.sources.json").write_text(json.dumps({"source": "서울 열린데이터광장 생활인구", "fetched_at": "2026-10-24T10:00:00Z"}), encoding="utf-8")
    r = tclient.get(f"/twin/{CODE}/population?date={DATE}&hour=14")
    assert r.status_code == 200
    j = r.json()
    assert [i["adm_nm"] for i in j["items"]] == ["가동", "나동"] and j["total"] == 2000 and j["age65p"] == 450
    assert j["items"][0]["matched_by"] == "adm_cd2[:8]" and j["items"][0]["polygon_id"] == "1111053000"
    assert j["items"][0]["walk_nodes"] == [0, 1, 2, 3] and j["items"][1]["walk_nodes"] == []  # the 4 test nodes sit in 가동
    assert j["sources"][0]["display_source"] == "서울시 생활인구 실측" and j["fetched_at"] == "2026-10-24T10:00:00Z"
    assert tclient.get(f"/twin/{CODE}/population?date={DATE}&hour=3").status_code == 409  # no rows for that hour
    assert tclient.get(f"/twin/{CODE}/population?date=2024-07-09&hour=14").status_code == 409
    assert tclient.get(f"/twin/99999/population?date={DATE}&hour=14").status_code == 404
    wn = tclient.get(f"/twin/{CODE}/walk-nodes").json()
    assert wn["count"] == 4 and len(wn["positions"]) == 8 and wn["coordinate_system"] == "METER_OFFSETS"
    # cache: the JSON copy serves the day even after the parquet is gone (runtime image has no pyarrow)
    (d / f"jongno_{DATE}.parquet").unlink()
    assert tclient.get(f"/twin/{CODE}/population?date={DATE}&hour=14").status_code == 200


def test_action_document_preview_and_fall_summary(tclient: TestClient) -> None:
    r = tclient.get(f"/twin/{CODE}/actions/cbs/document?date={DATE}&hour=14")
    assert r.status_code == 200
    j = r.json()
    assert set(j) >= {"수신", "제목", "본문", "근거", "생성시각", "status", "docx_url"} and j["수신"].startswith("재난문자 송출 요청 승인권자")
    assert any("재난문자 초안" in b for b in j["본문"]) and j["근거"][0]["항목"] == "기준일" and j["docx_url"].startswith("/documents/cbs.docx")
    assert all(("발령" not in b and "전송" not in b) or "전송되지" in b for b in j["본문"])
    src = next(b for b in j["본문"] if b.startswith("4. 출처"))
    assert "재난문자방송 기준 및 운영규정 별표 2(표준문안)" in src and "시행" not in src and "Meteostat" not in src
    assert "무더위쉼터 지자체 공개자료" in src  # 종로 has shelter rows in the fixture
    v = tclient.get(f"/twin/{CODE}/actions/voice/document?date={DATE}&hour=14").json()
    assert v["수신"].startswith("경로당") and v["docx_url"].startswith("/documents/cbs.docx")
    a = tclient.get(f"/twin/{CODE}/actions/aed.convert:1/document?date={DATE}&hour=23").json()
    assert a["수신"].startswith("설치기관 관리책임자")
    assert tclient.get(f"/twin/{CODE}/actions/nope/document?date={DATE}&hour=14").status_code == 404
    heat = tclient.get(f"/twin/{CODE}/state?date={DATE}&hour=15").json()["heat"]
    assert heat["decision_text"] == {"ours": "에이전트 권고: 대상 아님", "b1": "현행 특보 기준: 대상"}
    q = f"lat={NODES['n1'][1] + 0.00005}&lon={NODES['n1'][0] + 0.00005}&date={DATE}&hour=15"
    f = tclient.get(f"/twin/{CODE}/fall-summary?{q}").json()
    assert set(f["fields"]) == {"감지", "위치", "시각", "가장 가까운 열린 AED", "거리"} and f["fields"]["시각"] == f"{DATE} 15:00 (KST)"
    assert f["aed"]["id"] == "s1" and "발송 안 함" in f["summary"] and f["destination"]["label"].startswith("CCTV 통합관제센터")
    # control-room alert: the demo camera is the nearest crosswalk candidate (X1 at 37.565,126.975, 지번 address in the fixture)
    cam = f["camera"]
    assert cam["id_label"] == "시연 카메라 #1" and cam["address"] == "지번1" and cam["place_type"] == "횡단보도" and cam["real_cctv"] is False
    assert f["alert_text"] == "서울특별시 종로구 지번1 (횡단보도) 시연 카메라 #1에서 낙상 의심 감지 · 15:00 · 관제요원 확인 대기"
    assert twin_actions_module.fall_alert_text({"sido": "서울특별시", "name": "관악구"}, {"address": "서울특별시 관악구 보라매로5길 1", "place_type": "횡단보도",
                                                "id_label": "시연 카메라 #1"}, "15:00").startswith("서울특별시 관악구 보라매로5길 1 (횡단보도) 시연 카메라 #1에서")
    assert f["fields"]["감지"] == f["alert_text"] and "시연 카메라 #1" in f["fields"]["위치"]
    cams = tclient.get(f"/twin/{CODE}/cameras").json()
    assert cams["candidates"][0]["camera"]["id_label"] == "시연 카메라 #1" and "실제 CCTV 관리번호가 아니다" in cams["camera_note"]
    far = tclient.get(f"/twin/{CODE}/fall-summary?lat=37.5695&lon=126.9795&date={DATE}&hour=15").json()  # ~500 m from every candidate
    assert far["camera"] is None and far["alert_text"].startswith("서울특별시 종로구 (카메라 후보 없음")
    assert f["agent_run_context"]["context"]["hour"] == 15 and f["responder_roundtrip_s"] == round(2 * f["meters"] / 1.2)
    assert tclient.get(f"/twin/{CODE}/fall-summary?lat=37.7&lon=126.9&date={DATE}&hour=15").status_code == 422


def _texts_of(panel: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for a in panel["actions"]:
        out += [str(a.get("body", "")), str(a.get("easy_body") or ""), str(a.get("evidence_text") or "")]
        out += [str(d.get("text") or "") + str(d.get("easy_text") or "") for d in a.get("drafts", [])]
        out += [" ".join(st.get("lines", [])) for st in a.get("stops", []) if isinstance(st, dict)]
    return out


def test_generated_texts_name_only_shelters_inside_the_gu(tclient: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cap_audio, "available", lambda: True)
    panel = tclient.get(f"/twin/{CODE}/actions?date={DATE}&hour=14").json()
    texts = _texts_of(panel)
    doc = tclient.get(f"/twin/{CODE}/actions/cbs/document?date={DATE}&hour=14").json()
    texts += [str(b) for b in doc["본문"]]
    assert texts and not any("신대방" in t or "중구" in t for t in texts), [t for t in texts if "신대방" in t]
    assert any("밤쉼터" in t for t in texts)  # the 구's own shelter is still used
    cbs = panel["actions"][0]
    assert cbs["evidence_text"].startswith("체감 최고 35.1℃ · 에이전트 권고: 대상 아님 · 예상 환자") and "λ" not in cbs["evidence_text"]
    voice = next(a for a in panel["actions"] if a["id"] == "voice")
    assert voice["audio_url"] == f"/twin/{CODE}/actions/voice/audio.wav?date={DATE}"
    conv = tclient.get(f"/twin/{CODE}/actions?date={DATE}&hour=23").json()["actions"][0]
    assert conv["evidence_text"].startswith("평일 운영시간 0900-1800 · 평일 야간 개방 0%")


def test_no_shelter_data_omits_the_sentence(tclient: TestClient, tsettings: Settings) -> None:
    c = db.connect(tsettings.db_path)
    c.execute("DELETE FROM shelter WHERE region_code = ?", (CODE,))
    c.commit()
    c.close()
    panel = tclient.get(f"/twin/{CODE}/actions?date={DATE}&hour=14").json()
    texts = _texts_of(panel)
    assert not any("쉼터" in t or "시원한 곳" in t for t in texts if "연계" not in t), [t for t in texts if "쉼터" in t]
    cbs = panel["actions"][0]
    assert any(e["label"] == "무더위쉼터" and "구청 보유 목록 연계 필요" in str(e["value"]) for e in cbs["evidence"])
    assert gov_module.SHELTER_ABSENT_TEXT in panel["notes"]
    doc = tclient.get(f"/twin/{CODE}/actions/cbs/document?date={DATE}&hour=14").json()
    src = next(b for b in doc["본문"] if b.startswith("4. 출처"))
    assert "무더위쉼터" not in src and "재난문자방송 기준 및 운영규정 별표 2(표준문안)" in src


def test_voice_audio_endpoint(tclient: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cap_audio, "available", lambda: False)
    r = tclient.get(f"/twin/{CODE}/actions/voice/audio.wav?date={DATE}")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "tts_unavailable"
    assert tclient.get(f"/twin/{CODE}/actions/voice/audio.wav?date=2024-03-01").status_code == 404


def test_result_cache_and_demo_warm(tclient: TestClient, tsettings: Settings) -> None:
    import time

    twin_cache.clear()
    rep = twin_cache.warm_demo(tsettings, CODE, (DATE,), (6, 14, 23, 24))
    assert rep["entries"] == 8 and rep["errors"] == [] and twin_cache.size() == 8
    t0 = time.perf_counter()
    r = tclient.get(f"/twin/{CODE}/state?date={DATE}&hour=14")
    assert r.status_code == 200 and (time.perf_counter() - t0) < 0.5
    assert twin_cache.size() == 8  # served from the cache, no new entry
    # the actions panel is cached but the approval status is live
    assert tclient.get(f"/twin/{CODE}/actions?date={DATE}&hour=14").json()["actions"][0]["status"] == "draft"
    tclient.post("/feedback", json={"sgg_code": CODE, "date": DATE, "action_id": "cbs", "decision": "채택"}, headers=H)
    assert tclient.get(f"/twin/{CODE}/actions?date={DATE}&hour=14").json()["actions"][0]["status"] == "adopted"
    bad = twin_cache.warm_demo(tsettings, "99999", (DATE,), (6,))
    assert bad["errors"] and bad["entries"] == 0


def test_loopback_reads_are_not_rate_limited(tmp_path: Path) -> None:
    from app.main import create_app as mk

    base = {"KCF_SECRET_KEY": TEST_SECRET, "KCF_DB_PATH": str(tmp_path / "rl.sqlite3"), "KCF_DATA_DIR": str(tmp_path),
            "KCF_RATE_CAPACITY": "2", "KCF_RATE_REFILL_PER_S": "0.01", "KCF_VERIFY_RATE_CAPACITY": "1", "KCF_VERIFY_RATE_REFILL_PER_S": "0.01"}
    assert load_settings(base).rate_exempt_loopback is False  # production default: strict
    exempt = load_settings({**base, "KCF_ENV": "dev", "KCF_SIM_RATE_CAPACITY": "2", "KCF_SIM_RATE_REFILL_PER_S": "0.01"})
    assert exempt.rate_exempt_loopback is True  # demo/dev default
    assert load_settings({**base, "KCF_RATE_EXEMPT_LOOPBACK": "true"}).rate_exempt_loopback is True  # what `make demo` sets
    with TestClient(mk(exempt)) as c:
        assert all(c.get("/health").status_code == 200 for _ in range(10))
        assert all(c.get("/audit/verify").status_code == 200 for _ in range(6))  # demo: /audit/verify no longer budget-limited on loopback
        posts = [c.post("/sim/rsu", json={}).status_code for _ in range(4)]
        assert posts[-1] == 429  # writes keep their limiter (and their token checks)
    strict = load_settings({**base, "KCF_ENV": "dev", "KCF_RATE_EXEMPT_LOOPBACK": "false"})
    assert strict.rate_exempt_loopback is False
    with TestClient(mk(strict)) as c:
        codes = [c.get("/health").status_code for _ in range(4)]
        assert codes[-1] == 429
    proxied = load_settings({**base, "KCF_ENV": "dev", "KCF_TRUST_PROXY": "true"})
    assert proxied.rate_exempt_loopback is False  # behind a proxy every peer is loopback: never exempt


def test_effects_match_the_result_files(tclient: TestClient, tsettings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    twin_effects.clear_cache()
    opt = tmp_path / "opt.json"
    opt.write_text(json.dumps({"base": {"night": {"p65": 0.17550163}}, "convert": {"115": {"exact": {"night": {"p65": 0.20319096}}}},
                               "random": {"115": {"mean": 0.0030896}}}), encoding="utf-8")
    cv = tmp_path / "cv_summary.json"
    cv.write_text(json.dumps({"rule": {"lat_med": [2.25, 1.9, 2.5]}}), encoding="utf-8")
    gate = tmp_path / "gate_summary.json"
    gate.write_text(json.dumps({"ur_cv:gate=0.1": {"lat_med": [0.73333, 0.7, 0.8]}, "ur_cv:gate=0.2": {"lat_med": [0.9, 0.8, 1.0]}}), encoding="utf-8")
    model = tmp_path / "fall_model.json"
    model.write_text(json.dumps({"descent_gate": {"min": 0.1}}), encoding="utf-8")
    cov = tmp_path / "coverage_results.csv"
    cov.write_text("variant,stratum,pool,method,k,coverage,lo,hi,n_hot,cas\n"
                   "harm,all,C,current,3918,0.1541902,0.1278659,0.1802891,735,4248.0\n"
                   "harm,all,OPT,ours_opt,3918,0.9333804,0.9153905,0.9502843,735,4248.0\n"
                   "harm,new,OPT,ours_opt,3918,0.5,0.4,0.6,100,10.0\n", encoding="utf-8")
    monkeypatch.setattr(twin_effects, "SILVER_COVERAGE", cov)
    monkeypatch.setattr(twin_effects, "WALK4_OPT", opt)
    monkeypatch.setattr(twin_effects, "FALL_CV", cv)
    monkeypatch.setattr(twin_effects, "FALL_GATE", gate)
    monkeypatch.setattr(twin_effects, "FALL_MODEL", model)
    r = tclient.get("/twin/effects")
    assert r.status_code == 200
    items = {i["key"]: i for i in r.json()["items"]}
    assert r.json()["order"] == ["when", "where", "what", "night"] and list(items) == ["when", "where", "what", "night"]
    # heat: the fixture results.json has no pooled block -> honestly unavailable, never a substitute number
    assert items["when"]["available"] is False and items["when"]["baseline"] is None
    assert items["where"] == {**items["where"], "baseline": 15.4, "ours": 93.3, "unit": "%", "k": 3918, "n_hotspots": 735, "available": True,
                              "baseline_ci": [12.8, 18.0], "ours_ci": [91.5, 95.0], "label": "같은 3,918곳으로 다음 해 노인 사고 지점 포착",
                              "scope": "전국 같은 3,918곳 · 2023까지 선정 · 2024 평가"}
    assert tclient.get("/twin/effects").json() == r.json()  # cached
    twin_effects.clear_cache()
    monkeypatch.setattr(twin_effects, "SILVER_COVERAGE", tmp_path / "missing.csv")
    assert next(i for i in tclient.get("/twin/effects").json()["items"] if i["key"] == "where")["available"] is False  # no CSV, fixture has no silver_result rows
    assert items["night"] == {**items["night"], "baseline": 17.6, "ours": 20.3, "random_115": 17.9, "unit": "%", "available": True}
    assert items["what"] == {**items["what"], "baseline": 2.25, "ours": 0.73, "gate": 0.1, "unit": "초", "lower_is_better": True}
    blob = json.dumps(r.json(), ensure_ascii=False)
    assert "영점" not in blob and "어르신" not in blob


def test_no_product_name_or_eoreusin_in_human_strings(tclient: TestClient) -> None:
    docs = [tclient.get(u).json() for u in (
        f"/twin/{CODE}/state?date={DATE}&hour=15", f"/twin/{CODE}/actions?date={DATE}&hour=15", f"/twin/{CODE}/actions?date={DATE}&hour=23",
        f"/twin/{CODE}/actions/cbs/document?date={DATE}&hour=15", "/heat/verify", f"/twin/route?lat={NODES['n1'][1]}&lon={NODES['n1'][0]}&date={DATE}&hour=10&sgg_code={CODE}",
        f"/twin/{CODE}/fall-summary?lat={NODES['n1'][1]}&lon={NODES['n1'][0]}&date={DATE}&hour=15",
    )]
    blob = json.dumps(docs, ensure_ascii=False)
    assert "영점" not in blob and "어르신" not in blob
    assert "에이전트 권고" in blob and "고령자 보행" in blob


def test_use_category_mapping() -> None:
    uc = scene_build.use_category
    assert uc("단독주택") == 0 and uc("다가구주택") == 0 and uc("house") == 0 and uc("residential") == 0
    assert uc("아파트") == 1 and uc("다세대주택") == 1 and uc("apartments") == 1
    assert uc("제2종근린생활시설") == 2 and uc("업무시설") == 2 and uc("commercial") == 2 and uc("오피스텔") == 2
    assert uc("교육연구시설") == 3 and uc("의료시설") == 3 and uc("university") == 3 and uc("공공업무시설") == 3
    assert uc("종교시설") == 4 and uc("church") == 4 and uc("기타") == 4
    assert uc("yes") == 5 and uc(None) == 5 and uc("") == 5 and uc("something-new") == 5


def test_infra_inventory(tclient: TestClient) -> None:
    r = tclient.get(f"/twin/{CODE}/infra")
    assert r.status_code == 200
    j = r.json()
    c = j["counts"]
    assert c["aed"] == 3 and c["signal_crosswalk"] == 2 and c["silver_zone"] == 2 and c["cctv"] is None and c["fire_station"] is None
    stops = [d for d in j["devices"] if d["type"] == "bus_stop"]
    assert {s["id"] for s in stops} == {"S1", "S2", "S3"} and c["bus_stop"] == 3  # S4 is outside the 구, S5 has bad coordinates
    flagged = {s["id"] for s in stops if s["recommended_bit"]}
    assert "S1" in flagged and c["bus_stop_recommended_bit"] == len(flagged) and next(s for s in stops if s["id"] == "S1")["bit_label"] == "안내단말 연계 후보"
    assert j["control_center"] is None  # the fixture AED register has no '종로구청' record
    assert any("지어내지 않음" in n for n in j["notes"]) and all("source" in d for d in j["devices"])
    assert "영점" not in json.dumps(j, ensure_ascii=False) and "어르신" not in json.dumps(j, ensure_ascii=False)
    assert tclient.get("/twin/99999/infra").status_code == 404
    assert tclient.get(f"/twin/{CODE}/infra").json() == j  # cached


def test_satellite_tile_proxy(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import httpx

    from app.main import create_app as mk

    twin_tiles.clear_cache()
    base = {"KCF_SECRET_KEY": TEST_SECRET, "KCF_DB_PATH": str(tmp_path / "t.sqlite3"), "KCF_DATA_DIR": str(tmp_path), "KCF_ENV": "dev"}
    with TestClient(mk(load_settings(base))) as c:  # no key -> 503, frontend falls back
        r = c.get("/twin/tiles/satellite/15/27969/12932.jpg")
        assert r.status_code == 503 and r.json()["detail"]["code"] == "no_vworld_key"
    key = "SECRET-VWORLD-KEY-0001"
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        if "/19/" in req.url.path:
            return httpx.Response(500, text="boom " + key)
        return httpx.Response(200, content=b"\xff\xd8JPEGDATA", headers={"content-type": "image/jpeg"})

    app = mk(load_settings({**base, "VWORLD_KEY": key}))
    app.state.tile_http = httpx.Client(transport=httpx.MockTransport(handler))
    x, y = twin_tiles.lonlat_to_tile(126.944, 37.465, 15)  # 관악구 centre
    x19, y19 = twin_tiles.lonlat_to_tile(126.944, 37.465, 19)
    with TestClient(app) as c:
        r = c.get(f"/twin/tiles/satellite/15/{x}/{y}.jpg")
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.headers["cache-control"] == "private, max-age=3600"
        assert calls == [f"https://api.vworld.kr/req/wmts/1.0.0/{key}/Satellite/15/{y}/{x}.jpeg"]  # z / y / x order
        assert c.get(f"/twin/tiles/satellite/15/{x}/{y}.jpg").status_code == 200 and len(calls) == 1  # cached in memory
        assert twin_tiles.cache_size() == 1
        assert c.get(f"/twin/tiles/satellite/15/{x - 400}/{y}.jpg").status_code == 404  # far west of the demo bbox
        assert c.get("/twin/tiles/satellite/5/27/12.jpg").status_code == 404 and c.get("/twin/tiles/satellite/20/1/1.jpg").status_code == 404
        up = c.get(f"/twin/tiles/satellite/19/{x19}/{y19}.jpg")  # inside bbox, upstream fails
        assert up.status_code == 502 and key not in up.text and up.json()["detail"]["tile"] == [19, x19, y19]
    assert key not in caplog.text and not any(key in str(p) for p in tmp_path.rglob("*"))  # never logged, never on disk
    assert twin_tiles.tile_allowed(15, x, y) and not twin_tiles.tile_allowed(15, 0, 0)
    lon0, lat0, lon1, lat1 = twin_tiles.tile_bounds(15, x, y)
    assert lon0 < 126.944 < lon1 and lat0 < 37.465 < lat1


def test_lan_url(tclient: TestClient, tsettings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(twin_lan, "lan_ip", lambda: "192.168.0.23")
    j = tclient.get("/twin/lan-url").json()
    assert j == {**j, "ip": "192.168.0.23", "port": 5190, "stop_id": "S1", "terminal_url": "http://192.168.0.23:5190/terminal?stop=S1"}
    monkeypatch.setattr(twin_lan, "lan_ip", lambda: None)
    j = tclient.get("/twin/lan-url").json()
    assert j["terminal_url"] is None and j["note"] == "와이파이 연결 없음"
    phone = TestClient(tclient.app, client=("10.0.0.7", 51000))  # a non-loopback peer must not enumerate
    assert phone.get("/twin/lan-url").status_code == 403
    assert load_settings({"KCF_SECRET_KEY": TEST_SECRET, "KCF_FRONTEND_PORT": "8190"}).frontend_port == 8190
    real = twin_lan.lan_ip()  # whatever this host has: never loopback
    assert real is None or not real.startswith("127.")
    assert twin_lan.terminal_url("1.2.3.4", 5190, None) == "http://1.2.3.4:5190/terminal"


def test_cameras(tclient: TestClient) -> None:
    j = tclient.get(f"/twin/{CODE}/cameras").json()
    names = [c["name"] for c in j["candidates"]]
    assert "횡단보도 X1" in names and any(n.startswith("2024") for n in names) and "가1" in names
    assert tclient.get("/twin/99999/cameras").status_code == 404


FORBIDDEN_IN_DISPLAY = ("experiments/", "promoted", "_k3918", "fold", "OURS", "variant", "kma_sfctm2", "Meteostat")


def _display_values(obj: Any) -> Iterator[str]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.startswith("display") and isinstance(v, str):
                yield v
            else:
                yield from _display_values(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _display_values(v)


def _source_rows(obj: Any) -> Iterator[dict[str, Any]]:
    """Dicts that carry a technical `source` (evidence rows, eval blocks, sources lists)."""
    if isinstance(obj, dict):
        if "source" in obj and isinstance(obj["source"], str) and any(k in obj for k in ("label", "rule", "what", "why", "n_converted")):
            yield obj
        for v in obj.values():
            yield from _source_rows(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _source_rows(v)


def test_display_sources_are_judge_facing(tclient: TestClient) -> None:
    docs = [tclient.get(u).json() for u in (
        f"/twin/{CODE}/state?date={DATE}&hour=23", f"/twin/{CODE}/timeline?date={DATE}", f"/twin/{CODE}/actions?date={DATE}&hour=14",
        f"/twin/{CODE}/actions?date={DATE}&hour=23", f"/twin/{CODE}/cameras", f"/twin/rain-days?sgg_code={CODE}",
        f"/twin/route?lat={NODES['n1'][1]}&lon={NODES['n1'][0]}&date={DATE}&hour=10&sgg_code={CODE}", f"/twin/{CODE}/scene",
    )]
    shown = [v for d in docs for v in _display_values(d)]
    assert len(shown) > 20
    for v in shown:
        assert not any(tok in v for tok in FORBIDDEN_IN_DISPLAY), v
    rows = [r for d in docs for r in _source_rows(d)]
    assert rows and all("display_source" in r for r in rows), [r for r in rows if "display_source" not in r][:3]
    state = docs[0]
    assert "experiments/" in state["silver_eval_seoul"]["source"] and "TAAS 2024" in state["silver_eval_seoul"]["display_source"]
    ev = next(e for e in docs[3]["actions"][0]["evidence"] if e["source"].startswith("experiments/aed"))
    assert ev["display_source"].startswith("국립중앙의료원 AED")


# --- heat verify -----------------------------------------------------------------------------------------------------


def test_heat_verify_recomputes_capture_from_stored_runs(tclient: TestClient) -> None:
    r = tclient.get("/heat/verify?n_boot=200")
    assert r.status_code == 200
    j = r.json()
    f = j["variants"]["F"]
    assert f["b1_capture"] == pytest.approx(1 / 6) and f["ours_capture"] == pytest.approx(5 / 6) and f["years"] == [2024]
    assert f["delta_ci"][0] <= f["delta"] <= f["delta_ci"][1] and f["n_cases"] == 6
    assert j["status"] == "no_pooled_block" and j["sha256"]["results.json"] and len(j["lines"]) >= 5 and j["seconds"] < 10
    assert tclient.get("/heat/verify?n_boot=1").status_code == 422
    w = heat_verify.block_weights(10, np.random.default_rng(1), 5, 3)
    assert w.shape == (5, 10) and np.allclose(w.sum(axis=1), 10)


def test_heat_verify_409_without_runs(client: TestClient) -> None:
    r = client.get("/heat/verify")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_completed_run"


def test_verify_cli_exit_codes(tclient: TestClient, tsettings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KCF_SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("KCF_DB_PATH", str(tsettings.db_path))
    monkeypatch.setenv("KCF_DATA_DIR", str(tsettings.data_dir))
    assert heat_verify.main(["100"]) == 1  # no pooled block in the fixture results.json -> not "ok"
    assert os.environ["KCF_DB_PATH"] == str(tsettings.db_path)
