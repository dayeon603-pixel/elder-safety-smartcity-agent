"""Decision layer: allocation optimality, monotonicity, no future data, shelter rules, API contract."""

from __future__ import annotations

import itertools
import random
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import Settings
from app.heat import plan
from app.heat.plan import Cell
from app.main import create_app
from tests import heat_fixture as fx

D0, D1, D2 = fx.DATES


def _cells(n: int, rng: random.Random) -> list[Cell]:
    return [
        Cell(f"{i:05d}", f"r{i}", rng.uniform(0.1, 5.0), rng.choice([500, 3000, 20000, 90000]), rng.randrange(4))
        for i in range(n)
    ]


def _objective(cells: list[Cell], alloc: dict[str, int]) -> float:
    return sum(plan.coverage_gain(c.lam, c.pop65, alloc.get(c.code, 0)) for c in cells)


def _brute_best(cells: list[Cell], teams: int) -> float:
    best = 0.0
    for combo in itertools.product(range(teams + 1), repeat=len(cells)):
        if sum(combo) <= teams:
            best = max(best, _objective(cells, {c.code: n for c, n in zip(cells, combo, strict=True)}))
    return best


@pytest.mark.parametrize("seed", range(12))
def test_greedy_matches_brute_force(seed: int) -> None:
    rng = random.Random(seed)
    cells = _cells(rng.randint(1, 4), rng)
    teams = rng.randint(0, 5)
    alloc = plan.allocate_greedy(cells, teams)
    assert sum(alloc.values()) <= teams
    assert _objective(cells, alloc) == pytest.approx(_brute_best(cells, teams), rel=1e-12)


def test_objective_monotone_in_teams_and_uses_all_when_gain_positive() -> None:
    cells = _cells(6, random.Random(1))
    prev = -1.0
    for t in range(0, 15):
        alloc = plan.allocate_greedy(cells, t)
        assert sum(alloc.values()) == t
        obj = _objective(cells, alloc)
        assert obj >= prev
        prev = obj


def test_zero_lambda_or_empty_gets_no_teams_and_negative_rejected() -> None:
    assert plan.allocate_greedy([], 5) == {}
    assert plan.allocate_greedy([Cell("00001", "a", 0.0, 1000, 2)], 3) == {}
    with pytest.raises(ValueError):
        plan.allocate_greedy([], -1)
    with pytest.raises(ValueError):
        plan.allocate_uniform([], -1)


def test_greedy_is_deterministic_on_ties() -> None:
    cells = [Cell("00002", "b", 1.0, 1000, 0), Cell("00001", "a", 1.0, 1000, 0)]
    assert plan.allocate_greedy(cells, 1) == {"00001": 1}


def test_uniform_spreads_evenly_extras_to_larger_pop65() -> None:
    cells = [Cell("a", "a", 9.0, 100, 1), Cell("b", "b", 1.0, 300, 0), Cell("c", "c", 5.0, 200, 2)]
    assert plan.allocate_uniform(cells, 4) == {"b": 2, "c": 1, "a": 1}
    assert plan.allocate_uniform(cells, 2) == {"b": 1, "c": 1}
    assert plan.reached_cases(cells, {"b": 1, "c": 1}) == 2


def test_covered_and_spread() -> None:
    cells = [Cell("a", "a", 3.0, 1000, 4), Cell("b", "b", 2.0, 1000, 1), Cell("c", "c", 0.0, 1000, 9)]
    p = plan.team_share(1000)
    assert plan.covered_cases(cells, {"a": 2}) == pytest.approx(4 * (1 - (1 - p) ** 2))
    assert plan.covered_cases(cells, {}) == 0.0
    assert plan.allocate_spread(cells, 5) == {"a": 1, "b": 1}  # lambda 0 never gets a team
    assert plan.allocate_spread(cells, 1) == {"a": 1}


def test_spread_extra_teams_go_to_next_highest_lambda_one_each() -> None:
    cells = [Cell(str(i), str(i), float(10 - i), 1000, 1) for i in range(6)]
    assert plan.allocate_spread(cells, 4) == {"0": 1, "1": 1, "2": 1, "3": 1}
    assert plan.allocate_spread(cells, 99) == {c.code: 1 for c in cells}


def test_team_share_bounds() -> None:
    assert plan.team_share(0) == 0.0
    for pop in (1, 100, 5000, 10**6):
        assert 0.0 < plan.team_share(pop) <= 1.0
    assert plan.team_share(100) <= 100 / (100 + plan.SATURATION_C)


def test_bootstrap_ci_brackets_and_is_reproducible() -> None:
    vals = [1.0, 2.0, 0.0, 3.0] * 20
    lo, hi = plan.block_bootstrap_ci(vals)
    assert lo <= sum(vals) <= hi
    assert (lo, hi) == plan.block_bootstrap_ci(vals)
    assert plan.block_bootstrap_ci([]) == (0.0, 0.0)
    assert plan.block_bootstrap_ci([2.0, 2.0, 2.0]) == (6.0, 6.0)


def _sh(i: int, region: str, lat: float, night: int | None, wk: int | None) -> dict[str, object]:
    return {"id": i, "name": f"s{i}", "region_code": region, "lat": lat, "lon": 127.0,
            "night_open": night, "weekend_open": wk}


def test_shelter_rules_unknown_excluded_and_counted() -> None:
    cells = [Cell("A", "서울 A", 2.0, 10000, 0)]
    sh = [
        _sh(1, "A", 37.00, 0, 1),      # closed at night: candidate
        _sh(2, "A", 37.50, None, None),  # unknown: excluded + counted
        _sh(3, "A", 37.90, 1, 1),      # open both: not a candidate, not unknown
        _sh(4, "A", 38.30, 1, None),   # partially unknown, not known-closed: excluded + counted
        _sh(5, "B", 37.00, 0, 0),      # region not in plan
    ]
    out, unknown = plan.pick_shelter_extensions(cells, sh, 10)
    assert [e["shelter_id"] for e in out] == [1] and unknown == 2
    assert "야간" in out[0]["reason"] and "예상 환자 2.0명" in out[0]["reason"]
    assert plan.pick_shelter_extensions(cells, sh, 0)[0] == []


def test_shelter_priority_prefers_isolated_and_higher_lambda() -> None:
    cells = [Cell("A", "A", 1.0, 10000, 0), Cell("B", "B", 3.0, 10000, 0)]
    sh = [
        _sh(1, "A", 37.0, 0, 0), _sh(2, "A", 37.0001, 0, 0),  # overlapping pair share one catchment
        _sh(3, "B", 37.0, 0, 0), _sh(4, "B", 38.0, 0, 0),     # two isolated shelters, higher lambda
    ]
    out, _ = plan.pick_shelter_extensions(cells, sh, 2)
    assert {e["shelter_id"] for e in out} == {3, 4}


@pytest.fixture
def hdb(settings: Settings, tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.connect(settings.db_path)
    db.migrate(c)
    fx.build_heat_db(c, tmp_path / "out")
    yield c
    c.close()


@pytest.fixture
def hclient(settings: Settings, hdb: sqlite3.Connection) -> Iterator[TestClient]:
    hdb.commit()
    with TestClient(create_app(settings)) as client:
        yield client


def test_api_plan_shape_and_values(hclient: TestClient) -> None:
    body = hclient.get(f"/heat/plan?date={D0}&teams=3&shelters=2").json()
    assert set(body) == {"date", "teams", "mode", "allocations", "shelter_extensions", "unknown_hours_count", "compare"}
    a = body["allocations"]
    assert body["mode"] == "spread" and [x["code"] for x in a] == ["11120", "11110", "26110"]
    assert [x["teams"] for x in a] == [1, 1, 1] and a[0]["actual_cases"] == 3
    assert set(a[0]) == {"code", "name", "teams", "expected_cases", "actual_cases", "reason"}
    assert a[0]["name"] == "서울특별시 중구" and "예상 환자" in a[0]["reason"]
    assert "체감 최고 35.1℃" in a[0]["reason"]  # D0 has an apparent max; D1 falls back to Tmax
    d1 = hclient.get(f"/heat/plan?date={fx.DATES[1]}&teams=3").json()["allocations"]
    assert any("최고기온 34.5℃" in x["reason"] for x in d1)
    assert body["compare"] == {"ours_reached": 3, "b1_reached": 0}
    one = hclient.get(f"/heat/plan?date={D0}&teams=1").json()
    assert [x["code"] for x in one["allocations"]] == ["11120"]
    stacked = hclient.get(f"/heat/plan?date={D0}&teams=3&mode=coverage").json()
    assert stacked["mode"] == "coverage" and [(x["code"], x["teams"]) for x in stacked["allocations"]] == [("11120", 3)]
    assert hclient.get(f"/heat/plan?date={D0}&mode=nope").status_code == 422
    ext = body["shelter_extensions"]
    assert [(e["shelter_id"], e["region_code"]) for e in ext] == [(2, "26110")] and "주말 닫힘" in ext[0]["reason"]
    assert body["unknown_hours_count"] == 1  # "far": both flags NULL, excluded and counted


def test_api_plan_defaults_and_validation(hclient: TestClient) -> None:
    assert hclient.get(f"/heat/plan?date={D0}").json()["teams"] == 10
    for q in ("teams=0", "teams=101", "shelters=-1", "shelters=51", "teams=x"):
        assert hclient.get(f"/heat/plan?date={D0}&{q}").status_code == 422
    assert hclient.get(f"/heat/plan?date={D0}&teams=100&shelters=50").status_code == 200
    assert hclient.get("/heat/plan?date=2024-12-25").status_code == 422  # off season
    assert hclient.get("/heat/plan").status_code == 422
    assert hclient.get("/heat/plan?date=2023-07-01").status_code == 422  # not a test year
    assert hclient.get(f"/heat/plan?date={D0}&variant=Z").status_code == 422


def test_plan_never_reads_case_table(hclient: TestClient, hdb: sqlite3.Connection) -> None:
    """Allocation depends on stored scores only: rewriting every case count changes actuals, not teams."""
    before = hclient.get(f"/heat/plan?date={D1}&teams=4").json()
    hdb.execute("UPDATE heat_case_daily SET n_cases = 7, n_indoor = 0, n_outdoor = 0 WHERE date >= '2024-01-01'")
    hdb.commit()
    after = hclient.get(f"/heat/plan?date={D1}&teams=4").json()

    def strip(b: dict[str, Any]) -> list[tuple[str, int, float]]:
        return [(x["code"], x["teams"], x["expected_cases"]) for x in b["allocations"]]

    assert strip(before) == strip(after) and before["allocations"][0]["actual_cases"] != after["allocations"][0]["actual_cases"]


def test_api_plan_eval(hclient: TestClient) -> None:
    body = hclient.get("/heat/plan/eval?year=2024&teams=3").json()
    assert set(body) == {"year", "teams", "mode", "n_days", "total_cases", "ours_reached", "b1_reached", "delta",
                         "ci_lo", "ci_hi", "per_day"}
    assert body["n_days"] == 3 and body["total_cases"] == 6
    # teams=3 covers all 3 regions: ours reaches every case. D0 3 vs 0; D1 3 vs 1 (B1 alerts 26110); D2 0 vs 0
    assert (body["ours_reached"], body["b1_reached"], body["delta"]) == (6, 1, 5)
    assert hclient.get("/heat/plan/eval?year=2024&teams=3&mode=coverage").json()["mode"] == "coverage"
    assert body["ci_lo"] <= body["delta"] <= body["ci_hi"]
    assert [p["date"] for p in body["per_day"]] == [D0, D1, D2]
    assert hclient.get("/heat/plan/eval?year=2024&teams=0").status_code == 422
    assert hclient.get("/heat/plan/eval?year=2024&teams=101").status_code == 422
    assert hclient.get("/heat/plan/eval?year=2023").status_code == 422
    assert hclient.get("/heat/plan/eval").status_code == 422


def test_api_plan_without_runs_is_409(settings: Settings) -> None:
    c = db.connect(settings.db_path)
    db.migrate(c)
    c.close()
    with TestClient(create_app(settings)) as client:
        assert client.get(f"/heat/plan?date={D0}").status_code == 409
        assert client.get("/heat/plan/eval?year=2024").status_code == 409


def _pooled_doc(years: tuple[int, int] = (2023, 2025)) -> dict[str, Any]:
    def st(e: float) -> dict[str, float]:
        return {"est": e, "lo": e - 0.1, "hi": e + 0.1}

    block = {
        "methods": {
            "OURS": {"alerts": 5.0, "capture": st(0.56), "precision": st(0.3)},
            "B1": {"alerts": 5.0, "capture": st(0.42), "precision": st(0.25)},
        },
        "delta": {"OURS-B1": {"capture": st(0.14), "precision": st(0.05), "boot_share_capture_delta_le_0": 0.0}},
    }
    return {"test_years": list(range(years[0], years[1] + 1)), "variants": {"F": {
        "folds": {}, f"pooled_{years[0]}_{years[1]}": {"B1|per_day": block}, "pooled_2024_2025": {"B1|per_day": {}},
    }}}


@pytest.fixture
def pooled_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    out = tmp_path / "heat_out"
    out.mkdir()
    monkeypatch.setenv("KCF_HEAT_OUT_DIR", str(out))
    return out


def test_pooled_endpoint_widest_block_sha_and_cache(
    hclient: TestClient, pooled_dir: Path
) -> None:
    import hashlib
    import json
    import os

    f = pooled_dir / "results.json"
    f.write_text(json.dumps(_pooled_doc()), encoding="utf-8")
    body = hclient.get("/heat/eval/pooled?variant=F").json()
    assert body["test_years"] == [2023, 2024, 2025] and body["pooled_key"] == "pooled_2023_2025"
    assert body["results_sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()
    rows = {(r["method"], r["metric"]): r for r in body["rows"]}
    assert [r["method"] for r in body["rows"]] == ["B1", "B1", "OURS", "OURS"]
    assert rows[("OURS", "capture")]["delta_vs_b1"] == {"value": 0.14, "ci_lo": pytest.approx(0.04), "ci_hi": pytest.approx(0.24)}
    assert rows[("B1", "capture")]["delta_vs_b1"] is None and rows[("B1", "capture")]["ci_lo"] == pytest.approx(0.32)
    # a re-run that adds 2022 is picked up without restart (mtime/size change invalidates the cache)
    f.write_text(json.dumps(_pooled_doc((2022, 2025))), encoding="utf-8")
    os.utime(f, ns=(f.stat().st_atime_ns, f.stat().st_mtime_ns + 10**9))
    again = hclient.get("/heat/eval/pooled").json()
    assert again["test_years"] == [2022, 2023, 2024, 2025] and again["results_sha256"] != body["results_sha256"]


def test_pooled_endpoint_errors(hclient: TestClient, pooled_dir: Path) -> None:
    r = hclient.get("/heat/eval/pooled")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_completed_run"
    (pooled_dir / "results.json").write_text("{not json", encoding="utf-8")
    assert hclient.get("/heat/eval/pooled").status_code == 409
    (pooled_dir / "results.json").write_text('{"variants": {"F": {"folds": {}}}}', encoding="utf-8")
    assert hclient.get("/heat/eval/pooled").status_code == 409  # no pooled block
    assert hclient.get("/heat/eval/pooled?variant=Z").status_code == 422
