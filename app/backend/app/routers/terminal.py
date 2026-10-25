"""현장 단말 channel (TWIN_CONTRACT 백엔드 5).

GET  /terminal/stream?stop_id=    SSE: unnamed `message` events, data = JSON {kind, title, body, audio_url, stop_id, id,
                                  published_at}; the last matching message is replayed on connect; keep-alive comments.
POST /terminal/publish            X-Demo-Token. {kind: heat|fall|rain|clear, title, body, audio_url?, stop_id?,
                                  synthesize?} -> 201 with the stored message. Without audio_url (and unless
                                  synthesize=false) a 마을방송 WAV is synthesised offline and linked.
GET  /terminal/last?stop_id=      the message a new terminal would be shown (null when none yet).
GET  /terminal/audio/{id}.wav     the synthesised WAV.
POST /twin/{sgg}/publish-heat     X-Demo-Token; ?date=&hour=&daytype= -> publishes the day's agent heat decision
                                  (409 `no_alert` when the agent did not alert that day).
Nothing here transmits anywhere: subscribers are browsers connected to this server.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import FileResponse, Response, StreamingResponse

from ..agent import cap_audio
from ..config import Settings
from ..deps import get_conn, get_settings
from ..twin import constants as C
from ..twin import state as twin_state
from ..twin import route as twin_route
from ..twin import store, terminal
from .agent import require_demo_token

logger = logging.getLogger(__name__)
router = APIRouter(tags=["terminal"])
SSE_MIME = "text/event-stream"
SSE_RETRY_MS = 1000
Kind = Literal["heat", "fall", "rain", "clear"]
AUDIO_URL_RE = r"^/[A-Za-z0-9._/-]{1,200}$"


class PublishIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Kind
    title: str = Field(min_length=1, max_length=C.TERMINAL_TITLE_MAX)
    body: str = Field(min_length=1, max_length=C.TERMINAL_BODY_MAX)
    audio_url: str | None = Field(default=None, pattern=AUDIO_URL_RE, description="API-relative path, e.g. /cap/alerts/x/audio.wav")
    stop_id: str | None = Field(default=None, min_length=1, max_length=C.TERMINAL_STOP_ID_MAX)
    synthesize: bool = Field(default=True, description="synthesise 마을방송 audio when audio_url is absent")


def hub_of(request: Request) -> terminal.TerminalHub:
    hub: terminal.TerminalHub = request.app.state.terminal_hub
    return hub


def sse_frame(msg: dict[str, Any]) -> str:
    return f"id: {msg['id']}\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n"


@router.get("/terminal/stream")
async def stream(request: Request, stop_id: str | None = Query(None, min_length=1, max_length=C.TERMINAL_STOP_ID_MAX)) -> Response:
    """SSE stream for one terminal (optionally addressed by stop_id).

    First chunk flushes the headers at once (`: connected` + `retry:`), then the last matching message is replayed, then
    live messages; a keep-alive comment every TERMINAL_KEEPALIVE_S keeps proxies and EventSource happy.
    """
    hub = hub_of(request)

    async def events() -> AsyncIterator[str]:
        queue, detach = hub.attach(stop_id)
        try:
            yield f": connected\nretry: {SSE_RETRY_MS}\n\n"
            last = hub.last_for(stop_id)
            seen = 0
            if last is not None:
                seen = int(last["id"])
                yield sse_frame({**last, "replay": True})
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), C.TERMINAL_KEEPALIVE_S)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if int(msg["id"]) <= seen:  # published between attach and replay: already shown
                    continue
                seen = int(msg["id"])
                yield sse_frame(msg)
        finally:
            detach()

    return StreamingResponse(events(), media_type=SSE_MIME,
                             headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"})


@router.get("/terminal/last")
def last(request: Request, stop_id: str | None = Query(None, min_length=1, max_length=C.TERMINAL_STOP_ID_MAX)) -> dict[str, Any]:
    hub = hub_of(request)
    return {"message": hub.last_for(stop_id), "subscribers": hub.subscriber_count(), "drill": C.TERMINAL_DRILL_LABEL}


@router.post("/terminal/publish", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_demo_token)])
def publish(body: PublishIn, request: Request, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Broadcast one message to connected terminals (and keep it for late joiners)."""
    msg = terminal.publish_message(hub_of(request), settings.audio_dir, body.model_dump(exclude={"synthesize"}), body.synthesize)
    return {**msg, "subscribers": hub_of(request).subscriber_count(), "tts_available": cap_audio.available()}


@router.get("/terminal/audio/{message_id}.wav")
def audio(message_id: int = Path(..., ge=1), settings: Settings = Depends(get_settings)) -> Response:
    out = terminal.audio_file(settings.audio_dir, message_id)
    if not out.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no audio for this message")
    return FileResponse(str(out), media_type=cap_audio.AUDIO_MEDIA_TYPE, filename=out.name,
                        headers={"X-Audio-Seconds": f"{cap_audio.wav_seconds(out):.2f}"})


def _aed_hint_for_stop(conn: sqlite3.Connection, settings: Settings, sgg_code: str, stop_id: str | None, d: str, h: int,
                       dt: twin_state.DayType) -> str | None:
    """'가까운 열린 AED: <name> (도보 N분)' for a known mobility stop of the 구; None when unknown or no route."""
    if stop_id is None:
        return None
    row = conn.execute("SELECT lat, lon FROM mobility_stop WHERE stop_id = ? AND sgg_code = ?", (stop_id, sgg_code)).fetchone()
    if row is None:
        return None
    try:
        g = store.graph(conn, settings, sgg_code)
        r = twin_route.route_to_open_aed(conn, settings, g, float(row["lat"]), float(row["lon"]), d, h, dt)
    except (store.SceneNotBuilt, twin_route.TooFarFromNetwork, twin_route.NoOpenAed, twin_state.DataUnavailable) as exc:
        logger.info("no AED hint for stop %s: %s", stop_id, exc)
        return None
    minutes = max(1, round(r["seconds"] / 60))
    name = str(r["aed"]["name"])
    place = str(r["aed"].get("place") or "")
    if place.startswith(name):  # "성현동자치회관 내부" after "성현동자치회관" would repeat the name
        place = place[len(name):].strip()
    return f"가까운 열린 AED: {name}{' ' + place if place else ''} (도보 {minutes}분, 0.8 m/s)"


@router.post("/twin/{sgg_code}/publish-heat", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_demo_token)])
def publish_heat(
    request: Request, sgg_code: str = Path(..., pattern=C.SGG_CODE_RE),
    date_: str = Query(..., alias="date", pattern=C.DATE_RE), hour: int = Query(..., ge=C.HOUR_MIN, le=C.HOUR_MAX),
    daytype: twin_state.DayType | None = Query(None), stop_id: str | None = Query(None, max_length=C.TERMINAL_STOP_ID_MAX),
    conn: sqlite3.Connection = Depends(get_conn), settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Publish the day's agent heat decision to the terminals (the replay's "폭염 판단" moment)."""
    region = twin_state.region_or_404(conn, sgg_code)
    try:
        d, h, dt = twin_state.resolve_clock(date_, hour, daytype)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    heat = twin_state.heat_block(conn, region, d)
    has_shelters = bool(twin_state.shelter_items(conn, sgg_code, dt, h))
    aed_hint = _aed_hint_for_stop(conn, settings, sgg_code, stop_id, d, h, dt)
    msg = terminal.heat_message(region["name"], heat, twin_state.weather_block(conn, settings, region, d, h), h,
                                has_shelters=has_shelters, aed_hint=aed_hint)
    if msg is None:
        raise HTTPException(status.HTTP_409_CONFLICT, {"code": "no_alert", "message": "에이전트가 이 날 이 구에 경보를 내지 않았다",
                                                       "heat": heat})
    out = terminal.publish_message(hub_of(request), settings.audio_dir, {**msg, "stop_id": stop_id or None}, synthesize=True)
    return {**out, "heat": heat}


__all__ = ["PublishIn", "hub_of", "router", "sse_frame"]


@router.post("/twin/{sgg_code}/publish-fall", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_demo_token)])
def publish_fall(
    request: Request, sgg_code: str = Path(..., pattern=C.SGG_CODE_RE),
    lat: float = Query(..., ge=33, le=39), lon: float = Query(..., ge=124, le=132),
    date_: str = Query(..., alias="date", pattern=C.DATE_RE), hour: int = Query(..., ge=C.HOUR_MIN, le=C.HOUR_MAX),
    minute: int = Query(0, ge=0, le=59), daytype: twin_state.DayType | None = Query(None),
    stop_id: str | None = Query(None, max_length=C.TERMINAL_STOP_ID_MAX),
    conn: sqlite3.Connection = Depends(get_conn), settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Publish the control-room fall alert (camera · address · nearest open AED) to the terminals (훈련 · 발송 안 함)."""
    from ..twin import actions as twin_actions

    region = twin_state.region_or_404(conn, sgg_code)
    try:
        d, h, dt = twin_state.resolve_clock(date_, hour, daytype)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    camera = twin_actions.camera_for_point(conn, settings, region, lat, lon)
    alert = twin_actions.fall_alert_text(region, camera, f"{h:02d}:{minute:02d}")
    aed_hint = None
    try:
        g = store.graph(conn, settings, sgg_code)
        r = twin_route.route_to_open_aed(conn, settings, g, lat, lon, d, h, dt)
        aed_hint = f"가까운 열린 AED: {r['aed']['name']} (도보 {max(1, round(r['seconds'] / 60))}분)"
    except (store.SceneNotBuilt, twin_route.TooFarFromNetwork, twin_route.NoOpenAed, twin_state.DataUnavailable) as exc:
        logger.info("publish-fall without AED hint: %s", exc)
    body = (f"{alert}. 주변에 계신 분은 상태를 확인하고 119에 신고해 주세요." + (f" {aed_hint}." if aed_hint else ""))[: C.TERMINAL_BODY_MAX]
    msg = {"kind": "fall", "title": f"낙상 의심 감지 · {camera['id_label'] if camera else region['name']}"[: C.TERMINAL_TITLE_MAX], "body": body,
           "audio_url": None, "stop_id": stop_id or None, "meta": {"camera": camera, "date": d, "hour": h, "minute": minute, "lat": lat, "lon": lon}}
    out = terminal.publish_message(hub_of(request), settings.audio_dir, msg, synthesize=True)
    return {**out, "alert_text": alert, "camera": camera}
