"""Shared helpers for the heat ingest steps: paths, encodings, dataset provenance, season calendar."""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .. import repo
from ..config import APP_ROOT, DEFAULT_DATA_DIR, DEFAULT_DB_PATH
from ..routers.datasets import sha256_of

logger = logging.getLogger("heat")

# Study window. Mirrors experiments/heat/common.py (single place the numbers are agreed on).
YEARS: tuple[int, ...] = tuple(range(2019, 2026))
SEASON_START: tuple[int, int] = (5, 20)  # inclusive (month, day)
SEASON_END: tuple[int, int] = (9, 25)  # inclusive; the heat-case file ends 2025-09-25 (experiments/heat/common.py)
MAX_SOURCE_BYTES = 2 * 1024**3
ENCODINGS: tuple[str, ...] = ("utf-8-sig", "cp949")
EARTH_RADIUS_KM = 6371.0088
HEAT_DATASET_PREFIX = "heat_"
EXPERIMENT_DIR = APP_ROOT / "backend" / "experiments" / "heat"
EXPERIMENT_OUT_DIR = EXPERIMENT_DIR / "out"

# Source files, relative to KCF_DATA_DIR.
HEAT_CSV = "b_climate_env_energy/kr_15149889_heat_illness_reports_2011_2025.csv"
POP_CSV = "j_population_regional/kr_15097972_resident_pop_by_dong_sex_age_202609.csv"
GEOJSON = "extra/boundaries/HangJeongDong_ver20261001.geojson"
WX_DIR = "extra/meteostat_daily"
STATIONS_JSON = "extra/meteostat_daily/stations_lite.json.gz"
SHELTER_CHUNGNAM = "b_climate_env_energy/kr_15118638_chungnam_cooling_shelters.csv"
SHELTER_EXTRA_DIR = "extra/shelters"
SHELTER_BUSAN = f"{SHELTER_EXTRA_DIR}/kr_15152994_busan_cooling_shelters.csv"

_SPACE = re.compile(r"\s+")


class IngestError(RuntimeError):
    """Raised when a source file is missing, malformed, or an invariant fails."""


@dataclass(frozen=True, slots=True)
class HeatPaths:
    """Filesystem locations; the only inputs are KCF_DB_PATH, KCF_DATA_DIR and the config defaults."""

    db_path: Path
    data_dir: Path
    out_dir: Path


def resolve_paths(env: Mapping[str, str] | None = None) -> HeatPaths:
    """Resolve paths from the environment (no secret needed: these commands never serve HTTP)."""
    e = os.environ if env is None else env
    return HeatPaths(
        db_path=Path(e.get("KCF_DB_PATH", str(DEFAULT_DB_PATH))),
        data_dir=Path(e.get("KCF_DATA_DIR", str(DEFAULT_DATA_DIR))),
        out_dir=Path(e.get("KCF_HEAT_OUT_DIR", str(EXPERIMENT_OUT_DIR))),
    )


def nospace(text: str) -> str:
    """Remove all whitespace (heat labels and 2026 names differ only in spacing)."""
    return _SPACE.sub("", text)


def detect_encoding(path: Path) -> str:
    """Return the first of utf-8-sig / cp949 that decodes the whole file.

    Raises:
        IngestError: if neither decodes it.
    """
    raw = path.read_bytes()
    for enc in ENCODINGS:
        try:
            raw.decode(enc)
        except UnicodeDecodeError:
            continue
        return enc
    raise IngestError(f"{path.name}: not utf-8 or cp949")


def require_file(data_dir: Path, rel: str) -> Path:
    """Path of a source file under the data dir.

    Raises:
        IngestError: if it does not exist.
    """
    path = data_dir / rel
    if not path.is_file():
        raise IngestError(f"missing source file: {path}")
    return path


def season_dates(year: int) -> list[str]:
    """Every ISO date of the season window in `year` (inclusive of both ends)."""
    d = date(year, *SEASON_START)
    end = date(year, *SEASON_END)
    out: list[str] = []
    while d <= end:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def in_season(mmdd: tuple[int, int]) -> bool:
    """True if (month, day) lies in the season window."""
    return SEASON_START <= mmdd <= SEASON_END


def utc_now_iso() -> str:
    """Current UTC time, second precision."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def register_dataset(
    conn: sqlite3.Connection,
    *,
    name: str,
    path: Path | None,
    source_url: str,
    license_: str,
    description: str,
    row_count: int,
    roles: dict[str, Any],
    sha256: str | None = None,
) -> int:
    """Create or refresh a `datasets` row with the file's SHA-256; return its id.

    Joins the caller's transaction. An existing row with the same name is updated in place
    (hash, row count, ingest time), so re-ingesting after the file changed keeps FKs valid.
    """
    if sha256 is None:
        if path is None:
            raise IngestError("register_dataset needs a path or an explicit sha256")
        sha256 = sha256_of(path, MAX_SOURCE_BYTES)
    mtime = (
        datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")
        if path is not None
        else None
    )
    now = utc_now_iso()
    file_name = path.name if path is not None else None
    existing = repo.get_dataset_by_name(conn, name)
    if existing is None:
        dataset_id = repo.begin_dataset(
            conn, name=name, source_url=source_url, license_=license_, sha256=sha256,
            fetched_at=now, file_name=file_name, description=description, created_by=None,
            column_roles=roles, file_mtime=mtime,
        )
        repo.finalize_dataset(conn, dataset_id, row_count, roles)
        return dataset_id
    conn.execute(
        "UPDATE datasets SET source_url = ?, license = ?, sha256 = ?, fetched_at = ?, file_name = ?, "
        "description = ?, file_mtime = ? WHERE id = ?",
        (source_url, license_, sha256, now, file_name, description, mtime, existing["id"]),
    )
    repo.finalize_dataset(conn, int(existing["id"]), row_count, roles)
    return int(existing["id"])
