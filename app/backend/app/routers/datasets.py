"""Dataset listing and streaming CSV ingest with SHA-256 provenance and column roles."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from .. import db, repo
from ..config import Settings
from ..deps import current_user, get_conn, get_settings
from ..schemas import AvailableFile, DatasetOut, IngestIn, IngestOut
from ..security import audit

logger = logging.getLogger(__name__)
HTTP_422 = 422  # starlette renamed these constants; the numbers are stable
HTTP_413 = 413
BATCH_ROWS = 1000
READ_CHUNK = 1 << 20
MAX_LABEL_DIGITS = 18
_LABEL_RE = re.compile(rf"-?\d{{1,{MAX_LABEL_DIGITS}}}")
router = APIRouter(prefix="/datasets", tags=["datasets"])


class CsvTooLargeError(Exception):
    """The file exceeded the configured byte cap while being read."""


class _HashingReader(io.RawIOBase):
    """Raw reader that hashes and counts every byte handed to the CSV parser.

    The digest therefore covers exactly the bytes that were parsed, and a file that grows
    after the stat() size check is still capped.
    """

    def __init__(self, fh: io.BufferedReader, max_bytes: int) -> None:
        self._fh = fh
        self._max = max_bytes
        self.sha = hashlib.sha256()
        self.total = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        n = self._fh.readinto(buffer) or 0
        self.total += n
        if self.total > self._max:
            raise CsvTooLargeError
        self.sha.update(bytes(memoryview(buffer)[:n]))
        return n


def resolve_csv(data_dir: Path, file_name: str) -> Path:
    """Resolve file_name strictly inside data_dir (no traversal, no symlink escape)."""
    root = data_dir.resolve()
    path = (root / file_name).resolve()
    if path.parent != root or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file not found in data cache")
    return path


def sha256_of(path: Path, max_bytes: int) -> str:
    """Streaming SHA-256 of a file; CsvTooLargeError past `max_bytes`."""
    with path.open("rb") as fh:
        reader = _HashingReader(fh, max_bytes)
        while reader.readinto(bytearray(READ_CHUNK)):
            pass
        return reader.sha.hexdigest()


def normalize_event_time(raw: str) -> str:
    """Parse an ISO-8601 timestamp/date and return fixed-width UTC `YYYY-MM-DDTHH:MM:SS.ffffffZ`.

    Naive values are taken to be UTC. Fixed width makes string order equal time order.

    Raises:
        ValueError: if `raw` is empty or not ISO-8601.
    """
    text = raw.strip()
    if not text:
        raise ValueError("empty timestamp")
    try:
        dt = datetime.fromisoformat(text)
        dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
        return dt.isoformat(timespec="microseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"not an ISO-8601 timestamp: {text[:40]!r}") from exc


def parse_label(raw: str, allowed: frozenset[int]) -> int:
    """Parse a class label: a plain integer that is one of `allowed` (no floats, no NaN).

    Raises:
        ValueError: otherwise.
    """
    text = raw.strip()
    if not _LABEL_RE.fullmatch(text):
        raise ValueError(f"label {text[:20]!r} is not an integer")
    try:
        value = int(text)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"label {text[:20]!r} is not an integer") from exc
    if value not in allowed:
        raise ValueError(f"label {value} not in allowed classes {sorted(allowed)}")
    return value


def _finite_number(raw: str) -> bool:
    try:
        value = float(raw)
    except ValueError:
        return False
    return value == value and value not in (float("inf"), float("-inf"))


def _row_error(n: int, line: int, msg: str) -> HTTPException:
    return HTTPException(HTTP_422, f"row {n} (line {line}): {msg}")


def requested_mapping(body: IngestIn) -> dict[str, Any]:
    """The column mapping the caller asked for (excludes the inferred feature list)."""
    return {
        "id": body.id_column, "time": body.time_column, "label": body.label_column,
        "group": body.group_column, "label_values": sorted(body.label_values),
    }


def _mapping_matches(stored: dict[str, Any], body: IngestIn) -> bool:
    want = requested_mapping(body)
    if any(stored.get(k) != v for k, v in want.items()):
        return False
    return body.feature_columns is None or stored.get("features") == body.feature_columns


def _validate_header(header: list[str], body: IngestIn) -> list[str]:
    """Check the header and column roles; return the explicit-or-candidate feature columns."""
    if not header or any(not h.strip() for h in header):
        raise HTTPException(HTTP_422, "CSV header has an empty column name")
    if len(set(header)) != len(header):
        raise HTTPException(HTTP_422, "CSV header has duplicate column names")
    roles = [body.id_column, body.time_column, body.label_column, body.group_column]
    named = [r for r in roles if r is not None]
    for col in named:
        if col not in header:
            raise HTTPException(HTTP_422, f"column not in CSV: {col}")
    if len(set(named)) != len(named):
        raise HTTPException(HTTP_422, "id/time/label/group must be four different columns")
    if body.feature_columns is not None:
        for col in body.feature_columns:
            if col not in header:
                raise HTTPException(HTTP_422, f"feature column not in CSV: {col}")
            if col in named:
                raise HTTPException(HTTP_422, f"feature column {col!r} is also an id/time/label/group column")
        if len(set(body.feature_columns)) != len(body.feature_columns):
            raise HTTPException(HTTP_422, "duplicate feature column")
        return list(body.feature_columns)
    return [h for h in header if h not in named]


def stream_rows(
    reader: Any, header: list[str], body: IngestIn, features: list[str], max_rows: int,
    explicit_features: bool,
) -> Iterator[tuple[int, repo.RecordRow, dict[str, bool]]]:
    """Yield (row_number, record, numeric_flags) for each validated CSV row.

    `reader` is a csv.reader (its `line_num` is used in errors). `numeric_flags` is the live
    dict of per-feature "every value so far is a finite number". With `explicit_features`
    a non-finite value is a 422; otherwise the column is just dropped from the feature list.
    """
    col = {h: i for i, h in enumerate(header)}
    allowed = frozenset(body.label_values)
    numeric = {f: True for f in features}
    seen: set[str] = set()
    n = 0
    for fields in reader:
        if not fields:  # blank line
            continue
        n += 1
        line = int(reader.line_num)
        if n > max_rows:
            raise HTTPException(HTTP_413, f"more than {max_rows} rows")
        if len(fields) != len(header):
            raise _row_error(n, line, f"expected {len(header)} fields, got {len(fields)}")
        ext_id = fields[col[body.id_column]] if body.id_column else str(n - 1)
        if not ext_id.strip():
            raise _row_error(n, line, "empty id")
        if ext_id in seen:
            raise _row_error(n, line, f"duplicate id {ext_id!r}")
        seen.add(ext_id)
        try:
            label = parse_label(fields[col[body.label_column]], allowed) if body.label_column else None
            when = normalize_event_time(fields[col[body.time_column]]) if body.time_column else None
        except ValueError as exc:
            raise _row_error(n, line, str(exc)) from exc
        group = fields[col[body.group_column]] if body.group_column else None
        if group is not None and not group.strip():
            raise _row_error(n, line, "empty group")
        for f in features:
            if numeric[f] and not _finite_number(fields[col[f]]):
                if explicit_features:
                    raise _row_error(n, line, f"feature {f!r} is not a finite number")
                numeric[f] = False
        payload = json.dumps(dict(zip(header, fields)), ensure_ascii=False, sort_keys=True)
        yield n, (ext_id, when, label, group, payload), numeric


@router.get("", response_model=list[DatasetOut])
def list_all(conn: sqlite3.Connection = Depends(get_conn)) -> list[dict[str, Any]]:
    return repo.list_datasets(conn)


@router.get("/available", response_model=list[AvailableFile])
def available(
    settings: Settings = Depends(get_settings), _user: dict[str, Any] = Depends(current_user)
) -> list[AvailableFile]:
    if not settings.data_dir.is_dir():
        return []
    return [
        AvailableFile(file_name=p.name, size_bytes=p.stat().st_size)
        for p in sorted(settings.data_dir.glob("*.csv"))
        if p.is_file()
    ]


def _ingest_stream(
    conn: sqlite3.Connection, path: Path, body: IngestIn, settings: Settings, user_id: int,
    digest: str, mtime: str,
) -> dict[str, Any]:
    """Stream the CSV into `records` in batches; the dataset stays 'loading' until done."""
    dataset_id: int | None = None
    done = False
    try:
        with path.open("rb") as fh:
            hashing = _HashingReader(fh, settings.max_csv_bytes)
            text = io.TextIOWrapper(io.BufferedReader(hashing), encoding="utf-8-sig", newline="")
            reader = csv.reader(text)
            header = next(reader, None)
            if header is None:
                raise HTTPException(HTTP_422, "CSV is empty")
            candidates = _validate_header(header, body)
            roles: dict[str, Any] = {
                **requested_mapping(body), "features": [], "features_inferred": body.feature_columns is None,
            }
            dataset_id = repo.begin_dataset(
                conn, name=body.name, source_url=body.source_url, license_=body.license,
                sha256=digest, fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                file_name=body.file_name, description=body.description, created_by=user_id,
                column_roles=roles, file_mtime=mtime,
            )
            batch: list[repo.RecordRow] = []
            rows = 0
            numeric: dict[str, bool] = {}
            for rows, record, numeric in stream_rows(
                reader, header, body, candidates, settings.max_csv_rows, body.feature_columns is not None
            ):
                batch.append(record)
                if len(batch) >= BATCH_ROWS:
                    repo.insert_records(conn, dataset_id, batch)
                    batch = []
            if rows == 0:
                raise HTTPException(HTTP_422, "CSV has a header but no data rows")
            if batch:
                repo.insert_records(conn, dataset_id, batch)
            if hashing.sha.hexdigest() != digest:
                raise HTTPException(status.HTTP_409_CONFLICT, "file changed while it was being ingested")
        roles["features"] = [f for f in candidates if numeric.get(f, False)]
        with db.transaction(conn):
            repo.finalize_dataset(conn, dataset_id, rows, roles)
            audit.append(
                conn, settings.audit_key, f"user:{user_id}", "dataset.ingest",
                {"name": body.name, "sha256": digest, "rows": rows,
                 "declared": {"source_url": body.source_url, "license": body.license}},
            )
        done = True
    except UnicodeDecodeError as exc:
        raise HTTPException(HTTP_422, "CSV must be UTF-8") from exc
    except csv.Error as exc:
        raise HTTPException(HTTP_422, f"malformed CSV: {exc}") from exc
    except CsvTooLargeError as exc:
        raise HTTPException(HTTP_413, "file too large") from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "dataset conflict or ingest already in progress") from exc
    finally:
        if not done and dataset_id is not None:
            repo.delete_dataset(conn, dataset_id)
    found = repo.get_dataset_by_name(conn, body.name)
    assert found is not None
    logger.info("ingested %s rows=%d sha256=%s", body.name, found["row_count"], digest)
    return found


@router.post("/ingest", response_model=IngestOut)
def ingest(
    body: IngestIn,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
    user: dict[str, Any] = Depends(current_user),
) -> IngestOut:
    """Ingest a CSV from the data cache.

    Roles (id/time/label/group) and the feature list are stored with the dataset, so the
    label can never be picked up as a feature later. `source_url` and `license` are taken
    as declared; only `sha256` is computed by the server. Re-posting the same name with the
    same bytes and mapping is a no-op; a different mapping or content is a 409.
    """
    path = resolve_csv(settings.data_dir, body.file_name)
    st = path.stat()
    if st.st_size > settings.max_csv_bytes:
        raise HTTPException(HTTP_413, "file too large")
    try:
        digest = sha256_of(path, settings.max_csv_bytes)
    except CsvTooLargeError as exc:
        raise HTTPException(HTTP_413, "file too large") from exc
    existing = repo.get_dataset_by_name(conn, body.name)
    if existing is not None:
        if existing["status"] != "complete":
            raise HTTPException(status.HTTP_409_CONFLICT, "dataset conflict or ingest already in progress")
        if existing["sha256"] != digest:
            raise HTTPException(status.HTTP_409_CONFLICT, "dataset name exists with different content")
        if not _mapping_matches(existing["column_roles"], body):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "dataset exists with a different column mapping; use a new dataset name to re-ingest",
            )
        return IngestOut(created=False, dataset=DatasetOut(**existing))
    mtime = datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds")
    ds = _ingest_stream(conn, path, body, settings, int(user["id"]), digest, mtime)
    return IngestOut(created=True, dataset=DatasetOut(**ds))
