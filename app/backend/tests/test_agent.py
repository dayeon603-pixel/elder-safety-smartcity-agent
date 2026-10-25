"""Action agent: schemas, guardrails, grounding, simulators round trip, fallback, immutability."""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import re
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import db
from app.agent import AgentEnv
from app.agent import constants as C
from app.agent import standards
from app.agent.guard import bind_context, check_briefing, check_grounding, collect_evidence, validate_schema
from app.agent.ollama import OllamaClient
from app.agent.tools import TOOLS, ToolError, clean_vlm_text, decode_image
from app.config import ConfigError, Settings, load_settings
from app.main import create_app
from app.routers import agent as agent_router
from app.security.ratelimit import AGENT_RUN_PATH, SIM_PREFIX, RateLimitMiddleware, TokenBucketLimiter
from tests import heat_fixture as fx
from tests.conftest import TEST_SECRET

CODE, DATE = "11110", fx.DATES[1]
FALL: dict[str, Any] = {"trigger": "fall", "context": {"code": CODE, "date": DATE, "lat": 37.57, "lon": 126.98,
                                       "est_speed": 0.4, "torso_angle": 78}}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PNG_B64 = base64.b64encode(PNG).decode()


def _client(settings: Settings, tmp_path: Path) -> Iterator[TestClient]:
    c = db.connect(settings.db_path)
    db.migrate(c)
    fx.build_heat_db(c, tmp_path / "out")
    c.close()
    with TestClient(create_app(settings)) as client:
        client.app.state.agent_http = client  # type: ignore[attr-defined]
        yield client


@pytest.fixture
def aclient(settings: Settings, tmp_path: Path) -> Iterator[TestClient]:
    yield from _client(settings, tmp_path)


def _ollama(client: TestClient, handler: Callable[[httpx.Request], httpx.Response]) -> None:
    http = httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))
    client.app.state.agent_ollama = OllamaClient(http)  # type: ignore[attr-defined]


def _down(_: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused")





def _ev() -> dict[str, Any]:
    return {"kind": "fall", "code": CODE, "date": DATE}


# ---------- schema / guard ----------

def test_every_tool_has_a_valid_object_schema() -> None:
    assert set(TOOLS) == {"get_heat_region", "get_heat_plan", "get_actions", "get_medical", "make_cap_alert",
                          "make_fhir_bundle", "send_rsu_message", "send_119_report", "describe_scene"}
    for t in TOOLS.values():
        assert t.parameters["type"] == "object" and t.parameters["additionalProperties"] is False
        assert t.spec()["function"]["name"] == t.name


def test_schema_rejects_bad_arguments() -> None:
    schema = TOOLS["get_heat_plan"].parameters
    assert validate_schema({"code": CODE, "date": DATE, "teams": 3}, schema) == []
    assert validate_schema({"code": "1", "date": DATE}, schema)
    assert validate_schema({"code": CODE, "date": DATE, "teams": True}, schema)  # bool is not integer
    assert validate_schema({"code": CODE, "date": DATE, "teams": 101}, schema)
    assert validate_schema({"code": CODE, "date": DATE, "x": 1}, schema)  # unexpected property
    assert validate_schema({"code": CODE}, schema) == ["$.date: required"]
    ev = TOOLS["make_cap_alert"].parameters
    assert validate_schema({"event": {**_ev(), "lat": 91}}, ev)
    assert validate_schema({"event": {**_ev(), "kind": "flood"}}, ev)


def test_bind_context_overrides_and_fills() -> None:
    ctx = {"code": CODE, "date": DATE, "lat": 37.57, "kind": "fall", "est_speed": 0.4}
    bound, notes = bind_context({"event": {"kind": "heat", "code": "99999", "date": DATE, "lat": 1.0,
                                           "torso_angle": 80}}, ctx)
    ev = bound["event"]
    assert (ev["code"], ev["kind"], ev["lat"], ev["est_speed"]) == (CODE, "fall", 37.57, 0.4)
    assert "torso_angle" not in ev  # invented measurement removed (context has none)
    assert len(notes) >= 4


# ---------- grounding ----------

def test_grounding_catches_fabricated_number() -> None:
    ev = collect_evidence({"tmax_c": 30.5, "old_share": 0.19857, "pop65": 94733, "rank": 130})
    assert check_grounding("최고기온 30.5℃", ev) == []
    assert check_grounding("최고기온 38.2℃", ev) == ["38.2"]
    assert check_grounding("65세 이상 비중 19.9%", ev) == []
    assert check_grounding("65세 이상 9.5만 명", ev) == []
    assert check_grounding("순위 131위", ev) == ["131"]
    assert check_grounding("receipt rsu-3f9a12bc77de 수신", ev) == []  # ids are not data
    assert check_grounding("1. 최고기온 30.5℃\n2. 순위 130위", ev) == []  # list markers are not data
    reasons = check_briefing("낙상 진단 38.2", ev)
    assert any("ungrounded" in r for r in reasons) and any("banned" in r for r in reasons)
    assert "missing required word: 시뮬레이터" in check_briefing("한글 30.5", ev, ("시뮬레이터",))


# ---------- simulators ----------

def _rsu_body() -> dict[str, Any]:
    t = fx_now()
    return standards.build_rsu_envelope(cap_identifier="zp-run-abc-1", now=t, lat=37.5, lon=127.0,
                                        cause_code=12, text="전방 보행자 주의", sequence=7)


def fx_now() -> Any:
    from datetime import datetime, timezone
    return datetime(2026, 10, 24, 5, 0, 0, tzinfo=timezone.utc)


def test_rsu_simulator_round_trip(aclient: TestClient) -> None:
    r = aclient.post("/sim/rsu", json=_rsu_body())
    assert r.status_code == 202
    receipt = r.json()["receipt_id"]
    assert r.json()["simulator"] == "RSU 시뮬레이터"
    stored = aclient.get(f"/sim/inbox/{receipt}").json()
    assert stored["channel"] == "rsu" and stored["payload"]["denm"]["situation"]["eventType"]["causeCode"] == 12
    assert [x["receipt_id"] for x in aclient.get("/sim/inbox?channel=rsu").json()] == [receipt]


def test_rsu_simulator_rejects_bad_messages(aclient: TestClient) -> None:
    bad = _rsu_body()
    bad["denm"]["situation"]["eventType"]["causeCode"] = 999
    assert aclient.post("/sim/rsu", json=bad).status_code == 422
    extra = _rsu_body()
    extra["denm"]["extra"] = 1
    assert aclient.post("/sim/rsu", json=extra).status_code == 422
    nolat = _rsu_body()
    nolat["denm"]["management"]["eventPosition"]["latitude"] = 95
    assert aclient.post("/sim/rsu", json=nolat).status_code == 422
    assert aclient.get("/sim/inbox").json() == []  # nothing stored


def _119_body() -> dict[str, Any]:
    t = fx_now()
    cap = standards.build_cap(identifier="zp-run-abc-1", sent=t, event="e", headline="h", description="d",
                              instruction="i", area_desc="a", lat=37.5, lon=127.0)
    fhir = standards.build_fhir_bundle(incident_id="zp-run-abc-1", at=t, uuid_fn=lambda: uuid.uuid4().hex,
                                       address="a", lat=37.5, lon=127.0, tmax_c=30.5,
                                       torso_angle_deg=None, est_speed_mps=None, stage_label="s")
    return {"simulator": "119 시뮬레이터", "cap": cap, "fhir": fhir}


def test_119_simulator_round_trip_and_validation(aclient: TestClient) -> None:
    r = aclient.post("/sim/119", json=_119_body())
    assert r.status_code == 202 and r.json()["receipt_id"].startswith("emg-")
    no_loc = _119_body()
    no_loc["fhir"]["entry"] = [e for e in no_loc["fhir"]["entry"] if e["resource"]["resourceType"] != "Location"]
    resp = aclient.post("/sim/119", json=no_loc)
    assert resp.status_code == 422 and any("Location" in e for e in resp.json()["detail"]["errors"])
    zulu = _119_body()
    zulu["cap"]["alert"]["sent"] = "2026-10-24T05:00:00Z"
    assert aclient.post("/sim/119", json=zulu).status_code == 422
    dangling = _119_body()
    dangling["fhir"]["entry"][-1]["resource"]["subject"] = {"reference": "urn:uuid:nope"}
    assert aclient.post("/sim/119", json=dangling).status_code == 422
    assert len(aclient.get("/sim/inbox?channel=119").json()) == 1


def test_fhir_full_urls_are_rfc4122_urns() -> None:
    doc = _119_body()["fhir"]
    for e in doc["entry"]:
        assert re.fullmatch(r"urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", e["fullUrl"])
    doc["entry"][0]["fullUrl"] = "urn:uuid:deadbeef"  # hex without dashes is not RFC 4122 text form
    assert any("RFC 4122" in err for err in standards.validate_fhir(doc))
    with pytest.raises(ValueError, match="UUID"):
        standards.build_fhir_bundle(incident_id="x", at=fx_now(), uuid_fn=lambda: "u0", address="a", lat=0, lon=0,
                                    tmax_c=None, torso_angle_deg=None, est_speed_mps=None, stage_label="s")


def test_sim_119_rejects_deep_json_and_large_bodies(aclient: TestClient) -> None:
    deep: dict[str, Any] = {}
    cur = deep
    for _ in range(40):
        cur["a"] = {}
        cur = cur["a"]
    body = _119_body()
    body["cap"]["deep"] = deep
    assert aclient.post("/sim/119", json=body).status_code == 422
    big = _119_body()
    big["cap"]["pad"] = "x" * (64 * 1024)
    assert aclient.post("/sim/119", json=big).status_code == 413
    assert aclient.get("/sim/inbox").json() == []


# ---------- orchestrator ----------

def _plan(*tools: str, **args_by_tool: dict[str, Any]) -> str:
    return json.dumps({"steps": [{"tool": t, "args": args_by_tool.get(t, {})} for t in tools]})


def _ndjson(text: str) -> str:
    """What Ollama streams for a chat: one JSON object per token chunk, then done."""
    chunks = [text[i:i + 4] for i in range(0, len(text), 4)]
    lines = [json.dumps({"message": {"role": "assistant", "content": c}, "done": False}, ensure_ascii=False)
             for c in chunks]
    lines.append(json.dumps({"message": {"role": "assistant", "content": ""}, "done": True}))
    return "\n".join(lines) + "\n"


def _llm(plan: str | None, final: str, models: tuple[str, ...] = (C.PLANNER_MODEL, C.VLM_MODEL),
         seen: list[dict[str, Any]] | None = None) -> Callable[[httpx.Request], httpx.Response]:
    """Ollama double: /api/tags, one structured plan call (`format`), a streamed briefing, the VLM."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m, "digest": "d" * 64} for m in models]})
        body = json.loads(req.content)
        if seen is not None:
            seen.append(body)
        if body["model"] == C.VLM_MODEL:
            return httpx.Response(200, json={"message": {"role": "assistant",
                                                         "content": "바닥에 누운 자세입니다. 주변에 차량은 보이지 않습니다."}})
        if body.get("stream"):
            return httpx.Response(200, text=_ndjson(final), headers={"content-type": "application/x-ndjson"})
        if plan is None:
            raise httpx.ReadTimeout("slow")
        return httpx.Response(200, json={"message": {"role": "assistant", "content": plan}})

    return handler


FALL_TOOLS = ("get_heat_region", "get_actions", "get_medical", "make_cap_alert", "make_fhir_bundle",
              "send_rsu_message", "send_119_report")
N_FALL = len(FALL_TOOLS)


def test_deterministic_fallback_when_ollama_unreachable(aclient: TestClient) -> None:
    _ollama(aclient, _down)
    rec = aclient.post("/agent/run", json=FALL).json()
    assert rec["planner"] == "deterministic" and "Ollama 응답 없음" in rec["planner_reason"]
    assert rec["model_name"] is None and rec["briefing_source"] == "template"
    tools = [s["tool"] for s in rec["steps"]]
    assert sorted(tools[:3]) == ["get_actions", "get_heat_region", "get_medical"]  # prefetch wave (parallel)
    assert sorted(tools[3:5]) == ["make_cap_alert", "make_fhir_bundle"]  # CAP ∥ FHIR
    assert sorted(tools[5:]) == ["send_119_report", "send_rsu_message"]  # RSU ∥ 119
    med = next(s for s in rec["steps"] if s["tool"] == "get_medical")
    assert med["ok"] and med["result"] == {"available": False, "http_status": 503}  # no key, no snapshot: soft
    assert all(s["ms"] >= 0 and s["source"] == "deterministic" for s in rec["steps"]) and rec["total_ms"] > 0
    rsu = next(s for s in rec["steps"] if s["tool"] == "send_rsu_message")
    emg = next(s for s in rec["steps"] if s["tool"] == "send_119_report")
    assert rsu["http"][0]["status"] == 202 and rsu["summary"].startswith("RSU 시뮬레이터 수신 202 (receipt rsu-")
    assert len(rsu["http"]) == 1 and len(emg["http"]) == 1  # per-step HTTP logs do not bleed across threads
    # the receipts in the log really are in the simulators' inboxes
    assert aclient.get(f"/sim/inbox/{rsu['result']['receipt_id']}").status_code == 200
    assert aclient.get(f"/sim/inbox/{emg['result']['receipt_id']}").status_code == 200
    assert "시뮬레이터" in rec["briefing"] and "결정론적 플래너 사용" in rec["notes"][0]
    evidence = collect_evidence([rec["context"], [s.get("result", {}) for s in rec["steps"]]])
    assert check_briefing(rec["briefing"], evidence, ("시뮬레이터",)) == []  # the template is itself grounded
    stored = aclient.get(f"/agent/runs/{rec['run_id']}").json()
    assert stored["steps"] == rec["steps"] and stored["planner"] == "deterministic"


def _run_llm_happy(client: TestClient, final: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    seen: list[dict[str, Any]] = []
    _ollama(client, _llm(_plan(*FALL_TOOLS), final, seen=seen))
    return client.post("/agent/run", json=FALL).json(), seen


def test_llm_happy_path_single_plan_call_and_grounded_briefing(aclient: TestClient) -> None:
    rec, seen = _run_llm_happy(aclient, "RSU와 119 시뮬레이터에 전달했습니다.")
    assert rec["planner"] == "llm" and rec["plan"]["first_try_valid"] is True
    assert rec["model_name"] == C.PLANNER_MODEL and rec["model_digest"] == "d" * 64
    assert [t["kind"] for t in rec["plan"]["llm_turns"]] == ["plan", "brief"]  # exactly two LLM calls
    plan_call = next(b for b in seen if b["model"] == C.PLANNER_MODEL and not b.get("stream"))
    assert plan_call["format"]["properties"]["steps"]["items"]["properties"]["tool"]["enum"] == list(FALL_TOOLS)
    assert plan_call["options"]["temperature"] == 0 and plan_call["options"]["seed"] == 42
    by_tool = {s["tool"]: s for s in rec["steps"]}
    assert all(s["ok"] and s["status"] == "ok" for s in by_tool.values()) and len(by_tool) == N_FALL
    assert {by_tool[t]["source"] for t in ("get_heat_region", "get_actions", "get_medical")} == {"prefetch"}
    assert all(by_tool[t]["planned_by_llm"] for t in ("get_heat_region", "get_actions", "get_medical"))
    assert {by_tool[t]["source"] for t in FALL_TOOLS[3:]} == {"llm"}
    assert rec["briefing_source"] == "llm" and rec["grounding"]["ok"] is True


def test_fabricated_number_in_briefing_falls_back_to_template(aclient: TestClient) -> None:
    rec, _ = _run_llm_happy(aclient, "최고기온 38.2℃였고 시뮬레이터에 전달했습니다.")
    assert rec["briefing_source"] == "template"
    assert rec["grounding"]["ok"] is False and "ungrounded number: 38.2" in rec["grounding"]["reasons"]
    assert rec["grounding"]["rejected_llm_text"].startswith("최고기온 38.2")
    assert "38.2" not in rec["briefing"]


def test_briefing_without_simulator_label_is_rejected(aclient: TestClient) -> None:
    rec, _ = _run_llm_happy(aclient, "전달했습니다.")
    assert rec["briefing_source"] == "template" and "missing required word: 시뮬레이터" in rec["grounding"]["reasons"]


def test_step_latency_is_not_evidence(aclient: TestClient) -> None:
    """A number that only appears as a step's ms (or a coordinate) cannot ground a briefing figure."""
    rec, _ = _run_llm_happy(aclient, "최고기온 37.57℃, 시뮬레이터 전달.")  # 37.57 is the context latitude
    assert rec["briefing_source"] == "template" and "ungrounded number: 37.57" in rec["grounding"]["reasons"]


def test_rejections_then_fallback_complete_the_actions(aclient: TestClient) -> None:
    plan = json.dumps({"steps": [
        {"tool": "drop_database", "args": {}},
        {"tool": "send_119_report", "args": {"bundle": {"bundle_id": "x"}}},
        {"tool": "get_heat_region", "args": {"code": "bad", "date": DATE}},
        {"tool": "get_heat_region", "args": {}},
        "make_cap_alert",  # not even an object
    ]})
    _ollama(aclient, _llm(plan, "한글 브리핑 시뮬레이터"))
    rec = aclient.post("/agent/run", json=FALL).json()
    reasons = [r["reason"] for r in rec["plan"]["rejections"]]
    assert "허용되지 않은 도구: drop_database" in reasons[0] and "이미 계획한 도구" in reasons[1]
    assert rec["planner"] == "llm+fallback" and rec["plan"]["first_try_valid"] is False
    assert "빠진 필수 도구" in rec["planner_reason"]
    by_tool = {s["tool"]: s for s in rec["steps"]}
    assert all(s["ok"] for s in by_tool.values()) and len(by_tool) == N_FALL
    assert by_tool["send_119_report"]["source"] == "llm"  # planned, with the server-issued bundle id
    assert any("bundle_id" in n for n in by_tool["send_119_report"]["arg_corrections"])
    assert by_tool["make_fhir_bundle"]["source"] == "deterministic_fallback"


def test_context_binding_stops_redirected_alert(aclient: TestClient) -> None:
    plan = _plan("get_heat_region", "make_cap_alert",
                 make_cap_alert={"event": {"kind": "fall", "code": "99999", "date": DATE, "lat": 1.0, "lon": 2.0}})
    _ollama(aclient, _llm(plan, "x"))
    rec = aclient.post("/agent/run", json=FALL).json()
    cap = next(s for s in rec["steps"] if s["tool"] == "make_cap_alert")
    assert cap["args"]["event"]["code"] == CODE and cap["args"]["event"]["lat"] == 37.57
    assert any("99999" in n for n in cap["arg_corrections"]) and cap["source"] == "llm"


def test_unparseable_plan_falls_back_and_llm_is_not_retried_after_failure(aclient: TestClient) -> None:
    _ollama(aclient, _llm("not json", "x"))
    rec = aclient.post("/agent/run", json=FALL).json()
    assert rec["planner"] == "llm+fallback" and "JSON" in rec["planner_reason"]
    assert all(s["ok"] for s in rec["steps"]) and len(rec["steps"]) == N_FALL
    # a transport failure on the plan call marks the LLM dead: no briefing call follows
    _ollama(aclient, _llm(None, "x"))
    rec = aclient.post("/agent/run", json=FALL).json()
    assert rec["planner"] == "llm+fallback" and "LLM 호출 실패" in rec["planner_reason"]
    assert [t["kind"] for t in rec["plan"]["llm_turns"]] == ["plan"] and rec["briefing_source"] == "template"
    assert any("이후 LLM 호출 생략" in n for n in rec["notes"])


def test_run_budget_exhausted_skips_llm(settings: Settings, tmp_path: Path) -> None:
    for c in _client(settings, tmp_path):
        _ollama(c, _llm(_plan(*FALL_TOOLS), "x"))
        c.app.state.agent_budget_s = 0.0  # type: ignore[attr-defined]
        from app.agent import orchestrator as orch
        env = AgentEnv(http=c, ollama=c.app.state.agent_ollama, now=fx_now,  # type: ignore[attr-defined]
                       uuid_hex=lambda: uuid.uuid4().hex, budget_s=0.0)
        rec = orch.run_agent(env, "fall", FALL["context"])
        assert rec["planner"] == "llm+fallback" and rec["plan"]["llm_turns"] == []
        assert any("예산" in n for n in rec["notes"]) and all(s["ok"] for s in rec["steps"])


def test_tool_bug_is_a_failed_step_not_a_crashed_run(aclient: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agent import tools as tools_mod
    broken = dataclasses.replace(TOOLS["get_actions"], run=lambda tc, a: {}["missing"])  # KeyError inside a tool
    monkeypatch.setitem(tools_mod.TOOLS, "get_actions", broken)
    _ollama(aclient, _down)
    r = aclient.post("/agent/run", json=FALL)
    assert r.status_code == 200
    step = next(s for s in r.json()["steps"] if s["tool"] == "get_actions")
    assert step["status"] == "failed" and "KeyError" in step["summary"]
    assert len(r.json()["steps"]) == N_FALL


def test_snapshot_is_never_stored_and_vlm_text_is_labelled(aclient: TestClient, settings: Settings) -> None:
    _ollama(aclient, _llm("x", "x"))
    ctx = {**FALL["context"], "image_base64": "data:image/png;base64," + PNG_B64[:10] + "\n" + PNG_B64[10:]}
    rec = aclient.post("/agent/run", json={**FALL, "context": ctx}).json()
    scene = next(s for s in rec["steps"] if s["tool"] == "describe_scene")
    assert scene["ok"] and scene["result"]["image_sha256"] == hashlib.sha256(PNG).hexdigest()
    assert rec["image_sha256"] == hashlib.sha256(PNG).hexdigest() and rec["vlm_name"] == C.VLM_MODEL
    assert f"{C.VLM_LABEL}: 바닥에 누운 자세입니다." in rec["briefing"]
    conn = sqlite3.connect(settings.db_path)
    dump = "".join(str(r) for r in conn.execute("SELECT * FROM agent_run")) + "".join(
        str(r) for r in conn.execute("SELECT * FROM sim_inbox"))
    assert PNG_B64 not in dump and "iVBORw0KGgo" not in dump
    conn.close()


def test_vlm_missing_degrades_gracefully(aclient: TestClient) -> None:
    _ollama(aclient, _llm("x", "x", models=(C.PLANNER_MODEL,)))
    rec = aclient.post("/agent/run", json={**FALL, "context": {**FALL["context"], "image_base64": PNG_B64}}).json()
    assert "describe_scene" not in [s["tool"] for s in rec["steps"]]
    assert any("VLM" in n for n in rec["notes"])


def test_image_validation_and_vlm_filter() -> None:
    with pytest.raises(ToolError):
        decode_image("not base64!!")
    with pytest.raises(ToolError):
        decode_image(base64.b64encode(b"GIF89a....").decode())
    assert decode_image(PNG_B64) == PNG
    assert clean_vlm_text("하나. 둘. 셋.") == ("하나. 둘.", False)
    assert clean_vlm_text("뇌졸중으로 보입니다.") == ("", True)
    assert clean_vlm_text("여성이 누워 있습니다.") == ("", True)
    assert clean_vlm_text("사람 2명이 http://x.y 앞에 있습니다.") == ("사람 명이 앞에 있습니다.", False)


def test_request_validation(aclient: TestClient) -> None:
    assert aclient.post("/agent/run", json={"trigger": "fall", "context": {"code": CODE, "date": DATE}}).status_code == 422
    assert aclient.post("/agent/run", json={"trigger": "nope", "context": FALL["context"]}).status_code == 422
    assert aclient.post("/agent/run", json={"trigger": "fall", "context": {**FALL["context"], "code": "１１１１０"}}).status_code == 422


def test_heat_daily_runs_without_position_and_caps_only_when_hot(aclient: TestClient) -> None:
    _ollama(aclient, _down)
    rec = aclient.post("/agent/run", json={"trigger": "heat_daily", "context": {"code": CODE, "date": DATE}}).json()
    tools = [s["tool"] for s in rec["steps"]]
    assert sorted(tools[:3]) == ["get_actions", "get_heat_plan", "get_heat_region"] and tools[3] == "make_cap_alert"
    assert rec["steps"][3]["status"] == "ok" and "폭염 일일 브리핑" in rec["briefing"]  # Tmax 34.5 >= 33
    # 26110 has no station (Tmax unknown) and is not alerted: no CAP, and no "None" in the text
    rec = aclient.post("/agent/run", json={"trigger": "heat_daily", "context": {"code": "26110", "date": DATE}}).json()
    cap = next(s for s in rec["steps"] if s["tool"] == "make_cap_alert")
    assert cap["status"] == "skipped" and cap["ok"] and "경보 대상 아님" in cap["summary"]
    assert "None" not in rec["briefing"] and "경보 생략" in rec["briefing"] and "자료 없음" in rec["briefing"]
    assert rec["grounding"]["template_check"] == []


def test_runs_are_immutable(aclient: TestClient, settings: Settings) -> None:
    _ollama(aclient, _down)
    rec = aclient.post("/agent/run", json=FALL).json()
    aclient.post("/sim/rsu", json=_rsu_body())
    conn = db.connect(settings.db_path)
    for sql in ("UPDATE agent_run SET briefing = 'x'", "DELETE FROM agent_run",
                "UPDATE sim_inbox SET channel = '119'", "DELETE FROM sim_inbox"):
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            conn.execute(sql)
    assert conn.execute("SELECT briefing FROM agent_run WHERE run_id = ?", (rec["run_id"],)).fetchone()[0] == rec["briefing"]
    conn.close()


# ---------- streaming, auth, concurrency, loopback ----------

def _parse_sse(text: str) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        event, data = "message", []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        if data:
            out.append((event, json.loads("\n".join(data))))
    return out


def test_sse_streams_each_step_then_the_final_record(aclient: TestClient) -> None:
    _ollama(aclient, _llm(_plan(*FALL_TOOLS), "RSU와 119 시뮬레이터에 전달했습니다."))
    r = aclient.post("/agent/run", json=FALL, headers={"Accept": "text/event-stream"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(r.text)
    kinds = [k for k, _ in events]
    assert kinds[0] == "status" and kinds[-1] == "result" and kinds.count("step") == N_FALL
    assert "briefing_delta" in kinds and kinds.index("briefing_delta") > kinds.index("step")
    steps = [d for k, d in events if k == "step"]
    assert all({"n", "tool", "source", "status", "ok", "ms", "summary"} <= set(s) for s in steps)
    final = events[-1][1]
    assert "".join(d["text"] for k, d in events if k == "briefing_delta") == final["briefing"]
    assert final["briefing_source"] == "llm" and final["grounding"]["ok"] is True
    assert aclient.get(f"/agent/runs/{final['run_id']}").json()["steps"] == final["steps"]  # persisted first


def test_non_streaming_response_is_unchanged(aclient: TestClient) -> None:
    _ollama(aclient, _down)
    r = aclient.post("/agent/run", json=FALL, headers={"Accept": "application/json"})
    assert r.headers["content-type"].startswith("application/json") and "steps" in r.json()


def test_busy_returns_503_immediately(aclient: TestClient) -> None:
    slots: threading.BoundedSemaphore = aclient.app.state.agent_slots  # type: ignore[attr-defined]
    held = [slots.acquire(blocking=False) for _ in range(C.MAX_CONCURRENT_RUNS)]
    try:
        r = aclient.post("/agent/run", json=FALL)
        assert r.status_code == 503 and r.json()["detail"]["code"] == "busy" and r.headers["Retry-After"]
    finally:
        for h in held:
            if h:
                slots.release()
    _ollama(aclient, _down)
    assert aclient.post("/agent/run", json=FALL).status_code == 200  # slot released


def test_demo_token_guards_agent_and_sim_routes(tmp_path: Path) -> None:
    token = "demo-" + "t" * 20
    cfg = load_settings({"KCF_SECRET_KEY": TEST_SECRET, "KCF_DB_PATH": str(tmp_path / "d.sqlite3"),
                         "KCF_DATA_DIR": str(tmp_path), "KCF_DEMO_TOKEN": token, "KCF_RATE_CAPACITY": "1000",
                         "KCF_RATE_REFILL_PER_S": "1000", "KCF_AGENT_RATE_CAPACITY": "1000",
                         "KCF_AGENT_RATE_REFILL_PER_S": "1000", "KCF_SIM_RATE_CAPACITY": "1000",
                         "KCF_SIM_RATE_REFILL_PER_S": "1000"})
    for c in _client(cfg, tmp_path):
        _ollama(c, _down)
        assert c.post("/agent/run", json=FALL).status_code == 401
        assert c.post("/sim/rsu", json=_rsu_body()).status_code == 401
        assert c.get("/sim/inbox").status_code == 401 and c.get("/agent/runs").status_code == 401
        assert c.post("/sim/rsu", json=_rsu_body(), headers={C.DEMO_TOKEN_HEADER: token[:-1] + "x"}).status_code == 401
        ok = c.post("/agent/run", json=FALL, headers={C.DEMO_TOKEN_HEADER: token})
        assert ok.status_code == 200
        # the agent's own simulator calls carried the token: both sends were accepted
        assert all(s["ok"] for s in ok.json()["steps"]) and len(c.get("/sim/inbox", headers={C.DEMO_TOKEN_HEADER: token}).json()) == 2


def test_short_demo_token_is_rejected_at_startup() -> None:
    with pytest.raises(ConfigError, match="KCF_DEMO_TOKEN"):
        load_settings({"KCF_SECRET_KEY": TEST_SECRET, "KCF_DEMO_TOKEN": "short"})


def test_agent_self_url_is_loopback_only() -> None:
    env = {"KCF_SECRET_KEY": TEST_SECRET}
    assert load_settings({**env, "KCF_AGENT_SELF_URL": "http://127.0.0.1:8010/"}).agent_self_url == "http://127.0.0.1:8010"
    for bad in ("http://evil.example", "https://127.0.0.1:1", "http://127.0.0.1:1/path", "http://10.0.0.1:8000"):
        with pytest.raises(ConfigError, match="KCF_AGENT_SELF_URL"):
            load_settings({**env, "KCF_AGENT_SELF_URL": bad})


def test_self_calls_ignore_the_host_header(settings: Settings) -> None:
    scope = {"type": "http", "server": ("0.0.0.0", 8123), "headers": [(b"host", b"evil.example:9")],
             "method": "GET", "path": "/", "query_string": b"", "scheme": "http"}
    assert agent_router.self_base_url(Request(scope), settings) == "http://127.0.0.1:8123"


def test_agent_run_rate_limit_does_not_cover_run_listing() -> None:
    mw = RateLimitMiddleware(lambda s, r, se: None, general=TokenBucketLimiter(1, 1),
                             routes=((AGENT_RUN_PATH, TokenBucketLimiter(2, 2)), (SIM_PREFIX, TokenBucketLimiter(3, 3))))
    assert mw._limiter_for("/agent/run").capacity == 2 and mw._limiter_for("/agent/runs").capacity == 1
    assert mw._limiter_for("/agent/runs/run-0123456789ab").capacity == 1 and mw._limiter_for("/sim/rsu").capacity == 3


# ---------- grounding regressions from review ----------

def test_literals_match_with_digit_boundaries() -> None:
    assert check_grounding("1195명이 다쳤습니다", [5.0]) == ["1195"]  # "119" must not eat the 1195
    assert check_grounding("119에 신고했습니다", [5.0]) == []
    assert check_grounding("CAP 1.2 형식 경보", []) == [] and check_grounding("CAP 1.25 경보", []) == ["1.25"]
    assert check_grounding("61%", [61.0]) == ["61"] and check_grounding("61%", [0.61]) == []
    assert check_grounding("차이 -3.2", [3.2]) == ["-3.2"] and check_grounding("차이 -3.2", [-3.2]) == []
    assert check_grounding("12) 5명", [5.0]) == ["12"]  # two-digit "list markers" are numbers
    assert check_grounding("run-0123456789ab · rsu-0123456789ab", []) == []
    assert check_grounding("ab-123456 38.2", []) == ["123456", "38.2"]  # the old loose id pattern is gone


def test_none_and_conformance_claims_are_banned() -> None:
    assert "banned term: None" in check_briefing("최고기온 None℃ 시뮬레이터", [])
    assert "banned term: 준수" in check_briefing("CAP 1.2를 준수합니다 시뮬레이터", [])


def test_evidence_excludes_coordinates_ids_and_latency() -> None:
    ev = collect_evidence({"lat": 37.57, "lon": 126.98, "ms": 12.3, "receipt_id": "rsu-0123456789ab",
                           "http": [{"ms": 5}], "tmax_c": 30.5, "nested": {"lat": 1.0, "pop65": 94733}})
    assert ev == [30.5, 94733.0]
