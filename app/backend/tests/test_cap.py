"""CAP 1.2 channel: XML rendering, OASIS XSD validation, POST /cap/alerts, Atom feed, per-alert XML, agent publish."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from lxml import etree

from app import db
from app.agent import cap_xml, standards
from app.agent.ollama import OllamaClient
from app.config import Settings
from app.main import create_app
from tests import heat_fixture as fx

CODE, DATE = "11110", fx.DATES[1]
FALL: dict[str, Any] = {"trigger": "fall", "context": {"code": CODE, "date": DATE, "lat": 37.57, "lon": 126.98}}
XSD_SHA256 = "b7798ef25868b068c97b268bda02d067c7d4ba9373adc5638bf37105804ee723"  # app/agent/xsd/README.md


def _cap(identifier: str = "zp-run-0123456789ab-11110") -> dict[str, Any]:
    return standards.build_cap(identifier=identifier, sent=datetime(2026, 10, 24, 5, 0, tzinfo=timezone.utc),
                               event="보행자 낙상 의심", headline="종로구 보행자 낙상 의심", description="d",
                               instruction="i", area_desc="서울특별시 종로구", lat=37.57, lon=126.98)


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
def cclient(settings: Settings, tmp_path: Path) -> Iterator[TestClient]:
    yield from _client(settings, tmp_path)


def test_vendored_xsd_is_the_oasis_schema() -> None:
    assert hashlib.sha256(cap_xml.XSD_PATH.read_bytes()).hexdigest() == XSD_SHA256
    root = etree.parse(str(cap_xml.XSD_PATH)).getroot()
    assert root.get("targetNamespace") == "urn:oasis:names:tc:emergency:cap:1.2" and root.get("version") == "1.2"


def test_generated_cap_passes_the_oasis_xsd_and_bad_ones_do_not() -> None:
    xml = cap_xml.to_xml(_cap())
    assert cap_xml.validate_xml(xml) == []
    tree = etree.fromstring(xml)
    ns = {"cap": cap_xml.CAP_NS}
    assert tree.findtext("cap:status", namespaces=ns) == "Exercise"
    assert tree.findtext("cap:info/cap:area/cap:circle", namespaces=ns) == "37.57,126.98 0.3"
    assert [c.tag.split("}")[1] for c in tree][:6] == ["identifier", "sender", "sent", "status", "msgType", "scope"]
    bad_status = _cap()
    bad_status["alert"]["status"] = "Real"
    assert any("Real" in e for e in cap_xml.validate_xml(cap_xml.to_xml(bad_status)))
    zulu = _cap()
    zulu["alert"]["sent"] = "2026-10-24T05:00:00Z"  # CAP forbids 'Z'
    assert cap_xml.validate_xml(cap_xml.to_xml(zulu))
    no_event = _cap()
    del no_event["alert"]["info"]["event"]
    assert cap_xml.validate_xml(cap_xml.to_xml(no_event))
    assert cap_xml.validate_xml(b"<alert>") and cap_xml.validate_xml(b"<x/>")
    with pytest.raises(cap_xml.CapXmlError):
        cap_xml.to_xml({"nope": 1})


def test_post_alert_validates_stores_and_feeds(cclient: TestClient) -> None:
    r = cclient.post("/cap/alerts", json={"cap": _cap()})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["xsd_valid"] is True and body["xml_url"] == "/cap/alerts/zp-run-0123456789ab-11110.xml"
    x = cclient.get(body["xml_url"])
    assert x.status_code == 200 and x.headers["content-type"].startswith("application/cap+xml")
    assert cap_xml.validate_xml(x.content) == []
    assert cclient.post("/cap/alerts", json={"cap": _cap()}).status_code == 409  # identifiers are unique
    bad = _cap("zp-run-0123456789ab-11120")
    bad["alert"]["status"] = "Real"
    rb = cclient.post("/cap/alerts", json={"cap": bad})
    assert rb.status_code == 422 and rb.json()["detail"]["code"] == "selfcheck_failed"
    bad2 = _cap("zp-run-0123456789ab-11130")
    bad2["alert"]["info"]["urgency"] = "Soon"  # passes the self-check, fails the XSD enumeration
    rb2 = cclient.post("/cap/alerts", json={"cap": bad2})
    assert rb2.status_code == 422 and rb2.json()["detail"]["code"] == "xsd_failed" and rb2.json()["detail"]["errors"]
    feed = cclient.get("/cap/feed.atom", headers={"X-Forwarded-Prefix": "/api"})
    assert feed.status_code == 200 and feed.headers["content-type"].startswith("application/atom+xml")
    atom = etree.fromstring(feed.content)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = atom.findall("a:entry", namespaces=ns)
    assert len(entries) == 1 and atom.findtext("a:title", namespaces=ns)
    link = entries[0].find("a:link", namespaces=ns)
    assert link is not None and link.get("type") == "application/cap+xml"
    assert link.get("href") == "/api/cap/alerts/zp-run-0123456789ab-11110.xml"
    assert cclient.get("/cap/alerts/zp-run-000000000000-00000.xml").status_code == 404
    assert cclient.get("/cap/alerts/..%2F..%2Fetc.xml").status_code in (404, 422)


def test_agent_run_publishes_its_cap_to_the_feed(cclient: TestClient, settings: Settings) -> None:
    rec = cclient.post("/agent/run", json=FALL).json()
    cap = next(s for s in rec["steps"] if s["tool"] == "make_cap_alert")
    assert cap["ok"] and cap["result"]["xsd_valid"] is True and "OASIS CAP 1.2 XSD 검증 통과" in cap["summary"]
    assert cap["http"][0]["path"] == "/cap/alerts" and cap["http"][0]["status"] == 201
    assert rec["cap_feed_url"] == "/cap/feed.atom"
    x = cclient.get(cap["result"]["xml_url"])
    assert x.status_code == 200 and cap_xml.validate_xml(x.content) == []
    feed = etree.fromstring(cclient.get("/cap/feed.atom").content)
    ids = [e.findtext("{http://www.w3.org/2005/Atom}id") for e in feed.findall("{http://www.w3.org/2005/Atom}entry")]
    assert ids == [f"urn:oasis:names:tc:emergency:cap:1.2:{cap['result']['cap_id']}"]
    conn = db.connect(settings.db_path)
    row = conn.execute("SELECT run_id, status, xsd_valid FROM cap_alert").fetchone()
    assert (row["run_id"], row["status"], row["xsd_valid"]) == (rec["run_id"], "Exercise", 1)
    with pytest.raises(Exception, match="immutable"):
        conn.execute("DELETE FROM cap_alert")
    conn.close()


FHIR_JAR = Path("/Users/chloekang/kcf/data/cache/extra/standards/validator_cli.jar")
JAVA = Path("/opt/homebrew/opt/openjdk/bin/java")


@pytest.mark.skipif(not (os.environ.get("KCF_RUN_FHIR_VALIDATOR") == "1" and FHIR_JAR.is_file() and JAVA.is_file()),
                    reason="official HL7 validator run only with KCF_RUN_FHIR_VALIDATOR=1 (needs java + cached jar)")
def test_official_hl7_validator_reports_no_errors(tmp_path: Path) -> None:
    """Runs hapifhir validator_cli.jar (FHIR R4 4.0.1) on a generated bundle; errors fail, warnings are reported."""
    import json
    import subprocess
    import uuid

    doc = standards.build_fhir_bundle(incident_id="zp-run-0123456789ab-11620", at=datetime(2026, 10, 24, 5, 0, tzinfo=timezone.utc),
                                      uuid_fn=lambda: uuid.uuid4().hex, address="서울특별시 관악구", lat=37.478, lon=126.951,
                                      tmax_c=32.0, torso_angle_deg=78.0, est_speed_mps=0.4, stage_label="일 체감 최고 32.7℃")
    src, out = tmp_path / "bundle.json", tmp_path / "out.json"
    src.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    subprocess.run([str(JAVA), "-jar", str(FHIR_JAR), str(src), "-version", "4.0.1", "-output", str(out)],
                   check=False, capture_output=True, timeout=900)
    issues = json.loads(out.read_text(encoding="utf-8")).get("issue", [])
    errors = [i for i in issues if i["severity"] in ("error", "fatal")]
    assert errors == [], errors
