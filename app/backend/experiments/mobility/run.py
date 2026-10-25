"""Run the mobility module end to end. Writes out/*.csv, out/results.json.

uv run --project /Users/chloekang/kcf/app/backend python experiments/mobility/run.py
Backtests/optimisations here are hypotheses, not proofs.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.sparse as sp
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib as L  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("mobility.run")

STOP_DATA_MIN = 3.0  # stops per 1000 residents 65+; 시도 averages run 7.8 (Seoul, lowest) to 67; below 3.0 the file is judged incomplete for that 시군구
NS = [500, 1000, 2000, 5000, 10000]
N_HEAD = 2000
N_RANDOM = 200
DENSITY_M = 300.0


def read_kr(path: Path) -> pd.DataFrame:
    """Read a Korean CSV: utf-8-sig first, cp949 on decode failure."""
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="cp949")


def sido_short(s: str) -> str:
    """'충청남도' -> '충남', '서울특별시' -> '서울' (matches the special-transport table)."""
    special = {"충청북도": "충북", "충청남도": "충남", "전라북도": "전북", "전라남도": "전남", "경상북도": "경북", "경상남도": "경남"}
    return special.get(s, s[:2])


def bincount_share(idx: np.ndarray, w: np.ndarray, mask: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    tot = np.bincount(idx, weights=w, minlength=n)
    cov = np.bincount(idx, weights=w * mask, minlength=n)
    return tot, cov


def main() -> None:
    t0 = time.time()
    L.OUT.mkdir(exist_ok=True)
    rng = np.random.default_rng(L.SEED)
    res: dict = {"seed": L.SEED, "walk_speed_ms": L.WALK_SPEED_MS, "walk_min": L.WALK_MIN, "detour": L.DETOUR,
                 "radius_m": L.radius_m(), "detour_band": L.DETOUR_BAND}

    # ---------------------------------------------------------------- inputs
    dongs, geoms = L.load_dong_geoms()
    pop = L.load_pop()
    dongs = dongs.merge(pop, on="adm_cd2", how="left")
    no_pop = dongs[["pop", "p65", "p70"]].isna().any(axis=1)
    res["dongs_polygons"] = int(len(dongs)); res["dongs_polygon_without_pop_row"] = int(no_pop.sum())
    res["pop_rows_without_polygon"] = int((~pop.adm_cd2.isin(dongs.adm_cd2)).sum())
    res["p65_without_polygon"] = float(pop[~pop.adm_cd2.isin(dongs.adm_cd2)].p65.sum())
    dongs[["pop", "p65", "p70"]] = dongs[["pop", "p65", "p70"]].fillna(0.0)
    res["p65_total_in_polygons"] = float(dongs.p65.sum())
    hw, unit_tbl = L.heat_unit_weights(dongs)
    dongs = dongs.merge(hw.reset_index(), on="adm_cd2", how="left")
    res["heat_units"] = int(len(unit_tbl)); res["dongs_without_heat_unit_weight"] = int(dongs.heat_missing.sum())
    res["p65_without_heat_weight_share"] = float(dongs.loc[dongs.heat_missing, "p65"].sum() / dongs.p65.sum())

    gwangju_gu = {"동구", "서구", "남구", "북구", "광산구"}
    dongs["sido_legacy"] = np.where((dongs.sido == "전남광주통합특별시") & dongs.sgg.isin(gwangju_gu), "광주광역시",
                                    np.where(dongs.sido == "전남광주통합특별시", "전라남도", dongs.sido))
    pts = L.sample_points(dongs, geoms)
    res["n_points"] = int(len(pts.x))
    res["p65_pts_share_in_coarse_cells_gt200m"] = float(pts.w65[pts.cell_m > 200].sum() / pts.w65.sum())
    stops, rep = L.load_stops(geoms)
    res["stops"] = rep | {"clean": int(len(stops))}
    log.info("points=%d stops=%d (%.0fs)", len(pts.x), len(stops), time.time() - t0)

    heat_pt = dongs.heat_w.to_numpy()[pts.dong_idx]
    w_heat = pts.w65 * heat_pt
    w_pop = pts.w65

    # ---------------------------------------------------------------- descriptive access (all stops)
    d_all = L.nearest_dist(pts.x, pts.y, stops.x.to_numpy(), stops.y.to_numpy())
    cov = {}
    for tag, det in (("mid", L.DETOUR), ("hi_acc", L.DETOUR_BAND[0]), ("lo_acc", L.DETOUR_BAND[1])):
        cov[tag] = d_all <= L.radius_m(det)
    res["national_access"] = {
        k: float(L.share(v, w_pop)) for k, v in cov.items()
    } | {"p65_weighted_median_nearest_stop_m": float(np.sort(d_all)[np.searchsorted(np.cumsum(w_pop[np.argsort(d_all)]), 0.5 * w_pop.sum())])}
    n = len(dongs)
    di = pts.dong_idx
    fine = pts.cell_m <= 200
    res["national_access_fine_cells_le200m"] = {"p65_share_of_total": float(pts.w65[fine].sum() / pts.w65.sum()),
                                                "covered_share": float(L.share(cov["mid"][fine], w_pop[fine]))}
    res["national_access_coarse_cells_gt200m_covered_share"] = float(L.share(cov["mid"][~fine], w_pop[~fine]))
    dongs["coarse_w65"] = np.bincount(di, weights=w_pop * (~fine), minlength=n)
    tot65, c_mid = bincount_share(di, w_pop, cov["mid"], n)
    _, c_hi = bincount_share(di, w_pop, cov["hi_acc"], n)
    _, c_lo = bincount_share(di, w_pop, cov["lo_acc"], n)
    _, c_mid70 = bincount_share(di, pts.w70, cov["mid"], n)
    with np.errstate(invalid="ignore", divide="ignore"):
        dongs["share_65_walk5"] = np.where(tot65 > 0, c_mid / tot65, np.nan)
        dongs["share_65_walk5_detour1.5"] = np.where(tot65 > 0, c_lo / tot65, np.nan)
        dongs["share_65_walk5_detour1.15"] = np.where(tot65 > 0, c_hi / tot65, np.nan)
    dongs["uncovered_65"] = tot65 - c_mid
    dongs["cell_m"] = pd.Series(pts.cell_m).groupby(di).median().reindex(range(n)).to_numpy()
    dongs["n_stops_in_dong"] = np.bincount(stops.dong_idx[stops.dong_idx >= 0], minlength=n)
    dongs["flag"] = np.select(
        [dongs.p65 < L.MIN_P65_DONG, dongs.cell_m > 200],
        ["자료 부족(65+<100)", "격자 거침(>200m): 하한 추정"], default="")
    dcols = ["adm_cd2", "sido", "sgg", "dong", "pop", "p65", "share_65_walk5", "share_65_walk5_detour1.5",
             "share_65_walk5_detour1.15", "uncovered_65", "n_stops_in_dong", "heat_pct", "unit_key", "flag"]
    dongs[dcols].to_csv(L.OUT / "dong_access.csv", index=False, encoding="utf-8-sig", float_format="%.4f")

    g = dongs.assign(c_mid=c_mid, c_hi=c_hi, c_lo=c_lo, tot=tot65).groupby(["sido", "sgg"], as_index=False).agg(
        sido_legacy=("sido_legacy", "first"), p65=("p65", "sum"), n_dong=("adm_cd2", "size"), n_stops=("n_stops_in_dong", "sum"), coarse_w65=("coarse_w65", "sum"),
        c_mid=("c_mid", "sum"), c_hi=("c_hi", "sum"), c_lo=("c_lo", "sum"), tot=("tot", "sum"),
        n_dong_low_n=("flag", lambda s: int(s.str.startswith("자료").sum())))
    g["share_65_walk5"] = g.c_mid / g.tot; g["share_lo"] = g.c_lo / g.tot; g["share_hi"] = g.c_hi / g.tot
    g["uncovered_65"] = g.tot - g.c_mid
    g["stops_per_1000_p65"] = g.n_stops / g.p65 * 1000
    g["share_p65_in_coarse_cells"] = g.coarse_w65 / g.p65
    g["flag"] = np.select([g.stops_per_1000_p65 < STOP_DATA_MIN, g.share_p65_in_coarse_cells > 0.5, g.p65 < 1000],
                          ["정류장 자료 누락 의심", "농촌형: 균등분포 가정 하한", "자료 부족(65+<1000)"], default="")
    g.drop(columns=["c_mid", "c_hi", "c_lo", "tot", "coarse_w65"]).rename(columns={"share_lo": "share_detour1.5", "share_hi": "share_detour1.15"}).to_csv(
        L.OUT / "sigungu_access.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    res["sigungu_flag_stop_data_missing"] = {"n": int((g.flag == "정류장 자료 누락 의심").sum()),
        "p65_share": float(g.loc[g.flag == "정류장 자료 누락 의심", "p65"].sum() / g.p65.sum()),
        "names": g.loc[g.flag == "정류장 자료 누락 의심", "sgg"].tolist()}
    res["sigungu_flag_rural_uniform_lower_bound"] = {"n": int((g.flag == "농촌형: 균등분포 가정 하한").sum()),
        "p65_share": float(g.loc[g.flag == "농촌형: 균등분포 가정 하한", "p65"].sum() / g.p65.sum())}
    ok_ = g[g.flag == ""]
    res["national_share_excluding_flagged"] = float(1 - ok_.uncovered_65.sum() / ok_.p65.sum())
    s = g[g.flag == ""]
    res["sigungu_n"] = int(len(g)); res["sigungu_n_unflagged"] = int(len(s))
    res["sigungu_share_quantiles"] = {str(q): float(s.share_65_walk5.quantile(q)) for q in (0, .05, .25, .5, .75, .95, 1)}
    res["sigungu_worst5"] = s.nsmallest(5, "share_65_walk5")[["sido", "sgg", "share_65_walk5", "p65"]].to_dict("records")
    res["dong_n_p65_ge_100"] = int((dongs.p65 >= 100).sum())
    res["dong_share_lt50_p65_share"] = float(dongs.loc[dongs.share_65_walk5 < 0.5, "p65"].sum() / dongs.p65.sum())
    sido = g.groupby("sido_legacy").agg(c=("uncovered_65", "sum"), p=("p65", "sum"), st=("n_stops", "sum"))
    sido["share_65_walk5"] = 1 - sido.c / sido.p
    sido["stops_per_1000_p65"] = sido.st / sido.p * 1000

    # ---------------------------------------------------------------- siting at equal N
    cand = stops.drop_duplicates("loc_key").reset_index(drop=True)
    cx, cy = cand.x.to_numpy(), cand.y.to_numpy()
    r = L.radius_m()
    inc = L.incidence(cx, cy, pts.x, pts.y, r)
    log.info("incidence nnz=%d cand=%d (%.0fs)", inc.nnz, len(cand), time.time() - t0)
    nmax = max(NS)
    picks_heat, gains_heat = L.lazy_greedy(inc, w_heat, nmax)
    picks_pop, _ = L.lazy_greedy(inc, w_pop, nmax)
    score_pop = np.asarray(inc @ w_pop).ravel()
    score_heat = np.asarray(inc @ w_heat).ravel()
    order_static = np.lexsort((rng.random(len(cand)), -score_pop))[:nmax]  # random tie-break
    dens = np.array([len(x) for x in cKDTree(np.column_stack([cx, cy])).query_ball_point(np.column_stack([cx, cy]), DENSITY_M)])
    order_dens = np.lexsort((rng.random(len(cand)), -dens))[:nmax]
    quart = (cand.dong_idx.map(lambda i: dongs.heat_pct.iat[i] if i >= 0 else np.nan)).to_numpy()

    def ev(idx: np.ndarray, incm: sp.csr_matrix = inc) -> dict:
        m = L.covered_mask(incm, idx)
        q = quart[idx]
        return dict(heat_weighted=L.share(m, w_heat), p65=L.share(m, w_pop),
                    picks_in_top_heat_quartile=float(np.nanmean(q >= 0.75)) if len(idx) else float("nan"))

    methods = {"ours_greedy_heat": picks_heat, "pop_greedy": picks_pop, "top_n_by_p65_static": order_static,
               "stop_density_proxy": order_dens}
    table = []
    for N in NS:
        for name, ordr in methods.items():
            table.append(dict(N=N, method=name, **ev(ordr[:N])))
        rd = []
        for _ in range(N_RANDOM):
            rd.append(ev(rng.choice(len(cand), N, replace=False)))
        rd = pd.DataFrame(rd)
        table.append(dict(N=N, method="random_mean", heat_weighted=rd.heat_weighted.mean(), p65=rd.p65.mean(),
                          picks_in_top_heat_quartile=rd.picks_in_top_heat_quartile.mean()))
        for k in ("heat_weighted", "p65"):
            table[-1][f"{k}_lo"] = float(rd[k].quantile(0.025)); table[-1][f"{k}_hi"] = float(rd[k].quantile(0.975))
    # equal-budget variant with a per-시도 quota proportional to 65+ (spreads picks; nested picks are lost)
    cs = np.array([sido_short(dongs.sido_legacy.iat[i]) if i >= 0 else "" for i in cand.dong_idx])
    p65s = dongs.groupby(dongs.sido_legacy.map(sido_short)).p65.sum()
    for N in NS:
        q = p65s / p65s.sum() * N; qi = np.floor(q).astype(int)
        for k in (q - qi).sort_values(ascending=False).index[: N - qi.sum()]:
            qi[k] += 1
        sel_idx = []
        for k, nq in qi.items():
            rows = np.flatnonzero(cs == k)
            if nq > 0 and len(rows):
                pk, _ = L.lazy_greedy(inc[rows], w_heat, int(nq)); sel_idx.append(rows[pk])
        table.append(dict(N=N, method="ours_greedy_heat_sido_quota", **ev(np.concatenate(sel_idx))))
    tab = pd.DataFrame(table)
    tab.to_csv(L.OUT / "siting_equal_budget.csv", index=False, float_format="%.5f")
    res["siting_table"] = tab.to_dict("records")
    res["siting_note"] = ("Optimiser is scored on the objective it maximises (in-sample by construction); random band = "
                          f"{N_RANDOM} draws, 2.5-97.5 percentile. N_head={N_HEAD}.")
    res["candidates"] = int(len(cand)); res["p65_covered_by_any_stop"] = float(L.share(cov["mid"], w_pop))

    # unweighted-heat sensitivity (w=1): ours == pop_greedy by definition; report the cost of heat weighting
    # radius sensitivity: pick at 1.3, evaluate at other detours
    sens = []
    for det in L.DETOUR_BAND:
        inc2 = L.incidence(cx, cy, pts.x, pts.y, L.radius_m(det))
        for name, ordr in methods.items():
            m = L.covered_mask(inc2, ordr[:N_HEAD])
            sens.append(dict(detour=det, method=name, heat_weighted=L.share(m, w_heat), p65=L.share(m, w_pop)))
        rd = [L.share(L.covered_mask(inc2, rng.choice(len(cand), N_HEAD, replace=False)), w_heat) for _ in range(50)]
        sens.append(dict(detour=det, method="random_mean", heat_weighted=float(np.mean(rd)), p65=np.nan))
    res["detour_sensitivity_N2000_pick_at_1.3"] = sens

    # ---------------------------------------------------------------- Seoul: building-weighted network re-evaluation
    # Independent of the 1.3 detour AND of the uniform-in-polygon assumption: walk4's Seoul demand points
    # (OSM building footprint x levels weighted 65+, snapped to the OSM pedestrian graph).
    z = np.load(L.WALK4_OUT / "graph.npz"); A = sp.csr_matrix((z["data"], z["indices"], z["indptr"]), shape=tuple(z["shape"]))
    xy = np.load(L.WALK4_OUT / "graph_xy.npz"); gx, gy, lcc = xy["x"], xy["y"], xy["lcc"]
    lcc_ids = np.flatnonzero(lcc)
    ntree = cKDTree(np.column_stack([gx[lcc_ids], gy[lcc_ids]]))
    dem = pd.read_parquet(L.WALK4_OUT / "demand.parquet", columns=["adm_cd2", "x", "y", "node", "snap", "p65_w_main"])
    dem = dem[dem.p65_w_main > 0].reset_index(drop=True)
    dm = dongs.set_index("adm_cd2")
    dem["heat_w"] = dem.adm_cd2.map(dm.heat_w).fillna(1.0)
    dem["r70"] = dem.adm_cd2.map(dm.p70 / dm.p65.where(dm.p65 > 0)).fillna(0.0)
    dsnap_ok = (dem.snap <= 150.0).to_numpy()
    w65_b = dem.p65_w_main.to_numpy(); wh_b = w65_b * dem.heat_w.to_numpy(); w70_b = w65_b * dem.r70.to_numpy()
    dx, dy, dnode, dsnap = dem.x.to_numpy(), dem.y.to_numpy(), dem.node.to_numpy(), dem.snap.to_numpy()
    seoul_d = (dongs.sido == "서울특별시").to_numpy()
    cand_seoul = np.array([(i >= 0) and bool(seoul_d[i]) for i in cand.dong_idx])
    cd, cn = ntree.query(np.column_stack([cx[cand_seoul], cy[cand_seoul]]))
    cnode = np.full(len(cand), -1); cnode[np.flatnonzero(cand_seoul)] = lcc_ids[cn]
    csnap = np.full(len(cand), np.inf); csnap[np.flatnonzero(cand_seoul)] = cd
    res["seoul_network"] = {"demand_points": int(len(dem)), "p65_demand_total": float(w65_b.sum()),
                            "snap_gt150m_p65_share": float(w65_b[~dsnap_ok].sum() / w65_b.sum())}

    def net_cov(idx: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        idx = idx[cand_seoul[idx] & (csnap[idx] <= 150.0)]
        if len(idx) == 0:
            return np.zeros(len(dem), bool), np.full(len(dem), np.inf)
        dd = dijkstra(A, directed=True, indices=np.unique(cnode[idx]), limit=L.NET_LIMIT_M, min_only=True)
        nd = dd[dnode] + dsnap
        return (nd <= L.NET_LIMIT_M) & dsnap_ok, nd

    def str_cov(idx: np.ndarray, r_: float) -> np.ndarray:
        idx = idx[cand_seoul[idx]]
        return L.nearest_dist(dx, dy, cx[idx], cy[idx]) <= r_

    all_seoul = np.flatnonzero(cand_seoul)
    cov_net_all, nd_all = net_cov(all_seoul)
    ds_all = L.nearest_dist(dx, dy, cx[all_seoul], cy[all_seoul])
    sel = (ds_all >= 50) & (ds_all <= 185) & np.isfinite(nd_all) & (nd_all < 1e8)
    res["seoul_network"]["median_net_over_straight_ratio_nearest_stop_50_185m"] = float(np.median(nd_all[sel] / ds_all[sel]))
    res["seoul_network"]["ratio_iqr"] = [float(np.percentile(nd_all[sel] / ds_all[sel], q)) for q in (25, 75)]
    cov_str_all = ds_all <= r
    res["seoul_network"]["all_stops_building_weighted"] = {"straight_1.3": L.share(cov_str_all, w65_b), "network_240m": L.share(cov_net_all, w65_b),
                                                           "straight_1.5": L.share(ds_all <= L.radius_m(1.5), w65_b)}
    su = seoul_d[pts.dong_idx]
    res["seoul_network"]["all_stops_uniform_polygon_straight_1.3"] = L.share(cov["mid"][su], w_pop[su])
    seo = []
    for name, ordr in methods.items():
        idx = ordr[:N_HEAD]; idx_s = idx[cand_seoul[idx]]
        seo.append(dict(method=name, n_seoul_picks=int(len(idx_s)), straight_heat=L.share(str_cov(idx_s, r), wh_b),
                        network_heat=L.share(net_cov(idx_s)[0], wh_b), straight_p65=L.share(str_cov(idx_s, r), w65_b),
                        network_p65=L.share(net_cov(idx_s)[0], w65_b)))
    rr = []
    for _ in range(50):
        idx_s = (lambda a: a[cand_seoul[a]])(rng.choice(len(cand), N_HEAD, replace=False))
        rr.append((L.share(str_cov(idx_s, r), wh_b), L.share(net_cov(idx_s)[0], wh_b), len(idx_s)))
    rr = np.array(rr)
    seo.append(dict(method="random_mean", n_seoul_picks=float(rr[:, 2].mean()), straight_heat=rr[:, 0].mean(), network_heat=rr[:, 1].mean(),
                    straight_p65=np.nan, network_p65=np.nan))
    res["seoul_network"]["methods_N2000_nationwide_picks_restricted_to_seoul"] = seo
    pd.DataFrame(seo).to_csv(L.OUT / "seoul_network_check.csv", index=False, float_format="%.5f")

    # ---------------------------------------------------------------- Seoul outcome check (70+ pedestrian 119 calls by 구)
    em = pd.read_csv(L.OUT / "ems_seoul_2024_slim.csv")
    old = em[em.ptntAge.isin(["70~79세", "80세 이상"])]
    ped = old[old.ptntOccrTyCd1Nm == "기타 보행자"].groupby("egrcSiggCdNm").size().rename("ped70_calls")
    road = old[old.ruptOccrPlcCdNm.isin(["도로", "도로외교통지역"])].groupby("egrcSiggCdNm").size().rename("road70_calls")
    gu_of = dem.adm_cd2.map(dm.sgg)
    b = pd.DataFrame({"gu": gu_of, "w70": w70_b, "c_str": w70_b * cov_str_all, "c_net": w70_b * cov_net_all, "w70n": w70_b * dsnap_ok})
    gu = b.groupby("gu").sum()
    # uniform-polygon variant (nationwide method) for comparison
    ug = pd.DataFrame({"gu": dongs.sgg.to_numpy()[pts.dong_idx][su], "w": pts.w70[su], "c": pts.w70[su] * cov["mid"][su]}).groupby("gu").sum()
    gu["unc70_building_straight"] = 1 - gu.c_str / gu.w70
    gu["unc70_building_network"] = 1 - gu.c_net / gu.w70n
    gu["unc70_uniform_straight"] = 1 - ug.c / ug.w
    gu["p70"] = dongs[seoul_d].groupby("sgg").p70.sum()
    gu["area_km2"] = dongs[seoul_d].groupby("sgg").area_m2.sum() / 1e6
    gu = gu.join(ped).join(road).fillna({"ped70_calls": 0, "road70_calls": 0})
    gu["ped70_per1000"] = gu.ped70_calls / gu.p70 * 1000
    gu["road70_per1000"] = gu.road70_calls / gu.p70 * 1000
    gu["dens70_per_km2"] = gu.p70 / gu.area_km2
    gu.to_csv(L.OUT / "seoul_gu_outcome.csv", encoding="utf-8-sig", float_format="%.5f")
    oc = {"n_gu": int(len(gu)), "ped70_calls_total": int(gu.ped70_calls.sum()), "road70_calls_total": int(gu.road70_calls.sum()),
          "min_detectable_rho_n25_80pct_power": L.min_detectable_rho(len(gu))}
    for k in ("unc70_building_straight", "unc70_building_network", "unc70_uniform_straight"):
        oc[f"range_{k}"] = [float(gu[k].min()), float(gu[k].max())]
        for yname in ("ped70_per1000", "road70_per1000", "ped70_calls"):
            oc[f"{yname}~{k}"] = L.spearman_ci(gu[k].to_numpy(), gu[yname].to_numpy())
        oc[f"confound_{k}~dens70"] = L.spearman_ci(gu.dens70_per_km2.to_numpy(), gu[k].to_numpy())
    oc["confound_ped70_per1000~dens70"] = L.spearman_ci(gu.dens70_per_km2.to_numpy(), gu.ped70_per1000.to_numpy())
    res["outcome_seoul"] = oc

    # ---------------------------------------------------------------- 시도 저상버스 / 특별교통수단 (n=17, descriptive)
    lf = read_kr(L.LOWFLOOR_CSV); lf.columns = ["sido_full", "lowfloor_n", "lowfloor_rate"]
    lf["sido"] = lf.sido_full.map(sido_short)
    vu = read_kr(L.VULN_CSV)
    vu["sido"] = vu["지역"].map(sido_short)
    vs = vu.groupby("sido").agg(pop=("총인구", "sum"), vuln=("교통약자 전체", "sum"), elderly=("고령자", "sum")).reset_index()
    vs["vuln_ratio"] = vs.vuln / vs["pop"]; vs["elderly_ratio"] = vs.elderly / vs["pop"]
    sp_ = read_kr(L.SPECIAL_CSV); sp_.columns = ["sido", "pop_s", "disabled", "legal", "operating"]
    t = lf.merge(vs, on="sido").merge(sp_, on="sido").merge(sido.reset_index().assign(sido=lambda d: d.sido_legacy.map(sido_short)).drop(columns="sido_legacy"), on="sido")
    assert len(t) == 17, len(t)
    t["fleet_est"] = t.lowfloor_n / (t.lowfloor_rate / 100)
    pooled = t.lowfloor_n.sum() / t.fleet_est.sum() * 100
    t["lowfloor_per10k_elderly"] = t.lowfloor_n / t.elderly * 1e4
    t["gap_to_pooled_rate_buses"] = np.maximum(0, (pooled - t.lowfloor_rate) / 100 * t.fleet_est)
    t["special_ratio"] = t.operating / t.legal
    t["special_shortfall"] = np.maximum(0, t.legal - t.operating)
    t.to_csv(L.OUT / "sido_lowfloor_gap.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    res["sido"] = {
        "n": 17, "pooled_lowfloor_rate_2021": float(pooled),
        "lowfloor_rate~vuln_ratio": L.spearman_ci(t.vuln_ratio.to_numpy(), t.lowfloor_rate.to_numpy()),
        "lowfloor_rate~elderly_ratio": L.spearman_ci(t.elderly_ratio.to_numpy(), t.lowfloor_rate.to_numpy()),
        "lowfloor_per10k_elderly~elderly_ratio": L.spearman_ci(t.elderly_ratio.to_numpy(), t.lowfloor_per10k_elderly.to_numpy()),
        "lowfloor_rate~stop_walk5_share": L.spearman_ci(t.share_65_walk5.to_numpy(), t.lowfloor_rate.to_numpy()),
        "top5_gap_buses": t.nlargest(5, "gap_to_pooled_rate_buses")[["sido", "lowfloor_rate", "gap_to_pooled_rate_buses"]].to_dict("records"),
        "special_under_legal": t[t.operating < t.legal][["sido", "legal", "operating"]].to_dict("records"),
        "special_shortfall_total": int(t.special_shortfall.sum()),
        "note": "lowfloor 2021, special transport 2024, pop 교통약자 year per file; 17 rows, no inference beyond rank correlation",
    }

    # ---------------------------------------------------------------- map-ready stop table
    rank_h = np.full(len(cand), np.nan); rank_h[picks_heat] = np.arange(1, len(picks_heat) + 1)
    gain_h = np.full(len(cand), np.nan); gain_h[picks_heat] = gains_heat
    cand_tbl = pd.DataFrame({"loc_key": cand.loc_key, "score": score_heat / score_heat.max() * 100, "p65_within_walk": score_pop,
                             "greedy_rank": rank_h, "marginal_gain_heat_weighted": gain_h})
    out = stops.merge(cand_tbl, on="loc_key", how="left")
    out["recommended"] = (out.greedy_rank <= N_HEAD)
    out["sido"] = out.dong_idx.map(lambda i: dongs.sido.iat[i] if i >= 0 else "")
    out["sgg"] = out.dong_idx.map(lambda i: dongs.sgg.iat[i] if i >= 0 else "")
    out["dong"] = out.dong_idx.map(lambda i: dongs.dong.iat[i] if i >= 0 else "")
    out = out.rename(columns={"stop_id": "stop_id"})
    cols = ["stop_id", "name", "lat", "lon", "sido", "sgg", "dong", "score", "p65_within_walk", "greedy_rank", "recommended"]
    out[cols].to_csv(L.OUT / "stops_map.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    out[out.recommended][cols + ["marginal_gain_heat_weighted"]].sort_values("greedy_rank").to_csv(
        L.OUT / "recommended_top2000.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    res["recommended_by_sido_N2000"] = out[out.recommended].sido.value_counts().to_dict()
    res["runtime_s"] = round(time.time() - t0, 1)
    with open(L.OUT / "results.json", "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1, default=float)
    np.savez_compressed(L.OUT / "curves.npz", gains_heat=gains_heat)
    log.info("done %.0fs", time.time() - t0)


if __name__ == "__main__":
    main()
