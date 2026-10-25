"""Build walk4 inputs: densified pedestrian graph (12 m spacing), AED sites, demand points. Writes out/inputs.npz etc.

Why densify: osmnx 'simplify' keeps only intersections as nodes; snapping to them alone puts buildings up to
~100 m from their node, which is ~70% of a 144 m radius. Interpolating virtual nodes along true edge geometry
bounds the snap error by the node spacing (+ perpendicular offset, which is kept as an explicit leg).
"""
from __future__ import annotations

import logging
import re

import geopandas as gpd
import numpy as np
import osmnx as ox
import pandas as pd
import scipy.sparse as sp
import shapely
from scipy.spatial import cKDTree

import w4lib as L

log = logging.getLogger("walk4.build")
STEP = 12.0
RES_TAGS = {"apartments", "residential", "house", "detached", "semidetached_house", "terrace", "dormitory",
            "bungalow", "houseboat", "static_caravan"}


def build_graph() -> dict:
    G = ox.load_graphml(L.OSM / "seoul_walk.graphml")
    nodes, edges = ox.graph_to_gdfs(G)
    nodes = nodes.to_crs(L.CRS_M); edges = edges.reset_index().to_crs(L.CRS_M)
    nid = {n: i for i, n in enumerate(nodes.index)}
    u = edges["u"].map(nid).to_numpy(); v = edges["v"].map(nid).to_numpy()
    geom = edges.geometry.to_numpy()
    length = edges["length"].astype(float).to_numpy()
    flip = u > v
    geom = np.where(flip, shapely.reverse(geom), geom)
    u, v = np.where(flip, v, u), np.where(flip, u, v)
    e = pd.DataFrame({"u": u, "v": v, "len": np.round(length, 1)}); e["i"] = np.arange(len(e))
    keep = e.drop_duplicates(["u", "v", "len"]).i.to_numpy()
    keep = keep[u[keep] != v[keep]]
    u, v, geom, length = u[keep], v[keep], geom[keep], length[keep]
    length = np.maximum(length, 0.5)
    nseg = np.maximum(1, np.ceil(length / STEP).astype(int))
    n0 = len(nodes)
    n_new = int((nseg - 1).sum())
    log.info("orig nodes %d, undirected edges %d, virtual nodes %d", n0, len(u), n_new)
    # virtual node coords
    eid = np.repeat(np.arange(len(u)), nseg - 1)
    start = np.cumsum(nseg - 1) - (nseg - 1)
    k = np.arange(n_new) - np.repeat(start, nseg - 1) + 1  # 1..nseg-1
    frac = k / nseg[eid]
    pts = shapely.line_interpolate_point(geom[eid], frac, normalized=True)
    vx = shapely.get_x(pts); vy = shapely.get_y(pts)
    # chain edges: u -> v1 -> ... -> v(nseg-1) -> v
    seg_len = length / nseg
    a = np.empty(0, int); rows_a, rows_b, rows_w = [], [], []
    # single-segment edges
    one = nseg == 1
    rows_a.append(u[one]); rows_b.append(v[one]); rows_w.append(seg_len[one])
    multi = ~one
    vid = n0 + np.arange(n_new)
    first = np.where(multi)[0]
    # u -> first virtual
    rows_a.append(u[first]); rows_b.append(n0 + start[first]); rows_w.append(seg_len[first])
    # last virtual -> v
    last = n0 + start[first] + nseg[first] - 2
    rows_a.append(last); rows_b.append(v[first]); rows_w.append(seg_len[first])
    # virtual chain
    same = np.arange(n_new)
    nxt_ok = (k < nseg[eid] - 1)
    rows_a.append(vid[nxt_ok]); rows_b.append(vid[nxt_ok] + 1); rows_w.append(seg_len[eid][nxt_ok])
    A_ = np.concatenate(rows_a); B_ = np.concatenate(rows_b); W_ = np.concatenate(rows_w)
    n = n0 + n_new
    df = pd.DataFrame({"a": np.r_[A_, B_], "b": np.r_[B_, A_], "w": np.r_[W_, W_]}).groupby(["a", "b"], sort=False).w.min().reset_index()
    A = sp.csr_matrix((df.w.to_numpy(), (df.a.to_numpy(), df.b.to_numpy())), shape=(n, n))
    x = np.r_[nodes.geometry.x.to_numpy(), vx]; y = np.r_[nodes.geometry.y.to_numpy(), vy]
    lcc = L.largest_component(A)
    log.info("LCC share of nodes %.4f (%d)", lcc.mean(), lcc.sum())
    return {"A": A, "x": x, "y": y, "lcc": lcc, "n_orig": n0, "total_len_km": float(length.sum() / 1000)}


def load_aed_sites(tree, x, y, lcc_idx) -> pd.DataFrame:
    import sys
    sys.path.insert(0, str(L.Path(__file__).parent.parent))
    from aed import aed_lib as AL
    from pyproj import Transformer
    raw = AL.load_aed(); s = raw[raw.sido == "서울특별시"].reset_index(drop=True).copy()
    for c in ["wgs84Lat", "wgs84Lon"]:
        s[c] = pd.to_numeric(s[c], errors="coerce")
    assert s.wgs84Lat.notna().all() and s.serialSeq.is_unique
    tr = Transformer.from_crs("EPSG:4326", L.CRS_M, always_xy=True)
    s["x"], s["y"] = tr.transform(s.wgs84Lon.to_numpy(), s.wgs84Lat.to_numpy())
    d, j = tree.query(np.c_[s.x, s.y])
    s["node"] = lcc_idx[j]; s["snap"] = d
    return s


def schedules_by_daytype(s: pd.DataFrame) -> dict:
    """open[n,24] bool for wd/sat/sun/hol under three treatments of missing/placeholder weekend hours.
    wd: open at h:30 on >=3 of Mon-Fri. Treatments:
      primary  : placeholder(weekday not 24h but weekend 0000-2400) -> weekday hours; missing weekend/holiday entry -> closed
      upper    : placeholder -> weekday hours; missing weekend/holiday entry -> weekday hours
      asreport : as reported, missing -> closed
    """
    import sys
    sys.path.insert(0, str(L.Path(__file__).parent.parent))
    from aed import aed_lib as AL
    om = {d: AL.open_matrix(s, d) for d in AL.DAYS}
    wd = np.mean([om[d][0] for d in AL.DAYS[:5]], axis=0) >= 0.6
    S = AL.schedules(s)
    ph = S["placeholder"]
    out = {}
    for name in ("primary", "upper", "asreport"):
        o = {"wd": wd}
        for dt in ("sat", "sun", "hol"):
            m, known = om[dt]
            m = m.copy()
            if name != "asreport":
                m[ph] = wd[ph]
            if name == "upper":
                m[~known] = wd[~known]
            o[dt] = m
        out[name] = o
    out["_placeholder_n"] = int(ph.sum())
    return out


def build_demand(tree, x, y, lcc_idx) -> tuple[pd.DataFrame, dict]:
    pop = L.pop_by_dong()
    dg = gpd.read_file(L.DONG_GJ); dg = dg[dg.sido == "11"].to_crs(L.CRS_M)[["adm_cd2", "adm_nm", "geometry"]]
    dg = dg.merge(pop, on="adm_cd2", how="left"); assert dg.pop_tot.notna().all()
    b = gpd.read_parquet(L.OSM / "seoul_buildings.parquet").to_crs(L.CRS_M)
    b = b.reset_index(drop=True)
    area = b.area.to_numpy()
    lev_raw = b["building:levels"].astype(str).str.extract(r"(\d+(?:\.\d+)?)")[0].astype(float)
    lev_raw = lev_raw.where((lev_raw >= 1) & (lev_raw <= 60))
    med = lev_raw.groupby(b.building).median()
    lev = lev_raw.fillna(b.building.map(med)).fillna(1.0).to_numpy()
    cent = gpd.GeoDataFrame({"bid": np.arange(len(b))}, geometry=b.geometry.representative_point(), crs=L.CRS_M)
    j = gpd.sjoin(cent, dg[["adm_cd2", "geometry"]], how="inner", predicate="within")
    j = j.drop_duplicates("bid")
    bb = pd.DataFrame({"bid": j.bid.to_numpy(), "adm_cd2": j.adm_cd2.to_numpy()})
    bb["x"] = cent.geometry.x.to_numpy()[bb.bid]; bb["y"] = cent.geometry.y.to_numpy()[bb.bid]
    bb["tag"] = b.building.to_numpy()[bb.bid]; bb["area"] = area[bb.bid]; bb["lev"] = lev[bb.bid]
    bb["res"] = bb.tag.isin(RES_TAGS); bb["yes"] = bb.tag.eq("yes")
    bb["w_main"] = np.where(bb.res | bb.yes, bb.area * bb.lev, 0.0)      # footprint x levels, residential tags + untagged 'yes'
    bb["w_resonly"] = np.where(bb.res, bb.area * bb.lev, 0.0)             # residential tags only
    bb["w_foot"] = np.where(bb.res | bb.yes, bb.area, 0.0)                # footprint only (levels ignored)
    d, jn = tree.query(np.c_[bb.x, bb.y]); bb["node"] = lcc_idx[jn]; bb["snap"] = d
    # uniform fallback: network nodes (LCC, original+virtual) inside each dong
    nd = gpd.GeoDataFrame({"node": lcc_idx}, geometry=gpd.points_from_xy(x[lcc_idx], y[lcc_idx]), crs=L.CRS_M)
    jn2 = gpd.sjoin(nd, dg[["adm_cd2", "geometry"]], how="inner", predicate="within").drop_duplicates("node")
    un = pd.DataFrame({"node": jn2.node.to_numpy(), "adm_cd2": jn2.adm_cd2.to_numpy()})
    un["x"] = x[un.node]; un["y"] = y[un.node]; un["snap"] = 0.0
    for c in ("w_main", "w_resonly", "w_foot"):
        un[c] = 0.0
    wsum = bb.groupby("adm_cd2")[["w_main", "w_resonly", "w_foot"]].sum()
    meta = {"n_buildings_in_seoul": int(len(bb)), "dong_total": int(len(dg))}
    meta["dong_no_w_main"] = sorted(set(dg.adm_cd2) - set(wsum.index[wsum.w_main > 0]))
    meta["dong_no_w_resonly"] = sorted(set(dg.adm_cd2) - set(wsum.index[wsum.w_resonly > 0]))
    bb["kind"] = "bldg"; un["kind"] = "node"
    un["tag"] = "node"; un["area"] = 0.0; un["lev"] = 1.0
    dem = pd.concat([bb.drop(columns=["bid"]), un], ignore_index=True)
    # uniform-within-dong weight for every node point (sensitivity W0) and fallback weights
    dem["w_uni"] = np.where(dem.kind == "node", 1.0, 0.0)
    # per-point population for each weighting scheme (each dong's pop spread over its weight)
    for scheme in ("w_main", "w_resonly", "w_foot"):
        W = dem.groupby("adm_cd2")[scheme].transform("sum")
        fallback = (W <= 0)
        wnode = np.where(dem.kind == "node", 1.0, 0.0)
        use = np.where(fallback, wnode, dem[scheme].to_numpy())
        tot = pd.Series(use).groupby(dem.adm_cd2.to_numpy()).transform("sum").to_numpy()
        dem[f"share_{scheme}"] = use / tot
    Wu = dem.groupby("adm_cd2").w_uni.transform("sum"); dem["share_w_uni"] = dem.w_uni / Wu
    dem = dem.merge(pop, on="adm_cd2", how="left")
    for scheme in ("w_main", "w_resonly", "w_foot", "w_uni"):
        dem[f"p65_{scheme}"] = dem[f"share_{scheme}"] * dem.pop65
        dem[f"ptot_{scheme}"] = dem[f"share_{scheme}"] * dem.pop_tot
    # conservation check
    for scheme in ("w_main", "w_resonly", "w_foot", "w_uni"):
        assert abs(dem[f"p65_{scheme}"].sum() - pop.pop65.sum()) < 1e-3 * pop.pop65.sum(), scheme
    dem = dem[(dem[["p65_w_main", "p65_w_resonly", "p65_w_foot", "p65_w_uni"]].sum(axis=1) > 0)].reset_index(drop=True)
    meta["res_share_of_main_weight_in_yes"] = float(bb.loc[bb.yes, "w_main"].sum() / bb.w_main.sum())
    meta["snap_bldg_m_quantiles"] = {str(q): float(np.quantile(bb.snap, q)) for q in (.5, .9, .99)}
    return dem, meta


def main() -> None:
    import json
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    import time; t0 = time.time()
    if (L.OUT / "graph.npz").exists():
        z = np.load(L.OUT / "graph_xy.npz")
        g = {"A": sp.load_npz(L.OUT / "graph.npz"), "x": z["x"], "y": z["y"], "lcc": z["lcc"], "n_orig": int(z["n_orig"]) if "n_orig" in z.files else -1,
             "total_len_km": float(sp.load_npz(L.OUT / "graph.npz").data.sum() / 2000)}
    else:
        g = build_graph(); L.OUT.mkdir(exist_ok=True)
        sp.save_npz(L.OUT / "graph.npz", g["A"])
        np.savez_compressed(L.OUT / "graph_xy.npz", x=g["x"], y=g["y"], lcc=g["lcc"], n_orig=g["n_orig"], total_len_km=g["total_len_km"])
    log.info("graph ready %.0fs", time.time() - t0)
    lcc_idx = np.where(g["lcc"])[0]
    tree = cKDTree(np.c_[g["x"][lcc_idx], g["y"][lcc_idx]])
    s = load_aed_sites(tree, g["x"], g["y"], lcc_idx)
    log.info("aed ready %.0fs", time.time() - t0)
    sched = schedules_by_daytype(s)
    dem, meta = build_demand(tree, g["x"], g["y"], lcc_idx)
    log.info("demand ready %.0fs", time.time() - t0)
    s2 = s.drop(columns=["clerkTel", "managerTel", "manager"], errors="ignore")
    s2.to_pickle(L.OUT / "aed_snapped.pkl")
    np.savez_compressed(L.OUT / "sched.npz", **{f"{k}__{d}": v for k, o in sched.items() if isinstance(o, dict) for d, v in o.items()})
    dem.to_parquet(L.OUT / "demand.parquet")
    meta.update({"graph_nodes": int(g["A"].shape[0]), "graph_nodes_orig": int(g["n_orig"]), "lcc_nodes": int(len(lcc_idx)),
                 "network_km": g["total_len_km"], "n_aed_seoul": int(len(s)), "placeholder_n": sched["_placeholder_n"],
                 "aed_snap_m_quantiles": {str(q): float(np.quantile(s.snap, q)) for q in (.5, .9, .99, 1.0)},
                 "aed_snap_gt_R": {k: int((s.snap > r).sum()) for k, r in L.RADII.items()},
                 "demand_points": int(len(dem))})
    (L.OUT / "build_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info(json.dumps({k: v for k, v in meta.items() if not k.startswith("dong_no")}, ensure_ascii=False))
    log.info("dong without main weight: %d, without resonly: %d", len(meta["dong_no_w_main"]), len(meta["dong_no_w_resonly"]))


if __name__ == "__main__":
    main()
