"""All AEDs (전국 자동심장충격기, data.go.kr 15000652 API cache) as a slim table, and open-now by day/hour.

Source: `<data dir>/extra/ems/api/aed/*.json.gz` (64 pages, ~63.5k records). Only facility fields are kept:
manager names and phone numbers in the source are never loaded. Hours: `<day>SttTme`/`<day>EndTme` per
mon..sun and hol ("HHMM"; "0000"-"2400" = 24 h; missing = unknown). The nth-Sunday closure flags
(sunFrtYon...) are not modelled.
"""

from __future__ import annotations

import gzip
import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from ..heat.common import nospace
from . import constants as C
from .diskcache import cached

logger = logging.getLogger("actions.aed")
AED_DIR = "extra/ems/api/aed"
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun", "hol")
DAY_PARAM = {"weekday": "mon", "sat": "sat", "sun": "sun", "holiday": "hol"}  # weekday = Monday hours (representative)
KEEP = ("serialSeq", "sido", "gugun", "org", "buildPlace", "buildAddress", "wgs84Lat", "wgs84Lon")
VERSION = 1
MINUTES_PER_DAY = 1440


def _hhmm(v: Any) -> int | None:
    s = str(v).strip() if v is not None else ""
    if not s or s == "nan":
        return None
    s = s.zfill(4)
    if len(s) != 4 or not s.isdigit():
        return None
    h, m = int(s[:2]), int(s[2:])
    if (h, m) == (24, 0):
        return MINUTES_PER_DAY
    return h * 60 + m if h < 24 and m < 60 else None


def _items(obj: Any) -> list[dict[str, Any]]:
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        return obj
    if isinstance(obj, dict):
        for v in obj.values():
            found = _items(v)
            if found:
                return found
    return []


def _build(files: list[Path]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for f in files:
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            for it in _items(json.load(fh)):
                row: dict[str, Any] = {k: it.get(k) for k in KEEP}
                for d in DAYS:
                    row[f"{d}_s"], row[f"{d}_e"] = _hhmm(it.get(f"{d}SttTme")), _hhmm(it.get(f"{d}EndTme"))
                rows.append(row)
    df = pd.DataFrame(rows)
    df["lat"] = pd.to_numeric(df["wgs84Lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["wgs84Lon"], errors="coerce")
    df["gugun_key"] = df["gugun"].fillna("").map(nospace)
    df["sido"] = df["sido"].fillna("")
    df = df.drop(columns=["wgs84Lat", "wgs84Lon"])
    logger.info("AED table: %d records from %d files", len(df), len(files))
    return df


def load_aeds(data_dir: Path) -> pd.DataFrame:
    """All AED records (cached on disk).

    Raises:
        FileNotFoundError: the API cache directory has no files.
    """
    files = sorted((data_dir / AED_DIR).glob("*.json.gz"))
    if not files:
        raise FileNotFoundError(data_dir / AED_DIR)
    return cached("aed_points", VERSION, files, lambda: _build(files))


def in_region(df: pd.DataFrame, sido: str, name: str) -> pd.DataFrame:
    """Records whose sido matches and whose 구/군 equals the region name (or is contained in it, e.g. 수원시장안구)."""
    key = nospace(name)
    mask = (df["sido"] == sido) & ((df["gugun_key"] == key) | ((df["gugun_key"] != "") & df["gugun_key"].map(lambda g: g in key)))
    return df[mask]


def open_state(row: Any, day: str, hour: int) -> bool | None:
    """Open at `hour` on `day` (mon..sun|hol) per the posted hours; None when that day's hours are missing."""
    s, e = row[f"{day}_s"], row[f"{day}_e"]
    if s is None or e is None or pd.isna(s) or pd.isna(e):
        return None
    minute = hour * 60
    start, end = int(s), int(e)
    if end <= start:  # past midnight
        return minute >= start or minute < end
    return start <= minute < end


def hours_text(row: Any, day: str) -> str | None:
    s, e = row[f"{day}_s"], row[f"{day}_e"]
    if s is None or e is None or pd.isna(s) or pd.isna(e):
        return None

    def fmt(m: float) -> str:
        m = int(m)
        return f"{m // 60:02d}{m % 60:02d}"

    return f"{fmt(s)}-{fmt(e)}"


def table(df: pd.DataFrame, day: str, hour: int, limit: int = C.FIELD_LIST_LIMIT) -> dict[str, Any]:
    """Counts and the first `limit` records (open first) for one region at (day, hour)."""
    states = [open_state(r, day, hour) for r in df.to_dict("records")]
    items = []
    for r, st in zip(df.to_dict("records"), states, strict=True):
        items.append({"serial": r["serialSeq"], "org": r["org"], "place": r["buildPlace"], "address": r["buildAddress"],
                      "lat": None if pd.isna(r["lat"]) else float(r["lat"]), "lon": None if pd.isna(r["lon"]) else float(r["lon"]),
                      "hours": hours_text(r, day), "open_now": st})
    items.sort(key=lambda x: (x["open_now"] is not True, x["open_now"] is None, x["org"] or ""))
    return {"total": len(items), "open": sum(1 for s in states if s is True), "closed": sum(1 for s in states if s is False),
            "unknown_hours": sum(1 for s in states if s is None), "items": items[:limit]}


__all__ = ["DAY_PARAM", "DAYS", "hours_text", "in_region", "load_aeds", "open_state", "table"]
