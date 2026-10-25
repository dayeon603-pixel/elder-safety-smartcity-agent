"""walk4: "걸어서 4분" AED coverage. Shared loaders.

Conventions
- Metric CRS EPSG:5179 (meters). Distances are shortest walking paths on the OSM pedestrian network
  (c) OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright).
- Coverage and all numbers below are model outputs under stated assumptions: hypotheses, not measurements.
- No money, so float is fine. No outcome data is used anywhere except the validation step.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.sparse as sp
from scipy.sparse.csgraph import connected_components

log = logging.getLogger("walk4")

ROOT = Path("/Users/chloekang/kcf/data/cache/extra")
OSM = ROOT / "osm"
DONG_GJ = ROOT / "boundaries/HangJeongDong_ver20261001.geojson"
POP_CSV = Path("/Users/chloekang/kcf/data/cache/j_population_regional/kr_15097972_resident_pop_by_dong_sex_age_202609.csv")
OUT = Path(__file__).parent / "out"
CRS_M = "EPSG:5179"
SEED = 20261024

SPEEDS = {"0.8": 0.8, "1.2": 1.2}          # m/s
T_ONEWAY = {"2min": 120.0, "4min": 240.0}   # s, one-way
RADII = {f"v{v}_{t}": s * T for v, s in SPEEDS.items() for t, T in T_ONEWAY.items()}  # meters
R_MAX = max(RADII.values())
PRIMARY = "v1.2_2min"
NIGHT = [22, 23, 0, 1, 2, 3, 4, 5]
DAY = list(range(9, 18))


def pop_by_dong() -> pd.DataFrame:
    """Seoul 행정동 total and 65+ population (MOIS resident registration, 2026-09 snapshot)."""
    d = pd.read_csv(POP_CSV, encoding="cp949")
    d = d[d["시도명"] == "서울특별시"].copy()
    cols65 = []
    for c in d.columns:
        m = re.match(r"^(\d+)세(남자|여자)$", c)
        if m and int(m.group(1)) >= 65:
            cols65.append(c)
        elif c in ("110세이상 남자", "110세이상 여자"):
            cols65.append(c)
    assert len(cols65) == 2 * 45 + 2, len(cols65)  # 65..109 x 2 sexes + 110+ x 2
    for c in ["계"] + cols65:
        d[c] = pd.to_numeric(d[c].astype(str).str.replace(",", ""), errors="raise")
    out = pd.DataFrame({"adm_cd2": d["행정기관코드"].astype(str), "gu": d["시군구명"], "dong": d["읍면동명"],
                        "pop_tot": d["계"].astype(float), "pop65": d[cols65].sum(axis=1).astype(float)})
    assert out.pop_tot.sum() > 9e6 and (out.pop65 <= out.pop_tot).all()
    return out


def graph_csr(G) -> tuple[sp.csr_matrix, np.ndarray, np.ndarray]:
    """Undirected CSR (meters) over graph nodes, restricted later to the largest component by caller."""
    nodes = list(G.nodes)
    idx = {n: i for i, n in enumerate(nodes)}
    u, v, w = [], [], []
    for a, b, d in G.edges(data=True):
        u.append(idx[a]); v.append(idx[b]); w.append(float(d["length"]))
    n = len(nodes)
    u, v, w = np.array(u), np.array(v), np.array(w)
    w = np.maximum(w, 0.01)  # explicit zeros are dropped by csgraph
    # parallel edges: keep the shortest
    df = pd.DataFrame({"u": np.r_[u, v], "v": np.r_[v, u], "w": np.r_[w, w]}).groupby(["u", "v"], sort=False).w.min().reset_index()
    A = sp.csr_matrix((df.w.to_numpy(), (df.u.to_numpy(), df.v.to_numpy())), shape=(n, n))
    xs = np.array([G.nodes[k]["x"] for k in nodes]); ys = np.array([G.nodes[k]["y"] for k in nodes])
    return A, xs, ys


def largest_component(A: sp.csr_matrix) -> np.ndarray:
    _, lab = connected_components(A, directed=False)
    return lab == np.bincount(lab).argmax()
