"""Outbound webhook (signed, retried, audited), 공문 초안 DOCX export, and 마을방송 audio for CAP alerts."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from lxml import etree

from app import db
from app.agent import cap_audio
from app.agent import constants as C
from app.agent.ollama import OllamaClient
from app.config import ConfigError, Settings, load_settings
from app.documents import ACTION_DOCS, build_docx, render
from app.integrations import webhook
from app.main import create_app
from tests import heat_fixture as fx
from tests.conftest import TEST_SECRET

CODE, DATE = "11110", fx.DATES[1]
FALL: dict[str, Any] = {"trigger": "fall", "context": {"code": CODE, "date": DATE, "lat": 37.57, "lon": 126.98}}
SECRET = "whsec-" + "s" * 20
TOKEN = "demo-" + "t" * 20
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _settings(tmp_path: Path, **extra: str) -> Settings:
    env = {"KCF_SECRET_KEY": TEST_SECRET, "KCF_DB_PATH": str(tmp_path / "i.sqlite3"), "KCF_DATA_DIR": str(tmp_path),
           "KCF_AUDIO_DIR": str(tmp_path / "audio"), "KCF_RATE_CAPACITY": "1000", "KCF_RATE_REFILL_PER_S": "1000",
           "KCF_SIM_RATE_CAPACITY": "1000", "KCF_SIM_RATE_REFILL_PER_S": "1000", "KCF_AGENT_RATE_CAPACITY": "1000",
           "KCF_AGENT_RATE_REFILL_PER_S": "1000", "KCF_AUDIT_VERIFY_TTL_S": "0", **extra}
    return load_settings(env)


def _client(settings: Settings, tmp_path: Path) -> Iterator[TestClient]:
    c = db.connect(settings.db_path)
    db.migrate(c)
    fx.build_heat_db(c, tmp_path / "out")
    c.close()
    with TestClient(create_app(settings)) as client:
        client.app.state.agent_http = client  # type: ignore[attr-defined]
        client.app.state.webhook_http = lambda: client  # type: ignore[attr-defined]

        def down(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        client.app.state.agent_ollama = OllamaClient(  # type: ignore[attr-defined]
            httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(down)))
        yield client


def _join(client: TestClient) -> None:
    for t in list(client.app.state.webhook_threads):  # type: ignore[attr-defined]
        t.join(timeout=30)


# ---------------------------------------------------------------- webhook

def test_signature_roundtrip_and_rejections() -> None:
    body = webhook.canonical({"b": 1, "a": "x"})
    assert body == b'{"a":"x","b":1}'
    ts = "2026-10-24T05:00:00Z"
    sig = webhook.sign(b"k" * 16, ts, body)
    assert sig.startswith("sha256=") and len(sig) == 7 + 64
    now = datetime(2026, 10, 24, 5, 0, tzinfo=timezone.utc).timestamp()
    assert webhook.verify(b"k" * 16, ts, body, sig, now=now) is None
    assert webhook.verify(b"k" * 16, ts, body + b" ", sig, now=now) == "bad signature"
    assert webhook.verify(b"x" * 16, ts, body, sig, now=now) == "bad signature"
    assert webhook.verify(b"k" * 16, ts, body, sig, now=now + 10_000) == "stale timestamp"
    assert webhook.verify(b"k" * 16, "yesterday", body, sig, now=now) == "bad timestamp"


def test_deliver_retries_on_5xx_and_transport_errors_but_not_4xx() -> None:
    calls: list[int] = []
    answers = [httpx.ConnectError("x"), httpx.Response(503), httpx.Response(202)]

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        a = answers[len(calls) - 1]
        if isinstance(a, Exception):
            raise a
        return a

    slept: list[float] = []
    http = httpx.Client(transport=httpx.MockTransport(handler))
    r = webhook.deliver(http, "http://hook.test/x", b"k" * 16, "e", {"a": 1}, attempts=3, timeout_s=1, sleep=slept.append)
    assert r.ok and r.status == 202 and r.attempts == 3 and slept == [0.5, 1.0]
    calls.clear()
    http = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(401)))
    r = webhook.deliver(http, "http://hook.test/x", b"k" * 16, "e", {"a": 1}, attempts=3, timeout_s=1, sleep=slept.append)
    assert not r.ok and r.status == 401 and r.attempts == 1  # the receiver said no: do not hammer it


def test_approved_feedback_fires_signed_webhook_into_the_sink_and_audit(tmp_path: Path) -> None:
    settings = _settings(tmp_path, KCF_WEBHOOK_URL="http://testserver/sim/webhook", KCF_WEBHOOK_SECRET=SECRET,
                         KCF_DEMO_TOKEN=TOKEN)
    for c in _client(settings, tmp_path):
        h = {C.DEMO_TOKEN_HEADER: TOKEN}
        held = c.post("/feedback", json={"sgg_code": CODE, "date": DATE, "action_id": "cbs", "decision": "보류"}, headers=h)
        assert held.status_code == 201 and held.json()["webhook_started"] is False
        ok = c.post("/feedback", json={"sgg_code": CODE, "date": DATE, "action_id": "signals", "decision": "채택",
                                       "note": "경찰서 협조 요청"}, headers=h)
        assert ok.status_code == 201 and ok.json()["webhook_started"] is True
        _join(c)
        inbox = c.get("/sim/inbox?channel=webhook", headers=h).json()
        assert len(inbox) == 1 and inbox[0]["receipt_id"].startswith("whk-")
        item = c.get(f"/sim/inbox/{inbox[0]['receipt_id']}", headers=h).json()
        assert item["payload"]["event"] == "action.approved"
        assert item["payload"]["body"]["feedback"]["action_id"] == "signals" and item["payload"]["body"]["feedback"]["decision"] == "채택"
        conn = db.connect(settings.db_path)
        rows = conn.execute("SELECT action, detail FROM audit_log WHERE actor = 'webhook'").fetchall()
        assert [r["action"] for r in rows] == ["webhook.delivered"]
        detail = json.loads(rows[0]["detail"])
        assert detail["status"] == 202 and detail["attempts"] == 1 and detail["host"] == "testserver"
        conn.close()
        # forged / unsigned calls to the sink are refused and nothing is stored
        assert c.post("/sim/webhook", json={"x": 1}, headers=h).status_code == 401
        assert len(c.get("/sim/inbox?channel=webhook", headers=h).json()) == 1


def test_webhook_config_requires_secret() -> None:
    with pytest.raises(ConfigError, match="KCF_WEBHOOK_SECRET"):
        load_settings({"KCF_SECRET_KEY": TEST_SECRET, "KCF_WEBHOOK_URL": "http://127.0.0.1:9/hook"})
    with pytest.raises(ConfigError, match="KCF_WEBHOOK_URL"):
        load_settings({"KCF_SECRET_KEY": TEST_SECRET, "KCF_WEBHOOK_URL": "ftp://x", "KCF_WEBHOOK_SECRET": SECRET})
    assert load_settings({"KCF_SECRET_KEY": TEST_SECRET}).webhook_url is None


# ---------------------------------------------------------------- 공문 초안

def _docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert set(z.namelist()) == {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
        root = etree.fromstring(z.read("word/document.xml"))
    return "\n".join(t.text or "" for t in root.iter(f"{W}t"))


def test_docx_writer_is_valid_ooxml_and_deterministic() -> None:
    ev = {"date": DATE, "expected_cases_lambda": 0.338, "pop65": 94733, "apparent_max_c": 32.7, "tmax_c": 32.0,
          "station": {"id": "108", "name": "서울", "dist_km": 10.7}, "rank": 61, "n_cells": 250, "alerted": True,
          "data_sha256": {"heat_kma_asos_hourly": "a" * 64}, "model": "Poisson GBM"}
    title, meta, body, rows, footer = render("signals", "서울특별시 관악구", DATE, ev,
                                             {"signals": [{"crosswalk_id": "X1", "extend_s": 10}]}, {"signals": "src"})
    a = build_docx(title, meta, body, rows, footer)
    b = build_docx(title, meta, body, rows, footer)
    assert a == b and a[:2] == b"PK"
    text = _docx_text(a)
    assert "보행신호 시간 연장 협조 요청 (초안)" in text and "관할 경찰서" in text and "X1" in text
    assert "체감 최고 32.7℃" in text and "94,733명" in text and "250곳 중 61위" in text and "sha256 aaaaaaaaaaaaaaaa" in text
    assert "효력이 없" in text and "TTA 연계규격" in text and "주장하지 않" in text
    assert set(ACTION_DOCS) >= {"cbs", "bit", "signals", "route", "heat.alert", "heat.teams", "silver.check", "aed.convert"}
    with pytest.raises(KeyError):
        render("nope", "x", DATE, ev, {})


def test_document_endpoint_for_heat_actions(tmp_path: Path) -> None:
    for c in _client(_settings(tmp_path, KCF_DEMO_TOKEN=TOKEN), tmp_path):
        h = {C.DEMO_TOKEN_HEADER: TOKEN}
        assert c.get(f"/documents/heat.alert.docx?sgg_code={CODE}&date={DATE}").status_code == 401
        r = c.get(f"/documents/heat.alert.docx?sgg_code={CODE}&date={DATE}", headers=h)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.wordprocessingml")
        assert r.headers["x-document-status"] == "draft" and "heat.alert_11110" in r.headers["content-disposition"]
        text = _docx_text(r.content)
        assert "서울특별시 종로구" in text and "폭염 경보 대상 추가 검토 요청" in text and "65세 이상" in text
        assert "최고기온 34.5℃" in text  # D1 has Tmax only (no apparent max in the fixture)
        r2 = c.get(f"/documents/heat.teams.docx?sgg_code={CODE}&date={DATE}&teams=3", headers=h)
        assert r2.status_code == 200 and "방문 점검팀 배치" in _docx_text(r2.content)
        assert c.get(f"/documents/unknown.docx?sgg_code={CODE}&date={DATE}", headers=h).status_code == 404
        assert c.get(f"/documents/heat.alert.docx?sgg_code=99999&date={DATE}", headers=h).status_code == 404
        assert c.get(f"/documents/heat.alert.docx?sgg_code={CODE}&date=2024-01-01", headers=h).status_code == 422
        assert c.get(f"/documents/aed.convert.docx?sgg_code={CODE}&date={DATE}", headers=h).status_code == 404  # no AED data


# ---------------------------------------------------------------- 마을방송 audio + BIT

def test_audio_script_is_short_plain_korean() -> None:
    cap = {"alert": {"info": {"headline": "서울특별시 관악구 보행자 낙상 의심",
                              "instruction": "인근 차량 서행. 119 연계는 시뮬레이션으로만 수행."}}}
    s = cap_audio.script(cap)
    assert s == "서울특별시 관악구 보행자 낙상 의심. 인근 차량 서행. 훈련 방송입니다." and len(s) <= cap_audio.MAX_SCRIPT_CHARS
    long_cap = {"alert": {"info": {"headline": "가" * 80, "instruction": "나" * 40}}}
    assert len(cap_audio.script(long_cap)) <= cap_audio.MAX_SCRIPT_CHARS and cap_audio.script(long_cap).endswith("훈련 방송입니다.")


def test_cap_alert_carries_bit_lines_audio_link_and_feed_enclosure(tmp_path: Path) -> None:
    for c in _client(_settings(tmp_path), tmp_path):
        rec = c.post("/agent/run", json=FALL).json()
        cap = next(s for s in rec["steps"] if s["tool"] == "make_cap_alert")["result"]
        assert cap["bit_lines"] == [C.BIT_FALL_LINE1, C.BIT_FALL_LINE2] and all(len(x) <= 15 for x in cap["bit_lines"])
        assert cap["audio_url"].endswith("/audio.wav") and "훈련 방송입니다" in cap["audio_script"]
        detail = c.get(f"/cap/alerts/{cap['cap_id']}.json").json()
        assert detail["bit_lines"] == cap["bit_lines"] and detail["audio_url"] == cap["audio_url"] and detail["cap"]["alert"]
        feed = etree.fromstring(c.get("/cap/feed.atom").content)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        links = feed.findall("a:entry/a:link", namespaces=ns)
        assert {(x.get("rel"), x.get("type")) for x in links} == {("alternate", "application/cap+xml"), ("enclosure", "audio/wav")}
        assert "BIT: 횡단보도 낙상 의심 / 차량 서행 바랍니다" in (feed.findtext("a:entry/a:summary", namespaces=ns) or "")
        heat = c.post("/agent/run", json={"trigger": "heat_daily", "context": {"code": CODE, "date": DATE}}).json()
        hcap = next(s for s in heat["steps"] if s["tool"] == "make_cap_alert")["result"]
        assert hcap["bit_lines"] == ["오늘 최고 34℃ 폭염", "한낮 외출 자제"]  # Tmax 34.5 (fixture D1), hot
        r = c.get(cap["audio_url"])
        if not cap_audio.available():
            assert r.status_code == 503 and r.json()["detail"]["code"] == "tts_unavailable"
            return
        assert r.status_code == 200 and r.headers["content-type"].startswith("audio/wav")
        seconds = float(r.headers["x-audio-seconds"])
        assert 1.0 < seconds <= cap_audio.MAX_SECONDS and r.content[:4] == b"RIFF" and len(r.content) > 16000
        assert (tmp_path / "audio" / f"{cap['cap_id']}.wav").is_file()
        again = c.get(cap["audio_url"])  # cached
        assert again.status_code == 200 and again.headers["x-audio-seconds"] == r.headers["x-audio-seconds"]
