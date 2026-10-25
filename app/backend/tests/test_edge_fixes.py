"""EDGE_CASES.md follow-ups: rank uncertainty (AED), gated heat rank text, λ lower bound, CBS 422, holiday table flag."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.actions import cbs
from app.actions import service as actions_service
from app.config import Settings
from app.heat import ranktext
from app.medical import hours
from app.modules.rank import poisson_rank
from tests import heat_fixture as fx


def test_poisson_rank_membership_and_interval() -> None:
    events = {"A": 60.0, "B": 30.0, "C": 25.0, "D": 20.0, "E": 15.0, "F": 12.0, "G": 3.0, "H": 2.0}
    factors = dict.fromkeys(events, 0.5)
    top = poisson_rank(events, factors, "A")
    assert top.rank_point == 1 and top.rank_lo == 1 and top.top_k_probability > 0.99 and "상위 5구 안" in top.text()
    mid = poisson_rank(events, factors, "F")
    assert mid.rank_point == 6 and mid.rank_lo <= 6 <= mid.rank_hi and mid.rank_hi - mid.rank_lo >= 2
    assert 0.05 < mid.top_k_probability < 0.95
    low = poisson_rank(events, factors, "H")
    assert low.rank_point == 8 and low.top_k_probability < 0.05 and "밖일 가능성" in low.text()
    assert poisson_rank(events, factors, "A") == top  # fixed seed -> deterministic
    with pytest.raises(KeyError):
        poisson_rank(events, factors, "Z")
    with pytest.raises(ValueError):
        poisson_rank({"A": -1.0}, {}, "A")
    d = low.as_dict()
    assert d["rank_interval"] == [low.rank_lo, low.rank_hi] and d["n_units"] == 8 and d["top_k"] == 5


def test_heat_rank_text_gates_on_case_history() -> None:
    assert ranktext.rank_text(61, 250, 120).mode == "exact" and ranktext.rank_text(61, 250, 120).text == "250곳 중 61위"
    band = ranktext.rank_text(61, 250, 30)
    assert band.mode == "band" and "상위 40% 안" in (band.text or "") and "30건" in (band.text or "")
    assert ranktext.rank_text(1, 250, 20).text is not None and "상위 20% 안" in (ranktext.rank_text(1, 250, 20).text or "")
    poor = ranktext.rank_text(61, 250, 7)
    assert poor.mode == "insufficient" and "자료 부족" in (poor.text or "")
    assert ranktext.rank_text(None, 250, 500).mode == "none" and ranktext.rank_text(3, 0, 500).text is None
    assert ranktext.expected_cases_text(2.4, 35.9) == ("예상 환자 2.4명", False)
    text, lower = ranktext.expected_cases_text(2.4, 36.0)
    assert lower and text.startswith("예상 환자 2.4명 이상(하한")


def test_cbs_uses_gated_rank_text() -> None:
    base = dict(region_name="관악구", month=7, day=22, tmax_c=34.2, rank=3, n_cells=250, shelters=())
    exact = cbs.build_cbs(cbs.CbsInput(**base, rank_text="250곳 중 3위"), 157).text
    assert "온열질환 위험 250곳 중 3위." in exact
    band = cbs.build_cbs(cbs.CbsInput(**base, rank_text="250곳 중 상위 20% 안 (과거 환자 25건, 순위 구간으로만 표시)"), 157).text
    assert "상위 20% 안" in band and "3위" not in band


def test_actions_maps_cbs_too_long_to_422(client: TestClient, monkeypatch: pytest.MonkeyPatch, settings: Settings,
                                          tmp_path: Path) -> None:
    from app import db

    c = db.connect(settings.db_path)
    fx.build_heat_db(c, tmp_path / "out")
    c.close()

    def boom(*_: object, **__: object) -> None:
        raise cbs.MessageTooLongError("120 chars > limit 90")

    monkeypatch.setattr(actions_service, "build_actions", boom)
    r = client.get(f"/actions/11110?date={fx.DATES[0]}&cbs_limit=90")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "cbs_too_long" and "90자" in r.json()["detail"]["message"]


def test_holiday_table_coverage_is_loud(caplog: pytest.LogCaptureFixture) -> None:
    assert hours.holiday_table_covers(date(hours.HOLIDAY_TABLE_END_YEAR, 6, 1))
    beyond = date(hours.HOLIDAY_TABLE_END_YEAR + 1, 2, 17)
    assert not hours.holiday_table_covers(beyond)
    hours._warned_years.discard(beyond.year)
    with caplog.at_level(logging.WARNING):
        assert hours.is_holiday(date(beyond.year, 3, 1)) is True  # fixed-date holidays still work
        assert hours.is_holiday(beyond) is False
    assert "holiday table covers" in caplog.text
    assert hours.is_holiday(date(2026, 2, 17)) and hours.holiday_table_covers(date(2026, 2, 17))


def test_health_deep_reports_holiday_table(client: TestClient) -> None:
    body = client.get("/health/deep").json()
    h = body["checks"]["holiday_table"]
    assert h["covered_through"] == hours.HOLIDAY_TABLE_END_YEAR and isinstance(h["ok"], bool) and "today" in h
