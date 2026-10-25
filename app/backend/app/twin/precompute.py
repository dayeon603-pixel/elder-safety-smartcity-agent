"""CLI: precompute twin scenes. `python -m app.twin.precompute 11620 [11650 ...]` (needs pyarrow; shapely optional)."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from .. import db
from ..config import load_settings
from . import constants as C
from .scene_build import TwinBuildError, build_scene

logger = logging.getLogger("twin.precompute")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="precompute /twin/<sgg>/scene files")
    parser.add_argument("codes", nargs="*", default=[C.DEFAULT_SGG], help="시군구 codes (default 관악구 11620)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = load_settings()
    conn = db.connect(settings.db_path)
    rc = 0
    try:
        for code in args.codes:
            try:
                info = build_scene(conn, settings, code)
            except TwinBuildError as exc:
                logger.error("%s: %s", code, exc)
                rc = 1
                continue
            logger.info("%s: %s", code, info)
    finally:
        conn.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
