"""Shared paths and 시군구 geography for the 호우 case study (offline, no API keys).

Inputs are treated as untrusted: read with explicit encodings/dtypes, never executed.
"""
from __future__ import annotations

import gzip
import json
import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd

LOG = logging.getLogger("rain")
KCF = Path("/Users/chloekang/kcf")
EXTRA = KCF / "data" / "cache" / "extra"
RESCUE_CSV = EXTRA / "rescue" / "rescue_2025.csv"
GEOJSON = EXTRA / "boundaries" / "HangJeongDong_ver20261001.geojson"
POP_CSV = KCF / "data" / "cache" / "j_population_regional" / "kr_15097972_resident_pop_by_dong_sex_age_202609.csv"
METEOSTAT = EXTRA / "meteostat_daily"
OUT = Path(__file__).resolve().parent / "out"
SEED = 42

# Flood-related 사고원인코드명_사고종별 values found in the file (verified by value_counts).
FLOOD_TYPES = {"침수": "수난", "고립": "수난", "산사태": "붕괴·도괴(깔림)"}  # 종별 -> required 사고원인
# 2025 season window for rain (Meteostat prcp NaN = missing, never 0).
SEASON = ("2025-06-01", "2025-09-30")

_GWANGJU_JEONNAM = {"광주광역시", "전라남도", "전남광주통합특별시"}


def sido_group(s: str) -> str:
    """Collapse 광주/전남/통합 to one group (2026 boundaries merged them)."""
    return "전남광주" if s in _GWANGJU_JEONNAM else s


def norm_sgg(s: str) -> str:
    return re.sub(r"\s+", "", str(s))


def load_sgg_table() -> pd.DataFrame:
    """One row per 2026 시군구: code, names, population, 65+ share, area km2, density, pop-weighted centroid."""
    g = json.loads(GEOJSON.read_text(encoding="utf-8"))
    rows = []
    for f in g["features"]:
        p, geom = f["properties"], f["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        area = 0.0
        pts = []
        for poly in polys:
            ring = np.asarray(poly[0], dtype=float)
            lat0 = np.deg2rad(ring[:, 1].mean())
            x = np.deg2rad(ring[:, 0]) * 6371.0 * np.cos(lat0)
            y = np.deg2rad(ring[:, 1]) * 6371.0
            area += abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2
            pts.append(ring)
        c = np.vstack(pts).mean(axis=0)
        rows.append(dict(adm_cd2=str(p["adm_cd2"]), sgg_cd=str(p["sgg"]), sido=p["sidonm"], sgg=p["sggnm"],
                         area_km2=area, lon=c[0], lat=c[1]))
    dong = pd.DataFrame(rows)
    pop = pd.read_csv(POP_CSV, encoding="cp949", thousands=",", dtype={"행정기관코드": str})
    age_f = [c for c in pop.columns if re.fullmatch(r"\d+세(남자|여자)", c) or c.startswith("110세이상")]
    ages = pop[age_f].apply(pd.to_numeric, errors="coerce").fillna(0)
    ageyr = pd.Series([int(re.match(r"\d+", c).group()) for c in age_f], index=age_f)
    pop["pop65"] = ages.loc[:, ageyr[ageyr >= 65].index].sum(axis=1)
    pop["pop"] = pd.to_numeric(pop["계"], errors="coerce")
    d = dong.merge(pop[["행정기관코드", "pop", "pop65"]], left_on="adm_cd2", right_on="행정기관코드", how="left")
    d["pop"] = d["pop"].fillna(0)
    d["pop65"] = d["pop65"].fillna(0)
    w = d["pop"].clip(lower=1)
    d["lw"], d["latw"] = d["lon"] * w, d["lat"] * w
    s = d.groupby(["sgg_cd", "sido", "sgg"]).agg(pop=("pop", "sum"), pop65=("pop65", "sum"), area_km2=("area_km2", "sum"),
                                                 lw=("lw", "sum"), latw=("latw", "sum"), w=("lw", lambda x: 0)).reset_index()
    wsum = d.groupby("sgg_cd")["pop"].apply(lambda x: x.clip(lower=1).sum()).rename("wsum").reset_index()
    s = s.drop(columns="w").merge(wsum, on="sgg_cd")
    s["lon"], s["lat"] = s["lw"] / s["wsum"], s["latw"] / s["wsum"]
    s["share65"] = s["pop65"] / s["pop"].replace(0, np.nan)
    s["density"] = s["pop"] / s["area_km2"]
    s["key"] = s["sido"].map(sido_group) + "|" + s["sgg"].map(norm_sgg)
    return s.drop(columns=["lw", "latw", "wsum"])


def load_stations() -> pd.DataFrame:
    """Meteostat KR stations with daily prcp files on disk: id, lat, lon, tz-naive daily prcp."""
    meta = json.loads(gzip.open(METEOSTAT / "stations_lite.json.gz", "rt", encoding="utf-8").read())
    ids = {p.name.split(".")[0] for p in METEOSTAT.glob("*.csv.gz")}
    rows = [dict(id=m["id"], lat=m["location"]["latitude"], lon=m["location"]["longitude"],
                 name=m["name"].get("en", "")) for m in meta if m["id"] in ids]
    return pd.DataFrame(rows)


def load_prcp(ids: list[str]) -> pd.DataFrame:
    """Wide frame date x station of daily prcp (mm). NaN = missing."""
    cols = ["date", "tavg", "tmin", "tmax", "prcp", "snow", "wdir", "wspd", "wpgt", "pres", "tsun"]
    out = {}
    for i in ids:
        d = pd.read_csv(METEOSTAT / f"{i}.csv.gz", header=None, names=cols, usecols=["date", "prcp"], parse_dates=["date"])
        out[i] = d.set_index("date")["prcp"]
    return pd.DataFrame(out)


def haversine_km(lat1, lon1, lat2, lon2):
    p = np.pi / 180
    a = np.sin((lat2 - lat1) * p / 2) ** 2 + np.cos(lat1 * p) * np.cos(lat2 * p) * np.sin((lon2 - lon1) * p / 2) ** 2
    return 12742 * np.arcsin(np.sqrt(a))


def load_dong_index() -> pd.DataFrame:
    """(sido group, 읍면동 name) -> 2026 sgg_cd, from boundary properties (adm_nm = '시도 시군구 읍면동')."""
    g = json.loads(GEOJSON.read_text(encoding="utf-8"))
    rows = []
    for f in g["features"]:
        p = f["properties"]
        rows.append(dict(sido=p["sidonm"], sgg_cd=str(p["sgg"]), sgg=p["sggnm"], dong=str(p["adm_nm"]).split(" ")[-1]))
    d = pd.DataFrame(rows)
    d["sg"] = d["sido"].map(sido_group)
    return d
