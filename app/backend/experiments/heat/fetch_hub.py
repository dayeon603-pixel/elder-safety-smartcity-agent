"""Fetch hourly ASOS (all stations per call) for the heat seasons into the raw cache. Resume-safe.

Run: uv run --project /Users/chloekang/kcf/app/backend python experiments/heat/fetch_hub.py
Hours are fetched in priority order (afternoon first) so a quota stop still leaves the peak hours complete.
"""

from __future__ import annotations

import logging
import sys
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

import kma_hub
from common import YEARS

logger = logging.getLogger("heat.fetch")
WORKERS = 2  # more concurrency made the hub slower (head-of-line stalls), not faster
HOUR_PRIORITY = [14, 15, 13, 16, 12, 17, 11, 18, 10, 19]  # KST; 11-17 first
FETCH_START, FETCH_END = (5, 17), (9, 26)  # season May 20..Sep 25 plus 3 lag days before and 1 lead day after


def hub_times(hours: list[int]) -> list[str]:
    out = []
    for h in hours:
        for y in YEARS:
            for d in pd.date_range(f"{y}-{FETCH_START[0]:02d}-{FETCH_START[1]:02d}", f"{y}-{FETCH_END[0]:02d}-{FETCH_END[1]:02d}"):
                out.append(f"{d:%Y%m%d}{h:02d}00")
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    key = kma_hub._key()
    last = [0.0]
    hours = HOUR_PRIORITY
    if "--hours" in sys.argv:  # e.g. --hours 11,17,12 : a second worker drains these from the far end
        hours = [int(x) for x in sys.argv[sys.argv.index("--hours") + 1].split(",")]
    todo = [t for t in hub_times(hours) if not kma_hub.cache_path(t).is_file()]
    if "--reverse" in sys.argv:  # a second worker process can drain from the other end
        todo = todo[::-1]
    logger.info("to fetch: %d", len(todo))
    fails = 0

    def one(tm: str) -> bool:
        try:
            kma_hub.fetch_hour(tm, key, last)
            return True
        except kma_hub.HubError as exc:
            logger.error("%s", exc)
            return False

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for i, ok in enumerate(ex.map(one, todo)):
            fails += 0 if ok else 1
            if i % 200 == 0:
                logger.info("progress %d/%d", i, len(todo))
            if fails >= 40:
                logger.error("too many failures; stopping (rerun to resume)")
                ex.shutdown(wait=False, cancel_futures=True)
                return 2
    logger.info("done, failures=%d", fails)
    return 0


if __name__ == "__main__":
    sys.exit(main())
