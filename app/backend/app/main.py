"""Application factory."""

from __future__ import annotations

import logging
import threading

import httpx
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from . import __version__, db, ops, repo
from .actions import warm as actions_warm
from .agent import constants as agent_constants
from .config import Settings, load_settings
from .medical import MedicalService
from .routers import (
    actions, agent, audit, auth, cap, datasets, documents, feedback, field, heat, medical, meta, mobility, modules, runs,
    terminal, twin,
)
from .routers.audit import VerifyCache
from .routers.auth import FailureAggregator
from .security.bodylimit import BodyLimitMiddleware
from .twin import cache as twin_cache
from .twin.terminal import TerminalHub
from .security.headers import (
    INTERNAL_ERROR_BODY,
    ErrorBoundaryMiddleware,
    SecurityHeadersMiddleware,
    sanitize_validation_errors,
)
from .security.ratelimit import (
    AGENT_RUN_PATH, AUTH_PREFIX, CAP_PREFIX, DOCUMENTS_PREFIX, FEEDBACK_PATH, SIM_PREFIX, VERIFY_PREFIX,
    RateLimitMiddleware, TokenBucketLimiter,
)

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Settings default to the environment (fails fast without a secret)."""
    cfg = settings or load_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        conn = db.connect(cfg.db_path)
        try:
            logger.info("schema version %d at %s", db.migrate(conn), cfg.db_path)
            dropped = repo.delete_incomplete_datasets(conn)
            if dropped:
                logger.warning("removed %d half-ingested dataset(s) left by a crash", dropped)
        finally:
            conn.close()
        if cfg.demo_token is None:
            logger.warning("KCF_DEMO_TOKEN unset: /agent/* and /sim/* accept unauthenticated calls")
        if cfg.warmup:
            # Preload the planner and the parsed source tables off the event loop so the first run and the
            # first /actions call do not pay the load time.
            models = (agent_constants.PLANNER_MODEL,)
            threading.Thread(target=ops.warm_up, args=(models,), name="ollama-warm-up", daemon=True).start()
            threading.Thread(target=actions_warm.warm, args=(cfg.data_dir,), name="actions-warm-up", daemon=True).start()
            threading.Thread(target=twin_cache.warm_demo, args=(cfg,), name="twin-warm-up", daemon=True).start()
        yield

    app = FastAPI(
        title="KCF starter", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None,
        openapi_url="/openapi.json" if cfg.dev else None,
    )
    app.state.settings = cfg
    app.state.verify_cache = VerifyCache()
    app.state.login_failures = FailureAggregator()
    app.state.agent_slots = threading.BoundedSemaphore(agent_constants.MAX_CONCURRENT_RUNS)
    app.state.webhook_threads = []  # live outbound deliveries (tests join them)
    app.state.terminal_hub = TerminalHub()  # 현장 단말 SSE channel (in-process only)
    # NMC medical data: tests set app.state.medical_http (a client factory) before the first call.
    app.state.medical = MedicalService(cfg, http_factory=lambda: (getattr(app.state, "medical_http", None) or
                                                                  (lambda: httpx.Client()))())

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # pydantic's default body echoes `input` (e.g. the submitted password) and `ctx`.
        return JSONResponse({"detail": sanitize_validation_errors(list(exc.errors()))}, status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        # Fallback only: ErrorBoundaryMiddleware normally answers first, inside CORS/headers.
        logger.error("unhandled %s", type(exc).__name__)
        return JSONResponse(INTERNAL_ERROR_BODY, status_code=500)

    # add_middleware order: the last one added is outermost. Innermost to outermost:
    # body limit -> error boundary -> rate limit -> CORS -> security headers, so 413s, 429s and
    # 500s all carry the headers. The body limit sits inside the error boundary so its own 413
    # (raised from the wrapped receive) is not turned into a 500.
    app.add_middleware(
        BodyLimitMiddleware,
        limits=((SIM_PREFIX, cfg.max_sim_body_bytes), (FEEDBACK_PATH, cfg.max_sim_body_bytes),
                (CAP_PREFIX, cfg.max_sim_body_bytes), (AGENT_RUN_PATH, cfg.max_agent_body_bytes)),
        default=cfg.max_agent_body_bytes,
    )
    app.add_middleware(ErrorBoundaryMiddleware)
    app.add_middleware(
        RateLimitMiddleware,
        general=TokenBucketLimiter(cfg.rate_capacity, cfg.rate_refill_per_s),
        routes=(
            (AUTH_PREFIX, TokenBucketLimiter(cfg.auth_rate_capacity, cfg.auth_rate_refill_per_s)),
            (VERIFY_PREFIX, TokenBucketLimiter(cfg.verify_rate_capacity, cfg.verify_rate_refill_per_s)),
            (AGENT_RUN_PATH, TokenBucketLimiter(cfg.agent_rate_capacity, cfg.agent_rate_refill_per_s)),
            (SIM_PREFIX, TokenBucketLimiter(cfg.sim_rate_capacity, cfg.sim_rate_refill_per_s)),
            (FEEDBACK_PATH, TokenBucketLimiter(cfg.sim_rate_capacity, cfg.sim_rate_refill_per_s)),
            (CAP_PREFIX, TokenBucketLimiter(cfg.sim_rate_capacity, cfg.sim_rate_refill_per_s)),
            (DOCUMENTS_PREFIX, TokenBucketLimiter(cfg.sim_rate_capacity, cfg.sim_rate_refill_per_s)),
        ),
        trust_proxy=cfg.trust_proxy,
        exempt_loopback=cfg.rate_exempt_loopback,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cfg.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,
        max_age=600,
    )
    app.add_middleware(SecurityHeadersMiddleware, hsts=cfg.hsts)
    for r in (meta.router, auth.router, datasets.router, runs.router, audit.router, heat.router, actions.router,
              agent.router, feedback.router, cap.router, documents.router, medical.router, field.router,
              mobility.router, twin.router, terminal.router, *modules.ROUTERS):
        app.include_router(r)
    return app
