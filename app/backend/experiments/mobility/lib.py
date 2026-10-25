"""Mobility module (고령자 이동 인프라): bus-stop access, smart-stop (BIT/shelter) siting, 저상버스 gap.

Conventions
- All distances in EPSG:5179 meters. Walk time -> straight-line radius = v * t / detour (detour = 1.3 unless the
  OSM network is used, which exists for Seoul only).
- Backtests/optimisations here are hypotheses, not proofs. The siting optimiser is evaluated on the same objective
  it maximises (in-sample by construction); the only independent checks are the Seoul network re-evaluation and
  the Seoul 119 outcome correlation.
- Residents are not geocoded. Each 행정동 population is spread uniformly over a point grid inside its polygon.
"""

from __future__ import annotations

import heapq
import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.sparse as sp
import shapely
from pyproj import Transformer
from scipy.spatial import cKDTree

logger = logging.getLogger("mobility")

SEED = 42
HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
FIGS = HERE / "figs"
CACHE = Path("/Users/chloekang/kcf/data/cache")
STOPS_CSV = CACHE / "g_transport_mobility/kr_15067528_bus_stops_nationwide_2025.csv"
POP_CSV = CACHE / "j_population_regional/kr_15097972_resident_pop_by_dong_sex_age_202609.csv"
GEOJSON = CACHE / "extra/boundaries/HangJeongDong_ver20261001.geojson"
LOWFLOOR_CSV = CACHE / "d_disability_access/kr_15106061_low_floor_bus_by_region.csv"
SPECIAL_CSV = CACHE / "d_disability_access/kr_15066774_special_transport_service_2024.csv"
VULN_CSV = CACHE / "d_disability_access/kr_15106043_mobility_impaired_pop_by_region.csv"
HEAT_OUT = HERE.parent / "heat" / "out"
WALK4_OUT = HERE.parent / "walk4" / "out"

WALK_SPEED_MS = 0.8  # elderly walking speed given in the task
WALK_MIN = 5.0
DETOUR = 1.3  # straight-line -> path length factor when no pedestrian network is available
DETOUR_BAND = (1.15, 1.5)  # sensitivity band
NET_LIMIT_M = WALK_SPEED_MS * WALK_MIN * 60.0  # 240 m of walking
KOREA_BBOX = (33.0, 39.0, 124.0, 132.0)  # lat_min, lat_max, lon_min, lon_max (stops outside are bad coords)
MIN_P65_DONG = 100  # below this a 행정동 share is flagged 자료 부족 (small-n rule)
CELL_M = 100.0
MAX_PTS = 400

TO_M = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)


def radius_m(detour: float = DETOUR) -> float:
    """Straight-line radius equivalent to a 5-minute walk at 0.8 m/s."""
    return NET_LIMIT_M / detour


# ----------------------------------------------------------------------------------------------- population
def _age_cols(cols: list[str], lo: int) -> list[str]:
    out: list[str] = []
    for c in cols:
        m = re.match(r"^(\d+)세(남자|여자)$", c)
        if m and int(m.group(1)) >= lo:
            out.append(c)
        elif c in ("110세이상 남자", "110세이상 여자"):
            out.append(c)
    return out


def load_pop() -> pd.DataFrame:
    """행정동 total, 65+, 70+ residents (2026-09). Numeric parsing is strict (thousands separators)."""
    d = pd.read_csv(POP_CSV, encoding="cp949", thousands=",")
    c65, c70 = _age_cols(list(d.columns), 65), _age_cols(list(d.columns), 70)
    assert len(c65) == 2 * 45 + 2 and len(c70) == 2 * 40 + 2, (len(c65), len(c70))
    out = pd.DataFrame(
        {
            "adm_cd2": d["행정기관코드"].astype(str),
            "pop": d["계"].astype(float),
            "p65": d[c65].sum(axis=1).astype(float),
            "p70": d[c70].sum(axis=1).astype(float),
        }
    )
    assert (out.p65 <= out["pop"]).all() and not out.isna().any().any()
    return out


def load_dong_geoms() -> tuple[pd.DataFrame, list]:
    """2026 행정동 polygons projected to EPSG:5179 (shapely geometries) + attribute table."""
    with open(GEOJSON, encoding="utf-8") as fh:
        gj = json.load(fh)
    rows, geoms = [], []
    for f in gj["features"]:
        p = f["properties"]
        g = shapely.geometry.shape(f["geometry"])
        g = shapely.transform(g, lambda a: np.column_stack(TO_M.transform(a[:, 0], a[:, 1])))
        if not g.is_valid:
            g = shapely.make_valid(g)
        rows.append(dict(adm_cd2=str(p["adm_cd2"]), sido=p["sidonm"], sgg=p["sggnm"].replace(" ", ""),
                         dong=p["adm_nm"].split()[-1], area_m2=g.area))
        geoms.append(g)
    return pd.DataFrame(rows), geoms


@dataclass
class Points:
    """Resident sample points. `w65`/`w70` are persons represented by each point."""

    x: np.ndarray
    y: np.ndarray
    dong_idx: np.ndarray
    w65: np.ndarray
    w70: np.ndarray
    cell_m: np.ndarray


def sample_points(dongs: pd.DataFrame, geoms: list) -> Points:
    """Uniform grid inside each polygon; spacing >= CELL_M and <= MAX_PTS points per 행정동.

    ASSUMPTION (stated limit): residents are spread uniformly over the polygon. For large rural 행정동 the true
    population sits in villages near stops, so coverage there is understated (lower bound) and the grid is coarse
    (cell_m is stored so those dongs can be flagged).
    """
    xs, ys, di, ws65, ws70, cm = [], [], [], [], [], []
    for i, g in enumerate(geoms):
        s = max(CELL_M, float(np.sqrt(g.area / MAX_PTS))) if g.area > 0 else CELL_M
        minx, miny, maxx, maxy = g.bounds
        gx = np.arange(minx + s / 2, maxx, s)
        gy = np.arange(miny + s / 2, maxy, s)
        X, Y = np.meshgrid(gx, gy)
        X, Y = X.ravel(), Y.ravel()
        keep = shapely.contains_xy(g, X, Y) if len(X) else np.zeros(0, bool)
        px, py = X[keep], Y[keep]
        if len(px) == 0:
            rp = g.representative_point()
            px, py = np.array([rp.x]), np.array([rp.y])
        n = len(px)
        xs.append(px); ys.append(py); di.append(np.full(n, i)); cm.append(np.full(n, s))
        ws65.append(np.full(n, dongs.p65.iat[i] / n)); ws70.append(np.full(n, dongs.p70.iat[i] / n))
    return Points(np.concatenate(xs), np.concatenate(ys), np.concatenate(di), np.concatenate(ws65),
                  np.concatenate(ws70), np.concatenate(cm))


# ----------------------------------------------------------------------------------------------- stops
def load_stops(geoms: list) -> tuple[pd.DataFrame, dict]:
    """Bus stops: drop NaN/zero/out-of-Korea coords, de-duplicate by id, project, assign 행정동 (exact polygon test)."""
    raw = pd.read_csv(STOPS_CSV, encoding="cp949")
    rep: dict = {"raw": int(len(raw))}
    d = raw.rename(columns={"정류장번호": "stop_id", "정류장명": "name", "위도": "lat", "경도": "lon", "도시명": "city"})
    d = d.drop_duplicates("stop_id")
    rep["dup_id"] = rep["raw"] - len(d)
    nan = d.lat.isna() | d.lon.isna()
    zero = (d.lat == 0) | (d.lon == 0)
    la0, la1, lo0, lo1 = KOREA_BBOX
    out = ~((d.lat.between(la0, la1)) & (d.lon.between(lo0, lo1)))
    bad = nan | zero | out
    rep.update(dropped_nan=int(nan.sum()), dropped_zero=int(zero.sum()), dropped_outside_korea=int((out & ~nan).sum()),
               dropped_total=int(bad.sum()))
    d = d[~bad].copy().reset_index(drop=True)
    x, y = TO_M.transform(d.lon.to_numpy(), d.lat.to_numpy())
    d["x"], d["y"] = x, y
    # candidate dedup: same coordinates (opposite-direction poles etc.) collapse to one candidate, first id kept
    d["loc_key"] = d.lat.round(6).astype(str) + "," + d.lon.round(6).astype(str)
    rep["same_location_extra_poles"] = int(d.duplicated("loc_key").sum())
    tree = shapely.STRtree(geoms)
    pts = shapely.points(d.x.to_numpy(), d.y.to_numpy())
    pair = tree.query(pts, predicate="within")  # (2, k): input idx, tree idx
    dong = np.full(len(d), -1)
    dong[pair[0][::-1]] = pair[1][::-1]  # first match wins (reverse assign)
    d["dong_idx"] = dong
    rep["unassigned_to_dong"] = int((dong < 0).sum())
    return d, rep


def nearest_dist(px: np.ndarray, py: np.ndarray, sx: np.ndarray, sy: np.ndarray) -> np.ndarray:
    """Distance (m) from each resident point to the nearest stop."""
    if len(sx) == 0:
        return np.full(len(px), np.inf)
    t = cKDTree(np.column_stack([sx, sy]))
    return t.query(np.column_stack([px, py]), k=1)[0]


def incidence(sx: np.ndarray, sy: np.ndarray, px: np.ndarray, py: np.ndarray, r: float) -> sp.csr_matrix:
    """Binary stops x points incidence (point within r of stop)."""
    ts = cKDTree(np.column_stack([sx, sy]))
    tp = cKDTree(np.column_stack([px, py]))
    m = ts.sparse_distance_matrix(tp, r, output_type="coo_matrix")
    m = sp.coo_matrix((np.ones(m.nnz, dtype=np.float32), (m.row, m.col)), shape=(len(sx), len(px)))
    return m.tocsr()


# ----------------------------------------------------------------------------------------------- optimisation
def lazy_greedy(inc: sp.csr_matrix, w: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Greedy maximum coverage with lazy re-evaluation (exact greedy; (1-1/e) guarantee for the weighted MCLP).

    Returns picked stop indices (in pick order) and marginal gains. Picks are nested: the first N are the greedy
    solution for budget N.
    """
    M, P = inc.shape
    covered = np.zeros(P, dtype=bool)
    gain0 = np.asarray(inc @ w).ravel()
    heap = [(-g, i) for i, g in enumerate(gain0) if g > 0]
    heapq.heapify(heap)
    picks: list[int] = []
    gains: list[float] = []
    ip, ix = inc.indptr, inc.indices
    while heap and len(picks) < n:
        neg, i = heapq.heappop(heap)
        cols = ix[ip[i]:ip[i + 1]]
        g = float(w[cols[~covered[cols]]].sum())
        if g <= 0:
            continue
        if not heap or g >= -heap[0][0] - 1e-12:
            picks.append(i); gains.append(g); covered[cols] = True
        else:
            heapq.heappush(heap, (-g, i))
    return np.array(picks, dtype=int), np.array(gains)


def covered_mask(inc: sp.csr_matrix, idx: np.ndarray) -> np.ndarray:
    """Points covered by at least one selected stop."""
    if len(idx) == 0:
        return np.zeros(inc.shape[1], dtype=bool)
    return np.asarray(inc[idx].sum(axis=0)).ravel() > 0


def share(mask: np.ndarray, w: np.ndarray) -> float:
    """Weighted share of points covered."""
    tot = float(w.sum())
    return float(w[mask].sum() / tot) if tot > 0 else float("nan")


# ----------------------------------------------------------------------------------------------- stats
def spearman_ci(x: np.ndarray, y: np.ndarray, n_boot: int = 5000, seed: int = SEED) -> dict:
    """Spearman rho with percentile bootstrap CI (pairs resampled) and a two-sided permutation p-value."""
    from scipy.stats import rankdata, spearmanr

    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = ~(np.isnan(x) | np.isnan(y))
    x, y = x[ok], y[ok]
    n = len(x)
    if n < 4 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return dict(n=int(n), rho=float("nan"), lo=float("nan"), hi=float("nan"), p_perm=float("nan"))
    rho = float(spearmanr(x, y).statistic)
    rng = np.random.default_rng(seed)
    rx, ry = rankdata(x), rankdata(y)
    bs = []
    for _ in range(n_boot):
        i = rng.integers(0, n, n)
        a, b = x[i], y[i]
        if np.ptp(a) == 0 or np.ptp(b) == 0:
            continue
        bs.append(spearmanr(a, b).statistic)
    perm = np.array([np.corrcoef(rx, ry[rng.permutation(n)])[0, 1] for _ in range(n_boot)])
    p = float((np.sum(np.abs(perm) >= abs(rho) - 1e-12) + 1) / (n_boot + 1))
    return dict(n=int(n), rho=rho, lo=float(np.percentile(bs, 2.5)), hi=float(np.percentile(bs, 97.5)), p_perm=p)


def min_detectable_rho(n: int, seed: int = SEED, sims: int = 4000, alpha: float = 0.05, power: float = 0.8) -> float:
    """Smallest true Pearson-on-ranks rho detected with `power` by a permutation-free t-test at n (bivariate normal)."""
    from scipy.stats import t as tdist

    rng = np.random.default_rng(seed)
    crit = tdist.ppf(1 - alpha / 2, n - 2)
    for rho in np.arange(0.1, 0.95, 0.02):
        z = rng.standard_normal((sims, n))
        e = rng.standard_normal((sims, n))
        y = rho * z + np.sqrt(1 - rho**2) * e
        zc, yc = z - z.mean(1, keepdims=True), y - y.mean(1, keepdims=True)
        r = (zc * yc).sum(1) / np.sqrt((zc**2).sum(1) * (yc**2).sum(1))
        tt = r * np.sqrt((n - 2) / (1 - r**2))
        if np.mean(np.abs(tt) > crit) >= power:
            return float(rho)
    return float("nan")


# ----------------------------------------------------------------------------------------------- heat weights
def heat_unit_weights(dongs: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame]:
    """Per-행정동 heat weight from the heat engine (read-only).

    Risk intensity of a heat unit (시군구) = mean daily expected cases of the 우리 엔진 (OURS), observation-only
    variant O (no D+1 forecast proxy, so no LOOKAHEAD RISK), test year 2025 (trained on 2019-2024) summed over
    the 2025 season, divided by the unit's 65+ residents. Weight = 0.5 + percentile rank (0..1) of that intensity
    among the 250 units: the top unit counts 3x the bottom unit. The 0.5 floor and linear form are a design choice,
    not estimated; results are also reported with all weights = 1.
    Dongs in units the heat engine dropped (ambiguous city labels) get the neutral weight 1.0 and are flagged.
    """
    sys.path.insert(0, str(HERE.parent / "heat"))
    import build_panel as bp  # noqa: E402 (heat package, read-only import)

    d = dongs.rename(columns={"sido": "new_sido", "sgg": "new_sgg"}).copy()
    d = bp.assign_unit(d)
    pred = pd.read_csv(HEAT_OUT / "test_predictions.csv.gz")
    pred = pred[(pred.method == "OURS") & (pred.variant == "O") & (pred.test_year == 2025) & (pred.budget_kind == "per_day")]
    season = pred.groupby("region_code").score.sum().rename("exp_cases_2025")
    panel = pd.read_csv(HEAT_OUT / "panel.csv.gz", usecols=["unit_id", "unit_key"]).drop_duplicates()
    u = season.reset_index().merge(panel, left_on="region_code", right_on="unit_id")
    p65_unit = d.groupby("unit_key").p65.sum().rename("p65_unit")
    u = u.merge(p65_unit, on="unit_key", how="left")
    u["intensity"] = u.exp_cases_2025 / u.p65_unit.where(u.p65_unit > 0)
    u["heat_pct"] = u.intensity.rank(pct=True)
    u["heat_w"] = 0.5 + u.heat_pct
    unit_tbl = u[["unit_key", "exp_cases_2025", "p65_unit", "intensity", "heat_pct", "heat_w"]]
    m = d[["adm_cd2", "unit_key"]].merge(unit_tbl[["unit_key", "heat_pct", "heat_w"]], on="unit_key", how="left")
    m["heat_missing"] = m.heat_w.isna()
    m["heat_w"] = m.heat_w.fillna(1.0)
    return m.set_index("adm_cd2"), unit_tbl
