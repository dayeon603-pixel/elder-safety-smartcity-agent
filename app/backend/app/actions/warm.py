"""Warm the parsed-source caches (행정동, bus stops, crosswalks, AEDs) so the first /actions call is fast.

Used by `python -m app.cli preflight` (make demo-check) and, with KCF_WARMUP=true, by the app lifespan on
a background thread. Each table is built (or loaded from the disk cache) once per process; timings are
returned for the report. A missing source is reported, not raised.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from ..heat.common import EXPERIMENT_DIR
from . import aed_points, bit, dongs, signals

logger = logging.getLogger("actions.warm")
PROBE_REGION = ("서울특별시", "관악구")  # any region works; only the shared tables are built


def warm(data_dir: Path) -> dict[str, Any]:
    """{table: {"ms": float} | {"error": str}} for dongs, stops, crosswalks, aeds."""
    out: dict[str, Any] = {}
    steps = (
        ("dongs", lambda: dongs.dongs_of_region(PROBE_REGION[0], PROBE_REGION[1], data_dir, EXPERIMENT_DIR)),
        ("stops", lambda: bit.load_stops(data_dir)),
        ("crosswalks", lambda: signals.load_crosswalks(data_dir)),
        ("aeds", lambda: aed_points.load_aeds(data_dir)),
    )
    for name, fn in steps:
        t0 = time.perf_counter()
        try:
            fn()
        except (FileNotFoundError, dongs.DongDataError, OSError, ValueError) as exc:
            out[name] = {"error": f"{type(exc).__name__}: {exc}"}
            logger.warning("warm %s failed: %s", name, exc)
            continue
        out[name] = {"ms": round((time.perf_counter() - t0) * 1000)}
        logger.info("warm %s: %d ms", name, out[name]["ms"])
    return out


__all__ = ["warm"]
