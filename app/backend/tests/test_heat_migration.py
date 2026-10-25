"""0003_heat.sql: constraints that must hold at the database level."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app import db
from tests._migrations import ALL_VERSIONS, LATEST  # noqa: F401

MIGRATION = db.DB_DIR / "migrations" / "0003_heat.sql"
TABLES = {
    "region", "region_alias", "weather_station", "weather_daily", "region_station",
    "heat_case_daily", "heat_case_unmapped", "shelter", "alert_run", "alert", "eval_metric",
}
SHA = "d" * 64


def _region(conn: sqlite3.Connection, code: str = "11110") -> None:
    conn.execute(
        "INSERT INTO region VALUES (?, '서울특별시', ?, 37.5, 127.0, 100, 10, '2026-09-30')",
        (code, f"구{code}"),
    )


def _run(conn: sqlite3.Connection, method: str = "B1", train_max: int = 2022, year: int = 2023) -> int:
    cur = conn.execute(
        "INSERT INTO alert_run (method, variant, train_year_min, train_year_max, test_year, budget_kind, "
        "seed, dataset_sha, git_hash, created_at) VALUES (?, 'F', 2019, ?, ?, 'per_day_b1', 42, ?, 'g', 't')",
        (method, train_max, year, SHA),
    )
    return int(cur.lastrowid or 0)


def test_migration_applies_once_and_has_no_pragma(conn: sqlite3.Connection) -> None:
    assert db.migrate(conn) == LATEST
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLES <= names
    statements = db.split_statements(MIGRATION.read_text(encoding="utf-8"))
    assert not [s for s in statements if db._PRAGMA_START.match(s)]
    assert conn.execute("SELECT checksum FROM schema_version WHERE version=3").fetchone() is not None


def test_alert_rows_are_immutable(conn: sqlite3.Connection) -> None:
    _region(conn)
    run = _run(conn)
    conn.execute("INSERT INTO alert VALUES (?, '2023-07-01', '11110', 1.5, 0)", (run,))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE alert SET alerted = 1 WHERE run_id = ?", (run,))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE alert SET score = 9 WHERE run_id = ?", (run,))
    assert conn.execute("SELECT alerted FROM alert").fetchone()["alerted"] == 0


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO region VALUES ('1111', 's', 'n', 37, 127, 10, 1, 'x')",  # 4-digit code
        "INSERT INTO region VALUES ('1111a', 's', 'n', 37, 127, 10, 1, 'x')",  # non-digit
        "INSERT INTO region VALUES ('11110', 's', 'n', 40, 127, 10, 1, 'x')",  # lat out of range
        "INSERT INTO region VALUES ('11110', 's', 'n', 37, 127, 0, 0, 'x')",  # pop must be > 0
        "INSERT INTO region VALUES ('11110', 's', 'n', 37, 127, 10, 11, 'x')",  # pop65 > pop
    ],
)
def test_region_checks(conn: sqlite3.Connection, sql: str) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql)


def test_case_weather_and_shelter_checks(conn: sqlite3.Connection) -> None:
    _region(conn)
    with pytest.raises(sqlite3.IntegrityError):  # indoor + outdoor > cases
        conn.execute("INSERT INTO heat_case_daily VALUES ('11110', '2023-07-01', 1, 1, 1)")
    with pytest.raises(sqlite3.IntegrityError):  # month outside 05..09
        conn.execute("INSERT INTO heat_case_daily VALUES ('11110', '2023-10-01', 0, 0, 0)")
    with pytest.raises(sqlite3.IntegrityError):  # FK to region
        conn.execute("INSERT INTO heat_case_daily VALUES ('99999', '2023-07-01', 0, 0, 0)")
    conn.execute(
        "INSERT INTO datasets (name, source_url, license, sha256, fetched_at, row_count) "
        "VALUES ('d', 'https://x.org', 'l', ?, 't', 0)", (SHA,))
    conn.execute("INSERT INTO weather_station (id, lat, lon, dataset_id) VALUES ('s1', 37, 127, 1)")
    with pytest.raises(sqlite3.IntegrityError):  # apparent_max outside its CHECK range
        conn.execute("INSERT INTO weather_daily (station_id, date, apparent_max) VALUES ('s1', '2023-07-01', 61.0)")
    with pytest.raises(sqlite3.IntegrityError):  # unknown source label
        conn.execute("INSERT INTO weather_station (id, lat, lon, dataset_id, source) VALUES ('s2', 37, 127, 1, 'x')")
    with pytest.raises(sqlite3.IntegrityError):  # tmax 53.4 is the real outlier in the source data
        conn.execute("INSERT INTO weather_daily (station_id, date, tmax) VALUES ('s1', '2023-07-01', 53.4)")
    with pytest.raises(sqlite3.IntegrityError):  # malformed date
        conn.execute("INSERT INTO weather_daily (station_id, date, tmax) VALUES ('s1', '2023-7-1', 30)")
    with pytest.raises(sqlite3.IntegrityError):  # (0,0) coordinates never reach the table
        conn.execute("INSERT INTO shelter (name, lat, lon, dataset_id) VALUES ('x', 0, 0, 1)")
    with pytest.raises(sqlite3.IntegrityError):  # night_open is 0/1/NULL
        conn.execute("INSERT INTO shelter (name, lat, lon, night_open, dataset_id) VALUES ('x', 37, 127, 2, 1)")


def test_run_and_metric_checks(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):  # training year must precede the test year
        _run(conn, train_max=2023, year=2023)
    run = _run(conn)
    with pytest.raises(sqlite3.IntegrityError):  # same key twice
        _run(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE alert_run SET params = 'not json' WHERE id = ?", (run,))
    with pytest.raises(sqlite3.IntegrityError):  # unknown method
        _run(conn, method="XGB")
    with pytest.raises(sqlite3.IntegrityError):  # value outside its CI
        conn.execute("INSERT INTO eval_metric (run_id, metric, value, ci_lo, ci_hi) VALUES (?, 'capture', 0.9, 0.1, 0.5)", (run,))
    conn.execute("INSERT INTO eval_metric (run_id, metric, value, ci_lo, ci_hi) VALUES (?, 'capture', 0.3, 0.1, 0.5)", (run,))
    with pytest.raises(sqlite3.IntegrityError):  # uq_metric: one absolute capture per run
        conn.execute("INSERT INTO eval_metric (run_id, metric, value) VALUES (?, 'capture', 0.4)", (run,))
    conn.execute("INSERT INTO eval_metric (run_id, metric, x, value) VALUES (?, 'capture_curve', 0.5, 0.2)", (run,))
    conn.execute("INSERT INTO eval_metric (run_id, metric, x, value) VALUES (?, 'capture_curve', 1.0, 0.3)", (run,))


def test_strict_tables_reject_wrong_types(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO region VALUES ('11110', 's', 'n', 37, 127, 'many', 1, 'x')")


def test_modified_migration_is_detected(tmp_path: Path) -> None:
    d = tmp_path / "db"
    (d / "migrations").mkdir(parents=True)
    (d / "schema.sql").write_text((db.DB_DIR / "schema.sql").read_text(encoding="utf-8"), encoding="utf-8")
    for name in ("0002_hardening.sql", "0003_heat.sql"):
        (d / "migrations" / name).write_text((db.DB_DIR / "migrations" / name).read_text(encoding="utf-8"), encoding="utf-8")
    c = db.connect(tmp_path / "x.sqlite3")
    assert db.migrate(c, d) == 3
    (d / "migrations" / "0003_heat.sql").write_text("-- edited\n" + MIGRATION.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(db.MigrationError, match="modified"):
        db.migrate(c, d)
