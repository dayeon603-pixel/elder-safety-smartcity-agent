"""Agent endpoints and the two local simulators.

POST /agent/run        run the action agent (plan -> execute -> briefing); persisted immutably.
                       `Accept: text/event-stream` streams steps as Server-Sent Events (schema:
                       app/agent/README.md); otherwise the full record is returned as JSON.
GET  /agent/runs[/id]  list / fetch stored runs.
POST /sim/rsu          "RSU 시뮬레이터": DENM-like + TIM-like JSON, validated, stored, 202 + receipt.
POST /sim/119          "119 시뮬레이터": CAP 1.2-shaped alert + FHIR R4-shaped Bundle, self-checked, stored.
POST /sim/webhook      "행정 시스템 수신 시뮬레이터": verifies the HMAC-SHA256 webhook signature, stores the event.
GET  /sim/inbox[/id]   what the simulators actually received.
Nothing here transmits to any external system. When KCF_DEMO_TOKEN is set every route in this
module requires the `X-Demo-Token` header. The agent's own HTTP calls go to a loopback base URL
only (never the request's Host header), so tool results cannot be redirected by a client.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import sqlite3
import threading
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.responses import JSONResponse, StreamingResponse

from ..agent import AgentEnv, run_agent
from ..agent import constants as C
from ..agent import standards, store
from ..agent.ollama import OllamaClient
from ..config import Settings
from ..integrations import webhook
from ..deps import get_conn, get_settings
from ..twin import terminal

logger = logging.getLogger(__name__)
RECEIPT_RE = r"^[a-z0-9]{2,6}-[0-9a-f]{12}$"
RUN_RE = r"^run-[0-9a-f]{12}$"
HTTP_422 = 422
SSE_MIME = "text/event-stream"
MAX_JSON_DEPTH = 32
BUSY_DETAIL = {"code": "busy", "message": f"{C.MAX_CONCURRENT_RUNS} agent runs already in progress"}
BUSY_RETRY_AFTER_S = "5"
_DONE = object()


def require_demo_token(request: Request, settings: Settings = Depends(get_settings)) -> None:
    """401 unless the request carries the configured demo token (no-op when none is configured)."""
    if settings.demo_token is None:
        return
    given = request.headers.get(C.DEMO_TOKEN_HEADER, "")
    if not hmac.compare_digest(given.encode("utf-8"), settings.demo_token.encode("utf-8")):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "demo token required")


router = APIRouter(tags=["agent"], dependencies=[Depends(require_demo_token)])


def json_depth(obj: Any, level: int = 1) -> int:
    """Nesting depth of a JSON-like value (scalars are 1)."""
    if isinstance(obj, dict):
        return max((json_depth(v, level + 1) for v in obj.values()), default=level)
    if isinstance(obj, list):
        return max((json_depth(v, level + 1) for v in obj), default=level)
    return level


class AgentContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[0-9]{5}$")
    date: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    hour: int | None = Field(default=None, ge=0, le=24, description="twin clock hour (KST) the event happened at")
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    est_speed: float | None = Field(default=None, ge=0, le=30, description="m/s")
    torso_angle: float | None = Field(default=None, ge=0, le=180, description="degrees from vertical")
    image_base64: str | None = Field(default=None, max_length=C.MAX_IMAGE_B64_CHARS)


class AgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger: Literal["fall", "heat_daily"]
    context: AgentContext

    @model_validator(mode="after")
    def _fall_needs_position(self) -> AgentRunRequest:
        if self.trigger == "fall" and (self.context.lat is None or self.context.lon is None):
            raise ValueError("trigger=fall requires context.lat and context.lon")
        return self


class CapSimBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    simulator: Literal["119 시뮬레이터"]
    cap: dict[str, Any]
    fhir: dict[str, Any]

    @model_validator(mode="after")
    def _bounded_depth(self) -> CapSimBody:
        if max(json_depth(self.cap), json_depth(self.fhir)) > MAX_JSON_DEPTH:
            raise ValueError(f"JSON nested deeper than {MAX_JSON_DEPTH} levels")
        return self


def self_base_url(request: Request, settings: Settings) -> str:
    """Loopback base URL for the agent's own calls: configured, else 127.0.0.1 + the bound port.

    `scope["server"]` is the socket we accepted the connection on, not a client header.
    """
    if settings.agent_self_url is not None:
        return settings.agent_self_url
    server = request.scope.get("server") or ("127.0.0.1", 8000)
    return f"http://127.0.0.1:{int(server[1])}"


def _agent_env(request: Request, settings: Settings) -> tuple[AgentEnv, list[httpx.Client]]:
    """Real HTTP clients to ourselves and to Ollama unless the app state injects test doubles."""
    opened: list[httpx.Client] = []
    http = getattr(request.app.state, "agent_http", None)
    if http is None:
        http = httpx.Client(base_url=self_base_url(request, settings))
        opened.append(http)
    ollama = getattr(request.app.state, "agent_ollama", None)
    if ollama is None:
        client = httpx.Client(base_url=C.OLLAMA_URL)
        opened.append(client)
        ollama = OllamaClient(client)
    env = AgentEnv(http=http, ollama=ollama, now=lambda: datetime.now(timezone.utc),
                   uuid_hex=lambda: uuid.uuid4().hex, demo_token=settings.demo_token)
    return env, opened


def _slots(request: Request) -> threading.BoundedSemaphore:
    slots: threading.BoundedSemaphore = request.app.state.agent_slots
    return slots


def _run_and_store(env: AgentEnv, opened: list[httpx.Client], body: AgentRunRequest,
                   conn: sqlite3.Connection, hub: terminal.TerminalHub | None = None,
                   settings: Settings | None = None) -> dict[str, Any]:
    try:
        rec = run_agent(env, body.trigger, body.context.model_dump())
    finally:
        for c in opened:
            c.close()
    store.insert_run(conn, rec)
    logger.info("agent run %s trigger=%s planner=%s total_ms=%s", rec["run_id"], rec["trigger"],
                rec["planner"], rec["total_ms"])
    if hub is not None and settings is not None:
        msg = terminal.message_from_run(rec)
        if msg is not None:  # 현장 단말: a fall, or a heat day the agent decided to alert on
            published = terminal.publish_message(hub, settings.audio_dir, msg, synthesize=msg.get("audio_url") is None)
            rec["terminal_message_id"] = published["id"]
    return rec


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/agent/run")
async def agent_run(
    body: AgentRunRequest, request: Request, conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> Any:
    """Run the agent. The snapshot (if any) is never stored: only its sha256 and the VLM text.

    At most MAX_CONCURRENT_RUNS runs execute at once; further requests get 503 `busy` immediately.
    """
    slots = _slots(request)
    if not slots.acquire(blocking=False):
        return JSONResponse({"detail": BUSY_DETAIL}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            headers={"Retry-After": BUSY_RETRY_AFTER_S})
    env, opened = _agent_env(request, settings)
    hub: terminal.TerminalHub = request.app.state.terminal_hub
    if SSE_MIME not in request.headers.get("accept", ""):
        try:
            return await asyncio.to_thread(_run_and_store, env, opened, body, conn, hub, settings)
        finally:
            slots.release()

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Any] = asyncio.Queue()

    def on_event(kind: str, data: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (kind, data))

    env.on_event = on_event

    def work() -> None:
        try:
            rec = _run_and_store(env, opened, body, conn, hub, settings)
            loop.call_soon_threadsafe(queue.put_nowait, ("result", rec))
        except Exception as exc:  # surfaced to the client as an error event, logged here
            logger.exception("agent run failed")
            loop.call_soon_threadsafe(queue.put_nowait, ("error", {"detail": type(exc).__name__}))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, _DONE)

    async def events() -> AsyncIterator[str]:
        task = asyncio.create_task(asyncio.to_thread(work))
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), C.STREAM_KEEPALIVE_S)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if item is _DONE:
                    break
                kind, data = item
                yield sse(kind, data)
        finally:
            await task
            slots.release()

    return StreamingResponse(events(), media_type=SSE_MIME,
                             headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"})


@router.get("/agent/runs")
def runs(limit: int = Query(20, ge=1, le=C.INBOX_LIST_MAX),
         conn: sqlite3.Connection = Depends(get_conn)) -> list[dict[str, Any]]:
    return store.list_runs(conn, limit)


@router.get("/agent/runs/{run_id}")
def run_detail(run_id: str = Path(..., pattern=RUN_RE),
               conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    rec = store.get_run(conn, run_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return rec


def _receive(conn: sqlite3.Connection, channel: str, prefix: str, payload: dict[str, Any]) -> dict[str, Any]:
    receipt = f"{prefix}-{uuid.uuid4().hex[:12]}"
    received_at = store.insert_inbox(conn, receipt, channel, payload)
    return {"receipt_id": receipt, "channel": channel, "received_at": received_at}


@router.post("/sim/rsu", status_code=status.HTTP_202_ACCEPTED)
def sim_rsu(body: standards.RsuEnvelope, conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    """RSU 시뮬레이터. Schema validation is the pydantic model (422 on any violation)."""
    ack = _receive(conn, "rsu", "rsu", body.model_dump(mode="json"))
    logger.info("RSU simulator accepted %s", ack["receipt_id"])
    return {**ack, "simulator": C.SIM_RSU_LABEL}


@router.post("/sim/119", status_code=status.HTTP_202_ACCEPTED)
def sim_119(body: CapSimBody, conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    """119 시뮬레이터. Requires a CAP 1.2-shaped alert and a FHIR R4-shaped Bundle that pass the
    required-field self-checks in `standards` (422 with the error list); not profile conformance."""
    errors = standards.validate_cap({"alert": body.cap["alert"]} if "alert" in body.cap else body.cap)
    errors += standards.validate_fhir(body.fhir)
    if errors:
        raise HTTPException(HTTP_422, {"code": "selfcheck_failed", "errors": errors})
    ack = _receive(conn, "119", "emg", {"simulator": body.simulator, "cap": body.cap, "fhir": body.fhir})
    logger.info("119 simulator accepted %s", ack["receipt_id"])
    return {**ack, "simulator": C.SIM_119_LABEL}


@router.post("/sim/webhook", status_code=status.HTTP_202_ACCEPTED)
async def sim_webhook(request: Request, conn: sqlite3.Connection = Depends(get_conn),
                      settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Receiver double for 기존 행정 시스템: rejects a missing/forged/stale signature (401), stores the event."""
    if settings.webhook_secret is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {"code": "webhook_not_configured"})
    raw = await request.body()
    why = webhook.verify(settings.webhook_secret, request.headers.get(webhook.TIMESTAMP_HEADER, ""), raw,
                         request.headers.get(webhook.SIGNATURE_HEADER, ""))
    if why is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"webhook signature rejected: {why}")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(HTTP_422, "body is not JSON") from exc
    if not isinstance(payload, dict) or json_depth(payload) > MAX_JSON_DEPTH:
        raise HTTPException(HTTP_422, "body must be a JSON object")
    ack = _receive(conn, "webhook", "whk", {"event": request.headers.get(webhook.EVENT_HEADER, ""), "body": payload})
    logger.info("webhook simulator accepted %s", ack["receipt_id"])
    return {**ack, "simulator": "행정 시스템 수신 시뮬레이터"}


@router.get("/sim/inbox")
def inbox(channel: Literal["rsu", "119", "webhook"] | None = None, limit: int = Query(20, ge=1, le=C.INBOX_LIST_MAX),
          conn: sqlite3.Connection = Depends(get_conn)) -> list[dict[str, Any]]:
    return store.list_inbox(conn, channel, limit)


@router.get("/sim/inbox/{receipt_id}")
def inbox_item(receipt_id: str = Path(..., pattern=RECEIPT_RE),
               conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    row = store.get_inbox(conn, receipt_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "receipt not found")
    return row
