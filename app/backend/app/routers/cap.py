"""CAP 1.2 distribution channel without sending anything: a subscribable Atom feed.

POST /cap/alerts            store one alert (JSON shape of standards.build_cap); it is rendered to CAP XML
                            and validated against the OASIS CAP 1.2 XSD; 422 with the schema errors otherwise.
                            Demo-token protected (the agent's make_cap_alert tool is the caller).
GET  /cap/feed.atom         Atom 1.0 feed of the newest alerts (the OASIS/Google Public Alerts pattern:
                            one entry per alert, link type application/cap+xml). Public, status stays Exercise.
GET  /cap/alerts/{id}.xml   the stored CAP XML document (application/cap+xml).
GET  /cap/alerts/{id}.json  the alert plus its reach channels: xml_url, audio_url (마을방송), bit_lines (정류장 안내단말).
GET  /cap/alerts/{id}/audio.wav  마을방송/스피커용 음성 (macOS `say -v Yuna` -> ffmpeg 16 kHz mono WAV, cached).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import FileResponse, Response

from ..agent import cap_audio, cap_xml, standards
from ..agent import constants as C
from ..config import Settings
from ..deps import get_conn, get_settings
from .agent import MAX_JSON_DEPTH, RUN_RE, json_depth, require_demo_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cap", tags=["cap"])
ATOM_NS = "http://www.w3.org/2005/Atom"
ATOM_MEDIA_TYPE = "application/atom+xml; charset=utf-8"
FEED_ID = f"urn:{C.CAP_SENDER}:cap-feed"
FEED_TITLE = "고령자 안전 스마트도시 에이전트 CAP 1.2 경보 피드 (Exercise)"
FEED_LIMIT_DEFAULT, FEED_LIMIT_MAX = 50, 200
CAP_ID_RE = r"^[A-Za-z0-9._:-]{8,128}$"
FEED_PATH = "/cap/feed.atom"
BIT_LINES_MAX, BIT_LINE_CHARS = 2, 15


class CapIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cap: dict[str, Any]
    run_id: str | None = Field(default=None, pattern=RUN_RE)
    bit_lines: list[str] | None = Field(default=None, max_length=BIT_LINES_MAX,
                                        description="버스정류장 안내단말 2줄 문구 (각 15자 이하)")

    @property
    def bit_lines_json(self) -> str | None:
        if self.bit_lines is None:
            return None
        if any(len(line) > BIT_LINE_CHARS or not line.strip() for line in self.bit_lines):
            raise ValueError(f"bit_lines: each line 1..{BIT_LINE_CHARS} chars")
        return json.dumps(self.bit_lines, ensure_ascii=False)


def alert_path(identifier: str) -> str:
    return f"/cap/alerts/{identifier}.xml"


def audio_path(identifier: str) -> str:
    return f"/cap/alerts/{identifier}/audio.wav"


def json_path(identifier: str) -> str:
    return f"/cap/alerts/{identifier}.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def publish_alert(conn: sqlite3.Connection, body: CapIn) -> dict[str, Any]:
    """Self-check, render to CAP XML, validate against the OASIS XSD, store; the response of POST /cap/alerts.

    Raises:
        HTTPException: 422 (too_deep | selfcheck_failed | bad_bit_lines | render_failed | xsd_failed), 409 duplicate.
    """
    t0 = time.perf_counter()
    if json_depth(body.cap) > MAX_JSON_DEPTH:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, {"code": "too_deep", "errors": []})
    errors = standards.validate_cap(body.cap)
    if errors:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, {"code": "selfcheck_failed", "errors": errors})
    try:
        bit_lines = body.bit_lines_json
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, {"code": "bad_bit_lines", "errors": [str(exc)]}) from exc
    try:
        xml = cap_xml.to_xml(body.cap)
    except cap_xml.CapXmlError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, {"code": "render_failed", "errors": [str(exc)]}) from exc
    schema_errors = cap_xml.validate_xml(xml)
    if schema_errors:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, {"code": "xsd_failed", "errors": schema_errors})
    alert = body.cap["alert"]
    info = alert.get("info") or {}
    if isinstance(info, list):
        info = info[0] if info else {}
    created = _now()
    try:
        conn.execute(
            "INSERT INTO cap_alert (identifier, run_id, sent, status, msg_type, event, headline, xml, json, xsd_valid, "
            "created_at, bit_lines) VALUES (?,?,?,?,?,?,?,?,?,1,?,?)",
            (alert["identifier"], body.run_id, alert["sent"], alert["status"], alert["msgType"], str(info.get("event", "")),
             info.get("headline"), xml.decode("utf-8"), json.dumps(body.cap, ensure_ascii=False, sort_keys=True), created,
             bit_lines),
        )
    except sqlite3.IntegrityError as exc:
        if "UNIQUE" not in str(exc) and "PRIMARY KEY" not in str(exc):
            raise  # a CHECK violation is a server bug, not a client conflict
        raise HTTPException(status.HTTP_409_CONFLICT, {"code": "duplicate_identifier", "errors": ["identifier exists"]}) from exc
    generation_ms = round((time.perf_counter() - t0) * 1000, 1)
    logger.info("CAP alert %s stored (XSD valid, %.1f ms)", alert["identifier"], generation_ms)
    return {"identifier": alert["identifier"], "xsd_valid": True, "xsd": cap_xml.XSD_PATH.name, "generation_ms": generation_ms,
            "xml_url": alert_path(alert["identifier"]), "json_url": json_path(alert["identifier"]),
            "audio_url": audio_path(alert["identifier"]), "audio_script": cap_audio.script(body.cap),
            "bit_lines": body.bit_lines, "feed_url": FEED_PATH, "created_at": created, "xml": xml.decode("utf-8")}


@router.post("/alerts", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_demo_token)])
def post_alert(body: CapIn, request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    """Validate (self-check + XSD), store, and answer with where the alert is published (+ generation_ms, xml)."""
    return publish_alert(conn, body)


@router.get("/alerts/{identifier}.json")
def get_alert_json(identifier: str = Path(..., pattern=CAP_ID_RE), conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    """The alert and every channel that reaches people without a smartphone: XML feed, 마을방송 audio, BIT lines."""
    row = conn.execute("SELECT identifier, run_id, sent, status, event, headline, json, xsd_valid, bit_lines, created_at "
                       "FROM cap_alert WHERE identifier = ?", (identifier,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "alert not found")
    cap = json.loads(row["json"])
    return {"identifier": row["identifier"], "run_id": row["run_id"], "sent": row["sent"], "status": row["status"],
            "event": row["event"], "headline": row["headline"], "xsd_valid": bool(row["xsd_valid"]),
            "created_at": row["created_at"], "xml_url": alert_path(identifier), "audio_url": audio_path(identifier),
            "audio_script": cap_audio.script(cap), "audio_available": cap_audio.available(),
            "bit_lines": json.loads(row["bit_lines"]) if row["bit_lines"] else None, "feed_url": FEED_PATH, "cap": cap}


@router.get("/alerts/{identifier}/audio.wav")
def get_alert_audio(identifier: str = Path(..., pattern=CAP_ID_RE), conn: sqlite3.Connection = Depends(get_conn),
                    settings: Settings = Depends(get_settings)) -> Response:
    """마을방송/스피커용 WAV (16 kHz mono, <= ~20 s), synthesised on first request and cached."""
    row = conn.execute("SELECT json FROM cap_alert WHERE identifier = ?", (identifier,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "alert not found")
    out = settings.audio_dir / f"{identifier}.wav"
    if not out.is_file():
        try:
            seconds = cap_audio.synthesize(cap_audio.script(json.loads(row["json"])), out)
        except cap_audio.TtsUnavailable as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                {"code": "tts_unavailable", "message": str(exc)}) from exc
    else:
        seconds = cap_audio.wav_seconds(out)
    return FileResponse(str(out), media_type=cap_audio.AUDIO_MEDIA_TYPE, filename=f"{identifier}.wav",
                        headers={"X-Audio-Seconds": f"{seconds:.2f}"})


@router.get("/alerts/{identifier}.xml")
def get_alert_xml(identifier: str = Path(..., pattern=CAP_ID_RE), conn: sqlite3.Connection = Depends(get_conn)) -> Response:
    row = conn.execute("SELECT xml FROM cap_alert WHERE identifier = ?", (identifier,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "alert not found")
    return Response(content=str(row["xml"]), media_type=cap_xml.CAP_MEDIA_TYPE)


def render_feed(rows: list[dict[str, Any]], base: str, updated: str) -> str:
    """Atom 1.0 document; `base` is the public URL prefix for links (nginx serves the API under /api)."""
    entries = []
    for r in rows:
        link = f"{base}{alert_path(r['identifier'])}"
        bit = json.loads(r["bit_lines"]) if r.get("bit_lines") else None
        summary = escape(f"{r['status']} · {r['event']} · sent {r['sent']}" + (f" · BIT: {' / '.join(bit)}" if bit else ""))
        entries.append(
            f"  <entry>\n    <id>urn:oasis:names:tc:emergency:cap:1.2:{escape(r['identifier'])}</id>\n"
            f"    <title>{escape(r['headline'] or r['event'])}</title>\n"
            f"    <updated>{escape(r['created_at'])}</updated>\n"
            f"    <link rel=\"alternate\" type=\"{cap_xml.CAP_MEDIA_TYPE}\" href=\"{escape(link)}\"/>\n"
            f"    <link rel=\"enclosure\" type=\"{cap_audio.AUDIO_MEDIA_TYPE}\" "
            f"href=\"{escape(base + audio_path(r['identifier']))}\" title=\"마을방송 음성\"/>\n"
            f"    <summary>{summary}</summary>\n  </entry>\n")
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n<feed xmlns="{ATOM_NS}">\n'
        f"  <id>{FEED_ID}</id>\n  <title>{escape(FEED_TITLE)}</title>\n  <updated>{escape(updated)}</updated>\n"
        f"  <author><name>{escape(C.CAP_SENDER)}</name></author>\n"
        f'  <link rel="self" type="application/atom+xml" href="{escape(base + FEED_PATH)}"/>\n'
        + "".join(entries) + "</feed>\n"
    )


@router.get("/feed.atom")
def feed(request: Request, limit: int = Query(FEED_LIMIT_DEFAULT, ge=1, le=FEED_LIMIT_MAX),
         conn: sqlite3.Connection = Depends(get_conn)) -> Response:
    """Newest alerts first. Any CAP/Atom consumer can poll this; every entry is an Exercise message."""
    rows = [dict(r) for r in conn.execute(
        "SELECT identifier, sent, status, event, headline, created_at, bit_lines FROM cap_alert ORDER BY created_at DESC, identifier "
        "LIMIT ?", (limit,)).fetchall()]
    # Behind nginx the API is served under /api; the proxy sets Host and we keep links relative to it.
    prefix = request.headers.get("x-forwarded-prefix", "")
    updated = rows[0]["created_at"] if rows else _now()
    return Response(content=render_feed(rows, prefix, updated), media_type=ATOM_MEDIA_TYPE)
