"""Latest schema version, derived from db/migrations so tests don't hardcode it."""
from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).resolve().parents[1] / "db" / "migrations"
ALL_VERSIONS: list[int] = [1] + sorted(int(p.name[:4]) for p in _DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))
LATEST: int = ALL_VERSIONS[-1]
