"""Heat-illness cases -> dense (region x season-day) counts, with unmapped names counted.

Every region x every season day (05-20..09-25) of every study year gets a row, zeros included, so
the denominator of any rate is guaranteed by the database. Labels are mapped through
`region_alias`; cases whose label has no alias are counted per (sido, name) in
`heat_case_unmapped` instead of being dropped silently. Reconciliation (raw = mapped-in-season +
mapped-out-of-season + unmapped) is asserted.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from dataclasses import dataclass

import pandas as pd

from .. import db
from .common import (
    HEAT_CSV, SEASON_END, SEASON_START, YEARS, HeatPaths, IngestError, detect_encoding,
    nospace, register_dataset, require_file, resolve_paths, season_dates,
)

logger = logging.getLogger("heat.cases")

SOURCE_URL = "https://www.data.go.kr/data/15149889/fileData.do"
COL_DATE, COL_SIDO, COL_SGG, COL_PLACE = "발생일자", "발생시도", "발생시군구", "실내외구분"
REQUIRED_COLS = (COL_DATE, COL_SIDO, COL_SGG, COL_PLACE)
INDOOR, OUTDOOR = "실내", "실외"
MIN_MAPPED_SHARE = 0.98  # design risk #2: below this the analysis must say so


@dataclass(frozen=True, slots=True)
class CaseSummary:
    """Counts that reconcile: raw == mapped_in_season + mapped_out_of_season + unmapped."""

    raw: int
    mapped_in_season: int
    mapped_out_of_season: int
    unmapped: int
    dense_rows: int
    unmapped_names: list[tuple[str, str, int]]


def _in_season_mask(dates: pd.Series) -> pd.Series:
    md = dates.dt.month * 100 + dates.dt.day
    return (md >= SEASON_START[0] * 100 + SEASON_START[1]) & (md <= SEASON_END[0] * 100 + SEASON_END[1])


def aggregate_cases(
    raw: pd.DataFrame, alias: pd.DataFrame, region_codes: list[str], years: tuple[int, ...] = YEARS
) -> tuple[pd.DataFrame, pd.DataFrame, CaseSummary]:
    """Pure transform: raw case rows -> (dense daily frame, unmapped frame, summary).

    Args:
        raw: columns 발생일자, 발생시도, 발생시군구, 실내외구분.
        alias: columns sido_raw, name_raw, region_code.
        region_codes: every region code (the dense grid is region_codes x season days x years).
        years: study years; rows from other years are ignored (labels have no 시군구 before 2019).

    Raises:
        IngestError: on missing columns or if the counts do not reconcile.
    """
    missing = [c for c in REQUIRED_COLS if c not in raw.columns]
    if missing:
        raise IngestError(f"heat file lacks columns {missing}")
    h = raw[list(REQUIRED_COLS)].copy()
    h["date"] = pd.to_datetime(h[COL_DATE], errors="coerce")
    bad_dates = int(h["date"].isna().sum())
    if bad_dates:
        logger.warning("%d rows with unparseable date ignored", bad_dates)
    h = h.dropna(subset=["date"])
    h = h[h["date"].dt.year.isin(years)].copy()
    h["sido_raw"] = h[COL_SIDO].fillna("").astype(str).str.strip()
    h["name_raw"] = h[COL_SGG].fillna("").astype(str).map(nospace)
    h = h.merge(alias[["sido_raw", "name_raw", "region_code"]], on=["sido_raw", "name_raw"], how="left")

    unm = h[h["region_code"].isna()]
    unmapped = unm.groupby(["sido_raw", "name_raw"]).size().rename("n_cases").reset_index()
    mapped = h[h["region_code"].notna()].copy()
    season = _in_season_mask(mapped["date"])
    inside = mapped[season].copy()
    inside["indoor"] = (inside[COL_PLACE] == INDOOR).astype(int)
    inside["outdoor"] = (inside[COL_PLACE] == OUTDOOR).astype(int)
    inside["n"] = 1
    inside["date_s"] = inside["date"].dt.strftime("%Y-%m-%d")
    counts = inside.groupby(["region_code", "date_s"]).agg(
        n_cases=("n", "sum"), n_indoor=("indoor", "sum"), n_outdoor=("outdoor", "sum")
    )

    grid = pd.MultiIndex.from_product(
        [sorted(region_codes), [d for y in years for d in season_dates(y)]],
        names=["region_code", "date_s"],
    )
    dense = counts.reindex(grid, fill_value=0).reset_index().rename(columns={"date_s": "date"})
    for col in ("n_cases", "n_indoor", "n_outdoor"):
        dense[col] = dense[col].astype(int)

    summary = CaseSummary(
        raw=len(h), mapped_in_season=len(inside), mapped_out_of_season=int((~season).sum()),
        unmapped=len(unm), dense_rows=len(dense),
        unmapped_names=[(r.sido_raw, r.name_raw, int(r.n_cases)) for r in unmapped.itertuples()],
    )
    if int(dense["n_cases"].sum()) != summary.mapped_in_season:
        raise IngestError("dense case sum != mapped in-season cases")
    if summary.raw != summary.mapped_in_season + summary.mapped_out_of_season + summary.unmapped:
        raise IngestError("case counts do not reconcile")
    return dense, unmapped, summary


def ingest_cases(conn: sqlite3.Connection, paths: HeatPaths) -> CaseSummary:
    """Replace `heat_case_daily` and `heat_case_unmapped` from the source CSV; idempotent.

    Raises:
        IngestError: if regions/aliases are missing or the file is malformed.
    """
    path = require_file(paths.data_dir, HEAT_CSV)
    codes = [r["code"] for r in conn.execute("SELECT code FROM region ORDER BY code")]
    alias = pd.DataFrame(
        [tuple(r) for r in conn.execute("SELECT sido_raw, name_raw, region_code FROM region_alias")],
        columns=["sido_raw", "name_raw", "region_code"],
    )
    if not codes or alias.empty:
        raise IngestError("regions/aliases are empty: run ingest_regions first")
    raw = pd.read_csv(path, encoding=detect_encoding(path))
    dense, unmapped, s = aggregate_cases(raw, alias, codes)
    share = (s.mapped_in_season + s.mapped_out_of_season) / s.raw if s.raw else 0.0
    if share < MIN_MAPPED_SHARE:
        logger.error("only %.2f%% of cases mapped (< %.0f%%): the analysis must disclose this",
                     100 * share, 100 * MIN_MAPPED_SHARE)
    if s.mapped_out_of_season:
        logger.info("%d mapped cases fall outside the season window (not stored in the daily table)",
                    s.mapped_out_of_season)
    last = pd.to_datetime(raw[COL_DATE], errors="coerce").max()
    logger.warning("latest report date in file: %s; zero rows after it may be unreported, not zero",
                   last.date() if pd.notna(last) else "n/a")
    with db.transaction(conn):
        register_dataset(
            conn, name="heat_illness_cases", path=path, source_url=SOURCE_URL,
            license_="data.go.kr public data (terms not machine-verified)",
            description="Heat-illness reports (온열질환 신고현황) 2011-2025; 시군구 populated from 2019",
            row_count=len(raw), roles={"kind": "heat", "date": COL_DATE, "place": COL_PLACE},
        )
        conn.execute("DELETE FROM heat_case_daily")
        conn.executemany(
            "INSERT INTO heat_case_daily (region_code, date, n_cases, n_indoor, n_outdoor) "
            "VALUES (?, ?, ?, ?, ?)",
            list(zip(dense["region_code"], dense["date"], dense["n_cases"].tolist(),
                     dense["n_indoor"].tolist(), dense["n_outdoor"].tolist(), strict=True)),
        )
        conn.execute("DELETE FROM heat_case_unmapped")
        conn.executemany(
            "INSERT INTO heat_case_unmapped (sido_raw, name_raw, n_cases) VALUES (?, ?, ?)",
            s.unmapped_names,
        )
    logger.info("raw=%d mapped_in_season=%d out_of_season=%d unmapped=%d dense_rows=%d",
                s.raw, s.mapped_in_season, s.mapped_out_of_season, s.unmapped, s.dense_rows)
    return s


def main() -> int:
    """CLI: python -m app.heat.ingest_cases."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    paths = resolve_paths()
    conn = db.connect(paths.db_path)
    try:
        db.migrate(conn)
        ingest_cases(conn, paths)
    except IngestError as exc:
        logger.error("%s", exc)
        return 2
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
