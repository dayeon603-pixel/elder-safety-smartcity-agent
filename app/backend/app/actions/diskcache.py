"""Versioned on-disk cache for parsed source tables (행정동 rings, bus stops, crosswalks, AED points).

Parsing the 35 MB 행정동 GeoJSON twice plus two large CSVs took ~58 s on the first /actions call; the
parsed objects pickle to a few MB and load in well under a second. The cache key is the sha256 of the
source files' (path, size, mtime) plus a code version, so an updated source or loader never serves a
stale table. Pickles are read only from our own cache directory (KCF_ACTIONS_CACHE_DIR, default
backend/var/actions_cache); a corrupt file is rebuilt, never trusted.
"""

from __future__ import annotations

import hashlib
import logging
import os
import pickle
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypeVar

from ..config import APP_ROOT

logger = logging.getLogger("actions.diskcache")
T = TypeVar("T")
CACHE_DIR_ENV = "KCF_ACTIONS_CACHE_DIR"
DEFAULT_CACHE_DIR = APP_ROOT / "backend" / "var" / "actions_cache"
PROTOCOL = pickle.HIGHEST_PROTOCOL


def cache_dir() -> Path:
    return Path(os.environ.get(CACHE_DIR_ENV, str(DEFAULT_CACHE_DIR)))


def _key(name: str, version: int, sources: Sequence[Path]) -> str:
    h = hashlib.sha256(f"{name}|v{version}".encode())
    for p in sources:
        st = p.stat()
        h.update(f"|{p.resolve()}|{st.st_size}|{int(st.st_mtime)}".encode())
    return h.hexdigest()[:24]


def cached(name: str, version: int, sources: Sequence[Path], build: Callable[[], T], directory: Path | None = None) -> T:
    """Return `build()`'s result, served from `<dir>/<name>-<key>.pkl` when the sources are unchanged.

    Raises:
        FileNotFoundError: a source file is missing (the loader's own error contract).
    """
    for p in sources:
        if not p.is_file():
            raise FileNotFoundError(p)
    d = directory or cache_dir()
    path = d / f"{name}-{_key(name, version, sources)}.pkl"
    if path.is_file():
        t0 = time.perf_counter()
        try:
            with path.open("rb") as fh:
                obj: T = pickle.load(fh)  # noqa: S301 - our own cache directory, keyed by source hash
        except (OSError, pickle.UnpicklingError, EOFError, AttributeError, ImportError) as exc:
            logger.warning("cache %s unreadable (%s): rebuilding", path.name, type(exc).__name__)
        else:
            logger.info("cache hit %s (%.2fs)", path.name, time.perf_counter() - t0)
            return obj
    t0 = time.perf_counter()
    obj = build()
    try:
        d.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with tmp.open("wb") as fh:
            pickle.dump(obj, fh, protocol=PROTOCOL)
        tmp.replace(path)
        for old in d.glob(f"{name}-*.pkl"):  # keep one version per table
            if old != path:
                old.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("cache %s not written: %s", path.name, exc)
    logger.info("cache built %s (%.1fs)", path.name, time.perf_counter() - t0)
    return obj


__all__ = ["CACHE_DIR_ENV", "cache_dir", "cached"]
