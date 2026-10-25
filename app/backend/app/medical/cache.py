"""TTL cache with on-disk snapshots so the demo works without network.

Lookup order: memory (fresh) -> disk snapshot (fresh) -> live fetch (writes a snapshot with timestamp and
sha256) -> stale disk snapshot -> read-only seed snapshot (data cache) -> None. Every answer says where it
came from and how old it is, so the UI can label "N분 전 스냅샷".
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import constants as C
from .client import NmcError

logger = logging.getLogger(__name__)
_SAFE = re.compile(r"[^0-9A-Za-z가-힣_-]+")
Fetcher = Callable[[], list[dict[str, str]]]


@dataclass(frozen=True, slots=True)
class Fetched:
    items: list[dict[str, str]]
    fetched_at: str  # ISO 8601 UTC
    source: str  # live | memory | snapshot | seed
    age_s: float
    stale: bool  # older than the service TTL (live fetch failed)
    sha256: str
    error: str | None = None  # why live was not possible (when stale)

    def label(self) -> dict[str, Any]:
        return {"source": self.source, "fetched_at": self.fetched_at, "age_s": round(self.age_s),
                "stale": self.stale, "error": self.error}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds").replace("+00:00", "Z")


def snapshot_name(service: str, sido: str, sgg: str) -> str:
    return _SAFE.sub("_", f"{service}_{sido}_{sgg}") + C.SNAPSHOT_SUFFIX


class MedicalCache:
    def __init__(self, cache_dir: Path, seed_dir: Path | None, ttl_s: dict[str, float] | None = None,
                 clock: Callable[[], datetime] = _now) -> None:
        self._dir = cache_dir
        self._seed = seed_dir
        self._ttl = dict(ttl_s or C.TTL_S)
        self._clock = clock
        self._mem: dict[str, Fetched] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ disk
    def _read(self, path: Path, source: str) -> Fetched | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            items = list(data["items"])
            fetched_at = str(data["fetched_at"])
            age = (self._clock() - datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))).total_seconds()
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return Fetched(items, fetched_at, source, max(0.0, age), False, str(data.get("sha256", "")))

    def write_snapshot(self, service: str, sido: str, sgg: str, items: list[dict[str, str]],
                       directory: Path | None = None) -> Path:
        """Atomic JSON snapshot {service, sido, sgg, fetched_at, sha256(items), n, items}."""
        target_dir = directory or self._dir
        target_dir.mkdir(parents=True, exist_ok=True)
        body = json.dumps(items, ensure_ascii=False, sort_keys=True)
        doc = {"service": service, "sido": sido, "sgg": sgg, "fetched_at": _iso(self._clock()),
               "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(), "n": len(items), "items": items,
               "source": C.SOURCE_LABEL}
        path = target_dir / snapshot_name(service, sido, sgg)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return path

    # ------------------------------------------------------------------ lookup
    def get(self, service: str, sido: str, sgg: str, fetcher: Fetcher | None) -> Fetched | None:
        """See module docstring. `fetcher` is None when no API key/quota is available (snapshots only)."""
        ttl = self._ttl[service]
        key = snapshot_name(service, sido, sgg)
        with self._lock:
            mem = self._mem.get(key)
        if mem is not None:
            age = (self._clock() - datetime.fromisoformat(mem.fetched_at.replace("Z", "+00:00"))).total_seconds()
            if age <= ttl:
                return Fetched(mem.items, mem.fetched_at, "memory", age, False, mem.sha256)
        disk = self._read(self._dir / key, "snapshot")
        if disk is not None and disk.age_s <= ttl:
            with self._lock:
                self._mem[key] = disk
            return disk
        error: str | None = None
        if fetcher is not None:
            try:
                items = fetcher()
            except NmcError as exc:
                error = str(exc)
                logger.warning("nmc %s %s %s live fetch failed: %s", service, sido, sgg, error)
            else:
                path = self.write_snapshot(service, sido, sgg, items)
                fresh = self._read(path, "live")
                assert fresh is not None
                with self._lock:
                    self._mem[key] = fresh
                return fresh
        else:
            error = "no API key or quota: snapshots only"
        for candidate, source in ((disk, "snapshot"), (self._read(self._seed / key, "seed") if self._seed else None, "seed")):
            if candidate is not None:
                return Fetched(candidate.items, candidate.fetched_at, source, candidate.age_s, True, candidate.sha256, error)
        return None


__all__ = ["Fetched", "MedicalCache", "snapshot_name"]
