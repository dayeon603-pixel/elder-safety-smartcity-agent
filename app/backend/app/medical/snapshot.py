"""Pre-fetch offline snapshots: `python -m app.medical.snapshot 11620 [11110 ...] [--out DIR]`.

Writes one JSON per (service, 시군구) into the seed directory (default KCF_MEDICAL_SNAPSHOT_DIR =
<data cache>/extra/nmc_snapshots) so `/medical/*` answers without network, labelled `source: seed`.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from .. import db
from ..config import load_settings
from . import constants as C
from .client import NmcError
from .service import MedicalService, region_of

logger = logging.getLogger("medical.snapshot")


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(prog="app.medical.snapshot", description=__doc__)
    ap.add_argument("codes", nargs="+", help="시군구 codes")
    ap.add_argument("--out", type=Path, default=None, help="seed directory (default KCF_MEDICAL_SNAPSHOT_DIR)")
    args = ap.parse_args(argv)
    settings = load_settings()
    svc = MedicalService(settings)
    if not svc.live_possible:
        logger.error("no data.go.kr key: cannot fetch snapshots")
        return 2
    out = args.out or settings.medical_snapshot_dir
    conn = db.connect(settings.db_path)
    try:
        rc = 0
        for code in args.codes:
            region = region_of(conn, code)
            if region is None:
                logger.error("unknown region %s", code)
                rc = 2
                continue
            client = svc._client()  # noqa: SLF001 - CLI inside the package
            assert client is not None
            for service in C.SERVICES:
                try:
                    items = client.fetch(service, region.sido, region.name)
                except NmcError as exc:
                    logger.error("%s %s: %s", service, region.name, exc)
                    rc = 1
                    continue
                path = svc.cache.write_snapshot(service, region.sido, region.name, items, directory=out)
                logger.info("%s %s: %d items -> %s", service, region.name, len(items), path)
    finally:
        conn.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
