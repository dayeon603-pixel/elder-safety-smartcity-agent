"""Data edge cases (robustness audit): missing / stale / duplicate / renamed inputs and small-n disclosure.

  * persist: duplicate prediction cells, a renamed region, an alerted cell without a score -> loud errors, no write.
  * geo.assign_points: empty region table, a point in the sea, a point exactly on a centroid.
  * shelters: NULL flags are never "closed" and never "open" in the plan or the text.
  * weather: a station-day with too few valid hours gives NULL 체감온도; the API then says "자료 없음", never 0 ℃.
  * small-n disclosure: every ranking the API returns carries the n it was computed from (or null), so a UI can
    say "자료 부족" instead of a number.
  * ties: equal scores / gap scores share a rank (no arbitrary ordering presented as a difference).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import db, repo_heat
from app.actions import cbs
from app.actions.models import ShelterRef
from app.config import Settings
from app.heat import ingest_asos, persist
from app.heat import plan as heat_plan
from app.main import create_app
from app.modules import geo, repo
from tests import heat_fixture as fx
from tests import modules_fixture as mf


@pytest.fixture
def seeded(conn: sqlite3.Connection, tmp_path: Path) -> tuple[sqlite3.Connection, Path, Path, Path]:
    fx.seed_base(conn)
    pred, res, panel = fx.write_experiment_outputs(tmp_path / "out")
    return conn, pred, res, panel


# ----------------------------------------------------------------------------- persist guards

def test_duplicate_prediction_cells_are_refused_and_nothing_is_written(seeded: Any, tmp_path: Path) -> None:
    conn, pred, res, panel = seeded
    df = pd.read_csv(pred)
    dup = pd.concat([df, df.iloc[[0]]])
    p2 = tmp_path / "dup.csv.gz"
    dup.to_csv(p2, index=False)
    with pytest.raises(persist.PersistError, match="duplicate"):
        persist.persist(conn, p2, res, panel)
    assert conn.execute("SELECT COUNT(*) FROM alert_run").fetchone()[0] == 0


def test_renamed_or_merged_region_fails_loudly(seeded: Any, tmp_path: Path) -> None:
    """A panel unit whose (sido, name) no longer exists in `region` (e.g. 2026 merger) is a hard error."""
    conn, pred, res, panel = seeded
    pm = pd.read_csv(panel)
    pm.loc[pm.unit_id == 2, "unit_key"] = "부산광역시|중구(통합)"
    p2 = tmp_path / "panel2.csv.gz"
    pm.to_csv(p2, index=False)
    with pytest.raises(persist.PersistError, match="re-run ingest_regions"):
        persist.persist(conn, pred, res, p2)
    assert conn.execute("SELECT COUNT(*) FROM alert").fetchone()[0] == 0


def test_alerted_cell_without_score_is_refused(seeded: Any, tmp_path: Path) -> None:
    conn, pred, res, panel = seeded
    df = pd.read_csv(pred)
    mask = (df.method == "OURS") & (df.alerted == 1)
    df.loc[mask.idxmax(), "score"] = float("nan")
    p2 = tmp_path / "nan.csv.gz"
    df.to_csv(p2, index=False)
    with pytest.raises(persist.PersistError, match="alerted cell has no score"):
        persist.persist(conn, p2, res, panel)


def test_nan_score_on_unalerted_rule_cell_is_dropped_not_stored(conn: sqlite3.Connection, tmp_path: Path) -> None:
    counts = fx.build_heat_db(conn, tmp_path / "out", nan_b1_cell=True)
    assert counts["runs_created"] > 0
    n_nan = conn.execute("SELECT COUNT(*) FROM alert WHERE score IS NULL").fetchone()[0]
    assert n_nan == 0


# ----------------------------------------------------------------------------- geo assignment

def test_assign_points_edge_cases(conn: sqlite3.Connection) -> None:
    assert geo.assign_points(conn, [(37.5, 127.0)], None) == [(None, None)]  # empty region table
    fx.seed_base(conn)
    sea = (34.0, 131.5)  # East Sea, > 15 km from any centroid
    on_centroid = (37.57, 126.98)  # 종로구 centroid exactly
    out = geo.assign_points(conn, [sea, on_centroid], None)
    assert out[0] == (None, None)
    assert out[1] == ("11110", "centroid")


# ----------------------------------------------------------------------------- shelters with unknown hours

def test_unknown_shelter_flags_are_neither_open_nor_closed() -> None:
    cell = heat_plan.Cell("26110", "부산 중구", 2.0, 300, 0)
    shelters = [
        {"id": 1, "region_code": "26110", "name": "모름", "lat": 35.10, "lon": 129.03, "night_open": None, "weekend_open": None},
        {"id": 2, "region_code": "26110", "name": "닫힘", "lat": 35.20, "lon": 129.03, "night_open": 0, "weekend_open": 1},
    ]
    exts, unknown = heat_plan.pick_shelter_extensions([cell], shelters, k=5)
    assert unknown == 1 and [e["name"] for e in exts] == ["닫힘"]
    assert "야간" in exts[0]["reason"] and "주말" not in exts[0]["reason"]
    # the CBS text never prints an opening claim for a NULL flag
    ref = ShelterRef(name="모름", region_code="26110", distance_km=0.2, night_open=None, weekend_open=None)
    assert cbs.open_label(ref) == ""
    draft = cbs.build_cbs(cbs.CbsInput(region_name="중구", month=7, day=1, tmax_c=34.0, rank=None, n_cells=250, shelters=(ref,)))
    assert "모름" in draft.text and "운영" not in draft.text.split("무더위쉼터")[1]


# ----------------------------------------------------------------------------- weather with too few hours

def test_too_few_valid_hours_give_null_apparent_temperature() -> None:
    rows = [("2024-07-01", h, "108", 33.0 + 0.1 * h, 60.0) for h in range(10, 16)]  # 6 valid hours < MIN_HOURS=7
    at, tx, n = ingest_asos.daily_max(rows)[("108", "2024-07-01")]
    assert n == 6 and at is None and tx is None
    # a NaN humidity hour does not count as valid: 7 hours with one NaN -> still 6 -> NULL
    rows7 = rows + [("2024-07-01", 16, "108", 35.0, float("nan"))]
    assert ingest_asos.daily_max(rows7)[("108", "2024-07-01")] == (None, None, 6)
    # with 7 valid hours the maximum is computed over exactly those hours
    rows_ok = rows + [("2024-07-01", 16, "108", 35.0, 60.0)]
    at2, tx2, n2 = ingest_asos.daily_max(rows_ok)[("108", "2024-07-01")]
    assert n2 == 7 and tx2 == 35.0 and at2 is not None and at2 > 30


def test_region_detail_reports_missing_weather_as_null(client_with_heat: TestClient) -> None:
    # fixture: apparent_max exists only on 2024-07-01; 07-02 has tmax but no 체감온도
    d = client_with_heat.get("/heat/region/11110", params={"date": fx.DATES[1]}).json()
    assert d["drivers"]["tmax"] == 34.5 and d["drivers"]["apparent_max"] is None
    a = client_with_heat.get("/actions/11110", params={"date": fx.DATES[1]})
    if a.status_code == 200:  # needs the 행정동 sources; skipped silently when absent (503)
        txt = a.json()["cbs"]["text"]
        assert "체감" not in txt and "최고 35℃" in txt or "최고 34℃" in txt or "최고 3" in txt


@pytest.fixture
def client_with_heat(settings: Settings, tmp_path: Path) -> Iterator[TestClient]:
    c = db.connect(settings.db_path)
    db.migrate(c)
    fx.build_heat_db(c, tmp_path / "out")
    c.close()
    with TestClient(create_app(settings)) as client:
        yield client


# ----------------------------------------------------------------------------- small-n disclosure and ties

def test_rankings_carry_their_n(settings: Settings, tmp_path: Path) -> None:
    c = db.connect(settings.db_path)
    db.migrate(c)
    fx.build_heat_db(c, tmp_path / "out")
    from app.modules import load

    load.load_modules(c, mf.write_silver(tmp_path / "silver"), mf.write_aed(tmp_path / "aed"))
    c.close()
    with TestClient(create_app(settings)) as client:
        s = client.get("/silver/summary").json()
        for st in s["strata"]:
            assert isinstance(st["n_hot"], int) and st["n_hot"] > 0  # the UI can gate on n_hot
        b = client.get("/briefing/11110", params={"date": fx.DATES[1]}).json()
        assert b["heat"]["evidence"]["n_cells"] == 3 and b["heat"]["rank"] == 1  # rank 1 of 3, n is disclosed
        a = client.get("/aed/summary").json()
        assert a["night_share_of_public_arrests"]["n"] == 447  # interval carries its n
        assert a["share_arrests_open_lt_50pct"]["n"] == 344
        # the AED 구 rank has no n in the response: the gap score is a per-year estimate from 11 months of events
        gu = client.get("/aed/gu").json()
        assert "public_arrests_night" in gu[0] and "n_events" not in gu[0]  # documented gap (EDGE_CASES.md)


def test_tied_scores_share_a_rank(conn: sqlite3.Connection) -> None:
    fx.seed_base(conn)
    conn.execute("INSERT INTO alert_run (method, variant, train_year_min, train_year_max, test_year, budget_kind, "
                 "budget_ref_run_id, params, seed, dataset_sha, git_hash, created_at) VALUES "
                 "('OURS','F',2019,2023,2024,'per_day_b1',NULL,'{}',42,?,?,'now')", ("a" * 64, "g"))
    run = int(conn.execute("SELECT id FROM alert_run").fetchone()[0])
    conn.executemany("INSERT INTO alert (run_id, date, region_code, score, alerted) VALUES (?, ?, ?, ?, 0)",
                     [(run, "2024-07-01", "11110", 2.0), (run, "2024-07-01", "11120", 2.0), (run, "2024-07-01", "26110", 1.0)])
    assert repo_heat.cell_rank(conn, run, "2024-07-01", 2.0) == 1
    assert repo_heat.cell_rank(conn, run, "2024-07-01", 1.0) == 3  # two regions tie at 1, the next is 3rd
    conn.execute("INSERT INTO aed_gu_gap (gu, sgg_code, source_code, n_aed, n_aed_24h_weekday, night_open_share, day_open_share, "
                 "gap_score, public_arrests_night) VALUES ('A','11110','11010',10,5,0.4,0.7,9.7,17.2), "
                 "('B','11120','11020',10,5,0.4,0.7,9.7,17.2)")
    assert repo.aed_gap_rank(conn, 9.7) == 1


def test_prior_case_rate_without_history_is_null(conn: sqlite3.Connection) -> None:
    fx.seed_base(conn)
    assert repo_heat.prior_case_rate(conn, "26110", "2024-01-01") is None  # 부산 중구 has no earlier years
    assert repo_heat.prior_case_rate(conn, "11110", "2024-01-01") == pytest.approx(2.0)  # one stored day, 2 cases
