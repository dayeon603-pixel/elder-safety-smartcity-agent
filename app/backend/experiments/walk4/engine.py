"""Multi-source Dijkstra coverage engine (super-source trick) on the densified pedestrian graph."""
from __future__ import annotations

import numpy as np
import pandas as pd
import scipy.sparse as sp
from scipy.sparse.csgraph import dijkstra

import w4lib as L

EPS = 1e-3  # explicit zero weights are dropped by csgraph


def load_inputs(keep: np.ndarray | None = None) -> dict:
    A = sp.load_npz(L.OUT / "graph.npz").tocsr()
    z = np.load(L.OUT / "graph_xy.npz")
    aed = pd.read_pickle(L.OUT / "aed_snapped.pkl")
    sched = dict(np.load(L.OUT / "sched.npz"))
    if keep is not None:  # e.g. AEDs registered by a cutoff year; schedules are filtered row-wise
        aed = aed[keep].reset_index(drop=True); sched = {k: v[keep] for k, v in sched.items()}
    dem = pd.read_parquet(L.OUT / "demand.parquet")
    scen = {}
    for k in sched:
        a, b = k.split("__"); scen.setdefault(a, {})[b] = sched[k]
    # sites = AEDs sharing exact coordinates (indoor duplicates add no coverage); open if any AED there is open
    key = aed.wgs84Lat.round(6).astype(str) + "_" + aed.wgs84Lon.round(6).astype(str)
    site_id, uniq = pd.factorize(key)
    n_site = len(uniq)
    first = np.zeros(n_site, int); first[site_id[::-1]] = np.arange(len(aed))[::-1]
    sites = aed.iloc[first].reset_index(drop=True)[["x", "y", "node", "snap", "org", "gugun", "buildAddress"]].copy()
    sites["n_aed"] = np.bincount(site_id, minlength=n_site)
    S = {}
    for sc, o in scen.items():
        S[sc] = {}
        for dt, m in o.items():
            M = np.zeros((n_site, 24), bool)
            np.logical_or.at(M, site_id, m)
            S[sc][dt] = M
    return {"A": A, "x": z["x"], "y": z["y"], "aed": aed, "sites": sites, "site_id": site_id, "sched": S, "dem": dem}


class Engine:
    def __init__(self, A: sp.csr_matrix, sites: pd.DataFrame):
        self.n = A.shape[0]
        self.A = A
        self.node = sites.node.to_numpy(); self.snap = sites.snap.to_numpy()

    def dist(self, mask: np.ndarray, limit: float = L.R_MAX, extra_nodes=None, extra_snap=None) -> np.ndarray:
        """Shortest walking distance (m) from the nearest OPEN site (incl. site snap leg) to every node."""
        nodes = self.node[mask]; snap = self.snap[mask]
        if extra_nodes is not None:
            nodes = np.r_[nodes, extra_nodes]; snap = np.r_[snap, extra_snap]
        return self._run(nodes, snap, limit)

    def _run(self, nodes: np.ndarray, snap: np.ndarray, limit: float) -> np.ndarray:
        if len(nodes) == 0:
            return np.full(self.n, np.inf)
        o = np.lexsort((snap, nodes)); nodes, snap = nodes[o], snap[o]
        first = np.r_[True, nodes[1:] != nodes[:-1]]
        nodes, snap = nodes[first], snap[first]
        A = self.A
        indptr = np.r_[A.indptr, A.indptr[-1] + len(nodes)]
        indices = np.r_[A.indices, nodes]
        data = np.r_[A.data, snap + EPS]
        G = sp.csr_matrix((data, indices, indptr), shape=(self.n + 1, self.n + 1))
        d = dijkstra(G, directed=True, indices=self.n, limit=limit)
        return d[: self.n]
