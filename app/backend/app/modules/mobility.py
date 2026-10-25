"""Load experiments/mobility/out/recommended_top2000.csv into `mobility_stop` (idempotent, provenance-checked).

`python -m app.modules.mobility [--force]` (also run by `python -m app.modules.load`). The file is registered in
`datasets` with its sha256; the table is rewritten in one transaction only when that sha256 changed (or --force).
Values are copied, never computed; `sgg_code` is resolved from `region` by exact (sido, name) match.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import logging
import math
import os
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .. import db, repo
from ..config import APP_ROOT
from ..heat.common import register_dataset

logger = logging.getLogger("modules.mobility")

MOBILITY_ENV = "KCF_MOBILITY_OUT_DIR"
DEFAULT_OUT_DIR = APP_ROOT / "backend" / "experiments" / "mobility" / "out"
FILE = "recommended_top2000.csv"
DATASET_NAME = "mobility_recommended_top2000"
SOURCE_URL = "https://www.data.go.kr/data/15067528/fileData.do"  # 전국 버스정류장 위치정보 (input of the experiment)
LICENSE = "자체 산출물 (원 데이터 이용 조건을 따름)"
DESCRIPTION = ("이동(mobility) 모듈: 65세 이상 도보권 인구와 폭염 가중 한계이득으로 고른 추천 정류장 상위 2,000곳 "
               "(experiments/mobility/run.py 산출)")
ENCODING = "utf-8-sig"
REQUIRED = ("stop_id", "name", "lat", "lon", "sido", "sgg", "dong", "score", "p65_within_walk", "greedy_rank", "recommended",
            "marginal_gain_heat_weighted")
TRUE_VALUES = ("true", "1", "yes", "y")


class MobilityLoadError(RuntimeError):
    """The source file is missing/malformed or violates a table constraint."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _num(row: dict[str, str], col: str) -> float:
    try:
        v = float(row[col])
    except (KeyError, ValueError, TypeError) as exc:
        raise MobilityLoadError(f"{col!r}: not a number ({row.get(col)!r})") from exc
    if not math.isfinite(v):
        raise MobilityLoadError(f"{col!r}: not finite")
    return v


def _opt_num(row: dict[str, str], col: str) -> float | None:
    raw = (row.get(col) or "").strip()
    return _num(row, col) if raw else None


def read_rows(path: Path) -> list[dict[str, str]]:
    """CSV rows with the required columns present.

    Raises:
        MobilityLoadError: unreadable file or missing columns.
    """
    try:
        with path.open(encoding=ENCODING, newline="") as fh:
            reader = csv.DictReader(fh)
            missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
            if missing:
                raise MobilityLoadError(f"{path.name}: missing columns {missing}")
            return list(reader)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise MobilityLoadError(f"{path.name}: unreadable ({exc})") from exc


def load_mobility(conn: sqlite3.Connection, out_dir: Path | None = None, force: bool = False) -> str:
    """'loaded' | 'unchanged'.

    Raises:
        MobilityLoadError: missing/malformed file or a constraint violation (nothing is partially written).
    """
    d = out_dir or Path(os.environ.get(MOBILITY_ENV, str(DEFAULT_OUT_DIR)))
    path = d / FILE
    if not path.is_file():
        raise MobilityLoadError(f"missing source file: {path}")
    sha = _sha256(path)
    existing = repo.get_dataset_by_name(conn, DATASET_NAME)
    have_rows = conn.execute("SELECT 1 FROM mobility_stop LIMIT 1").fetchone() is not None
    if not force and existing is not None and existing.get("sha256") == sha and have_rows:
        logger.info("mobility: unchanged")
        return "unchanged"
    rows = read_rows(path)
    codes = {(str(r["sido"]), str(r["name"])): str(r["code"]) for r in conn.execute("SELECT code, sido, name FROM region")}
    try:
        with db.transaction(conn):
            conn.execute("DELETE FROM mobility_stop")
            ds = register_dataset(conn, name=DATASET_NAME, path=path, source_url=SOURCE_URL, license_=LICENSE,
                                  description=DESCRIPTION, row_count=len(rows),
                                  roles={"kind": "module", "module": "mobility", "loaded": True}, sha256=sha)
            records: list[tuple[Any, ...]] = []
            for r in rows:
                sido, sgg = r["sido"].strip(), r["sgg"].strip()
                if not r["stop_id"].strip() or not r["name"].strip():
                    raise MobilityLoadError(f"empty stop_id/name in row {r!r}")
                records.append((
                    r["stop_id"].strip(), r["name"].strip(), _num(r, "lat"), _num(r, "lon"), sido, sgg,
                    (r.get("dong") or "").strip() or None, codes.get((sido, sgg)), _num(r, "score"), _num(r, "p65_within_walk"),
                    int(_num(r, "greedy_rank")), int(r["recommended"].strip().lower() in TRUE_VALUES),
                    _opt_num(r, "marginal_gain_heat_weighted"), ds,
                ))
            conn.executemany(
                "INSERT INTO mobility_stop (stop_id, name, lat, lon, sido, sgg, dong, sgg_code, score, p65_within_walk, "
                "greedy_rank, recommended, marginal_gain, dataset_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", records)
    except sqlite3.Error as exc:
        raise MobilityLoadError(f"mobility: {exc}") from exc
    unmatched = sum(1 for rec in records if rec[7] is None)
    logger.info("mobility: loaded %d stops (%d without region code)", len(records), unmatched)
    return "loaded"


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(prog="app.modules.mobility", description=__doc__)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    from ..heat.common import resolve_paths

    conn = db.connect(resolve_paths().db_path)
    try:
        db.migrate(conn)
        logger.info("mobility: %s", load_mobility(conn, force=args.force))
    except MobilityLoadError as exc:
        logger.error("%s", exc)
        return 2
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
