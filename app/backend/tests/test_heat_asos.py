"""ASOS 95-station ingest: 체감온도 formula parity with the experiment, hub parsing, daily max rule, nearest station."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pytest

from app.heat import apparent, ingest_asos
from app.heat.common import APP_ROOT, HeatPaths, IngestError

HUB_HEADER = "#START7777\n# YYMMDDHHMI STN  WD   WS GST  GST  GST     PA     PS PT    PR    TA    TD    HM\n"
STN_CSV = (
    "﻿지점코드,종료시각,시작시각,(현)지점명,(현)주소,위도,경도,노장 해발고도(m),기압계,기온계,풍속계,강우계\n"
    " 108,~, 1907.10.01 00:00:00, 서울, 서울특별시 종로구 송월동, 37.57142, 126.96580, 85.67, 86.67, 1.5, 10.0, 0.6\n"
    " 159,~, 1904.04.09 00:00:00, 부산, 부산광역시 중구 대청동, 35.10468, 129.03203, 69.56, 70.56, 1.5, 10.0, 0.6\n"
)


def _line(tm: str, stn: int, ta: float, hm: float) -> str:
    # TM STN WD WS GST_WD GST_WS GST_TM PA PS PT PR TA TD HM ...
    return f"{tm} {stn:3d} 11 1.6 -9 -9.0 -9 1011.0 1013.1 -9 -9.0 {ta:5.1f} 12.3 {hm:5.1f} 14.3 -9.0\n"


def _hub(tmp: Path, hours_108: int = 10, hours_159: int = 5) -> HeatPaths:
    data = tmp / "cache"
    (data / "extra/kma_hub/stn_src").mkdir(parents=True)
    (data / "extra/kma_hub/stn_src/shj_stn.csv").write_text(STN_CSV, encoding="utf-8")
    hub = data / "extra/kma_hub/sfctm2"
    hub.mkdir()
    for h in range(10, 20):
        lines = [HUB_HEADER]
        if h - 10 < hours_108:
            lines.append(_line(f"20250722{h:02d}00", 108, 30.0 + (h - 10) * 0.5, 60.0))
        if h - 10 < hours_159:
            lines.append(_line(f"20250722{h:02d}00", 159, 28.0, 70.0))
        lines.append(_line(f"20250722{h:02d}00", 999, 20.0, 50.0))  # unknown station: ignored
        if h == 12:
            lines.append(_line(f"20250722{h:02d}00", 108, -9.0, -9.0))  # sentinel row -> NaN (not counted)
        (hub / f"20250722{h:02d}00.txt").write_text("".join(lines), encoding="utf-8")
    return HeatPaths(db_path=tmp / "x.sqlite3", data_dir=data, out_dir=tmp / "out")


def test_apparent_temperature_matches_the_experiment_formula() -> None:
    sys.path.insert(0, str(APP_ROOT / "backend" / "experiments" / "heat"))
    import weather_hub as wh  # the evaluation's implementation (read-only import)

    ta = np.array([25.0, 30.0, 33.0, 36.0, 38.5])
    rh = np.array([40.0, 55.0, 70.0, 80.0, 95.0])
    np.testing.assert_allclose(apparent.summer_apparent_temp(ta, rh), wh.summer_apparent_temp(ta, rh), rtol=1e-12)
    np.testing.assert_allclose(apparent.wet_bulb_stull(ta, rh), wh.wet_bulb_stull(ta, rh), rtol=1e-12)
    # humidity raises the felt temperature; a dry 33 C day feels cooler than a humid one
    assert apparent.summer_apparent_temp(33.0, 80.0) > apparent.summer_apparent_temp(33.0, 30.0)
    assert np.isnan(apparent.summer_apparent_temp(np.nan, 50.0))


def test_parse_and_daily_max_rule(tmp_path: Path) -> None:
    paths = _hub(tmp_path)
    rows = ingest_asos.parse_hub_file(paths.data_dir / "extra/kma_hub/sfctm2/202507221200.txt")
    assert [r[2] for r in rows] == ["108", "159", "999", "108"]
    assert rows[0][:2] == ("2025-07-22", 12) and np.isnan(rows[3][3]) and np.isnan(rows[3][4])
    daily = ingest_asos.daily_max([r for r in rows if r[2] != "999"] * 1, min_hours=1)
    assert daily[("108", "2025-07-22")][1] == 31.0 and daily[("108", "2025-07-22")][2] == 1
    all_rows = [r for f in sorted((paths.data_dir / "extra/kma_hub/sfctm2").glob("*.txt"))
                for r in ingest_asos.parse_hub_file(f)]
    daily = ingest_asos.daily_max(all_rows)
    at, tx, n = daily[("108", "2025-07-22")]
    assert n == 10 and tx == 34.5 and at is not None
    assert at == pytest.approx(float(apparent.summer_apparent_temp(34.5, 60.0)), abs=1e-9)  # peak hour is the max
    assert daily[("159", "2025-07-22")] == (None, None, 5)  # fewer than MIN_HOURS valid hours -> NULL


def test_nearest_station_brute_force() -> None:
    stations = np.array([[37.5, 127.0], [35.1, 129.0]])
    idx, km = ingest_asos.nearest(np.array([[35.2, 129.1], [37.6, 126.9]]), stations)
    assert idx.tolist() == [1, 0] and 10 < km[0] < 20 and 10 < km[1] < 20


def test_ingest_asos_end_to_end_and_idempotent(conn: sqlite3.Connection, tmp_path: Path) -> None:
    paths = _hub(tmp_path)
    with pytest.raises(IngestError, match="region table is empty"):
        ingest_asos.ingest_asos(conn, paths)
    conn.executemany(
        "INSERT INTO region (code, sido, name, lat, lon, pop, pop65, pop_asof) VALUES (?,?,?,?,?,?,?,'2026-09-30')",
        [("11620", "서울특별시", "관악구", 37.478, 126.951, 477073, 94733), ("26110", "부산광역시", "중구", 35.10, 129.03, 3000, 300)],
    )
    first = ingest_asos.ingest_asos(conn, paths)
    assert first == {"stations": 2, "files": 10, "station_days": 2, "apparent_null": 1, "region_station": 2}
    rs = {r["region_code"]: (r["station_id"], r["dist_km"]) for r in conn.execute("SELECT * FROM region_station")}
    assert rs["11620"][0] == "108" and rs["26110"][0] == "159" and rs["11620"][1] < 12
    row = conn.execute("SELECT tmax, apparent_max, n_hours FROM weather_daily WHERE station_id='108'").fetchone()
    assert row["tmax"] == 34.5 and row["apparent_max"] > 34.5 and row["n_hours"] == 10
    gap = conn.execute("SELECT tmax, apparent_max, n_hours FROM weather_daily WHERE station_id='159'").fetchone()
    assert gap["tmax"] is None and gap["apparent_max"] is None and gap["n_hours"] == 5
    st = conn.execute("SELECT name, source FROM weather_station WHERE id='108'").fetchone()
    assert (st["name"], st["source"]) == ("서울", "kma_asos")
    names = {r["name"]: r["sha256"] for r in conn.execute("SELECT name, sha256 FROM datasets")}
    assert {"heat_kma_asos_stations", "heat_kma_asos_hourly"} <= set(names)
    second = ingest_asos.ingest_asos(conn, paths)
    assert second == first
    assert conn.execute("SELECT COUNT(*) FROM weather_daily").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 2  # registered once each


def test_bad_station_file_is_rejected(tmp_path: Path) -> None:
    p = tmp_path / "stn.csv"
    p.write_text(STN_CSV.replace("35.10468", "45.0"), encoding="utf-8")
    with pytest.raises(IngestError, match="outside Korea"):
        ingest_asos.read_stations(p)
    p.write_text(STN_CSV + STN_CSV.splitlines()[1] + "\n", encoding="utf-8")
    with pytest.raises(IngestError, match="duplicate"):
        ingest_asos.read_stations(p)
