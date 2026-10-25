"""Module B (실버존 다시 긋기) evaluation: same-k re-selection vs current 노인보호구역.

Backtests are hypotheses, not proofs. Target population = TAAS 보행노인 사고다발지 casualties
(not all elderly pedestrian casualties). Rates are per-year counts; coverage is a ratio of sums.
Usage: run.py --protocol   (writes out/protocol.json, no evaluation)
       run.py              (verifies protocol sha, runs all folds)
"""
from __future__ import annotations

import hashlib, heapq, json, logging, random, re, sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.linear_model import PoissonRegressor
from sklearn.neighbors import BallTree

logger = logging.getLogger("silver")
HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
SZ = Path("/Users/chloekang/kcf/data/cache/extra/silverzone")
CACHE = Path("/Users/chloekang/kcf/data/cache")
GEOJSON = CACHE / "extra/boundaries/HangJeongDong_ver20261001.geojson"
POP_CSV = CACHE / "j_population_regional/kr_15097972_resident_pop_by_dong_sex_age_202609.csv"
R_EARTH = 6371008.8
CFG = dict(
    seed=42, radius_m=300, label_radius_m=100, dedupe_m=50, decay=0.8, test_years=[2022, 2023, 2024],
    harm_N_years=[2021, 2022, 2023, 2024], walk_speed_mps=0.7, ks=[250, 500, 1000, 2000, 3918],
    k_main=3918, n_random=200, n_boot=1000, label_window_years=4, poisson_alpha=1e-3,
    variants=["harm", "ge2021"], pools=["C", "C0"],
    methods_C=["current", "random_k", "pop65_k", "past_k", "ours_full", "ours_nohist"],
    methods_C0=["random_k", "pop65_k", "ours_nohist"],
    eb="prior mu0 from Poisson reg on non-history features (label=recent-yr casualty count within label_radius); "
       "gamma overdispersion by moments; posterior (a+y)/(a/mu0+e), y,e = decayed history; ours_nohist = mu0 only",
    selection="greedy lazy MCLP, demand=pool points weighted by posterior rate, cover radius radius_m, nested order",
    pool_C="past hotspot centers U current 노인 zones U 행정동 centroids, dedupe 50 m (priority hotspot>zone>dong)",
    pool_C0="zones U dong centroids (no history in the pool) -- control for nohist/random/pop65",
)


def hav(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    return np.radians(np.c_[lat, lon])


def radius_nbrs(tree_pts: np.ndarray, q_pts: np.ndarray, m: float) -> list[np.ndarray]:
    return BallTree(tree_pts, metric="haversine").query_radius(q_pts, r=m / R_EARTH)


def cover_matrix(targets: np.ndarray, pool: np.ndarray, m: float) -> sparse.csr_matrix:
    """A[h, p]=1 if pool point p within m of target h."""
    nb = radius_nbrs(pool, targets, m)
    ind = np.concatenate(nb) if len(nb) else np.array([], int)
    ptr = np.r_[0, np.cumsum([len(x) for x in nb])]
    return sparse.csr_matrix((np.ones(len(ind), bool), ind, ptr), shape=(len(targets), len(pool)))


def nearest_m(targets: np.ndarray, pool: np.ndarray) -> np.ndarray:
    d, _ = BallTree(pool, metric="haversine").query(targets, k=1)
    return d[:, 0] * R_EARTH


# ---------------------------------------------------------------- data
def load_dongs() -> pd.DataFrame:
    gj = json.load(open(GEOJSON, encoding="utf-8"))
    rows = []
    for f in gj["features"]:
        p, g = f["properties"], f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        pts = np.vstack([np.asarray(po[0], float) for po in polys])
        lat0 = np.radians(pts[:, 1].mean())
        area = 0.0
        for po in polys:  # shoelace on outer ring, km^2 (equirectangular, local)
            r = np.asarray(po[0], float)
            x = np.radians(r[:, 0]) * np.cos(lat0) * 6371.0088
            y = np.radians(r[:, 1]) * 6371.0088
            area += abs(np.dot(x[:-1], y[1:]) - np.dot(x[1:], y[:-1])) / 2
        rows.append(dict(adm_cd2=str(p["adm_cd2"]), sido=p["sidonm"], name=p["adm_nm"], lon=pts[:, 0].mean(),
                         lat=pts[:, 1].mean(), area_km2=max(area, 0.05)))
    d = pd.DataFrame(rows)
    pop = pd.read_csv(POP_CSV, encoding="cp949", thousands=",")
    pop["adm_cd2"] = pop["행정기관코드"].astype(str)
    ages = {c: int(m.group(1)) for c in pop.columns if (m := re.match(r"^(\d+)세", str(c)))}
    pop["p65"] = pop[[c for c, a in ages.items() if a >= 65]].sum(axis=1)
    # LEAKAGE RISK (mild): 2026-09 population snapshot applied to 2021-2024 folds (static, slow-moving).
    d = d.merge(pop[["adm_cd2", "계", "p65"]].rename(columns={"계": "pop"}), on="adm_cd2", how="left")
    n_miss = int(d.p65.isna().sum())
    logger.info("dongs=%d, p65 missing=%d", len(d), n_miss)
    d = d.dropna(subset=["p65"]).reset_index(drop=True)
    d["dens65"] = d.p65 / d.area_km2
    return d


def load_all() -> dict:
    z = pd.read_csv(SZ / "kr_15034532_silverzone_std.csv")
    z = z[z.LC_TYPE == 1].dropna(subset=["LATITUDE", "LONGITUDE"])
    z = z[z.LATITUDE.between(33, 39) & z.LONGITUDE.between(124, 132)]
    sc = pd.read_csv(SZ / "kr_15012891_schoolzone_std.csv", usecols=["LATITUDE", "LONGITUDE"]).dropna()
    sc = sc[sc.LATITUDE.between(33, 39) & sc.LONGITUDE.between(124, 132)]
    h = pd.read_csv(SZ / "kr_15029185_acc_hotspot_std.csv", usecols=lambda c: c != "ACDNT_MLTTD_PYN_INFO")
    h = h[h.ACDNT_TYPE_SE == "보행노인"].dropna(subset=["LATITUDE", "LONGITUDE", "CASLT_CO", "ACDNT_YEAR"])
    h = h[h.LATITUDE.between(33, 39) & h.LONGITUDE.between(124, 132) & (h.CASLT_CO >= 0)].copy()
    h["year"] = h.ACDNT_YEAR.astype(int)
    h = h.rename(columns={"LATITUDE": "lat", "LONGITUDE": "lon", "CASLT_CO": "cas", "OCCRRNC_CO": "occ"})
    cw = pd.read_csv(SZ / "kr_15028201_crosswalk_std.csv", usecols=["LATITUDE", "LONGITUDE", "ET", "GREEN_SGNGNR_TIME", "TFCLGHT_YN"])
    cw = cw[cw.LATITUDE.between(33, 39) & cw.LONGITUDE.between(124, 132)]
    ok = (cw.TFCLGHT_YN == "Y") & (cw.ET > 0) & (cw.ET <= 80) & (cw.GREEN_SGNGNR_TIME > 0) & (cw.GREEN_SGNGNR_TIME <= 200)
    cw = cw[ok].copy()
    cw["deficit"] = (cw.GREEN_SGNGNR_TIME < cw.ET / CFG["walk_speed_mps"]).astype(float)
    return dict(zones=z.reset_index(drop=True), school=sc.reset_index(drop=True), hot=h.reset_index(drop=True),
                cw=cw.reset_index(drop=True), dong=load_dongs())


def harmonize(h: pd.DataFrame, train_end: int, variant: str) -> pd.DataFrame:
    """Training hotspot history <= train_end. harm: pre-2021 years keep top-N casualties (N=median 2021-24 count)."""
    N = int(np.ceil(np.median([int((h.year == y).sum()) for y in CFG["harm_N_years"]])))
    t = h[h.year <= train_end]
    if variant == "ge2021":
        return t[t.year >= 2021]
    keep = []
    for y, g in t.groupby("year"):
        if y >= 2021:
            keep.append(g)
        else:
            keep.append(g.sort_values(["cas", "occ"], ascending=False, kind="stable").head(N))
    return pd.concat(keep)


# ---------------------------------------------------------------- pool & features
def build_pool(d: dict, hist: pd.DataFrame, with_hist: bool) -> pd.DataFrame:
    parts = []
    if with_hist:
        parts.append(hist[["lat", "lon"]].assign(src=0))
    parts.append(d["zones"][["LATITUDE", "LONGITUDE"]].rename(columns={"LATITUDE": "lat", "LONGITUDE": "lon"}).assign(src=1))
    parts.append(d["dong"][["lat", "lon"]].assign(src=2))
    p = pd.concat(parts, ignore_index=True)
    xy = hav(p.lat.values, p.lon.values)
    nb = radius_nbrs(xy, xy, CFG["dedupe_m"])
    keep = np.ones(len(p), bool)
    for i in range(len(p)):  # priority order = row order; greedy suppression (n~1e4)
        if keep[i]:
            for j in nb[i]:
                if j > i:
                    keep[j] = False
    return p[keep].reset_index(drop=True)


def static_features(d: dict, pool: pd.DataFrame) -> pd.DataFrame:
    xy = hav(pool.lat.values, pool.lon.values)
    r = CFG["radius_m"]
    dg = d["dong"]
    _, idx = BallTree(hav(dg.lat.values, dg.lon.values), metric="haversine").query(xy, k=1)
    # NOTE: nearest-centroid dong assignment (not polygon containment) -> noisy density at dong borders.
    f = pd.DataFrame({"dens65": dg.dens65.values[idx[:, 0]], "dong_sido": dg.sido.values[idx[:, 0]]})
    f["log_dens65"] = np.log1p(f.dens65)
    cw = d["cw"]
    nb = radius_nbrs(hav(cw.LATITUDE.values, cw.LONGITUDE.values), xy, r)
    n = np.array([len(x) for x in nb])
    defi = np.array([cw.deficit.values[x].sum() for x in nb])
    f["cw_n"] = n
    f["cw_missing"] = (n == 0).astype(float)
    f["cw_deficit_share"] = np.where(n > 0, defi / np.maximum(n, 1), 0.0)
    f["log_cw_n"] = np.log1p(n)
    # LEAKAGE RISK: current zones / school zones are 2026 snapshots (designation dates unknown).
    zz = d["zones"]
    f["log_zone_n"] = np.log1p([len(x) for x in radius_nbrs(hav(zz.LATITUDE.values, zz.LONGITUDE.values), xy, r)])
    sc = d["school"]
    f["log_school_n"] = np.log1p([len(x) for x in radius_nbrs(hav(sc.LATITUDE.values, sc.LONGITUDE.values), xy, r)])
    return f


FEATS = ["log_dens65", "cw_deficit_share", "cw_missing", "log_cw_n", "log_zone_n", "log_school_n"]


def hist_by_year(pool_xy: np.ndarray, hist: pd.DataFrame, m: float) -> dict[int, np.ndarray]:
    """For each train year: casualties of that year's hotspots with center within m of each pool point."""
    out = {}
    for y, g in hist.groupby("year"):
        nb = radius_nbrs(hav(g.lat.values, g.lon.values), pool_xy, m)  # per pool point -> hotspot idx
        c = g.cas.values.astype(float)
        out[int(y)] = np.array([c[x].sum() for x in nb])
    return out


# ---------------------------------------------------------------- selection
def nms(order: np.ndarray, xy: np.ndarray, m: float, limit: int) -> list[int]:
    nb = radius_nbrs(xy, xy, m)
    blocked = np.zeros(len(xy), bool)
    sel = []
    for i in order:
        if not blocked[i]:
            sel.append(int(i))
            blocked[nb[i]] = True
            if len(sel) >= limit:
                break
    return sel


def greedy_mclp(w: np.ndarray, nb: list[np.ndarray], limit: int) -> list[int]:
    """Lazy-greedy max coverage (1-1/e). Demand j (weight w_j) is covered by candidate i if j in nb[i]."""
    covered = np.zeros(len(w), bool)
    gain = np.array([w[x].sum() for x in nb])
    heap = [(-g, i, 0) for i, g in enumerate(gain) if g > 0]
    heapq.heapify(heap)
    sel, ver = [], 0
    while heap and len(sel) < limit:
        g, i, v = heapq.heappop(heap)
        if v != ver:
            g2 = w[nb[i][~covered[nb[i]]]].sum()
            if g2 > 0:
                heapq.heappush(heap, (-g2, i, ver))
            continue
        sel.append(i)
        covered[nb[i]] = True
        ver += 1
    return sel


def rank_fill(sel: list[int], score: np.ndarray, n: int) -> list[int]:
    seen = set(sel)
    rest = [int(i) for i in np.argsort(-score, kind="stable") if int(i) not in seen]
    return (list(sel) + rest)[:n]


def fit_prior(F: pd.DataFrame, lab: np.ndarray, nyrs: int) -> tuple[np.ndarray, float]:
    X = F[FEATS].values
    mu_sd = X.std(0)
    mu_sd[mu_sd == 0] = 1
    Xs = (X - X.mean(0)) / mu_sd
    rate = lab / nyrs
    m = PoissonRegressor(alpha=CFG["poisson_alpha"], max_iter=500).fit(Xs, rate, sample_weight=np.full(len(rate), float(nyrs)))
    mu = np.clip(m.predict(Xs), 1e-6, None)
    ex = ((lab - nyrs * mu) ** 2 - nyrs * mu).sum() / ((nyrs * mu) ** 2).sum()
    a = 1.0 / max(ex, 1e-3)  # gamma shape; method of moments
    return mu, float(a)


def rankings(d: dict, hist: pd.DataFrame, pool: pd.DataFrame, F: pd.DataFrame, train_end: int, rng: np.random.Generator,
             with_hist: bool, nmax: int) -> dict[str, list[int]]:
    xy = hav(pool.lat.values, pool.lon.values)
    r, lr = CFG["radius_m"], CFG["label_radius_m"]
    nb_r = radius_nbrs(xy, xy, r)
    out: dict[str, list[int]] = {}
    # pop65 with random tie-break
    out["pop65_k"] = [int(i) for i in np.lexsort((rng.random(len(pool)), -F.dens65.values))][:nmax]
    # prior from last label_window train years
    yrs_all = sorted(hist.year.unique()) if len(hist) else []
    # labels = training-period outcomes (allowed for nohist: they are targets, not features at the test year)
    hy = hist_by_year(xy, hist, lr)
    hy_lab = hy
    ly = sorted(hy_lab)[-CFG["label_window_years"]:]
    lab = np.sum([hy_lab[y] for y in ly], axis=0)
    mu0, a = fit_prior(F, lab, len(ly))
    out["_a"] = a
    if with_hist:
        # past_k: 300 m decayed casualties, NMS at 300 m, then fill by pop65
        h300 = hist_by_year(xy, hist, r)
        sc = sum(CFG["decay"] ** (train_end - y) * v for y, v in h300.items())
        order = np.argsort(-sc, kind="stable")
        order = order[sc[order] > 0]
        sel = nms(order, xy, r, nmax)
        out["past_k"] = rank_fill(sel, F.dens65.values, nmax)
        # ours_full: EB posterior
        wts = {y: CFG["decay"] ** (train_end - y) for y in hy}
        yv = sum(wts[y] * hy[y] for y in hy)
        e = sum(wts.values())
        post = (a + yv) / (a / mu0 + e)
        sel = greedy_mclp(post, nb_r, nmax)
        out["ours_full"] = rank_fill(sel, post, nmax)
    sel = greedy_mclp(mu0, nb_r, nmax)
    out["ours_nohist"] = rank_fill(sel, mu0, nmax)
    return out


_H_ALL: pd.DataFrame | None = None


def hist_for_labels(d: dict, train_end: int, variant: str = "harm") -> pd.DataFrame:
    return harmonize(d["hot"], train_end, CUR_VARIANT)


CUR_VARIANT = "harm"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    global CUR_VARIANT
    random.seed(CFG["seed"])
    np.random.seed(CFG["seed"])
    rng = np.random.default_rng(CFG["seed"])
    d = load_all()
    h = d["hot"]
    logger.info("zones=%d hot=%d cw_valid=%d", len(d["zones"]), len(h), len(d["cw"]))
    zxy = hav(d["zones"].LATITUDE.values, d["zones"].LONGITUDE.values)
    records = []  # per (variant,pool,method,k,fold): arrays
    hot_rows = []
    picks_keep = {}
    for variant in CFG["variants"]:
        CUR_VARIANT = variant
        for te in CFG["test_years"]:
            tr = te - 1
            hist = harmonize(h, tr, variant)
            test = h[h.year == te].reset_index(drop=True)
            txy = hav(test.lat.values, test.lon.values)
            new = (nearest_m(txy, hav(hist.lat.values, hist.lon.values)) > CFG["radius_m"])
            tmeta = pd.DataFrame({"variant": variant, "fold": te, "cas": test.cas.values.astype(float), "new": new,
                                  "hid": np.arange(len(test))})
            hot_rows.append(tmeta)
            logger.info("%s fold %d: train hotspots=%d test=%d (cas %d) new=%d (cas %d)", variant, te, len(hist), len(test),
                        test.cas.sum(), new.sum(), test.cas.values[new].sum())
            cov: dict[tuple, np.ndarray] = {}
            # current
            Az = cover_matrix(txy, zxy, CFG["radius_m"])
            cov[("C", "current", CFG["k_main"])] = (Az.sum(1).A1 > 0).astype(float)
            # oracle on test casualties (candidates = pool C, cover 300 m) -- ceiling
            for pname in CFG["pools"]:
                with_hist = pname == "C"
                pool = build_pool(d, hist, with_hist)
                pxy = hav(pool.lat.values, pool.lon.values)
                F = static_features(d, pool)
                nmax = max(CFG["ks"])
                R = rankings(d, hist, pool, F, tr, rng, with_hist, nmax)
                logger.info("%s fold %d pool %s n=%d a=%.3f", variant, te, pname, len(pool), R.pop("_a"))
                A = cover_matrix(txy, pxy, CFG["radius_m"]).tocsc()
                # oracle: demand = test hotspots, candidates = pool
                if True:  # oracle ceiling on both pools (greedy MCLP on test casualties; not an exact bound)
                    Ah = A.tocsr()
                    nb_c = [Ah.indices[Ah.indptr[i]:Ah.indptr[i + 1]] for i in range(len(test))]
                    inv = [[] for _ in range(len(pool))]
                    for hi, cs in enumerate(nb_c):
                        for c in cs:
                            inv[c].append(hi)
                    inv = [np.array(x, int) for x in inv]
                    for tag, wv in (("oracle", test.cas.values.astype(float)), ("oracle_new", test.cas.values * new.astype(float))):
                        sel = greedy_mclp(wv, inv, max(CFG["ks"]))
                        for k in CFG["ks"]:
                            m = np.zeros(len(pool), bool); m[sel[:k]] = True
                            cov[(pname, tag, k)] = ((A @ m.astype(np.int8)) > 0).astype(float)
                for k in CFG["ks"]:
                    for meth, order in R.items():
                        if meth in ("_a",):
                            continue
                        m = np.zeros(len(pool), bool); m[order[:k]] = True
                        cov[(pname, meth, k)] = ((A @ m.astype(np.int8)) > 0).astype(float)
                    # random_k: expected coverage over draws
                    M = np.zeros((len(pool), CFG["n_random"]), np.int8)
                    for j in range(CFG["n_random"]):
                        M[rng.choice(len(pool), k, replace=False), j] = 1
                    cov[(pname, "random_k", k)] = ((A @ M) > 0).mean(1)
                if te == 2024 and variant == "harm" and pname == "C":
                    picks_keep = dict(pool=pool, F=F, order=R["ours_full"][:CFG["k_main"]], past=R["past_k"][:CFG["k_main"]])
            for key, v in cov.items():
                records.append(pd.DataFrame({"variant": variant, "fold": te, "pool": key[0], "method": key[1], "k": key[2],
                                             "hid": np.arange(len(test)), "cov": v}))
    hot = pd.concat(hot_rows, ignore_index=True)
    rec = pd.concat(records, ignore_index=True).merge(hot, on=["variant", "fold", "hid"])
    rec.to_pickle(OUT / "per_hotspot.pkl")
    hot.to_csv(OUT / "test_hotspots_meta.csv", index=False)
    summarize(rec)
    export_map(d, picks_keep)


def summarize(rec: pd.DataFrame) -> None:
    rng = np.random.default_rng(CFG["seed"])
    rows, deltas = [], []
    for variant in CFG["variants"]:
        base = rec[rec.variant == variant]
        for strat in ("all", "new"):
            b = base if strat == "all" else base[base.new]
            # bootstrap indices stratified by fold(year): same hotspot resample for all methods (paired)
            keys = b[["fold", "hid"]].drop_duplicates().reset_index(drop=True)
            keys["row"] = np.arange(len(keys))
            idx_by_fold = [g.row.values for _, g in keys.groupby("fold")]
            B = CFG["n_boot"]
            counts = np.zeros((B, len(keys)))
            for ix in idx_by_fold:
                draws = rng.choice(ix, size=(B, len(ix)), replace=True)
                np.add.at(counts, (np.repeat(np.arange(B), len(ix)), draws.ravel()), 1)
            cas = b.drop_duplicates(["fold", "hid"]).sort_values(["fold", "hid"])
            keys = keys.merge(cas[["fold", "hid", "cas"]], on=["fold", "hid"])
            cvec = keys.cas.values
            piv = {}
            for (pool, meth, k), g in b.groupby(["pool", "method", "k"]):
                g = g.merge(keys[["fold", "hid", "row"]], on=["fold", "hid"])
                v = np.zeros(len(keys)); v[g.row.values] = g["cov"].values
                piv[(pool, meth, k)] = v
                num = (counts * (cvec * v)).sum(1); den = (counts * cvec).sum(1)
                bs = num / den
                pt = (cvec * v).sum() / cvec.sum()
                rows.append(dict(variant=variant, stratum=strat, pool=pool, method=meth, k=k, coverage=pt,
                                 lo=np.percentile(bs, 2.5), hi=np.percentile(bs, 97.5), n_hot=len(keys), cas=cvec.sum()))
            den = (counts * cvec).sum(1)
            for (pool, meth, k), v in piv.items():
                for ref in (("C", "current", CFG["k_main"]), ("C", "past_k", k), ("C", "random_k", k), ("C", "pop65_k", k),
                            (pool, "random_k", k), (pool, "pop65_k", k)):
                    if ref not in piv or (pool, meth, k) == ref or meth in ("oracle", "oracle_new"):
                        continue
                    if ref[1] == "current" and k != CFG["k_main"]:
                        continue
                    dd = (counts * (cvec * (v - piv[ref]))).sum(1) / den
                    pt = (cvec * (v - piv[ref])).sum() / cvec.sum()
                    deltas.append(dict(variant=variant, stratum=strat, pool=pool, method=meth, k=k, vs=f"{ref[0]}:{ref[1]}",
                                       delta_pp=100 * pt, lo_pp=100 * np.percentile(dd, 2.5), hi_pp=100 * np.percentile(dd, 97.5)))
    pd.DataFrame(rows).drop_duplicates().to_csv(OUT / "coverage_results.csv", index=False)
    pd.DataFrame(deltas).drop_duplicates().to_csv(OUT / "delta_results.csv", index=False)
    # per-fold (point) table
    pf = rec.assign(c=rec.cas * rec["cov"]).groupby(["variant", "fold", "pool", "method", "k"]).apply(
        lambda g: pd.Series(dict(all=g.c.sum() / g.cas.sum(), new=(g.c * g.new).sum() / max((g.cas * g.new).sum(), 1e-9),
                                 n_new=int(g.new.sum()), n=len(g))), include_groups=False).reset_index()
    pf.to_csv(OUT / "coverage_by_fold.csv", index=False)


def export_map(d: dict, pk: dict) -> None:
    pool, F, sel = pk["pool"], pk["F"], pk["order"]
    sub = pool.iloc[sel].assign(kind="ours", sido=F.dong_sido.values[sel], zone_nm="")
    z = d["zones"]
    cur = pd.DataFrame({"lat": z.LATITUDE, "lon": z.LONGITUDE, "kind": "current", "sido": z.CTPRVN_NM, "zone_nm": z.TRGET_FCLTY_NM})
    ours = sub[["lat", "lon", "kind", "sido", "zone_nm"]]
    t24 = d["hot"][d["hot"].year == 2024]
    hs = pd.DataFrame({"lat": t24.lat, "lon": t24.lon, "kind": "hotspot_2024", "sido": "", "zone_nm": t24.ACDNT_AREA_LC_NM,
                       "cas": t24.cas})
    allp = pd.concat([cur, ours, hs], ignore_index=True)
    box = allp.lat.between(37.41, 37.72) & allp.lon.between(126.76, 127.19)
    seoul = allp[box].copy()
    ox = hav(ours.lat.values, ours.lon.values)
    cx = hav(cur.lat.values, cur.lon.values)
    seoul["cur_within_300m"] = (nearest_m(hav(seoul.lat.values, seoul.lon.values), cx) <= 300).astype(int)
    seoul["ours_within_300m"] = (nearest_m(hav(seoul.lat.values, seoul.lon.values), ox) <= 300).astype(int)
    seoul.to_csv(OUT / "map_seoul_current_vs_ours_k3918_fold2024.csv", index=False, encoding="utf-8-sig")
    logger.info("map rows %s", seoul.kind.value_counts().to_dict())


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


if __name__ == "__main__":
    pp = OUT / "protocol.json"
    if "--protocol" in sys.argv:
        pp.write_text(json.dumps(dict(config=CFG, code_sha256=sha(Path(__file__)), code=str(Path(__file__)),
                                      data_sha256={p.name: sha(p) for p in SZ.glob("*.csv")}, git="not a git repo",
                                      note="Fixed before any test-fold evaluation. No post-hoc tuning."),
                                 ensure_ascii=False, indent=2))
        print("protocol written"); sys.exit()
    if "--dry" not in sys.argv:
        prot = json.loads(pp.read_text())
        assert prot["code_sha256"] == sha(Path(__file__)), "code changed after protocol"
    main()
