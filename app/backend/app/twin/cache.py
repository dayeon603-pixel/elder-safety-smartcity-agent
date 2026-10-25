"""Small in-process result cache for the twin's hourly documents (state, actions panel) + the demo warm-up.

The inputs are static during a demo (module tables, source caches), so a (sgg, date, hour, daytype) document can be
reused; feedback-driven fields (action `status`) are re-read per request by the router. `warm_demo` precomputes the demo
dates for 관악구 so the first drag of the slider is already < 10 ms (`make twin-warm`, or KCF_WARMUP=true at startup).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, Final

from .. import db
from ..config import Settings
from . import constants as C

logger = logging.getLogger("twin.cache")
MAX_ENTRIES: Final[int] = 1024
WARM_DATES: Final[tuple[str, ...]] = ("2025-07-22", "2025-09-04")  # 폭염일, 비 오는 밤 (STORY_CONTRACT)
WARM_HOURS: Final[tuple[int, ...]] = tuple(range(C.HOUR_MIN, C.HOUR_MAX + 1))
_LOCK = threading.Lock()
_STORE: OrderedDict[tuple[str, ...], dict[str, Any]] = OrderedDict()
Key = tuple[str, ...]


def get_or_build(key: Key, build: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Return the cached document for `key`, building it once (LRU, MAX_ENTRIES)."""
    with _LOCK:
        hit = _STORE.get(key)
        if hit is not None:
            _STORE.move_to_end(key)
            return hit
    doc = build()
    with _LOCK:
        _STORE[key] = doc
        _STORE.move_to_end(key)
        while len(_STORE) > MAX_ENTRIES:
            _STORE.popitem(last=False)
    return doc


def clear() -> None:
    with _LOCK:
        _STORE.clear()


def size() -> int:
    with _LOCK:
        return len(_STORE)


def warm_demo(settings: Settings, sgg_code: str = C.DEFAULT_SGG, dates: tuple[str, ...] = WARM_DATES,
              hours: tuple[int, ...] = WARM_HOURS) -> dict[str, Any]:
    """Precompute state + actions (default daytype) for every (date, hour); returns timings. Errors are reported, not raised."""
    from . import actions as twin_actions
    from . import state as twin_state
    from .. import repo_heat

    t0 = time.perf_counter()
    out: dict[str, Any] = {"sgg_code": sgg_code, "dates": list(dates), "hours": len(hours), "errors": [], "entries": 0}
    conn = db.connect(settings.db_path)
    try:
        region = repo_heat.get_region(conn, sgg_code)
        if region is None:
            out["errors"].append(f"region {sgg_code} not found")
            return out
        for date_ in dates:
            for hour in hours:
                try:
                    d, h, dt = twin_state.resolve_clock(date_, hour, None)
                    get_or_build(("state", sgg_code, d, str(h), dt),
                                 lambda: twin_state.build_state(conn, settings, region, d, h, dt, requested_date=date_, requested_hour=hour))
                    get_or_build(("actions", sgg_code, d, str(h), dt), lambda: twin_actions.build_panel(conn, settings, region, d, h, dt))
                    out["entries"] += 2
                except (twin_state.DataUnavailable, ValueError) as exc:
                    out["errors"].append(f"{date_} {hour:02d}: {exc}")
    finally:
        conn.close()
    out["seconds"] = round(time.perf_counter() - t0, 1)
    logger.info("twin warm %s: %d entries in %.1fs, %d errors", sgg_code, out["entries"], out["seconds"], len(out["errors"]))
    return out


def main() -> int:
    import sys

    from ..config import load_settings

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    rep = warm_demo(load_settings())
    sys.stdout.write(f"twin warm: {rep}\n")
    return 0 if not rep["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["MAX_ENTRIES", "WARM_DATES", "WARM_HOURS", "clear", "get_or_build", "size", "warm_demo"]
