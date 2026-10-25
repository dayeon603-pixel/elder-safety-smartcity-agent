"""Token-bucket rate limiter, client-key derivation and the ASGI middleware."""

from __future__ import annotations

import ipaddress
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from itertools import islice

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

MAX_TRACKED_KEYS = 10_000
EVICTION_SCAN = 64
OVERFLOW_KEY = "overflow"
IPV6_PREFIX_LEN = 64
UNKNOWN_CLIENT = "unknown"
FORWARDED_HEADER = "x-forwarded-for"
AUTH_PREFIX = "/auth/"
VERIFY_PREFIX = "/audit/"
AGENT_RUN_PATH = "/agent/run"
SIM_PREFIX = "/sim/"
FEEDBACK_PATH = "/feedback"
CAP_PREFIX = "/cap/"
DOCUMENTS_PREFIX = "/documents/"


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated: float


def normalize_ip(raw: str) -> str | None:
    """Canonical rate-limit key for an IP literal, or None if `raw` is not an IP.

    IPv4-mapped IPv6 collapses to IPv4; other IPv6 addresses are keyed by their /64
    (a single subscriber typically controls a whole /64, so per-address keys are free to
    rotate).
    """
    try:
        ip = ipaddress.ip_address(raw.strip())
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/{IPV6_PREFIX_LEN}", strict=False))
    return str(ip)


READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def is_loopback_peer(request: Request) -> bool:
    """True when the TCP peer is 127.0.0.0/8 or ::1 (the socket address, never a header)."""
    peer = request.client.host if request.client else None
    if not peer:
        return False
    try:
        return ipaddress.ip_address(peer.strip()).is_loopback
    except ValueError:
        return peer == "testclient"  # starlette's TestClient peer name


def client_key(request: Request, trust_proxy: bool) -> str:
    """Rate-limit key for the caller.

    With `trust_proxy`, the RIGHTMOST X-Forwarded-For entry is used: it is the one our own
    reverse proxy appended (the left side is whatever the client sent). Requires that the
    backend is reachable only through that proxy. An unparsable entry falls back to the
    socket peer. Without `trust_proxy`, the header is ignored.
    """
    if trust_proxy:
        fwd = request.headers.get(FORWARDED_HEADER)
        if fwd:
            key = normalize_ip(fwd.rsplit(",", 1)[-1])
            if key is not None:
                return key
    peer = request.client.host if request.client else None
    return (normalize_ip(peer) if peer else None) or peer or UNKNOWN_CLIENT


class TokenBucketLimiter:
    """Per-key token bucket: capacity C, refill r tokens/s, each request costs 1.

    Memory is bounded by `max_keys`. When full, only buckets that have already refilled to
    capacity are evicted (dropping them loses nothing: a new bucket also starts full). If
    every scanned bucket is still depleted, unseen keys share one overflow bucket instead
    of evicting anyone, so flooding with fresh keys cannot reset a throttled client.
    """

    def __init__(
        self,
        capacity: float,
        refill_per_s: float,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = MAX_TRACKED_KEYS,
    ) -> None:
        if capacity <= 0 or refill_per_s <= 0:
            raise ValueError("capacity and refill_per_s must be > 0")
        self.capacity = capacity
        self.refill = refill_per_s
        self._clock = clock
        self._max_keys = max_keys
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()
        self._overflow = _Bucket(capacity, clock())
        self._lock = threading.Lock()

    def _refilled(self, b: _Bucket, now: float) -> float:
        return min(self.capacity, b.tokens + (now - b.updated) * self.refill)

    def _evict_one(self, now: float) -> bool:
        for key in islice(self._buckets, EVICTION_SCAN):
            if self._refilled(self._buckets[key], now) >= self.capacity:
                del self._buckets[key]
                return True
        return False

    def allow(self, key: str) -> tuple[bool, float]:
        """Consume one token. Returns (allowed, seconds_until_next_token)."""
        now = self._clock()
        with self._lock:
            b = self._buckets.get(key)
            if b is None:
                if len(self._buckets) >= self._max_keys and not self._evict_one(now):
                    logger.warning("rate-limit table full of throttled clients; using shared bucket")
                    b = self._overflow
                else:
                    b = _Bucket(self.capacity, now)
                    self._buckets[key] = b
            if b is not self._overflow:
                self._buckets.move_to_end(key)
            b.tokens = self._refilled(b, now)
            b.updated = now
            if b.tokens >= 1.0:
                b.tokens -= 1.0
                return True, 0.0
            return False, (1.0 - b.tokens) / self.refill


class RateLimitMiddleware(BaseHTTPMiddleware):
    """429 + Retry-After when a client's bucket is empty.

    `routes` maps a path prefix to a stricter limiter (first match wins); everything else
    uses `general`. Each limiter keeps its own bucket per client key.
    """

    def __init__(
        self,
        app: ASGIApp,
        general: TokenBucketLimiter,
        routes: Sequence[tuple[str, TokenBucketLimiter]] = (),
        trust_proxy: bool = False,
        exempt_loopback: bool = False,
    ) -> None:
        super().__init__(app)
        self._general = general
        self._routes = tuple(routes)
        self._trust_proxy = trust_proxy
        # Read requests from the loopback peer skip the buckets (demo browser on the same machine). Never combined with
        # trust_proxy: behind a proxy every peer is loopback. POST/PUT keep their limits and their token checks.
        self._exempt_loopback = exempt_loopback and not trust_proxy

    def _limiter_for(self, path: str) -> TokenBucketLimiter:
        """A route key ending in "/" is a prefix; otherwise it matches that path or its sub-paths
        ("/agent/run" covers "/agent/run" and "/agent/run/x" but not "/agent/runs")."""
        for key, limiter in self._routes:
            if path == key or path.startswith(key if key.endswith("/") else key + "/"):
                return limiter
        return self._general

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if self._exempt_loopback and request.method in READ_METHODS and is_loopback_peer(request):
            return await call_next(request)
        limiter = self._limiter_for(request.url.path)
        allowed, wait = limiter.allow(client_key(request, self._trust_proxy))
        if not allowed:
            retry = max(1, int(wait + 0.999))
            return JSONResponse(
                {"detail": "rate limit exceeded"}, status_code=429, headers={"Retry-After": str(retry)}
            )
        return await call_next(request)
