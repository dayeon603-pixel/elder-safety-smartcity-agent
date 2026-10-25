"""Chaos / exceptional-situation tests (robustness audit).

Scenarios the product must survive without a crash, a stack trace, or a false "None"/"nan" in user text:
  * Ollama slow (read timeout), Ollama 5xx, Ollama answering 429, Ollama returning garbage -> deterministic
    fallback finishes inside the run budget.
  * Our own API answering 429 / 503 to the agent's self-calls -> failed steps, template briefing, run persisted.
  * A region-day the model never scored (in-season date of a test year with no stored cells) -> clean nulls,
    "평가 대상 아님" wording, plan with no allocations, CAP skipped because weather is unknown.
  * Season-edge dates (first/last day in, the day before/after out, leap day, impossible date).
  * SQLite write lock held by another connection -> reads still work (WAL); a write fails as a generic 500,
    and succeeds once the lock is released.
  * Oversized inputs -> 413/422, never 500.
These tests live beside the owners' suites and change no production code.
"""

from __future__ import annotations

import base64
import json
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db
from app.actions import cbs
from app.actions.models import ShelterRef
from app.agent import constants as C
from app.agent.ollama import OllamaClient
from app.agent.tools import PLANS
from app.config import Settings
from app.main import create_app
from app.security.headers import INTERNAL_ERROR_BODY
from tests import heat_fixture as fx
from tests import modules_fixture as mf

CODE, DATE = "11110", fx.DATES[1]
# In the fixture's test year and inside the season window, but no run stored a cell for it and the station has no row.
UNSCORED_DATE = "2024-07-04"
FALL: dict[str, Any] = {"trigger": "fall", "context": {"code": CODE, "date": DATE, "lat": 37.57, "lon": 126.98,
                                                       "est_speed": 0.4, "torso_angle": 78}}
BAD_TOKENS = ("None", "nan", "NaN", "null", "undefined", "Infinity")


def _client(settings: Settings, tmp_path: Path, with_modules: bool = False) -> Iterator[TestClient]:
    c = db.connect(settings.db_path)
    db.migrate(c)
    fx.build_heat_db(c, tmp_path / "out")
    if with_modules:
        from app.modules import load

        load.load_modules(c, mf.write_silver(tmp_path / "silver"), mf.write_aed(tmp_path / "aed"))
    c.close()
    with TestClient(create_app(settings)) as client:
        client.app.state.agent_http = client  # type: ignore[attr-defined]
        yield client


@pytest.fixture
def aclient(settings: Settings, tmp_path: Path) -> Iterator[TestClient]:
    yield from _client(settings, tmp_path)


@pytest.fixture
def mclient(settings: Settings, tmp_path: Path) -> Iterator[TestClient]:
    yield from _client(settings, tmp_path, with_modules=True)


def _ollama(client: TestClient, handler: Callable[[httpx.Request], httpx.Response]) -> None:
    http = httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))
    client.app.state.agent_ollama = OllamaClient(http)  # type: ignore[attr-defined]


def _tags(models: tuple[str, ...] = (C.PLANNER_MODEL, C.VLM_MODEL)) -> httpx.Response:
    return httpx.Response(200, json={"models": [{"name": m, "digest": "d" * 64} for m in models]})


def strings_in(obj: Any) -> Iterator[str]:
    """Every string value in a JSON-like tree (keys excluded)."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from strings_in(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings_in(v)


def assert_no_bad_tokens(obj: Any, *, allow_keys: tuple[str, ...] = ()) -> None:
    """No user-facing string carries a Python/JS null literal. Whole-word match so '영점' or 'nanometre' never trip it."""
    import re

    rx = re.compile(r"(?<![A-Za-z가-힣0-9])(" + "|".join(BAD_TOKENS) + r")(?![A-Za-z가-힣0-9])")
    offenders = [s for s in strings_in(obj) if rx.search(s)]
    assert not offenders, offenders[:5]


# ----------------------------------------------------------------------------- Ollama failure modes

def _slow_then_dead(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/api/tags":
        return _tags()
    raise httpx.ReadTimeout("slow model")


def _server_error(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/api/tags":
        return _tags()
    return httpx.Response(500, json={"error": "out of memory"})


def _too_many(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/api/tags":
        return _tags()
    return httpx.Response(429, json={"error": "busy"}, headers={"Retry-After": "7"})


def _garbage(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/api/tags":
        return _tags()
    if json.loads(req.content).get("stream"):
        return httpx.Response(200, text="not ndjson at all\n", headers={"content-type": "application/x-ndjson"})
    return httpx.Response(200, json={"message": {"role": "assistant", "content": "<html>oops</html>"}})


def _tags_malformed(req: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"models": "nope"})


@pytest.mark.parametrize("handler,label", [
    (_slow_then_dead, "slow"), (_server_error, "500"), (_too_many, "429"), (_garbage, "garbage"),
    (_tags_malformed, "tags-malformed"),
])
def test_ollama_failure_modes_fall_back_inside_budget(aclient: TestClient, handler: Callable[..., httpx.Response],
                                                      label: str) -> None:
    _ollama(aclient, handler)
    t0 = time.perf_counter()
    r = aclient.post("/agent/run", json=FALL)
    wall = time.perf_counter() - t0
    assert r.status_code == 200, (label, r.text)
    rec = r.json()
    assert rec["planner"] in ("deterministic", "llm+fallback"), (label, rec["planner"])
    assert rec["briefing_source"] == "template"
    # every required tool was still attempted and the simulators were reached
    tools = {s["tool"] for s in rec["steps"]}
    assert {"get_heat_region", "make_cap_alert", "make_fhir_bundle", "send_rsu_message", "send_119_report"} <= tools
    assert all(s["ok"] for s in rec["steps"] if s["tool"] in ("send_rsu_message", "send_119_report")), label
    # the run budget was respected (the deadline is monotonic; a slow model may not consume the whole budget)
    assert wall < C.RUN_BUDGET_S, (label, wall)
    assert rec["plan"]["budget_left_s"] >= 0
    # the LLM was not retried after its first failure (at most one plan turn, no brief turn after a failure)
    kinds = [t["kind"] for t in rec["plan"]["llm_turns"]]
    assert kinds.count("plan") <= 1, (label, kinds)
    if kinds and kinds[0] == "plan" and rec["plan"]["llm_turns"][0]["ok"] is False:
        assert "brief" not in kinds, label
    assert_no_bad_tokens(rec["briefing"])
    assert "시뮬레이터" in rec["briefing"]
    # persisted
    assert aclient.get(f"/agent/runs/{rec['run_id']}").status_code == 200


def test_ollama_fails_only_on_the_briefing_call(aclient: TestClient) -> None:
    """A valid plan, then a timeout while streaming the briefing: the template is used and nothing is lost."""
    # the registry's fall plan minus the VLM tool: a complete plan regardless of which read tools are required
    plan = json.dumps({"steps": [{"tool": t, "args": {}} for t in PLANS["fall"] if t != "describe_scene"]})

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/tags":
            return _tags()
        if json.loads(req.content).get("stream"):
            raise httpx.ReadTimeout("stalled mid-stream")
        return httpx.Response(200, json={"message": {"role": "assistant", "content": plan}})

    _ollama(aclient, handler)
    rec = aclient.post("/agent/run", json=FALL).json()
    assert rec["planner"] == "llm" and rec["briefing_source"] == "template"
    assert any("브리핑 LLM 호출 실패" in n for n in rec["notes"])
    assert rec["grounding"]["checked"] is False
    assert_no_bad_tokens(rec["briefing"])


# ----------------------------------------------------------------------------- upstream (our own API) failures

class _Upstream:
    """Wraps the TestClient as the agent's self-HTTP client, overriding some paths with a fixed status."""

    def __init__(self, inner: TestClient, override: dict[str, int]) -> None:
        self.inner = inner
        self.override = override
        self.calls: list[str] = []

    def request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        self.calls.append(path)
        for prefix, status in self.override.items():
            if path.startswith(prefix):
                hdrs = {"Retry-After": "3"} if status == 429 else {}
                return httpx.Response(status, json={"detail": {"code": "rate_limited" if status == 429 else "data_unavailable",
                                                               "message": "chaos"}}, headers=hdrs,
                                      request=httpx.Request(method, f"http://self{path}"))
        kw.pop("timeout", None)
        return self.inner.request(method, path, **kw)

    def close(self) -> None:  # the router closes clients it opened; ours is injected
        return None


def _down(_: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused")


@pytest.mark.parametrize("status", [429, 503])
def test_upstream_429_or_503_on_reads_is_a_failed_step_not_a_crash(aclient: TestClient, status: int) -> None:
    _ollama(aclient, _down)
    aclient.app.state.agent_http = _Upstream(aclient, {"/heat/region": status, "/actions": status})  # type: ignore[attr-defined]
    r = aclient.post("/agent/run", json=FALL)
    assert r.status_code == 200
    rec = r.json()
    by_tool = {s["tool"]: s for s in rec["steps"]}
    assert by_tool["get_heat_region"]["ok"] is False and str(status) in by_tool["get_heat_region"]["summary"]
    # get_actions degrades to "사용 불가" (ok, not failed) by design
    assert by_tool["get_actions"]["ok"] is True and by_tool["get_actions"]["result"]["available"] is False
    # the fall context carries lat/lon, so CAP/FHIR/RSU/119 still complete from trusted context
    for t in ("make_cap_alert", "make_fhir_bundle", "send_rsu_message", "send_119_report"):
        assert by_tool[t]["ok"] is True, t
    assert "지역 지표 조회 실패" in rec["briefing"]
    assert_no_bad_tokens(rec["briefing"])
    # the error detail message ("chaos") never reaches the step summary (only the code)
    assert "chaos" not in by_tool["get_heat_region"]["summary"]


def test_upstream_down_on_simulators_marks_sends_failed(aclient: TestClient) -> None:
    _ollama(aclient, _down)
    aclient.app.state.agent_http = _Upstream(aclient, {"/sim/": 503, "/cap/alerts": 503})  # type: ignore[attr-defined]
    rec = aclient.post("/agent/run", json=FALL).json()
    by_tool = {s["tool"]: s for s in rec["steps"]}
    assert by_tool["make_cap_alert"]["ok"] is False  # publishing refused -> the step fails loudly
    assert by_tool["send_rsu_message"]["ok"] is False and "선행 도구" in by_tool["send_rsu_message"]["summary"]
    assert by_tool["send_119_report"]["ok"] is False
    # the briefing must not claim a simulator received anything
    assert "수신 202" not in rec["briefing"]
    assert "전송 실패" in rec["briefing"] or "선행" in rec["briefing"] or "경보" in rec["briefing"]
    assert_no_bad_tokens(rec["briefing"])
    # health reports the failed last run as degraded (not fail): the DB and data are fine
    h = aclient.get("/health/deep").json()
    assert h["status"] in ("degraded", "fail") and h["checks"]["last_agent_run"]["ok"] is False


# ----------------------------------------------------------------------------- unscored region-day / missing weather

def test_unscored_day_gives_clean_nulls_everywhere(mclient: TestClient) -> None:
    """Date inside the season and the test year, but no run cell and no weather row exist for it."""
    region = mclient.get(f"/heat/region/{CODE}", params={"date": UNSCORED_DATE})
    assert region.status_code == 200
    d = region.json()
    assert d["scores"] == {"b1": None, "ours": None, "rank": None}
    assert d["drivers"]["tmax"] is None and d["drivers"]["apparent_max"] is None
    assert d["drivers"]["station"] is not None  # the station exists; only the day is missing

    alerts = mclient.get("/heat/alerts", params={"date": UNSCORED_DATE}).json()
    assert alerts["k"] == 0 and alerts["b1"] == [] and alerts["ours"] == [] and alerts["total_cases"] == 0

    plan = mclient.get("/heat/plan", params={"date": UNSCORED_DATE, "teams": 5}).json()
    assert plan["allocations"] == [] and plan["shelter_extensions"] == []
    assert plan["compare"] == {"ours_reached": 0, "b1_reached": 0}

    b = mclient.get(f"/briefing/{CODE}", params={"date": UNSCORED_DATE}).json()
    h = b["heat"]
    assert h["rank"] is None and h["alerted_b1"] is None and h["alerted_ours"] is None
    assert h["plan_allocation_for_region"] == {"teams": 0, "teams_total": 10, "expected_cases": None, "reason": None}
    assert h["evidence"]["expected_cases_lambda"] is None and h["evidence"]["n_cells"] is None
    heat_action = next(a for a in b["actions"] if a["action_id"] == "heat.alert")
    assert "평가 대상 아님" in heat_action["reason"] and "경보 대상이 아닙니다" in heat_action["text"]
    assert not any(a["action_id"] == "heat.teams" for a in b["actions"])
    assert_no_bad_tokens(b)


def test_agent_heat_daily_without_weather_skips_the_cap_with_a_reason(aclient: TestClient) -> None:
    _ollama(aclient, _down)
    rec = aclient.post("/agent/run", json={"trigger": "heat_daily", "context": {"code": CODE, "date": UNSCORED_DATE}}).json()
    by_tool = {s["tool"]: s for s in rec["steps"]}
    assert by_tool["get_heat_region"]["ok"] is True
    assert by_tool["get_heat_region"]["result"]["tmax_c"] is None
    assert by_tool["make_cap_alert"]["status"] == "skipped"
    assert "자료 없음" in by_tool["make_cap_alert"]["result"]["reason"]
    assert "최고기온 자료 없음" in rec["briefing"] and "경보 생략" in rec["briefing"]
    assert_no_bad_tokens(rec["briefing"])
    assert_no_bad_tokens([s["summary"] for s in rec["steps"]])


# ----------------------------------------------------------------------------- date edges

@pytest.mark.parametrize("date,status", [
    ("2024-05-20", 200),  # first season day
    ("2024-09-25", 200),  # last season day (inclusive)
    ("2024-05-19", 422),  # day before the season
    ("2024-09-26", 422),  # day after the season
    ("2024-02-29", 422),  # real leap day, outside the season
    ("2023-02-29", 422),  # impossible date
    ("2024-06-31", 422),  # impossible date inside the season months
    ("2024-7-01", 422),   # not zero-padded
    ("20240701", 422),
    ("2024-07-01T00:00", 422),
])
def test_season_and_calendar_edges(mclient: TestClient, date: str, status: int) -> None:
    for url in (f"/briefing/{CODE}", f"/heat/region/{CODE}", "/heat/alerts", "/heat/plan"):
        r = mclient.get(url, params={"date": date})
        assert r.status_code == status, (url, date, r.status_code)
        if status == 200:
            assert_no_bad_tokens(r.json())
        else:
            body = r.json()
            assert "detail" in body
            assert_no_bad_tokens(body)


def test_year_without_runs_inside_season_is_422_not_500(mclient: TestClient) -> None:
    r = mclient.get(f"/briefing/{CODE}", params={"date": "2025-07-22"})
    assert r.status_code == 422 and "not a test year" in r.json()["detail"]


# ----------------------------------------------------------------------------- SQLite lock

def _hold_write_lock(path: Path, held: threading.Event, release: threading.Event) -> None:
    other = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    try:
        other.execute("PRAGMA journal_mode = WAL")
        other.execute("BEGIN IMMEDIATE")
        held.set()
        release.wait(timeout=30)
        other.execute("ROLLBACK")
    finally:
        other.close()


def test_reads_survive_a_write_lock_and_writes_fail_generically(
    mclient: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db, "BUSY_TIMEOUT_MS", 300)  # keep the test fast; production waits 5 s
    held, release = threading.Event(), threading.Event()
    t = threading.Thread(target=_hold_write_lock, args=(settings.db_path, held, release), daemon=True)
    t.start()
    assert held.wait(5)
    try:
        # readers are not blocked in WAL mode
        assert mclient.get("/heat/meta").status_code == 200
        b = mclient.get(f"/briefing/{CODE}", params={"date": DATE})
        assert b.status_code == 200 and b.json()["heat"]["rank"] == 1
        # a write hits the busy timeout -> generic 500 with no SQL text or path leaked
        r = mclient.post("/feedback", json={"sgg_code": CODE, "date": DATE, "action_id": "heat.alert", "decision": "채택"})
        assert r.status_code == 500 and r.json() == INTERNAL_ERROR_BODY
        assert "sqlite" not in r.text.lower() and "locked" not in r.text.lower()
    finally:
        release.set()
        t.join(5)
    r = mclient.post("/feedback", json={"sgg_code": CODE, "date": DATE, "action_id": "heat.alert", "decision": "채택"})
    assert r.status_code == 201
    assert mclient.get("/feedback/summary", params={"sgg_code": CODE}).json()["total"] == 1


# ----------------------------------------------------------------------------- oversized / out-of-range inputs

def test_oversized_inputs_are_rejected_not_crashed(aclient: TestClient) -> None:
    _ollama(aclient, _down)
    huge = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * (C.MAX_IMAGE_BYTES + 1024)).decode()
    r = aclient.post("/agent/run", json={**FALL, "context": {**FALL["context"], "image_base64": huge}})
    assert r.status_code in (413, 422), r.status_code
    # an image that fits the body cap but is not an image: the VLM step fails, the run does not
    small_bad = base64.b64encode(b"GIF89a" + b"\x00" * 64).decode()
    _ollama(aclient, lambda req: _tags() if req.url.path == "/api/tags" else httpx.Response(500))
    rec = aclient.post("/agent/run", json={**FALL, "context": {**FALL["context"], "image_base64": small_bad}}).json()
    scene = next((s for s in rec["steps"] if s["tool"] == "describe_scene"), None)
    assert scene is not None and scene["ok"] is False and "JPEG/PNG" in scene["summary"]
    assert aclient.post("/feedback", json={"sgg_code": CODE, "date": DATE, "action_id": "x", "decision": "채택",
                                          "note": "n" * 201}).status_code == 422
    assert aclient.get("/heat/plan", params={"date": DATE, "teams": 0}).status_code == 422
    assert aclient.get("/heat/plan", params={"date": DATE, "teams": 10_000}).status_code == 422
    assert aclient.get("/heat/region/" + "1" * 40, params={"date": DATE}).status_code == 422
    assert aclient.get("/aed/candidates", params={"gu": "x" * 500}).status_code in (409, 422)


# ----------------------------------------------------------------------------- CBS length guard (pure function)

def test_cbs_mandatory_parts_longer_than_limit_raise_not_truncate() -> None:
    """A region name long enough to break the 90-char legacy limit raises; the router has no handler for it (500).
    Real 시군구 names are <= 8 characters, so this is a latent path, recorded as a risk in EDGE_CASES.md."""
    inp = cbs.CbsInput(region_name="가" * 70, month=7, day=22, tmax_c=35.0, rank=3, n_cells=250, shelters=(), hot=True)
    with pytest.raises(cbs.MessageTooLongError):
        cbs.build_cbs(inp, limit=90)
    # the longest real name in the 2026 unit table fits both limits
    longest = "전남광주통합특별시"
    draft = cbs.build_cbs(cbs.CbsInput(region_name=longest, month=7, day=22, tmax_c=None, rank=None, n_cells=0,
                                       shelters=(ShelterRef(name="아주긴이름의무더위쉼터경로당", region_code=None, distance_km=0.1,
                                                            night_open=None, weekend_open=None),), hot=False), limit=90)
    assert draft.length <= 90 and draft.easy_length <= 90
    assert_no_bad_tokens(draft.model_dump())
    assert "더위 주의" in draft.text  # hot=False and no temperature: never claims 폭염
