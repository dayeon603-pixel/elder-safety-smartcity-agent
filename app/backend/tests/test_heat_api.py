"""Heat endpoints against a tiny fixture database: shapes, ordering, errors, ETag, empty states."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import Settings
from app.main import create_app
from tests import heat_fixture as fx

D0, D1, D2 = fx.DATES


@pytest.fixture
def hclient(settings: Settings, tmp_path: Path) -> Iterator[TestClient]:
    c = db.connect(settings.db_path)
    db.migrate(c)
    fx.build_heat_db(c, tmp_path / "out")
    c.close()
    with TestClient(create_app(settings)) as client:
        yield client


def test_meta(hclient: TestClient) -> None:
    body = hclient.get("/heat/meta").json()
    assert set(body) == {"years", "variants", "methods", "season", "sources"}
    assert body["years"] == [2024] and body["variants"] == ["F"] and body["methods"] == ["B1", "OURS"]
    assert body["season"] == {"start": "05-20", "end": "09-25"}
    assert [s["name"] for s in body["sources"]] == ["heat_fixture"]
    assert set(body["sources"][0]) == {"name", "source_url", "license", "sha256", "fetched_at", "row_count"}


def test_eval_shape_and_delta(hclient: TestClient) -> None:
    rows = hclient.get("/heat/eval").json()
    assert set(rows[0]) == {"year", "method", "metric", "value", "ci_lo", "ci_hi", "delta_vs_b1"}
    ours = {r["metric"]: r for r in rows if r["method"] == "OURS"}
    assert ours["capture"]["value"] == 0.6 and ours["capture"]["delta_vs_b1"] == {"value": 0.35, "ci_lo": 0.1, "ci_hi": 0.5}
    assert ours["auc"]["delta_vs_b1"]["value"] == 0.1
    assert ours["n_alerts"]["ci_lo"] is None and ours["n_alerts"]["delta_vs_b1"] is None
    assert all(r["delta_vs_b1"] is None for r in rows if r["method"] == "B1")
    assert [r["metric"] for r in rows if r["method"] == "OURS"] == ["capture", "precision", "auc", "prauc", "n_alerts"]
    assert len(hclient.get("/heat/eval?budget=season_total").json()) == 3 + 3
    assert hclient.get("/heat/eval?variant=X").status_code == 422


def test_curve(hclient: TestClient) -> None:
    body = hclient.get("/heat/curve?year=2024&variant=F").json()
    assert body["b1_point"] == {"x": 1.0, "capture": 0.25}
    assert body["series"]["OURS"] == [{"x": 0.5, "capture": 0.3}, {"x": 1.0, "capture": 0.6}]
    assert list(body["series"]) == ["B1", "OURS"]
    assert hclient.get("/heat/curve?year=2023").status_code == 422  # not a test year
    assert hclient.get("/heat/curve").status_code == 422


def test_days_sorted_by_gap_then_date(hclient: TestClient) -> None:
    rows = hclient.get("/heat/days?year=2024").json()
    assert rows == [
        {"date": D0, "n_cases": 3, "k_budget": 1, "b1_captured": 0, "ours_captured": 3},
        {"date": D1, "n_cases": 3, "k_budget": 1, "b1_captured": 1, "ours_captured": 2},
        {"date": D2, "n_cases": 0, "k_budget": 1, "b1_captured": 0, "ours_captured": 0},
    ]
    by_date = hclient.get("/heat/days?year=2024&sort=date").json()
    assert [r["date"] for r in by_date] == sorted(r["date"] for r in rows)
    assert hclient.get("/heat/days?year=2024&sort=nope").status_code == 422


def test_regions_etag(hclient: TestClient) -> None:
    r = hclient.get("/heat/regions")
    assert r.status_code == 200 and [x["code"] for x in r.json()] == ["11110", "11120", "26110"]
    assert set(r.json()[0]) == {"code", "sido", "name", "lat", "lon", "pop65"}
    etag = r.headers["etag"]
    assert hclient.get("/heat/regions", headers={"If-None-Match": etag}).status_code == 304
    assert hclient.get("/heat/regions", headers={"If-None-Match": f"W/{etag}"}).status_code == 304
    assert hclient.get("/heat/regions", headers={"If-None-Match": '"other"'}).status_code == 200
    assert "x-content-type-options" in r.headers  # global security headers still apply


def test_alerts(hclient: TestClient) -> None:
    body = hclient.get(f"/heat/alerts?date={D0}").json()
    assert body["date"] == D0 and body["k"] == 1 and body["total_cases"] == 3
    assert body["b1"] == [{"code": "11110", "score": 30.0, "cases": 0}]
    assert body["ours"] == [{"code": "11120", "score": 2.5, "cases": 3}]
    assert body["cases"] == [{"code": "11120", "n": 3}]
    assert hclient.get(f"/heat/alerts?date={D2}").json()["cases"] == []


@pytest.mark.parametrize("date", ["2024-04-01", "2024-10-01", "2024-02-30", "20240701", "2024-7-1", "x"])
def test_bad_or_out_of_season_dates_are_422(hclient: TestClient, date: str) -> None:
    assert hclient.get(f"/heat/alerts?date={date}").status_code == 422
    assert hclient.get(f"/heat/region/11110?date={date}").status_code == 422


def test_non_test_year_is_422_and_unknown_variant_run_is_409(hclient: TestClient) -> None:
    assert hclient.get("/heat/alerts?date=2023-07-01").status_code == 422
    r = hclient.get(f"/heat/alerts?date={D0}&variant=O")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_completed_run"
    assert hclient.get("/heat/eval?variant=O").status_code == 409
    assert hclient.get("/heat/days?year=2024&variant=O").status_code == 409


def test_region_detail_with_shelters(hclient: TestClient) -> None:
    body = hclient.get(f"/heat/region/26110?date={D1}").json()
    assert set(body) == {"region", "cases_today", "scores", "drivers", "shelters"}
    assert body["region"]["pop"] == 3000 and body["cases_today"] == 1
    assert body["scores"]["b1"] == 32.0 and body["scores"]["ours"] == 1.0
    assert body["scores"]["rank"] == 3  # lowest of three scored cells that day
    assert body["drivers"]["old_share"] == 0.1 and body["drivers"]["tmax"] is None  # no station for 26110
    assert body["drivers"]["apparent_max"] is None and body["drivers"]["station"] is None
    assert body["shelters"]["coverage"] == "data"
    assert [s["name"] for s in body["shelters"]["nearby"]] == ["near", "far"]
    near = body["shelters"]["nearby"][0]
    assert set(near) == {"name", "dist_km", "night_open", "weekend_open"}
    assert near["night_open"] is True and near["weekend_open"] is False
    assert body["shelters"]["nearby"][1]["night_open"] is None  # unknown stays unknown
    assert near["dist_km"] < body["shelters"]["nearby"][1]["dist_km"]


def test_region_without_shelter_data_says_none_not_zero(hclient: TestClient) -> None:
    body = hclient.get(f"/heat/region/11110?date={D0}").json()
    assert body["shelters"] == {"coverage": "none", "nearby": []}
    assert body["drivers"]["tmax"] == 33.5 and body["drivers"]["apparent_max"] == 35.1
    assert body["drivers"]["station"] == {"id": "47108", "name": "서울", "source": "kma_asos", "dist_km": 5.0}
    assert body["drivers"]["hist_rate"] == 2.0  # only 2023-08-01 precedes 2024; 2 cases / 1 day
    assert body["scores"]["rank"] == 2 and body["cases_today"] == 0


def test_region_errors(hclient: TestClient) -> None:
    assert hclient.get(f"/heat/region/99999?date={D0}").status_code == 404
    assert hclient.get(f"/heat/region/abc?date={D0}").status_code == 422
    assert hclient.get("/heat/region/11110").status_code == 422


def test_empty_database_gives_empty_meta_and_409s(client: TestClient) -> None:
    meta = client.get("/heat/meta").json()
    assert meta["years"] == [] and meta["variants"] == [] and meta["methods"] == [] and meta["sources"] == []
    assert client.get("/heat/regions").json() == []
    for url in ("/heat/eval", "/heat/curve?year=2024", "/heat/days?year=2024", f"/heat/alerts?date={D0}"):
        r = client.get(url)
        assert r.status_code == 409 and "make heat-eval" in r.json()["detail"]["message"]


def test_heat_endpoints_are_read_only(hclient: TestClient) -> None:
    for path in ("/heat/meta", "/heat/eval", "/heat/regions"):
        assert hclient.post(path).status_code == 405


def test_latest_generation_wins(hclient: TestClient, settings: Settings, tmp_path: Path) -> None:
    import json

    from app.heat import persist

    out = tmp_path / "out"
    res = out / persist.RESULTS_NAME
    r = json.loads(res.read_text(encoding="utf-8"))
    r["code_sha256"] = {"evaluate.py": "9" * 64}
    cap = r["variants"]["F"]["folds"]["2024"]["budget"]["B1|per_day"]["methods"]["OURS"]["capture"]
    cap.update(est=0.7, lo=0.5, hi=0.9)
    res.write_text(json.dumps(r), encoding="utf-8")
    c = db.connect(settings.db_path)
    try:
        persist.persist(c, out / persist.PRED_NAME, res, out / persist.PANEL_NAME)
    finally:
        c.close()
    ours = {x["metric"]: x for x in hclient.get("/heat/eval").json() if x["method"] == "OURS"}
    assert ours["capture"]["value"] == 0.7
