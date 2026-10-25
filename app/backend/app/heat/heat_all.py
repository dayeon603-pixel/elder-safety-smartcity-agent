"""Run the heat ingest pipeline in dependency order: regions -> weather -> asos -> cases -> shelters.

`python -m app.heat.heat_all [--only regions weather ...]`. Persisting experiment output is a
separate step (`python -m app.heat.persist`) because it needs `make heat-eval` first.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from .. import db
from .common import IngestError, resolve_paths
from .ingest_cases import ingest_cases
from .ingest_regions import ingest_regions
from .ingest_shelters import ingest_shelters
from .ingest_asos import ingest_asos
from .ingest_weather import ingest_weather

logger = logging.getLogger("heat.all")
STEPS = ("regions", "weather", "asos", "cases", "shelters")


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(prog="app.heat.heat_all", description=__doc__)
    ap.add_argument("--only", nargs="+", choices=STEPS, default=list(STEPS))
    args = ap.parse_args(argv)
    paths = resolve_paths()
    conn = db.connect(paths.db_path)
    try:
        db.migrate(conn)
        summary: dict[str, object] = {}
        if "regions" in args.only:
            summary["regions"] = ingest_regions(conn, paths)
        if "weather" in args.only:
            summary["weather"] = ingest_weather(conn, paths)
        if "asos" in args.only:  # after weather: region_station must end on the ASOS station
            summary["asos"] = ingest_asos(conn, paths)
        if "cases" in args.only:
            summary["cases"] = ingest_cases(conn, paths)
        if "shelters" in args.only:
            summary["shelters"] = ingest_shelters(conn, paths)
    except IngestError as exc:
        logger.error("%s", exc)
        return 2
    finally:
        conn.close()
    for step, result in summary.items():
        logger.info("%s: %s", step, result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
