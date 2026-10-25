"""Ingest transforms: dense case rows, unmapped counting, region codes, shelters, encodings."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.heat import ingest_cases, ingest_regions, ingest_shelters, ingest_weather
from app.heat.common import (
    IngestError, detect_encoding, nospace, register_dataset, season_dates, YEARS,
)

REGIONS = ["11110", "11120", "26110"]
ALIAS = pd.DataFrame({
    "sido_raw": ["서울특별시", "서울특별시", "부산광역시", "강원도"],
    "name_raw": ["종로구", "중구", "중구", "춘천시"],
    "region_code": ["11110", "11120", "26110", "11110"],
})


def _raw(rows: list[tuple[str, str, str | None, str | None]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["발생일자", "발생시도", "발생시군구", "실내외구분"])


def test_season_has_129_days_and_bounds() -> None:
    d = season_dates(2024)
    assert len(d) == 129 and d[0] == "2024-05-20" and d[-1] == "2024-09-25"


def test_dense_rows_sum_to_mapped_cases_and_include_zeros() -> None:
    raw = _raw([
        ("2023-07-01", "서울특별시", "종로구", "실외"),
        ("2023-07-01", "서울특별시", "종로구", "실내"),
        ("2023-07-01", "서울특별시", "종로구", None),  # place unknown: counted in n_cases only
        ("2024-08-15", "부산광역시", "중 구", "실외"),  # spacing normalised
        ("2024-05-01", "서울특별시", "중구", "실외"),  # mapped but before the season
        ("2025-06-01", "강원도", "춘천시", "실내"),
    ])
    dense, unmapped, s = ingest_cases.aggregate_cases(raw, ALIAS, REGIONS)
    assert len(dense) == len(REGIONS) * len(YEARS) * 129
    assert int(dense.n_cases.sum()) == s.mapped_in_season == 5
    assert s.mapped_out_of_season == 1 and s.unmapped == 0 and s.raw == 6
    assert (dense.n_cases == 0).sum() > 0.99 * len(dense)
    cell = dense[(dense.region_code == "11110") & (dense.date == "2023-07-01")].iloc[0]
    assert (cell.n_cases, cell.n_indoor, cell.n_outdoor) == (3, 1, 1)
    assert (dense.n_indoor + dense.n_outdoor <= dense.n_cases).all()
    assert unmapped.empty


def test_unmapped_names_are_counted_not_dropped() -> None:
    raw = _raw([
        ("2023-07-01", "경기도", "성남시", "실외"),
        ("2023-07-02", "경기도", "성남시", "실외"),
        ("2023-07-02", "서울특별시", None, "실내"),  # blank 시군구
        ("2023-07-03", "서울특별시", "종로구", "실외"),
        ("2010-07-03", "서울특별시", "종로구", "실외"),  # outside the study years: ignored
    ])
    dense, unmapped, s = ingest_cases.aggregate_cases(raw, ALIAS, REGIONS)
    assert s.unmapped == 3 and s.mapped_in_season == 1 and s.raw == 4
    assert sorted(s.unmapped_names) == [("경기도", "성남시", 2), ("서울특별시", "", 1)]
    assert int(dense.n_cases.sum()) + s.unmapped + s.mapped_out_of_season == s.raw


def test_missing_columns_rejected() -> None:
    with pytest.raises(IngestError, match="lacks columns"):
        ingest_cases.aggregate_cases(pd.DataFrame({"a": [1]}), ALIAS, REGIONS)


def test_ingest_cases_is_idempotent(conn: sqlite3.Connection, tmp_path: Path) -> None:
    from app.heat.common import HEAT_CSV, HeatPaths

    conn.executemany(
        "INSERT INTO region VALUES (?, '서울특별시', ?, 37.5, 127.0, 100, 10, '2026-09')",
        [("11110", "종로구"), ("11120", "중구")],
    )
    conn.executemany("INSERT INTO region_alias VALUES ('서울특별시', ?, ?, 'exact')", [("종로구", "11110"), ("중구", "11120")])
    csv = tmp_path / HEAT_CSV
    csv.parent.mkdir(parents=True)
    csv.write_text(
        "발생일자,성별,나이,발생시도,발생시군구,실내외구분,발생장소\n"
        "2023-07-01,남자,70,서울특별시,종로구,실외,논밭\n"
        "2023-07-01,남자,70,서울특별시,없는구,실외,논밭\n", encoding="utf-8-sig")
    paths = HeatPaths(db_path=tmp_path / "x", data_dir=tmp_path, out_dir=tmp_path)
    s1 = ingest_cases.ingest_cases(conn, paths)
    s2 = ingest_cases.ingest_cases(conn, paths)
    assert s1 == s2 and s1.mapped_in_season == 1 and s1.unmapped == 1
    q = conn.execute("SELECT COUNT(*) n, SUM(n_cases) c FROM heat_case_daily").fetchone()
    assert (q["n"], q["c"]) == (2 * len(YEARS) * 129, 1)
    assert conn.execute("SELECT n_cases FROM heat_case_unmapped").fetchone()["n_cases"] == 1
    assert conn.execute("SELECT COUNT(*) FROM datasets WHERE name='heat_illness_cases'").fetchone()[0] == 1


def _dongs(rows: list[tuple[str, str, str, str, int]]) -> pd.DataFrame:
    return pd.DataFrame([
        {"adm_cd2": c, "new_sido": "인천광역시", "sgg_display": sgg, "new_sgg": nospace(sgg), "dong": d,
         "lat": 37.5, "lon": 126.7, "pop": p, "p65": p // 5, "pop_asof": "2026-09-30",
         "unit_sido": "인천광역시", "unit_sgg": unit, "unit_key": f"인천광역시|{unit}"}
        for c, sgg, d, unit, p in rows
    ])


def test_region_codes_are_unique_when_two_units_share_a_prefix() -> None:
    # 제물포구 (28125) is split between old 중구 and old 동구; 영종구 (28155) is only 중구.
    d = _dongs([
        ("2812510100", "제물포구", "a", "동구", 900),
        ("2812510200", "제물포구", "b", "중구", 500),
        ("2815510100", "영종구", "c", "중구", 300),
    ])
    r = ingest_regions.build_regions(d)
    codes = dict(zip(r.name, r.code, strict=True))
    assert codes == {"동구": "28125", "중구": "28155"}
    assert r.code.is_unique and int(r["pop"].sum()) == 1700


def test_region_requires_positive_population() -> None:
    d = _dongs([("2812510100", "제물포구", "a", "동구", 0)])
    with pytest.raises(IngestError, match="zero population"):
        ingest_regions.build_regions(d)


def test_aliases_exact_only_without_crosswalk_and_kinds_with_it(tmp_path: Path) -> None:
    regions = pd.DataFrame({"code": ["41590", "52110"], "sido": ["경기도", "전북특별자치도"],
                            "name": ["화성시", "전주시 완산구"]})
    only_exact = ingest_regions.build_aliases(regions, None)
    assert [(a[0], a[1], a[3]) for a in only_exact] == [
        ("경기도", "화성시", "exact"), ("전북특별자치도", "전주시완산구", "exact")]
    cw = tmp_path / "cw.csv"
    cw.write_text(
        "heat_sido,heat_sgg,status,n_cases_2019_25,unit_key,rule,comp_2026,n_dong,note\n"
        "경기도,화성시,matched,5,경기도|화성시,merge_gu_to_city,x,3,\n"
        "전라북도,전주시완산구,matched,2,전북특별자치도|전주시완산구,exact,x,3,\n"
        "경기도,성남시,dropped_ambiguous_city_label,1,,,,,\n", encoding="utf-8-sig")
    aliases = {(a[0], a[1]): (a[2], a[3]) for a in ingest_regions.build_aliases(regions, cw)}
    assert aliases[("경기도", "화성시")] == ("41590", "merge")
    assert aliases[("전라북도", "전주시완산구")] == ("52110", "sido_rename")
    assert ("경기도", "성남시") not in aliases


def test_shelters_drop_bad_rows_and_null_unknown_flags() -> None:
    df = pd.DataFrame({
        "시군구코드": [44131, 44131, 44131, 99999, 44131], "쉼터명칭": ["a", "b", "c", "d", "a"],
        "위도": [36.8, 0.0, 36.8, 36.9, 36.8], "경도": [127.2, 0.0, 127.3, 127.4, 127.2],
        "이용가능인원수": [27, 5, -3, np.nan, 27], "야간연장운영여부": ["Y", "N", None, "N", "Y"],
        "주말운영여부": ["N", "Y", "Y", None, "N"], "사용여부": ["Y", "Y", "Y", "Y", "Y"],
    })
    src = ingest_shelters.SOURCES[0]
    out, stats = ingest_shelters.clean_shelters(df, src, {"44131"}, {})
    assert stats["bad_coords"] == 1 and stats["duplicates"] == 1 and stats["no_region"] == 1
    assert len(out) == 3
    by = out.set_index("name")
    assert by.loc["a", "night_open"] == 1 and by.loc["a", "weekend_open"] == 0
    assert pd.isna(by.loc["c", "night_open"]) and pd.isna(by.loc["c", "capacity"])  # unknown stays NULL
    inactive = df.assign(사용여부=["N", "Y", "Y", "Y", "Y"])
    assert ingest_shelters.clean_shelters(inactive, src, {"44131"}, {})[1]["inactive"] == 1


def test_busan_shelters_have_no_night_or_weekend_fields() -> None:
    df = pd.DataFrame({"쉼터명": ["x", "y"], "상세주소": ["부산광역시 중구 중앙대로 1", ""],
                       "x좌표": [129.03, 129.04], "y좌표": [35.1, 35.2]})
    out, stats = ingest_shelters.clean_shelters(df, ingest_shelters.SOURCES[1], set(), {("부산광역시", "중구"): "26110"})
    assert out.region_code.tolist()[0] == "26110" and pd.isna(out.region_code.tolist()[1])
    assert out.night_open.isna().all() and out.weekend_open.isna().all() and stats["no_region"] == 1


def test_detect_encoding(tmp_path: Path) -> None:
    u8, cp = tmp_path / "u.csv", tmp_path / "c.csv"
    u8.write_bytes("가나다\n".encode("utf-8-sig"))
    cp.write_bytes("가나다\n".encode("cp949"))
    assert detect_encoding(u8) == "utf-8-sig" and detect_encoding(cp) == "cp949"
    bad = tmp_path / "b.csv"
    bad.write_bytes(b"\xff\xfe\xfa\xfb\x81")
    with pytest.raises(IngestError):
        detect_encoding(bad)


def test_nearest_station_haversine() -> None:
    stations = np.array([[37.5, 127.0], [35.1, 129.0]])
    idx, km = ingest_weather.nearest_station(np.array([[35.2, 129.1], [37.6, 126.9]]), stations)
    assert idx.tolist() == [1, 0]
    assert 10 < km[0] < 15 and 10 < km[1] < 16  # ~13 km and ~14 km


def test_register_dataset_is_idempotent_and_tracks_sha(conn: sqlite3.Connection, tmp_path: Path) -> None:
    f = tmp_path / "a.csv"
    f.write_text("x\n1\n")
    kw = dict(name="heat_t", path=f, source_url="https://example.org", license_="l",
              description="d", roles={"kind": "heat"})
    a = register_dataset(conn, row_count=1, **kw)  # type: ignore[arg-type]
    f.write_text("x\n1\n2\n")
    b = register_dataset(conn, row_count=2, **kw)  # type: ignore[arg-type]
    row = conn.execute("SELECT sha256, row_count FROM datasets WHERE id = ?", (a,)).fetchone()
    assert a == b and row["row_count"] == 2 and len(row["sha256"]) == 64
    assert conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 1


@pytest.mark.parametrize(("start", "end", "expected"), [
    ("09:00", "18:00", (0, None)),
    ("09:00:00", "21:00:00", (1, None)),
    ("10:00", "22:00", (1, None)),
    ("00:00", "24:00", (1, None)),
    ("18:00", "02:00", (1, None)),  # crosses midnight
    ("00:00", "00:00", (None, None)),  # ambiguous: unknown
    (None, "18:00", (None, None)),
    (np.nan, np.nan, (None, None)),
    ("garbage", "25:99", (None, None)),
])
def test_hours_flags_weekday_night_rule(start: object, end: object, expected: tuple[int | None, int | None]) -> None:
    assert ingest_shelters.hours_flags((start, end), None) == expected


def test_hours_flags_weekend_known_only_when_interval_known() -> None:
    assert ingest_shelters.hours_flags(("09:00", "18:00"), ("10:00", "22:00")) == (1, 1)
    assert ingest_shelters.hours_flags(("09:00", "18:00"), (np.nan, np.nan)) == (0, None)
    assert ingest_shelters.hours_flags(None, ("10:00", "17:00")) == (0, 1)


def test_clean_extra_source_swaps_coords_derives_flags_and_drops_deleted() -> None:
    df = pd.DataFrame({
        "무더위쉼터명": ["a", "b", "c"], "주소": ["대구광역시 수성구 x"] * 3,
        "수용인원": [10, 20, 30], "위도": [35.8, 128.6, 35.9], "경도": [128.6, 35.8, 128.7],
        "야간운영여부": ["Y", None, "N"], "주말운영여부": ["N", "Y", None],
        "사용여부": ["Y", "Y", "Y"], "삭제여부": ["N", "N", "Y"],
    })
    src = next(s for s in ingest_shelters.SOURCES if s.dataset == "heat_shelters_daegu_suseong")
    out, stats = ingest_shelters.clean_shelters(df, src, set(), {("대구광역시", "수성구"): "27260"})
    assert stats["swapped"] == 1 and stats["inactive"] == 1 and len(out) == 2
    by = out.set_index("name")
    assert by.loc["b", "lat"] == 35.8 and by.loc["b", "lon"] == 128.6
    assert pd.isna(by.loc["b", "night_open"]) and by.loc["b", "weekend_open"] == 1


def test_clean_gyeongsan_hours_columns() -> None:
    df = pd.DataFrame({
        "쉼터명칭": ["a", "b"], "도로명주소": ["경상북도 경산시 x"] * 2, "이용가능인원(명)": [5, 6],
        "위도": [35.9, 35.91], "경도": [128.8, 128.81],
        "평일시작시간": ["09:00:00", "09:00:00"], "평일종료시간": ["18:00:00", "18:00:00"],
        "주말시작시간": ["10:00:00", np.nan], "주말종료시간": ["22:00:00", np.nan],
    })
    src = next(s for s in ingest_shelters.SOURCES if s.dataset == "heat_shelters_gyeongsan")
    out, _ = ingest_shelters.clean_shelters(df, src, set(), {("경상북도", "경산시"): "47290"})
    by = out.set_index("name")
    assert (by.loc["a", "night_open"], by.loc["a", "weekend_open"]) == (1, 1)
    assert by.loc["b", "night_open"] == 0 and pd.isna(by.loc["b", "weekend_open"])


def test_blank_address_uses_single_district_fallback_only_when_blank() -> None:
    resolve = ingest_shelters._region_by_address_col("주소", ("대구광역시", "수성구"))
    names = {("대구광역시", "수성구"): "27260", ("대구광역시", "중구"): "27110"}
    df = pd.DataFrame({"주소": [np.nan, "대구광역시 중구 x", "대구광역시"]})
    assert resolve(df, set(), names).tolist() == ["27260", "27110", None]
