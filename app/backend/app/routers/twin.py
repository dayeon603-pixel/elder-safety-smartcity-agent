"""Digital-twin endpoints (app/TWIN_CONTRACT.md, 백엔드 1-4, 6). Read-only; every response carries sources/fetched_at.

GET /twin/{sgg}/scene          static bundle: all buildings (flat METER_OFFSETS positions), walk edges, boundary.
                               Pre-gzipped file served as-is (Content-Encoding: gzip); 409 twin_scene_not_built.
GET /twin/{sgg}/state          dynamic state at (date, hour, daytype): AED/shelter open flags, crosswalks, silver zones,
                               recommended stops, heat decision, observed weather, summary.
GET /twin/{sgg}/timeline       06..24 h of the day in one call (AED/shelter open counts, hourly weather).
GET /twin/route                walk-network shortest path to the nearest OPEN AED.
GET /twin/rain-days            real ASOS rain nights (>= 10 mm/h) near 관악, 2023-2025, with the data gaps stated.
GET /twin/{sgg}/actions        hour-aware action panel (day: heat channels; night: AED 24h conversions, shelter notes).
POST /twin/{sgg}/actions/{id}/cap   X-Demo-Token: CAP 1.2 for one action (XSD-validated, stored, generation_ms).
GET /twin/{sgg}/cameras        camera placement candidates (crosswalks in silver zones, hotspots, top stops).
GET /twin/{sgg}/population     서울 생활인구 per 행정동 at (date, hour) + walk-graph node indices per 동; 409 no_population_data.
GET /twin/{sgg}/walk-nodes     walk-graph node positions (metres from the scene centre) the population node indices refer to.
"""

from __future__ import annotations

import gzip
import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status

from ..config import Settings
from ..deps import get_conn, get_settings
from ..twin import constants as C
from ..twin import actions as twin_actions
from ..twin import cache as twin_cache
from ..twin import effects as twin_effects
from ..twin import infra as twin_infra
from ..twin import tiles as twin_tiles
from ..twin import lan as twin_lan
from ..security.ratelimit import is_loopback_peer
import httpx
from ..modules import feedback as fb
from ..twin import population as twin_pop
from ..twin import terminal as twin_terminal
from ..twin import rain, route as twin_route, state as twin_state, store
from .agent import require_demo_token
from .cap import CapIn, publish_alert
from .documents import _payload as document_payload
from ..documents.templates import ACTION_DOCS, render
from ..twin import gov
from ..agent import cap_audio
from starlette.responses import FileResponse
import hashlib
from datetime import date as date_type
from datetime import datetime, timezone
from ..twin.schemas import RainDays, RouteOut, TwinState, TwinTimeline

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/twin", tags=["twin"])
NOT_BUILT = "twin_scene_not_built"
NO_AED = "no_open_aed"
GZIP = "gzip"


def _not_built(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, {"code": NOT_BUILT, "message": str(exc)})


@router.get("/{sgg_code}/scene")
def scene(
    request: Request, sgg_code: str = Path(..., pattern=C.SGG_CODE_RE), meta: bool = False,
    conn: sqlite3.Connection = Depends(get_conn), settings: Settings = Depends(get_settings),
) -> Response:
    """Static scene of one 구 (관악구 11620 first). `?meta=1` returns counts only.

    404 unknown 구; 409 `twin_scene_not_built` when the file is missing and cannot be built in this process.
    """
    twin_state.region_or_404(conn, sgg_code)
    try:
        file = store.ensure_scene(conn, settings, sgg_code)
    except store.SceneNotBuilt as exc:
        raise _not_built(exc) from exc
    if meta:
        return Response(content=twin_state.dumps({**store.scene_summary(file), "cache": file.cache}),
                        media_type="application/json")
    body, etag = store.scene_bytes(file)
    headers = {"ETag": etag, "Cache-Control": "public, max-age=3600", "X-Twin-Cache": file.cache, "Vary": "Accept-Encoding"}
    wanted = {t.strip().removeprefix("W/") for t in request.headers.get("if-none-match", "").split(",")}
    if etag in wanted:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    if GZIP in request.headers.get("accept-encoding", ""):
        return Response(content=body, media_type="application/json", headers={**headers, "Content-Encoding": GZIP})
    return Response(content=gzip.decompress(body), media_type="application/json", headers=headers)


def _panel(conn: sqlite3.Connection, settings: Settings, region: dict[str, Any], d: str, h: int, dt: twin_state.DayType) -> dict[str, Any]:
    """Cached actions panel with the approval `status` of every action re-read from the feedback tables."""
    cached = twin_cache.get_or_build(("actions", region["code"], d, str(h), dt), lambda: twin_actions.build_panel(conn, settings, region, d, h, dt))
    actions = []
    for a in cached["actions"]:
        st = fb.current_status(conn, region["code"], d, a["feedback_ref"]["action_id"])
        actions.append({**a, "status": st, "status_label": gov.STATUS_LABEL[st]})
    return {**cached, "actions": actions}


def _hour_daytype(hour: int, daytype: str | None, date_: str) -> tuple[str, int, twin_state.DayType]:
    try:
        return twin_state.resolve_clock(date_, hour, daytype)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.get("/{sgg_code}/state", response_model=TwinState)
def state(
    sgg_code: str = Path(..., pattern=C.SGG_CODE_RE),
    date_: str = Query(..., alias="date", pattern=C.DATE_RE),
    hour: int = Query(..., ge=C.HOUR_MIN, le=C.HOUR_MAX),
    daytype: twin_state.DayType | None = Query(None, description="default: derived from the date (공휴일 table)"),
    conn: sqlite3.Connection = Depends(get_conn), settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Dynamic state at one clock reading. hour=24 means midnight ending the day (resolved to 00h of D+1)."""
    region = twin_state.region_or_404(conn, sgg_code)
    d, h, dt = _hour_daytype(hour, daytype, date_)
    try:
        return twin_cache.get_or_build(("state", sgg_code, d, str(h), dt),
                                       lambda: twin_state.build_state(conn, settings, region, d, h, dt, requested_date=date_, requested_hour=hour))
    except twin_state.DataUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {"code": "data_unavailable", "message": str(exc)}) from exc


@router.get("/{sgg_code}/timeline", response_model=TwinTimeline)
def timeline(
    sgg_code: str = Path(..., pattern=C.SGG_CODE_RE),
    date_: str = Query(..., alias="date", pattern=C.DATE_RE),
    daytype: twin_state.DayType | None = Query(None),
    conn: sqlite3.Connection = Depends(get_conn), settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Hourly summary 06..24 for the replay bar (one call)."""
    region = twin_state.region_or_404(conn, sgg_code)
    _hour_daytype(C.TIMELINE_START_HOUR, daytype, date_)
    try:
        return twin_state.build_timeline(conn, settings, region, date_, daytype)
    except twin_state.DataUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {"code": "data_unavailable", "message": str(exc)}) from exc


@router.get("/route", response_model=RouteOut)
def route(
    lat: float = Query(..., ge=33, le=39), lon: float = Query(..., ge=124, le=132),
    date_: str = Query(..., alias="date", pattern=C.DATE_RE),
    hour: int = Query(..., ge=C.HOUR_MIN, le=C.HOUR_MAX),
    daytype: twin_state.DayType | None = Query(None),
    sgg_code: str = Query(C.DEFAULT_SGG, pattern=C.SGG_CODE_RE, description="whose walk network to route on"),
    conn: sqlite3.Connection = Depends(get_conn), settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Shortest walk path from (lat, lon) to the nearest AED open at that clock reading.

    422 when the point is farther than ROUTE_MAX_SNAP_M from the network; 409 `no_open_aed` when none is reachable.
    """
    twin_state.region_or_404(conn, sgg_code)
    d, h, dt = _hour_daytype(hour, daytype, date_)
    try:
        g = store.graph(conn, settings, sgg_code)
    except store.SceneNotBuilt as exc:
        raise _not_built(exc) from exc
    try:
        return twin_route.route_to_open_aed(conn, settings, g, lat, lon, d, h, dt)
    except twin_route.TooFarFromNetwork as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except twin_route.NoOpenAed as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, {"code": NO_AED, "message": str(exc)}) from exc
    except twin_state.DataUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {"code": "data_unavailable", "message": str(exc)}) from exc


@router.get("/rain-days", response_model=RainDays)
def rain_days(
    sgg_code: str = Query(C.DEFAULT_SGG, pattern=C.SGG_CODE_RE),
    conn: sqlite3.Connection = Depends(get_conn), settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Summer nights with observed rain >= 10 mm/h at the ASOS station nearest the 구 (2023-2025).

    Hourly data is cached for 2025-06..09 only; 2023-2024 nights are listed from daily totals as *unverified*
    candidates. 409 `no_rain_data` when neither source is on disk.
    """
    region = twin_state.region_or_404(conn, sgg_code)
    try:
        return rain.rain_days(conn, settings, region)
    except rain.NoRainData as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, {"code": "no_rain_data", "message": str(exc)}) from exc


@router.get("/{sgg_code}/actions")
def actions(
    sgg_code: str = Path(..., pattern=C.SGG_CODE_RE),
    date_: str = Query(..., alias="date", pattern=C.DATE_RE),
    hour: int = Query(..., ge=C.HOUR_MIN, le=C.HOUR_MAX),
    daytype: twin_state.DayType | None = Query(None),
    conn: sqlite3.Connection = Depends(get_conn), settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Actions for the slider position. Each has evidence rows, feedback_ref and cap_url. Cached per (date, hour); status live."""
    region = twin_state.region_or_404(conn, sgg_code)
    d, h, dt = _hour_daytype(hour, daytype, date_)
    try:
        return _panel(conn, settings, region, d, h, dt)
    except twin_state.DataUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {"code": "data_unavailable", "message": str(exc)}) from exc


@router.post("/{sgg_code}/actions/{action_id}/cap", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_demo_token)])
def action_cap(
    sgg_code: str = Path(..., pattern=C.SGG_CODE_RE), action_id: str = Path(..., pattern=r"^[a-z][a-z0-9_.:-]{0,40}$"),
    date_: str = Query(..., alias="date", pattern=C.DATE_RE),
    hour: int = Query(..., ge=C.HOUR_MIN, le=C.HOUR_MAX),
    daytype: twin_state.DayType | None = Query(None),
    conn: sqlite3.Connection = Depends(get_conn), settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Generate, XSD-validate and store a CAP 1.2 (Exercise) alert for one panel action; 404 unknown action."""
    region = twin_state.region_or_404(conn, sgg_code)
    d, h, dt = _hour_daytype(hour, daytype, date_)
    try:
        panel = _panel(conn, settings, region, d, h, dt)
    except twin_state.DataUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {"code": "data_unavailable", "message": str(exc)}) from exc
    action = twin_actions.find_action(panel, action_id)
    if action is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {"code": "action_not_found", "available": [a["id"] for a in panel["actions"]]})
    cap = twin_actions.cap_for_action(region, action, d, h)
    pub = publish_alert(conn, CapIn(cap=cap))
    return {**pub, "action_id": action_id, "feedback_ref": action["feedback_ref"], "cap": cap}


@router.get("/{sgg_code}/cameras")
def cameras(sgg_code: str = Path(..., pattern=C.SGG_CODE_RE), conn: sqlite3.Connection = Depends(get_conn),
            settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Candidate camera points with names (for the demo camera icon)."""
    region = twin_state.region_or_404(conn, sgg_code)
    try:
        return twin_actions.camera_candidates(conn, settings, region)
    except twin_state.DataUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {"code": "data_unavailable", "message": str(exc)}) from exc


@router.get("/{sgg_code}/population")
def population(
    sgg_code: str = Path(..., pattern=C.SGG_CODE_RE),
    date_: str = Query(..., alias="date", pattern=C.DATE_RE),
    hour: int = Query(..., ge=C.HOUR_MIN, le=C.HOUR_MAX),
    conn: sqlite3.Connection = Depends(get_conn), settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Real living population per 행정동 at the hour (STORY_CONTRACT). 409 `no_population_data` until the file exists."""
    region = twin_state.region_or_404(conn, sgg_code)
    d, h, _ = _hour_daytype(hour, None, date_)
    try:
        return twin_pop.population(conn, settings, region, d, h)
    except twin_pop.NoPopulationData as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, {"code": "no_population_data", "message": str(exc)}) from exc
    except store.SceneNotBuilt as exc:
        raise _not_built(exc) from exc
    except twin_state.DataUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {"code": "data_unavailable", "message": str(exc)}) from exc


@router.get("/{sgg_code}/walk-nodes")
def walk_nodes(sgg_code: str = Path(..., pattern=C.SGG_CODE_RE), conn: sqlite3.Connection = Depends(get_conn),
               settings: Settings = Depends(get_settings)) -> Response:
    """Flat [x0,y0,x1,y1,...] metres from the scene centre for every walk-graph node (index = position / 2)."""
    twin_state.region_or_404(conn, sgg_code)
    try:
        g = store.graph(conn, settings, sgg_code)
    except store.SceneNotBuilt as exc:
        raise _not_built(exc) from exc
    flat = [round(float(v), 2) for pair in zip(g.node_x, g.node_y, strict=True) for v in pair]
    body = twin_state.dumps({"sgg_code": sgg_code, "center": list(g.center), "count": int(len(g.node_x)), "positions": flat,
                             "coordinate_system": "METER_OFFSETS", "built_at": g.built_at, "fetched_at": g.built_at,
                             "sources": [{"what": "OSM walk network", "license": C.OSM_LICENSE, "display_source": "오픈스트리트맵 보행망"}]})
    return Response(content=body, media_type="application/json", headers={"Cache-Control": "public, max-age=3600"})


DOC_TEMPLATE_OF = {"voice": "cbs"}  # the voice card reuses the 재난문자 letter text (쉬운 말 버전)


@router.get("/{sgg_code}/actions/{action_id}/document")
def action_document(
    sgg_code: str = Path(..., pattern=C.SGG_CODE_RE), action_id: str = Path(..., pattern=r"^[a-z][a-z0-9_.:-]{0,40}$"),
    date_: str = Query(..., alias="date", pattern=C.DATE_RE),
    hour: int = Query(..., ge=C.HOUR_MIN, le=C.HOUR_MAX),
    daytype: twin_state.DayType | None = Query(None),
    conn: sqlite3.Connection = Depends(get_conn), settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Korean 문안 preview of one action: {수신, 제목, 본문, 근거, 생성시각} from the existing 공문 templates (draft, 전달 대기)."""
    region = twin_state.region_or_404(conn, sgg_code)
    d, h, dt = _hour_daytype(hour, daytype, date_)
    try:
        panel = _panel(conn, settings, region, d, h, dt)
    except twin_state.DataUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {"code": "data_unavailable", "message": str(exc)}) from exc
    action = twin_actions.find_action(panel, action_id)
    if action is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {"code": "action_not_found", "available": [a["id"] for a in panel["actions"]]})
    kind = action_id.split(":", 1)[0]
    template = DOC_TEMPLATE_OF.get(kind, kind)
    if template not in ACTION_DOCS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {"code": "no_document_template", "message": f"{kind} 에는 공문 서식이 없다"})
    ev, payload, sources = document_payload(conn, settings, template, region, date_type.fromisoformat(d), 10)
    if kind == "aed.convert":  # letter for the specific candidate of the card, not the 구-level top candidate
        payload = {**payload, "candidate": {"org": action.get("org"), "address": next((e["value"] for e in action["evidence"] if e["label"] == "주소"), None),
                                            "weekday_hours": next((e["value"] for e in action["evidence"] if e["label"] == "평일 운영시간"), None)}}
    title, meta, body, rows, footer = render(template, f"{region['sido']} {region['name']}", d, ev, payload, sources)
    meta_d = dict(meta)
    return {"action_id": action_id, "title": title, "수신": action["destination"]["label"], "수신_기관": action["destination"]["org"],
            "제목": meta_d.get("제목", title), "본문": body, "근거": [{"항목": k, "값": v} for k, v in rows],
            "생성시각": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"), "발신": meta_d.get("발신"),
            "status": action["status"], "status_label": action["status_label"], "footer": footer, "never_sent": gov.NEVER_SENT,
            "docx_url": f"/documents/{template}.docx?sgg_code={sgg_code}&date={d}"}


@router.get("/{sgg_code}/fall-summary")
def fall_summary(
    sgg_code: str = Path(..., pattern=C.SGG_CODE_RE),
    lat: float = Query(..., ge=33, le=39), lon: float = Query(..., ge=124, le=132),
    date_: str = Query(..., alias="date", pattern=C.DATE_RE),
    hour: int = Query(..., ge=C.HOUR_MIN, le=C.HOUR_MAX), minute: int = Query(0, ge=0, le=59),
    daytype: twin_state.DayType | None = Query(None),
    conn: sqlite3.Connection = Depends(get_conn), settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """119 정보 preview for a detected fall (위치, 시각, 가장 가까운 열린 AED, 거리) as Korean lines + the destination block."""
    region = twin_state.region_or_404(conn, sgg_code)
    d, h, dt = _hour_daytype(hour, daytype, date_)
    try:
        g = store.graph(conn, settings, sgg_code)
        route_doc = twin_route.route_to_open_aed(conn, settings, g, lat, lon, d, h, dt)
    except store.SceneNotBuilt as exc:
        raise _not_built(exc) from exc
    except twin_route.TooFarFromNetwork as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except twin_route.NoOpenAed as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, {"code": NO_AED, "message": str(exc)}) from exc
    except twin_state.DataUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {"code": "data_unavailable", "message": str(exc)}) from exc
    aed = route_doc["aed"]
    metres = route_doc["meters"]
    responder_s = round(2 * metres / gov.WALK_SPEED_FALL_RESPONDER_M_S)
    when = f"{d} {h:02d}:{minute:02d} (KST)"
    camera = twin_actions.camera_for_point(conn, settings, region, lat, lon)
    alert_text = twin_actions.fall_alert_text(region, camera, f"{h:02d}:{minute:02d}")
    fields = {
        "감지": alert_text,
        "위치": f"{twin_actions.fall_where(region, camera)} · 위도 {lat:.5f}, 경도 {lon:.5f}",
        "시각": when,
        "가장 가까운 열린 AED": f"{aed['name']} ({aed['place'] or ''}) · {aed['address'] or ''}".replace(" () ", " "),
        "거리": f"보행 {metres:.0f} m (직선 {route_doc['straight_line_m']:.0f} m) · 고령자 보행 {route_doc['seconds'] // 60}분 {route_doc['seconds'] % 60}초 @0.8 m/s"
               f" · 행인 왕복 {responder_s // 60}분 {responder_s % 60}초 @{gov.WALK_SPEED_FALL_RESPONDER_M_S} m/s",
    }
    summary = (f"[훈련] {when} {region['name']}에서 낙상 의심 감지. 위치 {lat:.5f}, {lon:.5f}. "
               f"가장 가까운 열린 AED: {aed['name']} ({metres:.0f} m, 행인 왕복 약 {max(1, round(responder_s / 60))}분). "
               f"관제요원 확인 후 119 연계 (발송 안 함).")
    return {"sgg_code": sgg_code, "region": f"{region['sido']} {region['name']}", "date": d, "hour": h, "fields": fields, "summary": summary,
            "alert_text": alert_text, "camera": camera,
            "aed": aed, "path": route_doc["path"], "meters": metres, "responder_roundtrip_s": responder_s,
            "destination": gov.destination("fall"), "fhir_label": gov.FHIR_LABEL, "never_sent": gov.NEVER_SENT,
            "agent_run_context": {"trigger": "fall", "context": {"code": sgg_code, "date": d, "hour": h, "lat": lat, "lon": lon}},
            "sources": route_doc["sources"], "fetched_at": route_doc["fetched_at"]}


@router.get("/{sgg_code}/actions/voice/audio.wav")
def voice_audio(
    sgg_code: str = Path(..., pattern=C.SGG_CODE_RE), date_: str = Query(..., alias="date", pattern=C.DATE_RE),
    conn: sqlite3.Connection = Depends(get_conn), settings: Settings = Depends(get_settings),
) -> Response:
    """마을방송 WAV of the day's voice card text (synthesised offline on first request, cached by text hash)."""
    region = twin_state.region_or_404(conn, sgg_code)
    text = twin_actions.voice_text(conn, settings, region, date_type.fromisoformat(date_))
    if text is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {"code": "no_voice_text", "message": "이 날짜에는 폭염 음성 문안이 없다"})
    script = twin_terminal.script_for(f"{region['name']} 폭염 안내", text)
    out = settings.audio_dir / f"voice-{sgg_code}-{hashlib.sha256(script.encode('utf-8')).hexdigest()[:16]}.wav"
    if not out.is_file():
        try:
            cap_audio.synthesize(script, out)
        except cap_audio.TtsUnavailable as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {"code": "tts_unavailable", "message": str(exc)}) from exc
    return FileResponse(str(out), media_type=cap_audio.AUDIO_MEDIA_TYPE, filename=out.name,
                        headers={"X-Audio-Seconds": f"{cap_audio.wav_seconds(out):.2f}", "X-Audio-Script": script.encode("unicode_escape").decode("ascii")})


@router.get("/effects")
def effects(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    """The four ★ effect chips (언제 · 어디에 · 무엇을 · 밤) from the same result files as the deck ledger (cached, read-only)."""
    return twin_effects.effects(conn)


@router.get("/{sgg_code}/infra")
def infra(sgg_code: str = Path(..., pattern=C.SGG_CODE_RE), conn: sqlite3.Connection = Depends(get_conn),
          settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """City-infrastructure inventory: AED, signal crosswalks, bus stops (+ 안내단말 연계 후보 flag), silver zones, control centre."""
    region = twin_state.region_or_404(conn, sgg_code)
    try:
        return twin_infra.inventory(conn, settings, region)
    except (twin_infra.InfraUnavailable, twin_state.DataUnavailable) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {"code": "data_unavailable", "message": str(exc)}) from exc


def tile_http(request: Request) -> httpx.Client:
    """Shared upstream client (tests inject app.state.tile_http with a mock transport)."""
    client = getattr(request.app.state, "tile_http", None)
    if client is None:
        client = httpx.Client()
        request.app.state.tile_http = client
    assert isinstance(client, httpx.Client)
    return client


@router.get("/tiles/satellite/{z}/{x}/{y}.jpg")
def satellite_tile(request: Request, z: int = Path(..., ge=0, le=30), x: int = Path(..., ge=0), y: int = Path(..., ge=0),
                   settings: Settings = Depends(get_settings)) -> Response:
    """V-World satellite tile via the server (key stays server-side). 404 outside zoom 6-19 / the demo bbox; 503 without a key."""
    if settings.vworld_key is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {"code": "no_vworld_key", "message": "VWORLD_KEY is not configured; use the offline basemap"})
    if not twin_tiles.tile_allowed(z, x, y):
        raise HTTPException(status.HTTP_404_NOT_FOUND, {"code": "tile_out_of_scope", "message": f"tile {z}/{x}/{y} is outside zoom 6-19 or the demo area"})
    try:
        body = twin_tiles.fetch_tile(tile_http(request), settings.vworld_key, z, x, y)
    except twin_tiles.TileError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {"code": "tile_upstream", "message": str(exc), "tile": [z, x, y]}) from exc
    return Response(content=body, media_type=twin_tiles.MEDIA_TYPE, headers={"Cache-Control": twin_tiles.CACHE_CONTROL})


@router.get("/lan-url")
def lan_url(request: Request, conn: sqlite3.Connection = Depends(get_conn), settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Phone-terminal URL on this laptop's LAN (loopback callers only; 403 otherwise so a phone cannot enumerate)."""
    if not is_loopback_peer(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, {"code": "loopback_only", "message": "이 주소는 노트북(로컬)에서만 조회할 수 있다"})
    ip = twin_lan.lan_ip()
    stop_id = twin_lan.default_stop_id(conn)
    url = twin_lan.terminal_url(ip, settings.frontend_port, stop_id)
    return {"terminal_url": url, "ip": ip, "port": settings.frontend_port, "stop_id": stop_id,
            "note": twin_lan.NO_LAN_NOTE if ip is None else "같은 와이파이의 휴대폰에서 열기 (tools/demo_tools.sh lan 과 동일)"}
