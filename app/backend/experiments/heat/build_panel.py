"""Build the deterministic 시군구 x day heat-illness panel (2019-2025, season May 20 - Sep 25).

Weather = KMA ASOS hourly (API허브, all ~95 stations) -> daily max 체감온도 (KMA 여름철 formula) per station, plus the
daily max 기온 on the same station-hours (Tmax sensitivity). Population / 65+ = 행정안전부 December-of-previous-year
registry snapshot per season year (falls back to the 2026-09 dong snapshot where a unit cannot be matched).

Outputs (experiments/heat/out/):
  panel.csv.gz                  unit-day panel (features + label y); columns at_* (체감온도) and tx_* (기온 sensitivity)
  crosswalk_heat_to_2026.csv    explicit heat-label name -> 2026 boundary crosswalk
  coverage_report.json          cases matched %, weather coverage, station distances, population matching

Units are the heat-label 시군구 (pre-2026 geography, which is what the labels use). 2026 행정동
(boundaries + population) are aggregated UP to those units, never split, so no case is
apportioned by assumption. Read-only on the data cache.

Run:  uv run --project /Users/chloekang/kcf/app/backend python experiments/heat/build_panel.py
"""

from __future__ import annotations

import json
import logging
import re

import numpy as np
import pandas as pd

import weather_hub as wh
from common import (
    COVERAGE_PATH, CROSSWALK_PATH, FAMILIES, FETCH_END, FETCH_START, GEOJSON, HEAT_CSV, HOT_C, HOURS, MIN_HOURS,
    OUT_DIR, PANEL_PATH, POP_CSV, POP_SNAP_CSV, SEASON_END, SEASON_START, SEED, YEARS, dump_json, set_seed,
)

logger = logging.getLogger("heat.build")

# ---- crosswalk rules (documented; mirrored row-by-row into the crosswalk CSV) -------------------
SIDO_OLD_TO_2026 = {
    "광주광역시": "전남광주통합특별시",  # 2026 merger; 광주 and 전남 sgg names do not collide
    "전라남도": "전남광주통합특별시",
    "전라북도": "전북특별자치도",
    "강원도": "강원특별자치도",
}
# 2026 sgg that are merged back to the (coarser) heat-label sgg.
MERGE_PREFIX = {("경기도", "화성시"): "화성시", ("경기도", "부천시"): "부천시"}  # 구 split (2026) vs whole city (labels)
# 인천 2026 reorganisation -> old 서구 / 중구 / 동구 (labels use the old three).
INCHEON_SGG_TO_OLD = {"서해구": "서구", "검단구": "서구", "영종구": "중구"}
# 제물포구 straddles old 중구 and old 동구; assigned by 행정동 name. est., verify against 인천시 고시.
JEMULPO_OLD_JUNG = {"연안동", "신포동", "신흥동", "도원동", "율목동", "동인천동", "개항동"}
# Heat labels that are a whole city while 2026 data (and most labels) are at 구 level: ambiguous -> dropped.
AMBIGUOUS_HEAT = {("경기도", "성남시"), ("경기도", "안산시"), ("전라북도", "전주시")}
# 군위군 moved 경북 -> 대구 (2023); labels carry both.
HEAT_SGG_SIDO_FIX = {("경상북도", "군위군"): "대구광역시"}


def nospace(s: pd.Series) -> pd.Series:
    return s.astype(str).str.replace(r"\s+", "", regex=True)


def load_dongs() -> pd.DataFrame:
    """2026 행정동 table: code, names, vertex-mean centroid, population, 65+ (single snapshot 2026-09)."""
    with open(GEOJSON, encoding="utf-8") as fh:
        gj = json.load(fh)
    rows = []
    for f in gj["features"]:
        p, g = f["properties"], f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        pts = np.vstack([np.asarray(poly[0], dtype=float) for poly in polys])
        rows.append(
            dict(adm_cd2=str(p["adm_cd2"]), new_sido=p["sidonm"], new_sgg=p["sggnm"].replace(" ", ""),
                 dong=p["adm_nm"].split()[-1], lon=pts[:, 0].mean(), lat=pts[:, 1].mean())
        )
    d = pd.DataFrame(rows)

    pop = pd.read_csv(POP_CSV, encoding="cp949", thousands=",")
    pop["adm_cd2"] = pop["행정기관코드"].astype(str)
    ages = {c: int(m.group(1)) for c in pop.columns if (m := re.match(r"^(\d+)세", str(c)))}
    pop["p65"] = pop[[c for c, a in ages.items() if a >= 65]].sum(axis=1)
    # 2026-09 snapshot: used for unit geometry weights and as the fallback when the per-year registry cannot be
    # matched to a unit. Per-year values (December of year-1, no lookahead) come from load_pop_by_year().
    d = d.merge(pop[["adm_cd2", "계", "p65"]].rename(columns={"계": "pop"}), on="adm_cd2", how="left")
    return d


def assign_unit(d: pd.DataFrame) -> pd.DataFrame:
    """Map each 2026 행정동 to a heat-label unit (unit_sido_2026, unit_sgg)."""
    d = d.copy()
    d["unit_sgg"] = d["new_sgg"]
    d["rule"] = "exact"
    for (sido, pref), nm in MERGE_PREFIX.items():
        m = (d.new_sido == sido) & d.new_sgg.str.startswith(pref)
        d.loc[m, "unit_sgg"] = nm
        d.loc[m, "rule"] = "merge_gu_to_city"
    inc = d.new_sido == "인천광역시"
    for new, old in INCHEON_SGG_TO_OLD.items():
        m = inc & (d.new_sgg == new)
        d.loc[m, "unit_sgg"] = old
        d.loc[m, "rule"] = "incheon_reorg"
    m = inc & (d.new_sgg == "제물포구")
    d.loc[m, "unit_sgg"] = np.where(d.loc[m, "dong"].isin(JEMULPO_OLD_JUNG), "중구", "동구")
    d.loc[m, "rule"] = "incheon_jemulpo_by_dong"
    m = d.new_sido == "세종특별자치시"
    d.loc[m, "unit_sgg"] = "세종특별자치시"
    d.loc[m, "rule"] = "sejong_rename"
    d["unit_sido"] = d["new_sido"]
    d["unit_key"] = d.unit_sido + "|" + d.unit_sgg
    return d


def load_heat() -> pd.DataFrame:
    h = pd.read_csv(HEAT_CSV, encoding="utf-8-sig")
    h["date"] = pd.to_datetime(h["발생일자"], errors="coerce")
    h = h.dropna(subset=["date"])
    h = h[h.date.dt.year.isin(YEARS)].copy()  # 시군구 is 0% filled before 2019
    return h


def map_heat_to_unit(h: pd.DataFrame, valid_keys: set[str]) -> pd.DataFrame:
    """Add unit_key (or NaN) and a status per heat row."""
    h = h.copy()
    h["heat_sido"] = h["발생시도"]
    h["heat_sgg"] = nospace(h["발생시군구"].fillna("")).replace("", np.nan)
    sido26 = h.heat_sido.map(lambda s: SIDO_OLD_TO_2026.get(s, s))
    for (hs, sg), tgt in HEAT_SGG_SIDO_FIX.items():
        sido26 = sido26.where(~((h.heat_sido == hs) & (h.heat_sgg == sg)), tgt)
    h["unit_key"] = sido26 + "|" + h.heat_sgg.fillna("")
    h["status"] = np.where(h.heat_sgg.isna(), "missing_sgg_label", "matched")
    amb = pd.Series([(a, b) in {(x, nospace(pd.Series([y])).iloc[0]) for x, y in AMBIGUOUS_HEAT}
                     for a, b in zip(h.heat_sido, h.heat_sgg.fillna(""))], index=h.index)
    h.loc[amb, "status"] = "dropped_ambiguous_city_label"
    bad = (h.status == "matched") & ~h.unit_key.isin(valid_keys)
    h.loc[bad, "status"] = "unmatched_name"
    h.loc[h.status != "matched", "unit_key"] = np.nan
    return h


def build_crosswalk(h: pd.DataFrame, d: pd.DataFrame) -> pd.DataFrame:
    comp = (
        d.groupby("unit_key")
        .agg(rule=("rule", lambda s: "+".join(sorted(set(s)))),
             comp_2026=("new_sido", lambda s: ""), n_dong=("adm_cd2", "size"))
        .reset_index()
    )
    comp["comp_2026"] = d.groupby("unit_key").apply(
        lambda g: "+".join(sorted((g.new_sido + " " + g.new_sgg).unique())), include_groups=False
    ).reindex(comp.unit_key).values
    cw = (
        h.groupby(["heat_sido", "heat_sgg", "status"], dropna=False).size().rename("n_cases_2019_25").reset_index()
    )
    cw = cw.merge(
        h[["heat_sido", "heat_sgg", "unit_key"]].drop_duplicates(), on=["heat_sido", "heat_sgg"], how="left"
    ).merge(comp, on="unit_key", how="left")
    notes = {
        "incheon_jemulpo_by_dong": "제물포구 dongs split old 중구 / old 동구 by dong name list (est., verify)",
        "incheon_reorg": "2026 서해구+검단구 = old 서구; 영종구 = old 중구",
        "merge_gu_to_city": "2026 구 split merged back to whole city used by labels",
        "sejong_rename": "세종시 -> 세종특별자치시",
    }
    cw["note"] = cw.rule.map(lambda r: "; ".join(v for k, v in notes.items() if isinstance(r, str) and k in r))
    cw.loc[cw.status == "dropped_ambiguous_city_label", "note"] = "whole-city label while 구 labels also exist; cannot assign -> dropped"
    cw.loc[cw.status == "missing_sgg_label", "note"] = "발생시군구 blank in 2019-2025"
    cw.loc[(cw.heat_sido != cw.unit_key.str.split("|").str[0]) & cw.unit_key.notna(), "note"] += " sido renamed/moved to 2026 sido"
    return cw.sort_values(["status", "heat_sido", "heat_sgg"]).reset_index(drop=True)


# ---- population by year ---------------------------------------------------------------------------
def load_pop_by_year(unit_keys: set[str]) -> pd.DataFrame:
    """(unit_key, year) -> pop, p65 from the December(year-1) 주민등록 시군구 snapshot.

    Registry names are pre-2026 (same geography as the heat labels): sido renames go through SIDO_OLD_TO_2026,
    군위군 is mapped to 대구 for every year, 세종 has no 시군구 level. Units with no registry row are absent
    from the result (caller falls back to the 2026 snapshot and counts them).
    """
    r = pd.read_csv(POP_SNAP_CSV, dtype={"code": str})
    parts = r["name"].str.split(r"\s+", n=1, expand=True)
    sido, sgg = parts[0], parts[1].fillna("")
    sgg = sgg.where(sgg != "", sido)  # 세종특별자치시 has a single row
    sgg = nospace(sgg)
    sido26 = sido.map(lambda s: SIDO_OLD_TO_2026.get(s, s))
    for (hs, sg), tgt in HEAT_SGG_SIDO_FIX.items():
        sido26 = sido26.where(~((sido == hs) & (sgg == sg)), tgt)
    r["unit_key"] = sido26 + "|" + sgg
    r["year"] = r["snapshot"].str[:4].astype(int) + 1  # December Y-1 -> season year Y
    r = r[r.unit_key.isin(unit_keys)].drop_duplicates(["unit_key", "year"])
    return r[["unit_key", "year", "pop", "pop65"]].rename(columns={"pop": "pop_y", "pop65": "p65_y"})


# ---- weather -------------------------------------------------------------------------------------
def load_station_daily(dates: pd.DatetimeIndex) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """{family: date x station daily matrix on a continuous calendar} + station meta (stn, lat, lon).

    Stations without coordinates (hub stations absent from the 기상청 지점 table) are dropped and counted.
    Causal gap fill only (ffill, <=2 days); interpolate() would use D+1 (LOOKAHEAD RISK) and is not used.
    """
    coords = wh.load_station_coords()
    in_cal = dates[[(d.month, d.day) >= FETCH_START and (d.month, d.day) <= FETCH_END for d in dates]]
    hourly = wh.load_hourly(HOURS, in_cal)
    at, tx, diag = wh.daily_max(hourly, MIN_HOURS)
    keep = [c for c in at.columns if c in set(coords.stn)]
    dropped = sorted(set(at.columns) - set(keep))
    mats = {}
    for fam, m in (("at", at), ("tx", tx)):
        mats[fam] = m[keep].reindex(dates).ffill(limit=2)
    meta = coords.set_index("stn").loc[keep].reset_index()
    meta.attrs["dropped_no_coords"] = dropped
    meta.attrs["window_diag"] = diag
    return mats, meta


def haversine_km(lat1, lon1, lat2, lon2):
    return wh.haversine_km(lat1, lon1, lat2, lon2)


def lag(a: np.ndarray, k: int) -> np.ndarray:
    out = np.full_like(a, np.nan, dtype=float)
    out[k:] = a[:-k] if k else a
    return out if k else a.astype(float)


def family_columns(M: np.ndarray, order: np.ndarray) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """Unit-day arrays (T x U) for one weather family from a station matrix (T x S) and each unit's 3 nearest stations.

    Falls back to the 2nd/3rd nearest station when the nearest has a gap that day. Also returns which rank was used.
    """
    T = np.full((M.shape[0], order.shape[0]), np.nan)
    rank_used = np.full(T.shape, -1)
    for r in range(3):
        cand = M[:, order[:, r]]
        take = np.isnan(T) & ~np.isnan(cand)
        T[take] = cand[take]
        rank_used[take] = r
    hot = (T >= HOT_C).astype(np.int64)
    cs = np.cumsum(hot, axis=0)
    reset = np.maximum.accumulate(np.where(hot == 0, cs, 0), axis=0)
    streak = np.where(np.isnan(T), 0, cs - reset)  # consecutive hot days ending at (and including) D0
    lead = np.full_like(T, np.nan)
    lead[:-1] = T[1:]
    cols = {"l0": T, "l1": lag(T, 1), "l2": lag(T, 2), "l3": lag(T, 3),
            "p1": lead,  # LOOKAHEAD RISK: observed D+1 used as a perfect-forecast proxy (variant F only)
            "streak": streak, "streak_d1": lag(streak.astype(float), 1)}
    return cols, rank_used


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    set_seed(SEED)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    d = assign_unit(load_dongs())
    h = map_heat_to_unit(load_heat(), set(d.unit_key))

    # ---- unit table ------------------------------------------------------------------------------
    w = d["pop"].fillna(0).to_numpy()
    d["w"] = np.where(w > 0, w, 1.0)
    d["wlat"], d["wlon"] = d.lat * d.w, d.lon * d.w
    u = d.groupby("unit_key").agg(
        sido=("unit_sido", "first"), sgg=("unit_sgg", "first"), pop=("pop", "sum"), p65=("p65", "sum"),
        wlat=("wlat", "sum"), wlon=("wlon", "sum"), w=("w", "sum"), n_dong=("adm_cd2", "size"),
    ).reset_index()
    u["lat"], u["lon"] = u.wlat / u.w, u.wlon / u.w
    u = u.drop(columns=["wlat", "wlon", "w"]).sort_values("unit_key").reset_index(drop=True)
    u["unit_id"] = np.arange(len(u))
    assert u["pop"].gt(0).all(), "unit with zero population"

    # ---- weather ---------------------------------------------------------------------------------
    cal = pd.date_range(f"{YEARS[0]}-{FETCH_START[0]:02d}-{FETCH_START[1]:02d}",
                        f"{YEARS[-1]}-{FETCH_END[0]:02d}-{FETCH_END[1]:02d}", freq="D")
    mats, smeta = load_station_daily(cal)
    md = cal.month * 100 + cal.day
    season = (md >= SEASON_START[0] * 100 + SEASON_START[1]) & (md <= SEASON_END[0] * 100 + SEASON_END[1])
    cov = mats["at"][season].notna().mean()
    ok = cov[cov >= 0.5].index
    smeta = smeta[smeta.stn.isin(ok)].reset_index(drop=True)
    D = haversine_km(u.lat.to_numpy()[:, None], u.lon.to_numpy()[:, None], smeta.lat.to_numpy()[None], smeta.lon.to_numpy()[None])
    order = np.argsort(D, axis=1)[:, :3]
    u["station_id"] = smeta.stn.to_numpy()[order[:, 0]]
    u["station_km"] = D[np.arange(len(u)), order[:, 0]]
    fam_cols, rank_used = {}, None
    for fam in FAMILIES:
        fam_cols[fam], ru = family_columns(mats[fam][smeta.stn].to_numpy(), order)
        if rank_used is None:
            rank_used = ru

    # ---- long panel ------------------------------------------------------------------------------
    sidx = np.flatnonzero(season)
    nT, nU = len(sidx), len(u)
    P = pd.DataFrame({"unit_id": np.tile(u.unit_id.to_numpy(), nT), "date": np.repeat(cal[sidx].to_numpy(), nU)})
    pick = lambda a: a[sidx].reshape(-1)  # noqa: E731  (row-major: date outer, unit inner -> matches tile/repeat)
    # LOOKAHEAD RISK: *_l0 / *_streak (incl. D0) / *_3d are same-day observed values; at issue time D0 must be a
    # forecast. D-1-only columns (*_l1..l3, *_streak_d1, *_3d_d1) carry no such risk. *_p1 = observed D+1 as a
    # PERFECT forecast proxy for variant F (optimistic for every F method including the rule). Variant O never uses them.
    for fam in FAMILIES:
        c = fam_cols[fam]
        for k in ("l0", "l1", "l2", "l3", "p1", "streak", "streak_d1"):
            P[f"{fam}_{k}"] = pick(c[k])
        P[f"{fam}_3d"] = P[[f"{fam}_l0", f"{fam}_l1", f"{fam}_l2"]].mean(axis=1, skipna=False)  # LOOKAHEAD RISK (uses D0)
        P[f"{fam}_3d_d1"] = P[[f"{fam}_l1", f"{fam}_l2", f"{fam}_l3"]].mean(axis=1, skipna=False)
    P["wx_rank_used"] = pick(rank_used)
    P["doy"] = P.date.dt.dayofyear
    P["dow"] = P.date.dt.dayofweek
    P["weekend"] = (P.dow >= 5).astype(int)
    P["year"] = P.date.dt.year

    # ---- population / 65+ by season year (Dec of year-1; fallback = 2026 snapshot) ----------------------
    py = load_pop_by_year(set(u.unit_key))
    uy = u[["unit_id", "unit_key", "pop", "p65"]].merge(pd.DataFrame({"year": YEARS}), how="cross")
    uy = uy.merge(py, on=["unit_key", "year"], how="left")
    uy["pop_from_registry"] = uy.pop_y.notna()
    uy["pop_y"] = uy.pop_y.fillna(uy["pop"]).clip(lower=1)
    uy["p65_y"] = uy.p65_y.fillna(uy.p65).clip(lower=0).clip(upper=uy.pop_y)
    uy["old_share"] = uy.p65_y / uy.pop_y
    uy["lpop"] = np.log(uy.pop_y)
    ustat = u[["unit_id", "unit_key", "sido", "sgg", "lat", "lon", "station_id", "station_km"]]
    P = P.merge(ustat, on="unit_id").merge(
        uy[["unit_id", "year", "pop_y", "old_share", "lpop", "pop_from_registry"]].rename(columns={"pop_y": "pop"}),
        on=["unit_id", "year"])

    # B1 = 2026 특보 criterion: 일최고체감온도 >= 33 C on 2+ consecutive days. at_* = 체감온도; tx_* = Tmax sensitivity.
    # F (value(D) + forecast proxy value(D+1)): 2 consecutive hot days = D hot AND (D-1 hot OR D+1 hot).
    # O (obs up to D-1 only): D-1 hot AND D-2 hot.   B1S = single-day sensitivity.  NaN compares False.
    for fam in FAMILIES:
        pre = "" if fam == "at" else "tx_"
        l0, l1, l2, p1 = (P[f"{fam}_{k}"] for k in ("l0", "l1", "l2", "p1"))
        P[f"{pre}b1_F"] = ((l0 >= HOT_C) & ((l1 >= HOT_C) | (p1 >= HOT_C))).astype(int)
        P[f"{pre}b1s_F"] = (l0 >= HOT_C).astype(int)
        P[f"{pre}b1_O"] = ((l1 >= HOT_C) & (l2 >= HOT_C)).astype(int)
        P[f"{pre}b1s_O"] = (l1 >= HOT_C).astype(int)

    # ---- labels ----------------------------------------------------------------------------------
    hm = h[h.status == "matched"].merge(u[["unit_key", "unit_id"]], on="unit_key")
    cnt = hm.groupby(["unit_id", "date"]).size().rename("y").reset_index()
    P = P.merge(cnt, on=["unit_id", "date"], how="left")
    P["y"] = P["y"].fillna(0).astype(int)
    in_season_cases = int(P.y.sum())

    # ---- historical rate: EXPANDING, years strictly before the row's year -------------------------
    # LEAKAGE RISK if computed over all years; here each row only sees years < its own year, so the same
    # column is valid for training rows and for any test year T (rows of T use only <= T-1).
    yc = P.groupby(["unit_id", "year"]).y.agg(["sum", "size"]).reset_index()
    yc = yc.sort_values(["unit_id", "year"])
    yc["hist_cases"] = yc.groupby("unit_id")["sum"].cumsum() - yc["sum"]
    yc["hist_days"] = yc.groupby("unit_id")["size"].cumsum() - yc["size"]
    gy = P.groupby("year").y.agg(["sum", "size"]).sort_index()
    g_rate = (gy["sum"].cumsum() - gy["sum"]) / (gy["size"].cumsum() - gy["size"]).replace(0, np.nan)
    yc["g_rate"] = yc.year.map(g_rate)
    K_SHRINK = 130.0  # pseudo-days (~1 season) of national-average prior; empirical-Bayes shrinkage
    yc["hist_rate"] = np.where(
        yc.hist_days > 0, (yc.hist_cases + K_SHRINK * yc.g_rate) / (yc.hist_days + K_SHRINK), np.nan
    )
    P = P.merge(yc[["unit_id", "year", "hist_rate", "hist_cases", "hist_days"]], on=["unit_id", "year"], how="left")

    P = P.sort_values(["date", "unit_id"]).reset_index(drop=True)
    P.to_csv(PANEL_PATH, index=False, float_format="%.6g")

    # ---- crosswalk + coverage ------------------------------------------------------------------------
    cw = build_crosswalk(h, d)
    cw.to_csv(CROSSWALK_PATH, index=False, encoding="utf-8-sig")
    hmd = h.date.dt.month * 100 + h.date.dt.day
    h["in_season"] = (hmd >= SEASON_START[0] * 100 + SEASON_START[1]) & (hmd <= SEASON_END[0] * 100 + SEASON_END[1])
    by_year = {
        int(y): dict(cases=int(len(g)), matched=int((g.status == "matched").sum()),
                     matched_pct=round(100 * float((g.status == "matched").mean()), 3),
                     in_season_matched=int(((g.status == "matched") & g.in_season).sum()))
        for y, g in h.groupby(h.date.dt.year)
    }
    prev_path = OUT_DIR / "prev" / "coverage_report.json"
    prev = json.loads(prev_path.read_text()) if prev_path.is_file() else {}
    miss_year = P.groupby("year").at_l0.apply(lambda s: round(100 * float(s.isna().mean()), 3)).to_dict()
    reg_share = uy.groupby("year").pop_from_registry.mean().round(4).to_dict()
    mt = uy[uy.pop_from_registry]
    ratio = (mt.pop_y / mt["pop"]).describe()
    cov_rep = dict(
        cases_2019_2025_total=int(len(h)),
        cases_matched=int((h.status == "matched").sum()),
        cases_matched_pct=round(100 * float((h.status == "matched").mean()), 3),
        status_counts=h.status.value_counts().to_dict(),
        cases_matched_in_season=in_season_cases,
        cases_matched_outside_season=int(((h.status == "matched") & ~h.in_season).sum()),
        by_year=by_year,
        unmatched_names=cw[cw.status == "unmatched_name"][["heat_sido", "heat_sgg", "n_cases_2019_25"]].to_dict("records"),
        dropped_ambiguous=cw[cw.status == "dropped_ambiguous_city_label"][["heat_sido", "heat_sgg", "n_cases_2019_25"]].to_dict("records"),
        n_units=int(len(u)),
        n_units_never_reported=int((P.groupby("unit_id").y.sum() == 0).sum()),
        panel_rows=int(len(P)),
        panel_rows_missing_at_l0=int(P.at_l0.isna().sum()),
        panel_missing_at_pct=round(100 * float(P.at_l0.isna().mean()), 4),
        panel_missing_at_pct_by_year=miss_year,
        cases_on_rows_missing_at=int(P.loc[P.at_l0.isna(), "y"].sum()),
        wx_fallback_share_rank1plus=round(float((P.wx_rank_used > 0).mean()), 5),
        n_stations_used=int(len(smeta)),
        stations_dropped_no_coords=[int(x) for x in smeta.attrs.get("dropped_no_coords", [])],
        station_km_nearest_median=round(float(u.station_km.median()), 2),
        station_km_nearest_p90=round(float(u.station_km.quantile(0.9)), 2),
        station_km_nearest_max=round(float(u.station_km.max()), 2),
        weather_hours_kst=HOURS, weather_min_valid_hours=MIN_HOURS, weather_window_diag=smeta.attrs.get("window_diag"),
        prev_run=dict(n_stations_used=prev.get("n_stations_used"), station_km_nearest_median=prev.get("station_km_nearest_median"),
                      station_km_nearest_p90=prev.get("station_km_nearest_p90"), station_km_nearest_max=prev.get("station_km_nearest_max"),
                      panel_missing_tmax_pct=prev.get("panel_missing_tmax_pct")),
        pop_missing_dongs=int(d["pop"].isna().sum()),
        pop_registry_matched_share_by_year=reg_share,
        pop_registry_vs_2026_ratio_describe={k: round(float(v), 4) for k, v in ratio.items()},
        pop_units_without_registry_row=sorted(set(u.unit_key) - set(uy[uy.pop_from_registry].unit_key)),
        hist_rate_nan_rows=int(P.hist_rate.isna().sum()),
        notes=[
            "at_* = daily max KMA 여름철 체감온도 over KST hours %s of the nearest ASOS station (then 2nd/3rd nearest if gap)" % HOURS,
            "tx_* = daily max 기온 over the same station-hours (sensitivity); not the official 24h Tmax",
            "gap fill is causal ffill(limit=2)",
            "pop / 65+ = December(year-1) 주민등록 snapshot per season year; unmatched units fall back to the 2026-09 snapshot",
            "season window is May 20 - Sep 25 for every year (heat file ends 2025-09-25)",
        ],
    )
    dump_json(cov_rep, COVERAGE_PATH)
    logger.info("panel rows=%d units=%d cases(in-season, matched)=%d matched%%=%.2f", len(P), len(u), in_season_cases,
                cov_rep["cases_matched_pct"])
    logger.info("at_l0 missing rows=%d; stations=%d; nearest km median=%.1f max=%.1f", P.at_l0.isna().sum(), len(smeta),
                u.station_km.median(), u.station_km.max())


if __name__ == "__main__":
    main()
