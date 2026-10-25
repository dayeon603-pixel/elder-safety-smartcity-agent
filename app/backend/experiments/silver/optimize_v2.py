"""Optimal 실버존 re-siting search with nested (inner-validation) tuning.

Backtests are hypotheses, not proofs. Target = casualties at TAAS 보행노인 hotspots of the test year
(NOT all elderly pedestrian casualties). Coverage is a ratio of sums (casualty-weighted).
Outer folds as in run.py: test 2022/2023/2024, history = harmonize(h, test-1, "harm").
ALL selection is on inner folds inside the training years: inner test year y in {tr, tr-1},
inner history = harmonize(h, y-1). The outer test year is evaluated once, with the config chosen inner.

Does not touch out/*.csv (writes only out/opt/). Imports run.py helpers (run.py itself unchanged).
Usage: optimize.py --protocol | optimize.py | optimize.py --report
"""
from __future__ import annotations

import hashlib, heapq, itertools, json, logging, multiprocessing as mp, random, sys, time
from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib.path import Path as MPath
from scipy import sparse
from sklearn.neighbors import BallTree

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run  # noqa: E402  (helpers only; main() not invoked)
from run import CFG, R_EARTH, hav, fit_prior, FEATS  # noqa: E402

logger = logging.getLogger("opt")
ROOT = HERE / "out" / "opt"
OUT = ROOT / "v2" / "tmp"
EXP: dict = {}
OPT = dict(
    seed=42, n_boot=2000, ks=[250, 500, 1000, 2000, 3918], k_main=3918, radius_m=300,
    grid_m=250, grid_min_dens65_per_km2=200,  # design choice fixed a priori (keeps ~114k cells); not tuned
    inner_years="tr and tr-1; inner hist=harmonize(h,y-1); inner test = harmonized year-y hotspots",
    objective="J = 0.5*cov_all + 0.5*cov_new at k=3918, mean over 2 inner folds",
    decays=[0.5, 0.8, 1.0], past_radii=[150, 300], wE=[0, .25, .5, 1, 2], wpop=[.02, .1, .25, .5, 1], wdef=[0, .1, .25, .5],
    n_stage1_weight_combos=40, stage2_top=3, stage2_mclp_top=2, rhos=[150, 300], fracs=[0.0, 0.1, 0.2, 0.3, 0.4],
    s2_weights={"mu0": [1, 0, 0], "pop": [0, 1, 0], "mix": [1, .5, .5]},
    pool_D="C U crosswalk points (all 50k rows of file, truncated at portal limit) U 250 m grid over dongs with dens65>=200/km2",
    blend="S1 = P(decay,rad) + wE*EB(decay) + wpop*POP + wdef*DEF ; each log1p(x/mean_pos)/q99.5 normalised on the pool",
)


# ------------------------------------------------------------------ geometry / pools
def grid_points(d_all_path: Path, qualified: set[str], step_m: float) -> pd.DataFrame:
    cache = ROOT / f"grid_{int(step_m)}_{OPT['grid_min_dens65_per_km2']}.pkl"
    if cache.exists():
        return pd.read_pickle(cache)
    gj = json.load(open(run.GEOJSON, encoding="utf-8"))
    chunks = []
    for f in gj["features"]:
        if str(f["properties"]["adm_cd2"]) not in qualified:
            continue
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        for po in polys:
            ring = np.asarray(po[0], float)
            lat0 = np.radians(ring[:, 1].mean())
            dlat = step_m / 111320.0
            dlon = step_m / (111320.0 * np.cos(lat0))
            xs = np.arange(ring[:, 0].min() + dlon / 2, ring[:, 0].max(), dlon)
            ys = np.arange(ring[:, 1].min() + dlat / 2, ring[:, 1].max(), dlat)
            if len(xs) == 0 or len(ys) == 0:
                continue
            gx, gy = np.meshgrid(xs, ys)
            pts = np.c_[gx.ravel(), gy.ravel()]
            inside = MPath(ring).contains_points(pts)
            if inside.any():
                chunks.append(pts[inside])
    p = np.vstack(chunks)
    df = pd.DataFrame({"lat": p[:, 1], "lon": p[:, 0]})
    df.to_pickle(cache)
    return df


def build_pool(hist: pd.DataFrame, static: pd.DataFrame) -> pd.DataFrame:
    """Priority order: hist(0) > zone(1) > dong(2) > crosswalk(3) > grid(4); greedy 50 m suppression."""
    p = pd.concat([hist[["lat", "lon"]].assign(src=0), static], ignore_index=True)
    xy = hav(p.lat.values, p.lon.values)
    nb = BallTree(xy, metric="haversine").query_radius(xy, r=CFG["dedupe_m"] / R_EARTH)
    keep = np.ones(len(p), bool)
    for i in range(len(p)):
        if keep[i]:
            j = nb[i]
            keep[j[j > i]] = False
    return p[keep].reset_index(drop=True)


def csr_cover(targets_xy: np.ndarray, pool_xy: np.ndarray, m: float) -> sparse.csr_matrix:
    return run.cover_matrix(targets_xy, pool_xy, m)


def static_feats(d: dict, pool: pd.DataFrame, pxy: np.ndarray) -> pd.DataFrame:
    """Vectorised version of run.static_features (identical definitions, sparse counts)."""
    r = CFG["radius_m"]
    dg = d["dong"]
    _, idx = BallTree(hav(dg.lat.values, dg.lon.values), metric="haversine").query(pxy, k=1)
    f = pd.DataFrame({"dens65": dg.dens65.values[idx[:, 0]]})
    f["log_dens65"] = np.log1p(f.dens65)
    cw = d["cw"]
    A = csr_cover(pxy, hav(cw.LATITUDE.values, cw.LONGITUDE.values), r).astype(np.float32)
    n = np.asarray(A.sum(1)).ravel()
    defi = A @ cw.deficit.values.astype(np.float32)
    f["cw_deficit_share"] = np.where(n > 0, defi / np.maximum(n, 1), 0.0)
    f["cw_missing"] = (n == 0).astype(float)
    f["log_cw_n"] = np.log1p(n)
    zz, sc = d["zones"], d["school"]
    # LEAKAGE RISK: current zones / school zones are 2026 snapshots (same caveat as run.py).
    f["log_zone_n"] = np.log1p(np.asarray(csr_cover(pxy, hav(zz.LATITUDE.values, zz.LONGITUDE.values), r).sum(1)).ravel())
    f["log_school_n"] = np.log1p(np.asarray(csr_cover(pxy, hav(sc.LATITUDE.values, sc.LONGITUDE.values), r).sum(1)).ravel())
    return f


def norm(x: np.ndarray) -> np.ndarray:
    pos = x[x > 0]
    if len(pos) == 0:
        return np.zeros_like(x, dtype=float)
    z = np.log1p(x / pos.mean())
    q = np.percentile(z, 99.5)
    return z / q if q > 0 else z


class Ctx:
    """Everything computable from history <= train_end for one pool."""

    def __init__(self, d: dict, hist: pd.DataFrame, static: pd.DataFrame, train_end: int):
        self.te = train_end
        self.hist = hist
        self.pool = build_pool(hist, static)
        self.xy = hav(self.pool.lat.values, self.pool.lon.values)
        self.F = static_feats(d, self.pool, self.xy)
        tree = BallTree(self.xy, metric="haversine")
        self.N = {}
        for rho in sorted(set(OPT["rhos"] + [OPT["radius_m"]])):
            nb = tree.query_radius(self.xy, r=rho / R_EARTH)
            ind = np.concatenate(nb)
            ptr = np.r_[0, np.cumsum([len(x) for x in nb])]
            self.N[rho] = sparse.csr_matrix((np.ones(len(ind), np.float32), ind, ptr), shape=(len(self.xy),) * 2)
        self.Y: dict[int, dict[int, np.ndarray]] = {}
        for rad in sorted(set(OPT["past_radii"] + [CFG["label_radius_m"]])):
            self.Y[rad] = {}
            for y, g in hist.groupby("year"):
                A = csr_cover(hav(g.lat.values, g.lon.values), self.xy, rad).astype(np.float32)
                self.Y[rad][int(y)] = A.T.dot(g.cas.values.astype(np.float32)).astype(float)
        ly = sorted(self.Y[100])[-CFG["label_window_years"]:]
        lab = np.sum([self.Y[100][y] for y in ly], axis=0)
        self.mu0, self.a = fit_prior(self.F, lab, len(ly))
        self._c: dict = {}
        self.POP = norm(self.F.dens65.values)
        self.DEF = norm(self.F.cw_deficit_share.values)
        self.MU0 = norm(self.mu0)
        self.dens65 = self.F.dens65.values

    def P(self, decay: float, rad: int) -> np.ndarray:
        k = ("P", decay, rad)
        if k not in self._c:
            self._c[k] = sum(decay ** (self.te - y) * v for y, v in self.Y[rad].items())
        return self._c[k]

    def Pn(self, decay: float, rad: int) -> np.ndarray:
        k = ("Pn", decay, rad)
        if k not in self._c:
            self._c[k] = norm(self.P(decay, rad))
        return self._c[k]

    def E(self, decay: float) -> np.ndarray:
        k = ("E", decay)
        if k not in self._c:
            w = {y: decay ** (self.te - y) for y in self.Y[100]}
            yv = sum(w[y] * self.Y[100][y] for y in w)
            e = sum(w.values())
            self._c[k] = norm((self.a + yv) / (self.a / self.mu0 + e))
        return self._c[k]

    def S1(self, c: dict) -> np.ndarray:
        return self.Pn(c["decay"], c["rad"]) + c["wE"] * self.E(c["decay"]) + c["wpop"] * self.POP + c["wdef"] * self.DEF

    def S2(self, c: dict) -> np.ndarray:
        a, b, g = OPT["s2_weights"][c["s2"]]
        return a * self.MU0 + b * self.POP + g * self.DEF + 1e-9 * self.POP


# ------------------------------------------------------------------ selection
def nms_csr(order: np.ndarray, N: sparse.csr_matrix, limit: int, blocked: np.ndarray | None = None) -> list[int]:
    ind, ptr = N.indices, N.indptr
    bl = np.zeros(N.shape[0], bool) if blocked is None else blocked.copy()
    sel: list[int] = []
    if limit <= 0:
        return sel
    for i in order.tolist():
        if not bl[i]:
            sel.append(i)
            bl[ind[ptr[i]:ptr[i + 1]]] = True
            if len(sel) >= limit:
                break
    return sel


def mclp_csr(w: np.ndarray, N: sparse.csr_matrix, limit: int) -> list[int]:
    """Lazy-greedy max coverage; demand = pool points weighted by w, candidate i covers N[i]."""
    ind, ptr = N.indices, N.indptr
    covered = np.zeros(len(w), bool)
    gain = N.dot(w.astype(np.float32))
    heap = [(-float(g), i, 0) for i, g in enumerate(gain) if g > 0]
    heapq.heapify(heap)
    sel, ver = [], 0
    while heap and len(sel) < limit:
        g, i, v = heapq.heappop(heap)
        nb = ind[ptr[i]:ptr[i + 1]]
        if v != ver:
            g2 = float(w[nb[~covered[nb]]].sum())
            if g2 > 0:
                heapq.heappush(heap, (-g2, i, ver))
            continue
        sel.append(i)
        covered[nb] = True
        ver += 1
    return sel


def generate(ctx: Ctx, c: dict, k: int) -> np.ndarray:
    """Boolean mask of k sites. c: decay, rad, wE, wpop, wdef, sel(nms|mclp), rho, f, s2."""
    S1 = ctx.S1(c)
    k2 = int(round(c["f"] * k))
    k1 = k - k2
    Nrho = ctx.N[c["rho"]]
    if c["sel"] == "nms":
        base = nms_csr(np.argsort(-S1, kind="stable"), Nrho, k1)
    else:
        base = mclp_csr(S1, ctx.N[OPT["radius_m"]], k1)
    chosen = list(base)
    if k2 > 0:
        bl = np.zeros(len(S1), bool)
        bl[np.asarray(chosen, int)] = True
        bl = (Nrho @ bl.astype(np.float32)) > 0
        S2 = ctx.S2(c)
        chosen += nms_csr(np.argsort(-S2, kind="stable"), Nrho, k2, bl)
    if len(chosen) < k:  # exhausted: fill by S1 rank
        seen = np.zeros(len(S1), bool)
        seen[np.asarray(chosen, int)] = True
        rest = np.argsort(-S1, kind="stable")
        rest = rest[~seen[rest]][: k - len(chosen)]
        chosen += rest.tolist()
    m = np.zeros(len(S1), bool)
    m[np.asarray(chosen[:k], int)] = True
    return m


def cov_vec(A: sparse.csr_matrix, mask: np.ndarray) -> np.ndarray:
    return (A.dot(mask.astype(np.int8)) > 0).astype(float)


def strata(cov: np.ndarray, cas: np.ndarray, new: np.ndarray) -> tuple[float, float]:
    return float((cov * cas).sum() / cas.sum()), float((cov * cas * new).sum() / max((cas * new).sum(), 1e-9))


# ------------------------------------------------------------------ folds
NTRAIN: int = 0


def set_ntrain(h: pd.DataFrame, tr: int) -> None:
    """N for top-N harmonization from TRAINING years only: median hotspot count over 2021..tr (outer train end)."""
    global NTRAIN
    NTRAIN = int(np.ceil(np.median([int((h.year == y).sum()) for y in range(2021, tr + 1)])))


def hv(h: pd.DataFrame, train_end: int) -> pd.DataFrame:
    t = h[h.year <= train_end]
    keep = [g if y >= 2021 else g.sort_values(["cas", "occ"], ascending=False, kind="stable").head(NTRAIN) for y, g in t.groupby("year")]
    return pd.concat(keep)


def year_set(h: pd.DataFrame, y: int) -> pd.DataFrame:
    """Hotspots of year y, harmonized like training history (pre-2021: top-N by casualties)."""
    N = NTRAIN
    g = h[h.year == y]
    if y >= 2021:
        return g.reset_index(drop=True)
    return g.sort_values(["cas", "occ"], ascending=False, kind="stable").head(N).reset_index(drop=True)


def make_fold(d: dict, static: pd.DataFrame, test_df: pd.DataFrame, train_end: int):
    hist = hv(d["hot"], train_end)
    ctx = Ctx(d, hist, static, train_end)
    txy = hav(test_df.lat.values, test_df.lon.values)
    new = run.nearest_m(txy, hav(hist.lat.values, hist.lon.values)) > CFG["radius_m"]
    A = csr_cover(txy, ctx.xy, OPT["radius_m"])
    return ctx, A, test_df.cas.values.astype(float), new.astype(float)


def configs_stage1(rng: np.random.Generator) -> list[dict]:
    combos = list(itertools.product(OPT["wE"], OPT["wpop"], OPT["wdef"]))
    pick = [combos[i] for i in rng.choice(len(combos), OPT["n_stage1_weight_combos"], replace=False)]
    forced = (0, OPT["wpop"][0], 0)  # past-only (+ pop tie-break) = past_k analogue
    if forced not in pick:
        pick.append(forced)
    return [dict(decay=dc, rad=rd, wE=a, wpop=b, wdef=g, sel="nms", rho=300, f=0.0, s2="mu0")
            for dc in OPT["decays"] for rd in OPT["past_radii"] for a, b, g in pick]


def inner_J(inner, c: dict) -> float:
    js = []
    for ctx, A, cas, new in inner:
        for k in OPT["obj_ks"]:
            a, n = strata(cov_vec(A, generate(ctx, c, k)), cas, new)
            js.append(0.5 * a + 0.5 * n)
    return float(np.mean(js))


def key(c: dict) -> str:
    return json.dumps(c, sort_keys=True)


def tune(inner) -> list[dict]:
    rng = np.random.default_rng(OPT["seed"])
    res: dict[str, dict] = {}

    def ev(c: dict) -> None:
        kk = key(c)
        if kk not in res:
            res[kk] = dict(cfg=c, J=inner_J(inner, c))

    for c in configs_stage1(rng):
        ev(c)
    s1 = sorted(res.values(), key=lambda r: -r["J"])
    for r in s1[: OPT["stage2_top"]]:
        for rho in OPT["rhos"]:
            for f in OPT["fracs"]:
                for s2 in (OPT["s2_weights"] if f > 0 else ["mu0"]):
                    ev({**r["cfg"], "sel": "nms", "rho": rho, "f": f, "s2": s2})
    for r in s1[: OPT["stage2_mclp_top"]]:
        for f in OPT["fracs"]:
            for s2 in (OPT["s2_weights"] if f > 0 else ["mu0"]):
                ev({**r["cfg"], "sel": "mclp", "rho": 300, "f": f, "s2": s2})
    return sorted(res.values(), key=lambda r: -r["J"])


def best_in(res: list[dict], pred) -> dict:
    return next(r for r in res if pred(r["cfg"]))


# ------------------------------------------------------------------ job
G: dict = {}


def job(args: tuple[int, str]) -> dict:
    te, pname = args
    t0 = time.time()
    d, st = G["d"], G["static"][pname]
    h = d["hot"]
    tr = te - 1
    set_ntrain(h, tr)
    inner = []
    yrs = (tr, tr - 1) if OPT["inner_mode"] == "two" else tuple(range(tr, 2017, -1))
    for y_in in yrs:
        inner.append(make_fold(d, st, year_set(h, y_in), y_in - 1))
    logger.info("fold %d pool %s inner built (%.0fs) pool n=%d", te, pname, time.time() - t0, len(inner[0][0].pool))
    res = tune(inner)
    logger.info("fold %d pool %s tuned (%.0fs) best J=%.4f", te, pname, time.time() - t0, res[0]["J"])
    del inner
    test = h[h.year == te].reset_index(drop=True)
    ctx, A, cas, new = make_fold(d, st, test, tr)
    out: dict = dict(fold=te, pool=pname, cas=cas, new=new, cov={}, choice={})
    # oracle ceilings (greedy MCLP on test casualties; not exact bounds) and reachability
    reach = (np.asarray(A.sum(1)).ravel() > 0).astype(float)
    out["reach"] = strata(reach, cas, new)
    Ac = A.tocsc()
    inv = [Ac.indices[Ac.indptr[i]:Ac.indptr[i + 1]] for i in range(A.shape[1])]
    for tag, wv in (("oracle", cas), ("oracle_new", cas * new)):
        sel = run.greedy_mclp(wv, inv, OPT["k_main"])
        m = np.zeros(A.shape[1], bool); m[sel] = True
        out["cov"][(tag, OPT["k_main"])] = cov_vec(A, m)
    out["npool"] = A.shape[1]
    # subspace arms chosen on inner only
    arms = {
        "blend": best_in(res, lambda c: c["sel"] == "nms" and c["f"] == 0 and c["rho"] == 300),
        "mclp": best_in(res, lambda c: c["sel"] == "mclp" and c["f"] == 0),
        "split": best_in(res, lambda c: c["f"] > 0),
        "best": res[0],
    }
    for an, r in arms.items():
        out["choice"][an] = dict(cfg=r["cfg"], inner_J=r["J"])
        ks = OPT["ks"] if an == "best" else [OPT["k_main"]]
        for k in ks:
            out["cov"][(f"opt_{an}", k)] = cov_vec(A, generate(ctx, r["cfg"], k))
    # baselines in this pool
    rng = np.random.default_rng(OPT["seed"] + te)
    for k in OPT["ks"]:
        order = np.lexsort((rng.random(len(ctx.pool)), -ctx.dens65))
        m = np.zeros(len(ctx.pool), bool); m[order[:k]] = True
        out["cov"][("pop65_k", k)] = cov_vec(A, m)
        sc = ctx.P(CFG["decay"], 300)
        o = np.argsort(-sc, kind="stable"); o = o[sc[o] > 0]
        sel = run.nms(o, ctx.xy, CFG["radius_m"], k)
        sel = run.rank_fill(sel, ctx.dens65, k)
        m = np.zeros(len(ctx.pool), bool); m[sel] = True
        out["cov"][("past_k", k)] = cov_vec(A, m)
    if pname == "C":
        zxy = hav(d["zones"].LATITUDE.values, d["zones"].LONGITUDE.values)
        out["cov"][("current", OPT["k_main"])] = (csr_cover(hav(test.lat.values, test.lon.values), zxy, CFG["radius_m"]).sum(1).A1 > 0).astype(float)
        R = run.rankings(d, ctx.hist, ctx.pool, ctx.F.assign(dong_sido=""), tr, rng, True, max(OPT["ks"]))
        for k in OPT["ks"]:
            for nm in ("ours_full", "ours_nohist"):
                m = np.zeros(len(ctx.pool), bool); m[R[nm][:k]] = True
                out["cov"][(nm, k)] = cov_vec(A, m)
    if te == 2024:
        m = generate(ctx, res[0]["cfg"], OPT["k_main"])
        out["sites"] = ctx.pool[["lat", "lon"]][m].reset_index(drop=True)
    logger.info("fold %d pool %s done (%.0fs)", te, pname, time.time() - t0)
    return out


# ------------------------------------------------------------------ aggregate
def boot_idx(folds: np.ndarray, B: int, rng: np.random.Generator) -> np.ndarray:
    """(B, n) resample counts, stratified by fold; same draws reused for all methods (paired)."""
    n = len(folds)
    cnt = np.zeros((B, n))
    for f in np.unique(folds):
        ix = np.where(folds == f)[0]
        dr = ix[rng.integers(0, len(ix), size=(B, len(ix)))]
        for b in range(B):
            cnt[b] += np.bincount(dr[b], minlength=n)
    return cnt


def aggregate(results: list[dict]) -> None:
    folds_all = sorted({r["fold"] for r in results})
    rng = np.random.default_rng(OPT["seed"])
    # method label -> fold -> cov vec ; C-pool and D-pool baselines distinguished
    M: dict[tuple, dict[int, np.ndarray]] = {}
    meta = {}
    choice = {}
    ceil_rows = []
    for r in results:
        meta[r["fold"]] = (r["cas"], r["new"])
        choice[(r["fold"], r["pool"])] = r["choice"]
        for (nm, k), v in r["cov"].items():
            if nm in ("current",) and r["pool"] != "C":
                continue
            lab = f"{nm}@{r['pool']}" if nm not in ("current",) else nm
            M.setdefault((lab, k), {})[r["fold"]] = v
        ceil_rows.append(dict(fold=r["fold"], pool=r["pool"], n_pool=r["npool"], reach_all=r["reach"][0], reach_new=r["reach"][1],
                              oracle_all=strata(r["cov"][("oracle", OPT["k_main"])], r["cas"], r["new"])[0],
                              oracle_new_on_new=strata(r["cov"][("oracle_new", OPT["k_main"])], r["cas"], r["new"])[1]))
    # best_selected: per fold choose pool arm by inner J
    for k in OPT["ks"]:
        M[("best_selected", k)] = {}
    sel_pool = {}
    for f in folds_all:
        jc, jd = choice[(f, "C")]["best"]["inner_J"], choice[(f, "D")]["best"]["inner_J"]
        p = "C" if jc >= jd else "D"
        sel_pool[f] = p
        for k in OPT["ks"]:
            M[("best_selected", k)][f] = M[(f"opt_best@{p}", k)][f]
    pd.DataFrame(ceil_rows).to_csv(OUT / "ceiling.csv", index=False)
    cas = np.concatenate([meta[f][0] for f in folds_all])
    new = np.concatenate([meta[f][1] for f in folds_all])
    fid = np.concatenate([np.full(len(meta[f][0]), f) for f in folds_all])
    cnt = boot_idx(fid, OPT["n_boot"], rng)

    def vec(lab: str, k: int) -> np.ndarray | None:
        dd = M.get((lab, k))
        if dd is None or any(f not in dd for f in folds_all):
            return None
        return np.concatenate([dd[f] for f in folds_all])

    def cov_ci(v: np.ndarray, stratum: str) -> tuple[float, float, float]:
        w = cas * (new if stratum == "new" else 1.0)
        pt = (w * v).sum() / w.sum()
        bs = (cnt * (w * v)).sum(1) / (cnt * w).sum(1)
        return pt, np.percentile(bs, 2.5), np.percentile(bs, 97.5)

    def delta_ci(v: np.ndarray, u: np.ndarray, stratum: str) -> tuple[float, float, float]:
        w = cas * (new if stratum == "new" else 1.0)
        pt = (w * (v - u)).sum() / w.sum()
        bs = (cnt * (w * (v - u))).sum(1) / (cnt * w).sum(1)
        return 100 * pt, 100 * np.percentile(bs, 2.5), 100 * np.percentile(bs, 97.5)

    labels = ["current", "past_k@C", "pop65_k@C", "ours_full@C", "past_k@D", "pop65_k@D", "opt_blend@C", "opt_blend@D", "opt_mclp@C",
              "opt_mclp@D", "opt_split@C", "opt_split@D", "opt_best@C", "opt_best@D", "best_selected", "past_k@G", "pop65_k@G", "opt_best@G"]
    rows = []
    for lab in labels:
        for k in OPT["ks"]:
            v = vec(lab, k)
            if v is None:
                continue
            for st in ("all", "new"):
                pt, lo, hi = cov_ci(v, st)
                rows.append(dict(method=lab, k=k, stratum=st, scope="pooled", coverage=pt, lo=lo, hi=hi))
                for f in folds_all:
                    msk = fid == f
                    w = cas[msk] * (new[msk] if st == "new" else 1.0)
                    rows.append(dict(method=lab, k=k, stratum=st, scope=str(f), coverage=(w * v[msk]).sum() / w.sum(), lo=np.nan, hi=np.nan))
    cov_df = pd.DataFrame(rows)
    cov_df.to_csv(OUT / "opt_coverage.csv", index=False)
    drows = []
    for cand in ("best_selected", "opt_best@C", "opt_best@D", "opt_blend@C", "opt_blend@D", "opt_mclp@C", "opt_mclp@D",
                 "opt_split@C", "opt_split@D", "ours_full@C"):
        for ref in ("current", "past_k@C", "pop65_k@C", "past_k@D", "pop65_k@D", "ours_full@C"):
            for k in OPT["ks"]:
                v, u = vec(cand, k), vec(ref, k if ref != "current" else OPT["k_main"])
                if v is None or u is None or cand == ref or (ref == "current" and k != OPT["k_main"]):
                    continue
                for st in ("all", "new"):
                    dp, lo, hi = delta_ci(v, u, st)
                    drows.append(dict(cand=cand, ref=ref, k=k, stratum=st, delta_pp=dp, lo_pp=lo, hi_pp=hi, sig=("+" if lo > 0 else "-" if hi < 0 else "0")))
    pd.DataFrame(drows).to_csv(OUT / "opt_deltas.csv", index=False)
    json.dump({f"{f}_{p}": v for (f, p), v in choice.items()}, open(OUT / "opt_choices.json", "w"), indent=1, default=str)
    json.dump({"selected_pool_by_inner": sel_pool}, open(OUT / "opt_selected_pool.json", "w"))
    pd.to_pickle(dict(M=M, meta=meta), OUT / "opt_per_hotspot.pkl")


EXPS = {
    "e0_k3918_fixN": dict(obj="k3918", inner_mode="two", thr=200),
    "e1_curve": dict(obj="curve", inner_mode="two", thr=200),  # PROMOTION CANDIDATE (declared before running)
    "e2_curve_inner2018": dict(obj="curve", inner_mode="ext", thr=200),
    "e3_curve_thr100": dict(obj="curve", inner_mode="two", thr=100),
    "e4_curve_thr500": dict(obj="curve", inner_mode="two", thr=500),
}


def promote(results: list[dict], d: dict) -> None:
    """Write promoted CSVs in the run.py schema (variant=harm only; pool 'OPT' = per-fold pool chosen by inner J)."""
    P = ROOT / "promoted"
    P.mkdir(parents=True, exist_ok=True)
    cov = pd.read_csv(OUT / "opt_coverage.csv")
    cov = cov[cov.scope == "pooled"]
    meta = pd.read_pickle(OUT / "opt_per_hotspot.pkl")["meta"]
    cas = np.concatenate([meta[f][0] for f in sorted(meta)]); new = np.concatenate([meta[f][1] for f in sorted(meta)])
    nh = {"all": (len(cas), cas.sum()), "new": (int(new.sum()), (cas * new).sum())}
    amap = {"current": ("C", "current"), "past_k@C": ("C", "past_k"), "pop65_k@C": ("C", "pop65_k"), "ours_full@C": ("C", "ours_full"),
            "past_k@D": ("D", "past_k"), "pop65_k@D": ("D", "pop65_k"), "best_selected": ("OPT", "ours_opt")}
    rows = []
    for _, r in cov[cov.method.isin(amap)].iterrows():
        pl, mt = amap[r.method]
        rows.append(dict(variant="harm", stratum=r.stratum, pool=pl, method=mt, k=r.k, coverage=r.coverage, lo=r.lo, hi=r.hi,
                         n_hot=nh[r.stratum][0], cas=nh[r.stratum][1]))
    pd.DataFrame(rows).to_csv(P / "coverage_results.csv", index=False)
    dl = pd.read_csv(OUT / "opt_deltas.csv")
    dl = dl[(dl.cand == "best_selected") & dl.ref.isin(amap)]
    drows = [dict(variant="harm", stratum=r.stratum, pool="OPT", method="ours_opt", k=r.k, vs=":".join(amap[r.ref]), delta_pp=r.delta_pp,
                  lo_pp=r.lo_pp, hi_pp=r.hi_pp) for _, r in dl.iterrows()]
    pd.DataFrame(drows).to_csv(P / "delta_results.csv", index=False)
    # map (fold 2024, k=3918): the 2024 fold's selected pool's sites
    sp = json.load(open(OUT / "opt_selected_pool.json"))["selected_pool_by_inner"]["2024"]
    sites = next(r["sites"] for r in results if r["fold"] == 2024 and r["pool"] == sp)
    dg = d["dong"]
    _, ix = BallTree(hav(dg.lat.values, dg.lon.values), metric="haversine").query(hav(sites.lat.values, sites.lon.values), k=1)
    ours = sites.assign(kind="ours", sido=dg.sido.values[ix[:, 0]], zone_nm="")
    z = d["zones"]
    cur = pd.DataFrame({"lat": z.LATITUDE, "lon": z.LONGITUDE, "kind": "current", "sido": z.CTPRVN_NM, "zone_nm": z.TRGET_FCLTY_NM})
    t24 = d["hot"][d["hot"].year == 2024]
    hs = pd.DataFrame({"lat": t24.lat, "lon": t24.lon, "kind": "hotspot_2024", "sido": "", "zone_nm": t24.ACDNT_AREA_LC_NM, "cas": t24.cas})
    allp = pd.concat([cur, ours, hs], ignore_index=True)
    sz = allp[allp.lat.between(37.41, 37.72) & allp.lon.between(126.76, 127.19)].copy()
    cx, ox = hav(cur.lat.values, cur.lon.values), hav(ours.lat.values, ours.lon.values)
    sz["cur_within_300m"] = (run.nearest_m(hav(sz.lat.values, sz.lon.values), cx) <= 300).astype(int)
    sz["ours_within_300m"] = (run.nearest_m(hav(sz.lat.values, sz.lon.values), ox) <= 300).astype(int)
    sz[["lat", "lon", "kind", "sido", "zone_nm", "cas", "cur_within_300m", "ours_within_300m"]].to_csv(
        P / "map_seoul_current_vs_ours_k3918_fold2024.csv", index=False, encoding="utf-8-sig")


def main(name: str) -> None:
    global OUT
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    random.seed(OPT["seed"]); np.random.seed(OPT["seed"])
    e = EXPS[name]
    OUT = ROOT / "v2" / name
    OUT.mkdir(parents=True, exist_ok=True)
    OPT["obj_ks"] = [OPT["k_main"]] if e["obj"] == "k3918" else list(OPT["ks"])
    OPT["inner_mode"] = e["inner_mode"]
    OPT["grid_min_dens65_per_km2"] = e["thr"]
    (OUT / "protocol_opt.json").write_text(json.dumps(dict(exp=name, spec=e, opt=OPT, code_sha256=sha(Path(__file__)),
                                                           note="Fixed before running; N for harmonization from training years only."), indent=1))
    d = run.load_all()
    cwall = pd.read_csv(run.SZ / "kr_15028201_crosswalk_std.csv", usecols=["LATITUDE", "LONGITUDE"]).dropna()
    cwall = cwall[cwall.LATITUDE.between(33, 39) & cwall.LONGITUDE.between(124, 132)]
    dg = d["dong"]
    grid = grid_points(run.GEOJSON, set(dg.adm_cd2[dg.dens65 >= e["thr"]]), OPT["grid_m"])
    z = d["zones"][["LATITUDE", "LONGITUDE"]].rename(columns={"LATITUDE": "lat", "LONGITUDE": "lon"}).assign(src=1)
    dd = dg[["lat", "lon"]].assign(src=2)
    cwp = cwall.rename(columns={"LATITUDE": "lat", "LONGITUDE": "lon"}).assign(src=3)
    G["d"] = d
    G["static"] = {"C": pd.concat([z, dd], ignore_index=True), "D": pd.concat([z, dd, cwp, grid.assign(src=4)], ignore_index=True)}
    logger.info("exp %s grid=%d static D=%d", name, len(grid), len(G["static"]["D"]))
    jobs = [(te, p) for te in CFG["test_years"] for p in ("D", "C")]
    with mp.get_context("fork").Pool(6) as pool:
        results = pool.map(job, jobs, chunksize=1)
    pd.to_pickle(results, OUT / "opt_results.pkl")
    aggregate(results)
    if name == "e1_curve":
        promote(results, d)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


if __name__ == "__main__":
    for nm in (list(EXPS) if sys.argv[1] == "all" else [sys.argv[1]]):
        main(nm)
