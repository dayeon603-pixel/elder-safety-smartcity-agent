"""Cooling shelters: a descriptive layer, never an input to the prediction.

Coverage is partial by construction (충남, 부산 plus the per-city files below); a region with no
shelter rows must be reported as "no data", not "zero shelters" (the API does this). Files with no
coordinates are skipped (SKIPPED_NO_COORDS), never geocoded online. Rows with (0,0) or otherwise
out-of-range coordinates, rows flagged not in use / deleted, and rows already loaded from an
earlier source (same name, coordinates rounded to DEDUPE_DECIMALS) are dropped and counted.
Latitude/longitude columns that are swapped in the file (Daejeon Seo-gu) are detected per row.

night_open / weekend_open rules (NULL whenever the source does not say):
  1. An explicit Y/N column wins (야간연장운영여부, 야간운영여부, 주말운영여부).
  2. Else derive from opening-time columns with `hours_flags`:
     - an interval is "unknown" if either time is blank/unparseable or it is 00:00-00:00;
     - 24 h intervals (00:00-24:00, 00:00-23:59) count as open at night;
     - night_open = 1 if any known interval (weekday or weekend) is open at/after NIGHT_START
       (21:00) or crosses midnight; 0 if known intervals exist and none does; NULL if none known;
     - weekend_open = 1 if the weekend interval is known; NULL otherwise (a blank weekend column
       may mean "closed" or "not recorded", so it is never turned into 0).

Adding a source (e.g. a future 재난안전데이터 공유플랫폼 export saved as CSV) = append a ShelterSource.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from .. import db
from .common import (
    SHELTER_BUSAN, SHELTER_CHUNGNAM, SHELTER_EXTRA_DIR, HeatPaths, IngestError, detect_encoding, nospace,
    register_dataset, require_file, resolve_paths,
)

logger = logging.getLogger("heat.shelters")

LAT_RANGE = (33.0, 39.0)  # mirrors the shelter table CHECKs
LON_RANGE = (124.0, 132.0)
LICENSE = "data.go.kr public data (terms not machine-verified)"
YES, NO = "Y", "N"
DEDUPE_DECIMALS = 4  # ~11 m: same name within this distance is one shelter
NIGHT_START = 21 * 60  # minutes after midnight; "open at night" = open at/after 21:00
DAY_MINUTES = 24 * 60
_HHMM = re.compile(r"^(\d{1,2}):(\d{2})(?::\d{2})?$")


@dataclass(frozen=True, slots=True)
class ShelterSource:
    """Column mapping of one shelter file."""

    dataset: str
    rel_path: str
    url: str
    description: str
    name_col: str
    lat_col: str
    lon_col: str
    capacity_col: str | None
    night_col: str | None
    weekend_col: str | None
    active_col: str | None
    region_of: Callable[[pd.DataFrame, set[str], dict[tuple[str, str], str]], pd.Series]
    deleted_col: str | None = None  # "Y" = deleted
    weekday_hours: tuple[str, str] | None = None  # (start, end) columns
    weekend_hours: tuple[str, str] | None = None


def _region_by_sigungu_code(
    df: pd.DataFrame, codes: set[str], _names: dict[tuple[str, str], str]
) -> pd.Series:
    code = df["시군구코드"].astype(str).str.strip()
    return code.where(code.isin(codes), None)


def _region_by_address_col(
    col: str, fallback: tuple[str, str] | None = None
) -> Callable[[pd.DataFrame, set[str], dict[tuple[str, str], str]], pd.Series]:
    """Region resolver from "<시도> <시군구> ..." in column `col` (first two tokens).

    `fallback` = (시도, 시군구) for a single-district file: used only where the address is blank.
    """

    def resolve(
        df: pd.DataFrame, _codes: set[str], names: dict[tuple[str, str], str]
    ) -> pd.Series:
        def one(addr: object) -> str | None:
            parts = str(addr).split() if isinstance(addr, str) else []
            if len(parts) >= 2:
                return names.get((parts[0], nospace(parts[1])))
            return names.get((fallback[0], nospace(fallback[1]))) if fallback and not parts else None

        return df[col].map(one)

    return resolve


_region_by_address = _region_by_address_col("상세주소")


def parse_minutes(value: object) -> int | None:
    """"HH:MM" or "HH:MM:SS" -> minutes after midnight (24:00 allowed); None if blank/invalid."""
    if not isinstance(value, str):
        return None
    m = _HHMM.match(value.strip())
    if m is None:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    total = h * 60 + mi
    return total if mi < 60 and total <= DAY_MINUTES else None


def _interval(start: object, end: object) -> tuple[int, int] | None:
    """A known opening interval, or None (blank, invalid, or the ambiguous 00:00-00:00)."""
    s, e = parse_minutes(start), parse_minutes(end)
    if s is None or e is None or s == e:
        return None
    return s, e


def _covers_night(iv: tuple[int, int]) -> bool:
    s, e = iv
    return e < s or e >= NIGHT_START or (s == 0 and e >= DAY_MINUTES - 1)


def hours_flags(
    weekday: tuple[object, object] | None, weekend: tuple[object, object] | None
) -> tuple[int | None, int | None]:
    """Derive (night_open, weekend_open) from (start, end) time pairs; rules in the module docstring."""
    wd = _interval(*weekday) if weekday else None
    we = _interval(*weekend) if weekend else None
    known = [iv for iv in (wd, we) if iv is not None]
    night: int | None = None
    if known:
        night = 1 if any(_covers_night(iv) for iv in known) else 0
    return night, (1 if we is not None else None)


SOURCES: tuple[ShelterSource, ...] = (
    ShelterSource(
        "heat_shelters_chungnam", SHELTER_CHUNGNAM, "https://www.data.go.kr/data/15118638/fileData.do",
        "충청남도 무더위쉼터 2024", "쉼터명칭", "위도", "경도", "이용가능인원수", "야간연장운영여부",
        "주말운영여부", "사용여부", _region_by_sigungu_code,
    ),
    ShelterSource(
        "heat_shelters_busan", SHELTER_BUSAN, "https://www.data.go.kr/data/15152994/fileData.do",
        "부산광역시 무더위쉼터", "쉼터명", "y좌표", "x좌표", None, None, None, None, _region_by_address,
    ),
    ShelterSource(
        "heat_shelters_daejeon_seogu", f"{SHELTER_EXTRA_DIR}/kr_15104075_daejeon_seogu_cooling_shelters.csv",
        "https://www.data.go.kr/data/15104075/fileData.do", "대전광역시 서구 무더위쉼터",
        "쉼터명", "위도", "경도", "이용가능인원", None, None, None, _region_by_address_col("도로명주소"),
    ),
    ShelterSource(
        "heat_shelters_gyeongsan", f"{SHELTER_EXTRA_DIR}/kr_15123891_gyeongsan_cooling_shelters.csv",
        "https://www.data.go.kr/data/15123891/fileData.do", "경상북도 경산시 무더위쉼터",
        "쉼터명칭", "위도", "경도", "이용가능인원(명)", None, None, None, _region_by_address_col("도로명주소"),
        weekday_hours=("평일시작시간", "평일종료시간"), weekend_hours=("주말시작시간", "주말종료시간"),
    ),
    ShelterSource(
        "heat_shelters_daegu_suseong", f"{SHELTER_EXTRA_DIR}/kr_15148351_daegu_suseong_cooling_shelters.csv",
        "https://www.data.go.kr/data/15148351/fileData.do", "대구광역시 수성구 무더위쉼터",
        "무더위쉼터명", "위도", "경도", "수용인원", "야간운영여부", "주말운영여부", "사용여부",
        _region_by_address_col("주소", ("대구광역시", "수성구")), deleted_col="삭제여부",
    ),
    ShelterSource(
        "heat_shelters_seoul_dongjak", f"{SHELTER_EXTRA_DIR}/kr_15148744_seoul_dongjak_cooling_shelters.csv",
        "https://www.data.go.kr/data/15148744/fileData.do", "서울특별시 동작구 무더위쉼터",
        "쉼터명", "위도", "경도", "이용가능인원수", "야간연장운영여부", "주말운영여부", None,
        _region_by_address_col("소재지도로명주소"),
    ),
    ShelterSource(
        "heat_shelters_seoul_junggu", f"{SHELTER_EXTRA_DIR}/kr_15148999_seoul_junggu_cooling_shelters.csv",
        "https://www.data.go.kr/data/15148999/fileData.do", "서울특별시 중구 무더위쉼터",
        "쉼터명", "위도", "경도", "이용가능 인원수", None, None, None, _region_by_address_col("소재지 주소"),
    ),
    ShelterSource(
        "heat_shelters_jeungpyeong", f"{SHELTER_EXTRA_DIR}/kr_15152511_jeungpyeong_cooling_shelters.csv",
        "https://www.data.go.kr/data/15152511/fileData.do", "충청북도 증평군 무더위쉼터",
        "시설명", "위도", "경도", None, None, None, None, _region_by_address_col("주소"),
    ),
    ShelterSource(
        "heat_shelters_daejeon_yuseong", f"{SHELTER_EXTRA_DIR}/kr_15154062_daejeon_yuseong_cooling_shelters.csv",
        "https://www.data.go.kr/data/15154062/fileData.do", "대전광역시 유성구 무더위쉼터",
        "쉼터명", "위도", "경도", None, None, None, None, _region_by_address_col("도로명주소"),
    ),
)

# Local files with no coordinate column: not loaded (no online geocoding). file -> reason.
SKIPPED_NO_COORDS: dict[str, str] = {
    "kr_15093592_daegu_seogu_cooling_shelters.csv": "address only",
    "kr_15125647_seongnam_cooling_shelters.csv": "address only (hours 00:00-00:00)",
    "kr_15126558_daejeon_junggu_cooling_shelters.csv": "address only",
    "kr_15131091_busan_bukgu_indoor_cooling_shelters.csv": "address only",
    "kr_15134860_busan_gijang_cooling_shelters.csv": "address only",
    "kr_15143580_yeongcheon_cooling_shelters.csv": "address only",
    "kr_15155904_jeonju_cooling_shelters.csv": "address only",
    "kr_15157635_incheon_yeonsu_cooling_shelters.csv": "address only",
}


def _flag(series: pd.Series) -> pd.Series:
    mapped = series.map({YES: 1, NO: 0})
    return mapped.astype("object").where(mapped.notna(), None)


def clean_shelters(
    df: pd.DataFrame, src: ShelterSource, codes: set[str], names: dict[tuple[str, str], str]
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Apply one source's mapping; return (rows ready for insert, drop counters).

    Raises:
        IngestError: if a mapped column is missing from the file.
    """
    cols = [c for c in (src.name_col, src.lat_col, src.lon_col, src.capacity_col, src.night_col,
                        src.weekend_col, src.active_col, src.deleted_col,
                        *(src.weekday_hours or ()), *(src.weekend_hours or ())) if c]
    absent = [c for c in cols if c not in df.columns]
    if absent:
        raise IngestError(f"{src.dataset}: missing columns {absent}")
    out = pd.DataFrame({
        "name": df[src.name_col].astype(str).str.strip(),
        "lat": pd.to_numeric(df[src.lat_col], errors="coerce"),
        "lon": pd.to_numeric(df[src.lon_col], errors="coerce"),
    })
    swap = (
        ~out["lat"].between(*LAT_RANGE) & ~out["lon"].between(*LON_RANGE)
        & out["lat"].between(*LON_RANGE) & out["lon"].between(*LAT_RANGE)
    )
    out.loc[swap, ["lat", "lon"]] = out.loc[swap, ["lon", "lat"]].to_numpy()
    out["region_code"] = src.region_of(df, codes, names)
    cap = pd.to_numeric(df[src.capacity_col], errors="coerce") if src.capacity_col else None
    out["capacity"] = cap.where(cap >= 0).round().astype("Int64").astype("object") if cap is not None else None
    night: pd.Series | None = _flag(df[src.night_col]) if src.night_col else None
    weekend: pd.Series | None = _flag(df[src.weekend_col]) if src.weekend_col else None
    if src.weekday_hours or src.weekend_hours:
        derived = [
            hours_flags(
                (df[src.weekday_hours[0]].iat[i], df[src.weekday_hours[1]].iat[i]) if src.weekday_hours else None,
                (df[src.weekend_hours[0]].iat[i], df[src.weekend_hours[1]].iat[i]) if src.weekend_hours else None,
            )
            for i in range(len(df))
        ]
        d_night = pd.Series([d[0] for d in derived], index=df.index, dtype="object")
        d_weekend = pd.Series([d[1] for d in derived], index=df.index, dtype="object")
        night = d_night if night is None else night.where(night.notna(), d_night)
        weekend = d_weekend if weekend is None else weekend.where(weekend.notna(), d_weekend)
    out["night_open"] = night
    out["weekend_open"] = weekend
    stats = {"rows": len(out), "swapped": int(swap.sum())}
    keep_all = pd.Series(True, index=out.index)
    if src.active_col:
        keep_all &= df[src.active_col].astype(str).str.strip() != NO
    if src.deleted_col:
        keep_all &= df[src.deleted_col].astype(str).str.strip() != YES
    if src.active_col or src.deleted_col:
        stats["inactive"] = int((~keep_all).sum())
        out = out[keep_all]
    valid = (
        out["lat"].between(*LAT_RANGE) & out["lon"].between(*LON_RANGE) & out["name"].ne("")
    )
    stats["bad_coords"] = int((~valid).sum())
    out = out[valid]
    stats["no_region"] = int(out["region_code"].isna().sum())
    before = len(out)
    out = out.drop_duplicates(["name", "lat", "lon"])
    stats["duplicates"] = before - len(out)
    stats["kept"] = len(out)
    return out.reset_index(drop=True), stats


def _opt(v: object) -> object:
    """NaN/NA -> None and numpy scalars -> Python scalars (sqlite3 binds neither numpy types nor NA)."""
    if v is None or v is pd.NA or (isinstance(v, float) and v != v):
        return None
    item = getattr(v, "item", None)
    return item() if callable(item) else v


def ingest_shelters(conn: sqlite3.Connection, paths: HeatPaths) -> dict[str, dict[str, int]]:
    """Replace shelter rows of each configured source; idempotent. Requires regions first."""
    regions = conn.execute("SELECT code, sido, name FROM region").fetchall()
    if not regions:
        raise IngestError("region table is empty: run ingest_regions first")
    codes = {r["code"] for r in regions}
    names = {(r["sido"], nospace(r["name"])): r["code"] for r in regions}
    report: dict[str, dict[str, int]] = {}
    seen: set[tuple[str, float, float]] = set()
    for fname, reason in SKIPPED_NO_COORDS.items():
        logger.info("skipped %s: %s", fname, reason)
    with db.transaction(conn):
        for src in SOURCES:
            path = require_file(paths.data_dir, src.rel_path)
            df = pd.read_csv(path, encoding=detect_encoding(path))
            rows, stats = clean_shelters(df, src, codes, names)
            keys = [
                (r.name, round(float(r.lat), DEDUPE_DECIMALS), round(float(r.lon), DEDUPE_DECIMALS))
                for r in rows.itertuples()
            ]
            fresh = [k not in seen for k in keys]
            stats["cross_source_duplicates"] = len(keys) - sum(fresh)
            stats["kept"] = sum(fresh)
            rows = rows[fresh]
            seen.update(k for k, f in zip(keys, fresh) if f)
            ds_id = register_dataset(
                conn, name=src.dataset, path=path, source_url=src.url, license_=LICENSE,
                description=src.description, row_count=len(df),
                roles={"kind": "heat", "use": "shelter (descriptive layer)"},
            )
            conn.execute("DELETE FROM shelter WHERE dataset_id = ?", (ds_id,))
            conn.executemany(
                "INSERT INTO shelter (region_code, name, lat, lon, capacity, night_open, weekend_open, "
                "dataset_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (_opt(r.region_code), r.name, float(r.lat), float(r.lon), _opt(r.capacity),
                     _opt(r.night_open), _opt(r.weekend_open), ds_id)
                    for r in rows.itertuples()
                ],
            )
            report[src.dataset] = stats
            logger.info("%s: %s", src.dataset, stats)
    return report


def main() -> int:
    """CLI: python -m app.heat.ingest_shelters."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    paths = resolve_paths()
    conn = db.connect(paths.db_path)
    try:
        db.migrate(conn)
        ingest_shelters(conn, paths)
    except IngestError as exc:
        logger.error("%s", exc)
        return 2
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
