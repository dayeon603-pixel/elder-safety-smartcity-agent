"""Budgeted night-coverage optimisation (hypothesis: model output under stated assumptions).

Objective (population only; no outcome data is used, hence no train/test split is needed for selection):
  maximise  sum_b p65_b * u_b * 1[b within R of a chosen 24h AED]
  u_b = share of 'night states' (hours 22-06 x {Wed-like weekday 5/7, Sat 1/7, Sun 1/7}) at which b is NOT covered.
Policies, equal budget B in {1%, 5%} of Seoul AEDs (115, 575; one unit = one site):
  convert  : B existing sites become 24h (greedy max coverage)           [baselines: random, top-B by static ball pop]
  relocate : remove the B non-24h sites with least daytime-unique 65+ coverage, add B new 24h sites (greedy)
Selection is evaluated EXACTLY afterwards by re-running multi-source Dijkstra (independent of the ball matrices).
"""
from __future__ import annotations

import json
import logging
import multiprocessing as mp
import time

import numpy as np
import pandas as pd
import scipy.sparse as sp
from scipy.sparse.csgraph import dijkstra

import engine as E
import w4lib as L

log = logging.getLogger("walk4.opt")
RK = L.PRIMARY
NSTATE_W = {"wd": 5 / 7, "sat": 1 / 7, "sun": 1 / 7}
_G: dict = {}


def _ball(args):
    """Demand points within walking distance rad of node: Dijkstra on the Euclidean-window subgraph (path >= Euclid)."""
    node, rad = args
    if rad <= 0:
        return np.empty(0, np.int32)
    xy0 = _G["xy"][int(node)]
    win = np.asarray(_G["tree"].query_ball_point(xy0, rad), dtype=np.int64)
    sub = _G["A"][win][:, win]
    src = int(np.flatnonzero(win == node)[0])
    d = dijkstra(sub, directed=True, indices=src, limit=float(rad))
    full = _G.setdefault("scratch", np.full(_G["A"].shape[0], np.inf)); full[win] = d
    nd = win[d <= rad]
    st = _G["ptr"][nd]; cnt = _G["ptr"][nd + 1] - st
    if cnt.sum() == 0:
        full[win] = np.inf
        return np.empty(0, np.int32)
    idx = np.repeat(st - (np.cumsum(cnt) - cnt), cnt) + np.arange(cnt.sum())
    dem_i = _G["order"][idx]
    ok = full[_G["dnode"][dem_i]] + _G["dsnap"][dem_i] <= rad
    out = dem_i[ok].astype(np.int32); full[win] = np.inf
    return out


def ball_matrix(nodes: np.ndarray, rads: np.ndarray, nproc: int = 8) -> sp.csc_matrix:
    with mp.get_context("fork").Pool(nproc) as pool:
        cols = pool.map(_ball, list(zip(nodes, rads)), chunksize=64)
    indptr = np.r_[0, np.cumsum([len(c) for c in cols])]
    return sp.csc_matrix((np.ones(indptr[-1], np.float32), np.concatenate(cols), indptr), shape=(len(_G["dnode"]), len(nodes)))


def greedy(M: sp.csc_matrix, w: np.ndarray, B: int) -> tuple[list[int], list[float]]:
    """Greedy weighted max coverage. w = p65 * u (updated). Returns picks and marginal gains."""
    w = w.astype(np.float64).copy(); Mr = M.tocsr(); gain = np.asarray(M.T @ w).ravel()
    picks, gains = [], []
    for _ in range(B):
        c = int(np.argmax(gain))
        if gain[c] <= 0:
            break
        picks.append(c); gains.append(float(gain[c]))
        b = M.indices[M.indptr[c]:M.indptr[c + 1]]
        delta = w[b].copy(); w[b] = 0
        if len(b):
            gain -= np.asarray(Mr[b].T @ delta).ravel()
        gain[c] = -1
    return picks, gains


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    t0 = time.time()
    I = E.load_inputs(); sites = I["sites"]; eng = E.Engine(I["A"], sites)
    dem_all = I["dem"]
    dem = dem_all[dem_all.p65_w_main > 0].reset_index(drop=True)
    p65 = dem.p65_w_main.to_numpy(); ptot = dem.ptot_w_main.to_numpy()
    R = L.RADII[RK]
    order = np.argsort(dem.node.to_numpy(), kind="stable"); nsorted = dem.node.to_numpy()[order]
    nn = I["A"].shape[0]
    ptr = np.searchsorted(nsorted, np.arange(nn + 1))
    from scipy.spatial import cKDTree
    _G.update(tree=cKDTree(np.c_[I["x"], I["y"]]), xy=np.c_[I["x"], I["y"]], A=I["A"], ptr=ptr, order=order, dnode=dem.node.to_numpy(), dsnap=dem.snap.to_numpy())
    S = I["sched"]["primary"]
    ns = len(sites)
    log.info("sites %d, demand pts %d (p65 %.0f, ptot %.0f)", ns, len(dem), p65.sum(), ptot.sum())

    def covered_exact(mask, radius=R):
        return (eng.dist(mask)[dem.node.to_numpy()] + dem.snap.to_numpy()) <= radius

    def states(hours):
        return [(dt, h, wt / len(hours)) for dt, wt in NSTATE_W.items() for h in hours for wt in [NSTATE_W[dt]]]

    _cache: dict = {}
    NIGHT_S = states(L.NIGHT); DAY_S = states(L.DAY)

    def eval_exact(mask24: np.ndarray, removed: np.ndarray | None = None, add_nodes=None):
        """Exact coverage (65+ and total) by day/night for a modified AED set. mask24: sites forced 24h."""
        out = {}
        for name, ss in (("night", NIGHT_S), ("day", DAY_S)):
            c65 = ct = 0.0
            for dt, h, w in ss:
                m = S[dt][:, h] | mask24
                if removed is not None:
                    m = m & ~removed
                key = (np.packbits(m).tobytes(), None if add_nodes is None else add_nodes.tobytes())
                if key not in _cache:
                    if add_nodes is not None:
                        d = eng.dist(m, extra_nodes=add_nodes, extra_snap=np.zeros(len(add_nodes)))
                        cov = (d[dem.node.to_numpy()] + dem.snap.to_numpy()) <= R
                    else:
                        cov = covered_exact(m)
                    _cache[key] = ((p65 * cov).sum(), (ptot * cov).sum())
                a, b_ = _cache[key]
                c65 += w * a; ct += w * b_
            out[name] = {"p65": c65 / p65.sum(), "ptot": ct / ptot.sum()}
        return out

    base = eval_exact(np.zeros(ns, bool))
    allopen = eval_exact(np.ones(ns, bool))   # upper bound: every site 24h
    log.info("baseline %s ; all-24h %s", base, allopen)
    # u_b: night uncovered share per demand point
    u = np.zeros(len(dem))
    for dt, h, w in NIGHT_S:
        u += w * ~covered_exact(S[dt][:, h])
    # ---- convert
    always24 = np.ones(ns, bool)
    for dt, h, w in NIGHT_S:
        always24 &= S[dt][:, h]
    cand = np.flatnonzero(~always24)
    log.info("convert candidates %d of %d sites (not open all night states)", len(cand), ns)
    rad_c = R - sites.snap.to_numpy()[cand]
    ok = rad_c > 0
    cand, rad_c = cand[ok], rad_c[ok]
    Mc = ball_matrix(sites.node.to_numpy()[cand], rad_c)
    log.info("ball matrix convert: nnz %d, %.0fs", Mc.nnz, time.time() - t0)
    BMAX = 1200
    picks, gains = greedy(Mc, p65 * u, BMAX)
    budgets = [0, 25, 57, 115, 230, 345, 575, 800, 1200]
    n_aed = int(len(I["aed"]))
    res = {"n_aed": n_aed, "n_sites": ns, "R_m": R, "base": base, "all24": allopen,
           "night_uncov_p65_initial": float((p65 * u).sum() / p65.sum()), "budgets": budgets, "convert": {}, "random": {}, "topstatic": {}, "relocate": {}}
    cum = np.cumsum(gains)
    # greedy curve from the ball model (fast) for all budgets; random + top-static baselines in the same model
    rng = np.random.default_rng(L.SEED)
    gain0 = np.asarray(Mc.T @ (p65 * u)).ravel()
    def model_gain(sel):
        sel = np.asarray(sel, int)
        if len(sel) == 0:
            return 0.0
        covd = np.asarray(Mc[:, sel].sum(axis=1)).ravel() > 0
        return float((p65 * u * covd).sum() / p65.sum())
    for B in budgets:
        res["convert"][B] = {"model_night_gain_p65": float(cum[min(B, len(cum)) - 1] / p65.sum()) if B > 0 else 0.0}
        rs = [model_gain(rng.choice(len(cand), B, replace=False)) for _ in range(30)] if B > 0 else [0.0]
        res["random"][B] = {"mean": float(np.mean(rs)), "lo": float(np.percentile(rs, 2.5)), "hi": float(np.percentile(rs, 97.5))}
        res["topstatic"][B] = {"model_night_gain_p65": model_gain(np.argsort(-gain0)[:B])}
    # exact evaluation for the named budgets (independent recompute)
    for B in (115, 575):
        m24 = np.zeros(ns, bool); m24[cand[picks[:B]]] = True
        res["convert"][B]["exact"] = eval_exact(m24)
        rsel = rng.choice(len(cand), B, replace=False)
        m24r = np.zeros(ns, bool); m24r[cand[rsel]] = True
        res["random"][B]["exact_one_draw"] = eval_exact(m24r)
        res["convert"][B]["picked_by_gu"] = sites.iloc[cand[picks[:B]]].gugun.value_counts().to_dict()
    pd.DataFrame({"site_row": cand[picks], "org": sites.org.to_numpy()[cand[picks]], "gu": sites.gugun.to_numpy()[cand[picks]],
                  "addr": sites.buildAddress.to_numpy()[cand[picks]], "gain_p65": gains}).to_csv(L.OUT / "convert_picks.csv", index=False)
    log.info("convert done %.0fs", time.time() - t0)

    # ---- relocate: remove least daytime-unique non-24h sites, add best new 24h sites
    Mall = ball_matrix(sites.node.to_numpy(), R - sites.snap.to_numpy())
    log.info("ball matrix all sites nnz %d %.0fs", Mall.nnz, time.time() - t0)
    wd12 = S["wd"][:, 12]; sat12 = S["sat"][:, 12]
    uniq = np.zeros(ns)
    Mcsr = Mall.tocsr()
    for st in (wd12, sat12):
        cnt = np.asarray(Mcsr @ st.astype(np.float32)).ravel()
        one = (cnt == 1)
        # unique contribution of s: sum_b p65_b [cnt_b==1] M[b,s] st[s]
        uniq += np.asarray(Mall.T @ (p65 * one)).ravel() * st / 2
    removable = np.flatnonzero(~np.all([S[dt][:, h] for dt in ("wd", "sat", "sun") for h in range(24)], axis=0))
    rem_order = removable[np.argsort(uniq[removable], kind="stable")]
    # new-site candidates: one node per 100 m cell holding demand (node of the heaviest building)
    cell = (dem.x.to_numpy() // 100).astype(np.int64) * 100000 + (dem.y.to_numpy() // 100).astype(np.int64)
    top = pd.DataFrame({"cell": cell, "w": p65, "node": dem.node.to_numpy()}).sort_values("w", ascending=False).drop_duplicates("cell")
    new_nodes = top.node.to_numpy()
    log.info("new-site candidates %d", len(new_nodes))
    Mn = ball_matrix(new_nodes, np.full(len(new_nodes), R))
    log.info("ball matrix new nnz %d %.0fs", Mn.nnz, time.time() - t0)
    for B in (115, 575):
        rem = np.zeros(ns, bool); rem[rem_order[:B]] = True
        # u after removal (model): uncovered if no open, non-removed site ball contains b
        u2 = np.zeros(len(dem))
        for dt, h, w in NIGHT_S:
            m = (S[dt][:, h] & ~rem).astype(np.float32)
            u2 += w * (np.asarray(Mcsr @ m).ravel() == 0)
        pk, gn = greedy(Mn, p65 * u2, B)
        add = new_nodes[pk]
        ex = eval_exact(np.zeros(ns, bool), removed=rem, add_nodes=add)
        # removal only (no add) for transparency
        ex_rm = eval_exact(np.zeros(ns, bool), removed=rem)
        res["relocate"][B] = {"exact": ex, "removal_only_exact": ex_rm, "removed_uniq_day_p65_sum": float(uniq[rem].sum()),
                              "n_added": int(len(add))}
        pd.DataFrame({"x": I["x"][add], "y": I["y"][add], "gain_p65": gn}).to_csv(L.OUT / f"relocate_new_B{B}.csv", index=False)
        np.save(L.OUT / f"relocate_removed_B{B}.npy", np.flatnonzero(rem))
    np.save(L.OUT / "convert_pick_sites.npy", cand[picks])
    np.save(L.OUT / "u_night.npy", u)
    (L.OUT / "opt.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    log.info("saved %.0fs", time.time() - t0)


if __name__ == "__main__":
    main()
