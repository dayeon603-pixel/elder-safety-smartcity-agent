"""POST /feedback (append-only human decisions) and GET /feedback/summary; demo-token and size guards."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db
from app.agent import constants as C
from app.agent.ollama import OllamaClient
from app.config import Settings, load_settings
from app.main import create_app
from tests import heat_fixture as fx
from tests.conftest import TEST_SECRET

CODE, DATE = "11110", fx.DATES[1]
FALL: dict[str, Any] = {"trigger": "fall", "context": {"code": CODE, "date": DATE, "lat": 37.57, "lon": 126.98}}


def _client(settings: Settings, tmp_path: Path) -> Iterator[TestClient]:
    c = db.connect(settings.db_path)
    db.migrate(c)
    fx.build_heat_db(c, tmp_path / "out")
    c.close()
    with TestClient(create_app(settings)) as client:
        client.app.state.agent_http = client  # type: ignore[attr-defined]

        def down(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        client.app.state.agent_ollama = OllamaClient(  # type: ignore[attr-defined]
            httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(down)))
        yield client


@pytest.fixture
def fclient(settings: Settings, tmp_path: Path) -> Iterator[TestClient]:
    yield from _client(settings, tmp_path)


def test_feedback_by_region_and_by_run_then_summary(fclient: TestClient, settings: Settings) -> None:
    r = fclient.post("/feedback", json={"sgg_code": CODE, "date": DATE, "action_id": "cbs", "decision": "채택",
                                        "note": "문자 초안 그대로 발송 검토"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"] == 1 and body["sgg_code"] == CODE and "재학습" in body["retraining"]
    run = fclient.post("/agent/run", json=FALL).json()
    r2 = fclient.post("/feedback", json={"run_id": run["run_id"], "action_id": "heat.alert", "decision": "보류",
                                         "actual_outcome": {"patients_confirmed": 0}})
    assert r2.status_code == 201 and r2.json()["sgg_code"] == CODE and r2.json()["date"] == DATE  # resolved from the run
    fclient.post("/feedback", json={"sgg_code": "26110", "date": DATE, "action_id": "cbs", "decision": "부적합"})
    s = fclient.get("/feedback/summary").json()
    assert s["total"] == 3 and s["by_decision"] == {"채택": 1, "보류": 1, "부적합": 1}
    assert s["by_action"]["cbs"] == {"채택": 1, "보류": 0, "부적합": 1} and s["by_action"]["heat.alert"]["보류"] == 1
    mine = fclient.get(f"/feedback/summary?sgg_code={CODE}").json()
    assert mine["total"] == 2 and "재학습" in mine["retraining"]
    conn = db.connect(settings.db_path)
    for sql in ("UPDATE feedback SET decision = '채택'", "DELETE FROM feedback"):
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            conn.execute(sql)
    conn.close()


def test_feedback_validation(fclient: TestClient) -> None:
    base = {"sgg_code": CODE, "date": DATE, "action_id": "cbs", "decision": "채택"}
    assert fclient.post("/feedback", json={**base, "decision": "maybe"}).status_code == 422
    assert fclient.post("/feedback", json={**base, "note": "x" * 201}).status_code == 422
    assert fclient.post("/feedback", json={"action_id": "cbs", "decision": "채택"}).status_code == 422  # no reference
    assert fclient.post("/feedback", json={"sgg_code": CODE, "action_id": "cbs", "decision": "채택"}).status_code == 422
    assert fclient.post("/feedback", json={**base, "action_id": "DROP TABLE"}).status_code == 422
    assert fclient.post("/feedback", json={**base, "extra": 1}).status_code == 422
    assert fclient.post("/feedback", json={**base, "run_id": "run-000000000000"}).status_code == 404
    assert fclient.post("/feedback", json={**base, "sgg_code": "99999"}).status_code == 404
    big = {**base, "actual_outcome": {f"k{i}": "v" for i in range(17)}}
    assert fclient.post("/feedback", json=big).status_code == 422
    deep: dict[str, Any] = {}
    cur = deep
    for _ in range(40):
        cur["a"] = {}
        cur = cur["a"]
    assert fclient.post("/feedback", json={**base, "actual_outcome": deep}).status_code == 422
    assert fclient.post("/feedback", json={**base, "note": "x" * 70000}).status_code == 413  # body cap
    assert fclient.get("/feedback/summary").json()["total"] == 0


def test_feedback_requires_demo_token_when_configured(tmp_path: Path) -> None:
    token = "demo-" + "t" * 20
    cfg = load_settings({"KCF_SECRET_KEY": TEST_SECRET, "KCF_DB_PATH": str(tmp_path / "d.sqlite3"),
                         "KCF_DATA_DIR": str(tmp_path), "KCF_DEMO_TOKEN": token, "KCF_RATE_CAPACITY": "1000",
                         "KCF_RATE_REFILL_PER_S": "1000", "KCF_SIM_RATE_CAPACITY": "1000",
                         "KCF_SIM_RATE_REFILL_PER_S": "1000"})
    for c in _client(cfg, tmp_path):
        body = {"sgg_code": CODE, "date": DATE, "action_id": "cbs", "decision": "채택"}
        assert c.post("/feedback", json=body).status_code == 401
        assert c.get("/feedback/summary").status_code == 401
        assert c.post("/feedback", json=body, headers={C.DEMO_TOKEN_HEADER: token}).status_code == 201
