"""Module loaders (idempotence, provenance, privacy) and the silver / aed / briefing endpoints."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import Settings
from app.main import create_app
from app.modules import load
from tests import heat_fixture as fx
from tests import modules_fixture as mf


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    return mf.write_silver(tmp_path / "silver"), mf.write_aed(tmp_path / "aed")


def _seeded(conn: sqlite3.Connection, tmp_path: Path) -> None:
    fx.build_heat_db(conn, tmp_path / "out")


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608


def test_topic_particle() -> None:
    from app.modules.briefing import topic

    assert [topic(w) for w in ("관악구", "포항시남구", "중구", "종로구", "부산")] == [
        "관악구는", "포항시남구는", "중구는", "종로구는", "부산은"]
    assert topic("A") == "A는"


# ------------------------------------------------------------------ loaders
def test_load_tables_and_provenance(conn: sqlite3.Connection, tmp_path: Path, dirs: tuple[Path, Path]) -> None:
    _seeded(conn, tmp_path)
    assert load.load_modules(conn, *dirs) == {"silver": "loaded", "aed": "loaded"}
    assert _count(conn, "silver_result") == len(mf.SILVER_RESULTS) + 1 + len(mf.SILVER_OPT)  # promoted file: + ours_opt rows
    assert _count(conn, "silver_site") == 6 and _count(conn, "silver_hotspot_2024") == 2
    assert _count(conn, "aed_hourly_gap") == 24 and _count(conn, "aed_gu_gap") == 2
    assert _count(conn, "aed_candidate") == 3 + 2
    # every source file, loaded or only registered, has a dataset row and a module_run row with the same sha256
    rows = conn.execute(
        "SELECT m.source_file, m.sha256, d.sha256 AS ds_sha, m.protocol_sha FROM module_run m "
        "JOIN datasets d ON d.id = m.dataset_id"
    ).fetchall()
    assert len(rows) == len(load.SILVER_SOURCES) + len(load.AED_SOURCES)
    assert all(r["sha256"] == r["ds_sha"] and len(r["sha256"]) == 64 for r in rows)
    assert all(r["protocol_sha"] and len(r["protocol_sha"]) == 64 for r in rows)
    assert conn.execute("SELECT COUNT(*) FROM datasets WHERE name = 'aed_policy_eval'").fetchone()[0] == 1


def test_region_assignment_and_gu_codes(conn: sqlite3.Connection, tmp_path: Path, dirs: tuple[Path, Path]) -> None:
    _seeded(conn, tmp_path)
    load.load_modules(conn, *dirs)  # no GeoJSON in tests: nearest region centroid
    by = {tuple(r) for r in conn.execute("SELECT kind, sgg_code FROM silver_site")}
    assert ("ours", "26110") in by and ("current", "11110") in by
    assert {r[0] for r in conn.execute("SELECT DISTINCT sgg_method FROM silver_site")} == {"centroid"}
    assert dict(conn.execute("SELECT gu, sgg_code FROM aed_gu_gap").fetchall()) == {"종로구": "11110", "중구": "11120"}
    assert conn.execute("SELECT night_open_share FROM aed_gu_gap WHERE gu = '종로구'").fetchone()[0] == 0.4  # exact, no float noise
    assert conn.execute("SELECT weekday_hours FROM aed_candidate WHERE serial_seq = '010-2' LIMIT 1").fetchone()[0] is None


def test_without_regions_codes_are_null(conn: sqlite3.Connection, dirs: tuple[Path, Path]) -> None:
    load.load_modules(conn, *dirs)
    assert _count(conn, "silver_site") == 6
    assert conn.execute("SELECT COUNT(*) FROM silver_site WHERE sgg_code IS NOT NULL").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM aed_gu_gap WHERE sgg_code IS NOT NULL").fetchone()[0] == 0


def test_idempotent_and_reload_on_change(conn: sqlite3.Connection, tmp_path: Path, dirs: tuple[Path, Path]) -> None:
    _seeded(conn, tmp_path)
    load.load_modules(conn, *dirs)
    before = conn.execute("SELECT id, created_at FROM module_run ORDER BY id").fetchall()
    assert load.load_modules(conn, *dirs) == {"silver": "unchanged", "aed": "unchanged"}
    assert [tuple(r) for r in conn.execute("SELECT id, created_at FROM module_run ORDER BY id")] == [tuple(r) for r in before]
    assert load.load_modules(conn, *dirs, force=True) == {"silver": "loaded", "aed": "loaded"}
    assert _count(conn, "silver_site") == 6 and _count(conn, "aed_candidate") == 5 and _count(conn, "module_run") == len(before)
    # a changed source file reloads only its module and updates the stored sha256
    hourly = dirs[1] / "hourly_gap.csv"
    hourly.write_text(hourly.read_text(encoding="utf-8-sig").replace("\n0,10,", "\n0,11,"), encoding="utf-8-sig")
    assert load.load_modules(conn, *dirs) == {"silver": "unchanged", "aed": "loaded"}
    assert conn.execute("SELECT public_arrests FROM aed_hourly_gap WHERE hour = 0").fetchone()[0] == 11
    # new regions change the fingerprint: codes depend on them, so both modules reload
    conn.execute("UPDATE region SET name = '종로구X' WHERE code = '11110'")
    assert load.load_modules(conn, *dirs) == {"silver": "loaded", "aed": "loaded"}


def test_bad_row_rolls_back(conn: sqlite3.Connection, tmp_path: Path, dirs: tuple[Path, Path]) -> None:
    _seeded(conn, tmp_path)
    load.load_modules(conn, *dirs)
    gu = dirs[1] / "seoul_gu_night_gap_map.csv"
    gu.write_text(gu.read_text(encoding="utf-8-sig").replace("40.0", "140.0"), encoding="utf-8-sig")  # 140% open
    with pytest.raises(load.ModuleLoadError):
        load.load_modules(conn, *dirs)
    assert _count(conn, "aed_gu_gap") == 2 and _count(conn, "aed_candidate") == 5  # previous state intact


def test_missing_file_and_bad_number(conn: sqlite3.Connection, dirs: tuple[Path, Path]) -> None:
    promoted = dirs[0] / load.sc.SILVER_RELPATH  # the loaded silver files live in the promoted sub-directory
    (promoted / "delta_results.csv").unlink()
    with pytest.raises(load.ModuleLoadError, match="missing source file"):
        load.load_modules(conn, *dirs)
    mf.write_silver(dirs[0])
    cov = promoted / "coverage_results.csv"
    cov.write_text(cov.read_text(encoding="utf-8-sig").replace("0.15,0.12", "nan,0.12"), encoding="utf-8-sig")
    with pytest.raises(load.ModuleLoadError, match="coverage"):
        load.load_modules(conn, *dirs)
    assert _count(conn, "silver_result") == 0
    # a promoted file without the ours_opt headline row is refused (the API would otherwise show no "ours")
    mf.write_silver(dirs[0])
    cov.write_text("\n".join(x for x in cov.read_text(encoding="utf-8-sig").splitlines() if "ours_opt" not in x),
                   encoding="utf-8-sig")
    with pytest.raises(load.ModuleLoadError, match="ours_opt"):
        load.load_modules(conn, *dirs)


def test_no_personal_columns_in_schema(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("SELECT name FROM pragma_table_info('aed_candidate')")}
    assert not {c for c in cols if any(w in c for w in ("manager", "tel", "phone", "name_"))}
    assert {"org", "address", "place", "weekday_hours"} <= cols


def test_migration_checks(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO silver_site (kind, lat, lon, cur_within_300m, ours_within_300m) VALUES ('x',37,127,0,0)")
    with pytest.raises(sqlite3.IntegrityError):  # sgg_code and sgg_method are both set or both NULL
        conn.execute("INSERT INTO silver_hotspot_2024 (lat, lon, casualties, sgg_method, covered_current, covered_ours) "
                     "VALUES (37,127,1,'polygon',0,0)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO aed_hourly_gap VALUES (24, 1, 0.5, 0.5, 0.5)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO silver_site (kind, lat, lon, sgg_code, sgg_method, cur_within_300m, ours_within_300m) "
                     "VALUES ('ours',37,127,'99999','polygon',0,0)")  # FK to region


# ------------------------------------------------------------------ endpoints
@pytest.fixture
def mclient(settings: Settings, tmp_path: Path, dirs: tuple[Path, Path]) -> Iterator[TestClient]:
    c = db.connect(settings.db_path)
    db.migrate(c)
    _seeded(c, tmp_path)
    c.execute("INSERT INTO region (code, sido, name, lat, lon, pop, pop65, pop_asof) "
              "VALUES ('47111','경상북도','포항시',36.0,129.3,1000,200,'2026-09-30')")
    load.load_modules(c, *dirs)
    c.close()
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def empty_client(settings: Settings, tmp_path: Path) -> Iterator[TestClient]:
    c = db.connect(settings.db_path)
    db.migrate(c)
    _seeded(c, tmp_path)
    c.close()
    with TestClient(create_app(settings)) as client:
        yield client


def test_silver_summary(mclient: TestClient) -> None:
    b = mclient.get("/silver/summary").json()
    # k=250 rows exist but 3918 is the headline; ours = promoted ours_opt (pool OPT), baselines from pool C
    assert b["k"] == 3918 and b["variant"] == "harm" and b["pool"] == "OPT" and b["comparison_pool"] == "C"
    assert b["ours_method"] == "ours_opt" and b["source"] == "promoted"
    assert [s["stratum"] for s in b["strata"]] == ["all", "new"]
    allm = {m["method"]: m for m in b["strata"][0]["methods"]}
    assert list(allm) == ["current", "random_k", "pop65_k", "past_k", "ours_opt"]
    assert (allm["current"]["coverage"], allm["current"]["ci_lo"], allm["current"]["ci_hi"]) == (0.15, 0.12, 0.18)
    assert allm["ours_opt"]["coverage"] == 0.93 and allm["ours_opt"]["label"] == "에이전트" and allm["current"]["label"] == "현행"
    assert b["strata"][0]["n_hot"] == 70 and b["strata"][1]["n_hot"] == 10
    assert {d["vs"] for d in b["strata"][0]["ours_minus"]} == {"C:current", "C:random_k", "C:pop65_k", "C:past_k"}
    new_minus = {d["vs"]: d["delta_pp"] for d in b["strata"][1]["ours_minus"]}
    assert new_minus["C:current"] == pytest.approx(27.0)  # 0.39 - 0.12, in %p
    assert "포착률" in b["definitions"]["coverage"] and "%p" in b["definitions"]["pp"]
    assert {s["file"] for s in b["sources"]} >= {"opt/promoted/coverage_results.csv", "opt/protocol_opt.json",
                                                 "coverage_by_fold.csv"}
    assert all(len(s["sha256"]) == 64 for s in b["sources"])


def test_silver_map(mclient: TestClient) -> None:
    b = mclient.get("/silver/map?city=seoul").json()
    assert set(b) == {"city", "k", "current", "ours", "hotspots_2024", "sources"}
    assert len(b["current"]) == 2 and len(b["ours"]) == 4 and len(b["hotspots_2024"]) == 2
    assert b["hotspots_2024"][0] == {"lat": 37.58, "lon": 126.981, "casualties": 7.0, "sgg_code": "11110",
                                     "covered_current": False, "covered_ours": True}
    assert set(b["current"][0]) == {"lat", "lon", "name", "sgg_code", "near_current", "near_ours"}
    assert b["current"][0]["name"] == "사직경로당" and b["ours"][0]["name"] is None
    assert mclient.get("/silver/map").status_code == 200
    assert mclient.get("/silver/map?city=busan").status_code == 422


def test_aed_summary(mclient: TestClient) -> None:
    b = mclient.get("/aed/summary").json()
    assert b["weekday"]["day"] == pytest.approx(0.783) and b["weekday"]["night"] == pytest.approx(0.493)
    assert b["weekend"]["night"] == pytest.approx(0.515)
    assert b["public_arrests_total"] == 2534
    assert b["night_share_of_public_arrests"] == {"value": 0.18, "ci_lo": 0.16, "ci_hi": 0.19, "n": 447}
    assert b["share_arrests_open_lt_50pct"]["n"] == 344
    assert [g["gu"] for g in b["worst_gu"]] == ["중구", "종로구"]  # gap 10.56 > 9.7
    assert "열림 비율" in b["definitions"]["open_share"] and "서울" in b["scope"]


def test_aed_hourly_gu_candidates(mclient: TestClient) -> None:
    h = mclient.get("/aed/hourly").json()
    assert [r["hour"] for r in h] == list(range(24)) and set(h[0]) == {"hour", "public_arrests", "open_share_weekday", "open_share_weekend"}
    assert h[0]["open_share_weekday"] == pytest.approx(0.49) and h[12]["open_share_weekday"] == pytest.approx(0.78)
    gu = mclient.get("/aed/gu").json()
    assert [g["gu"] for g in gu] == ["중구", "종로구"] and gu[0]["sgg_code"] == "11120"
    assert set(gu[0]) == {"gu", "sgg_code", "night_open_share", "day_open_share", "gap_score", "public_arrests_night", "n_aed",
                          "n_aed_24h_weekday", "rank_interval", "top5_probability", "rank_text"}
    assert gu[0]["rank_interval"][0] == 1 and 0 <= gu[0]["top5_probability"] <= 1 and "상위 5구" in gu[0]["rank_text"]
    c = mclient.get("/aed/candidates", params={"gu": "종로구"}).json()
    assert [(x["rank"], x["org"]) for x in c] == [(1, "B초등학교"), (2, "A경로당")]  # best gain first
    assert c[0]["weekday_hours"] is None and set(c[0]) == {
        "gu", "rank", "org", "address", "place", "weekday_hours", "night_open_weekday_share", "gain_score", "lat", "lon"}
    assert len(mclient.get("/aed/candidates").json()) == 3
    assert mclient.get("/aed/candidates", params={"gu": "없는구"}).status_code == 404
    assert mclient.get("/aed/candidates", params={"gu": ""}).status_code == 422


def test_empty_database_gives_409(empty_client: TestClient) -> None:
    for url in ("/silver/summary", "/silver/map", "/aed/summary", "/aed/hourly", "/aed/gu", "/aed/candidates"):
        r = empty_client.get(url)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "no_module_data", url


def test_briefing_seoul_gu(mclient: TestClient) -> None:
    b = mclient.get("/briefing/11110", params={"date": fx.DATES[1], "teams": 1}).json()
    assert b["region"] == {"code": "11110", "name": "종로구", "sido": "서울특별시", "pop65": 250}
    h = b["heat"]
    assert h["alerted_b1"] is False and h["alerted_ours"] is True and h["cases_actual"] == 2  # D1: B1 alerts 26110
    assert h["rank"] == 1
    assert h["plan_allocation_for_region"]["teams_total"] == 1
    assert h["nearby_open_shelters"] == {"coverage": "none", "items": []}
    s = b["silver"]
    assert (s["current_zones_in_region"], s["our_picks_in_region"], s["hotspots_2024_in_region"]) == (2, 2, 1)
    assert (s["hotspots_covered_current"], s["hotspots_covered_ours"]) == (0, 1)
    assert s["crosswalk_green_deficit_count"] is None and s["note"] is None
    a = b["aed"]
    assert a["night_open_share"] == pytest.approx(0.4) and a["day_open_share"] == pytest.approx(0.72) and a["gap_rank"] == 2
    unc = a["gap_rank_uncertainty"]  # EDGE_CASES C: the point rank is noisy, so membership + interval travel with it
    assert unc["rank_interval"][0] <= 2 <= unc["rank_interval"][1] and unc["n_units"] == 2 and 0 <= unc["top_k_probability"] <= 1
    assert "상위 5구" in a["gap_rank_text"] and "밤 공백:" in next(x for x in b["actions"] if x["action_id"] == "aed.gap")["reason"]
    assert [c["org"] for c in a["candidates_top5"]] == ["B초등학교", "A경로당"] and b["aed_note"] is None
    assert {x["module"] for x in b["actions"]} == {"heat", "silver", "aed"}
    assert all(x["text"] and x["reason"] and x["action_id"] for x in b["actions"])
    heat_action = next(x for x in b["actions"] if x["action_id"] == "heat.alert")
    ev = heat_action["evidence"]
    assert set(ev) >= {"expected_cases_lambda", "rank", "pop65", "apparent_max_c", "tmax_c", "station", "data_sha256"}
    assert ev == b["heat"]["evidence"] and ev["pop65"] == 250 and all(len(v) == 64 for v in ev["data_sha256"].values())
    assert any("밤 AED 열림 40% (낮 72%)" in x["text"] for x in b["actions"])


def test_briefing_alert_and_plan(mclient: TestClient) -> None:
    # D0: ours alerts unit 1 (11120), B1 alerts unit 0 (11110)
    ours = mclient.get("/briefing/11120", params={"date": fx.DATES[0], "teams": 1}).json()
    assert ours["heat"]["alerted_ours"] is True and ours["heat"]["alerted_b1"] is False and ours["heat"]["rank"] == 1
    assert ours["heat"]["plan_allocation_for_region"]["teams"] == 1 and ours["heat"]["cases_actual"] == 3
    assert any("빠뜨린 곳" in x["text"] for x in ours["actions"]) and any("방문팀 1팀" in x["text"] for x in ours["actions"])
    b1 = mclient.get("/briefing/11110", params={"date": fx.DATES[0], "teams": 1}).json()
    assert b1["heat"]["alerted_b1"] is True and b1["heat"]["alerted_ours"] is False


def test_briefing_non_seoul(mclient: TestClient) -> None:
    b = mclient.get("/briefing/26110", params={"date": fx.DATES[1]}).json()
    assert b["aed"] is None and b["aed_note"] == "서울만 분석"
    assert b["silver"]["our_picks_in_region"] is None and b["silver"]["note"] == "서울 지도만 분석"
    assert b["heat"]["nearby_open_shelters"]["coverage"] == "data"
    assert [s["name"] for s in b["heat"]["nearby_open_shelters"]["items"]] == ["near"]
    assert {x["module"] for x in b["actions"]} == {"heat"}
    # a region the heat run never evaluated: nulls instead of false claims
    u = mclient.get("/briefing/47111", params={"date": fx.DATES[1]}).json()
    assert u["heat"]["rank"] is None and u["heat"]["alerted_ours"] is None and u["heat"]["alerted_b1"] is None
    assert u["aed"] is None and u["actions"][0]["module"] == "heat"


def test_briefing_errors(mclient: TestClient) -> None:
    assert mclient.get("/briefing/99999", params={"date": fx.DATES[0]}).status_code == 404
    for bad in ("2024-7-1", "2024-02-30", "not-a-date", "2024-12-25", "2024-07-01 "):
        assert mclient.get("/briefing/11110", params={"date": bad}).status_code == 422, bad
    assert mclient.get("/briefing/11110").status_code == 422
    assert mclient.get("/briefing/1111", params={"date": fx.DATES[0]}).status_code == 422
    assert mclient.get("/briefing/abcde", params={"date": fx.DATES[0]}).status_code == 422
    for teams in (0, 101, "x"):
        assert mclient.get("/briefing/11110", params={"date": fx.DATES[0], "teams": teams}).status_code == 422
    assert mclient.get("/briefing/11110", params={"date": "2023-07-01"}).status_code == 422  # not a test year


def test_briefing_before_modules_load(empty_client: TestClient) -> None:
    b = empty_client.get("/briefing/11110", params={"date": fx.DATES[0]}).json()
    assert b["aed"] is None and "make modules-load" in b["aed_note"] and b["silver"]["note"] is not None
    assert b["heat"]["alerted_b1"] is True
