"""Hourly rain panel from cached API허브 kma_sfctm2 text (untrusted input; parsed with strict field checks).

RN (col 16) = hourly precip mm for the hour ending at TM (Apr-Oct); -9/-99 = missing -> NaN (never 0).
Station coordinates: API허브 stn_inf.php returned 403 (not applied), so we map KMA stn no = last 3 digits of WMO 47xxx
from Meteostat stations_lite (coords there). Stations without coordinates are dropped and counted.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd

from common import EXTRA, haversine_km

HUB = EXTRA / "rain_kma" / "hub_sfctm2"
T0 = pd.Timestamp("2025-06-01 00:00")
NH = 122 * 24
RN_IDX = 15  # 0-based whitespace field of RN (verified against the help=1 header)
TA_IDX, RNDAY_IDX = 11, 16


MANUAL = {93: (37.95, 127.75), 100: (37.677, 128.718), 102: (37.967, 124.63), 104: (37.805, 128.855), 108: (37.571, 126.966),
          112: (37.477, 126.625), 115: (37.48, 130.90), 133: (36.372, 127.372), 135: (36.22, 127.995), 143: (35.885, 128.619),
          152: (35.558, 129.321), 156: (35.17, 126.89), 169: (34.685, 125.451), 185: (33.294, 126.163), 188: (33.386, 126.88),
          255: (35.30, 128.57)}  # [est., verify] approximate city/observatory coordinates for non-시군구 station names


def station_coords() -> pd.DataFrame:
    """ASOS stn no -> (lat, lon). Names from the data.go.kr ASOS guide table (cached txt); coords = centroid of the same-named
    시군구 in the 2026 boundaries, else MANUAL. Station-name -> 시군구 centroid is ~5-20 km coarse; fine for 시군구-level rain."""
    import re
    t = (EXTRA / "rain_kma" / "asos_guide_station_table.txt").read_text(encoding="utf-8")
    tab = t[t.find("# 첨부 지점코드"):].replace("\n", " ")
    names = {int(a): b for a, b in re.findall(r"(\d{2,3})\s*\|\s*([가-힣]+)\s*\|\s*[가-힣]+", tab)}
    sg = pd.read_csv(Path(__file__).resolve().parent / "out" / "sgg_table.csv")
    nn = sg["sgg"].str.replace(" ", "")
    rows = []
    for s, n in names.items():
        if s in MANUAL:
            rows.append(dict(stn=s, name=n, lat=MANUAL[s][0], lon=MANUAL[s][1], src="manual"))
            continue
        b = re.sub(r"(시|군)$", "", n)
        m = sg[nn.str.startswith(b) & nn.str.contains("[시군구]")]
        if len(m):
            rows.append(dict(stn=s, name=n, lat=m["lat"].mean(), lon=m["lon"].mean(), src=f"sgg x{len(m)}/{m['sido'].nunique()}sido"))
    return pd.DataFrame(rows)


def load_hourly() -> tuple[pd.DataFrame, dict]:
    """DataFrame hours x stn of RN (NaN=missing) + QC info."""
    recs, nfiles, nbad = [], 0, 0
    for p in sorted(HUB.glob("2025*.txt")):
        nfiles += 1
        for ln in p.read_text(encoding="euc-kr", errors="replace").splitlines():
            if not ln or ln[0] == "#":
                continue
            f = ln.split()
            if len(f) <= RN_IDX or len(f[0]) != 12 or not f[0].isdigit():
                nbad += 1
                continue
            try:
                recs.append((f[0], int(f[1]), float(f[RN_IDX]), float(f[TA_IDX]), float(f[RNDAY_IDX])))
            except ValueError:
                nbad += 1
    d = pd.DataFrame(recs, columns=["tm", "stn", "rn", "ta", "rnday"])
    d["t"] = pd.to_datetime(d["tm"], format="%Y%m%d%H%M", errors="coerce")
    d = d.dropna(subset=["t"])
    # RN=-9 means "no precipitation or missing" (IR code 3). If the station reported a valid air temperature that hour the
    # station was alive, so -9 -> 0.0 mm; otherwise NaN (missing). Validated below against RN_DAY at 23:00.
    alive = d["ta"] > -50
    d["rn"] = np.where(d["rn"] >= 0, d["rn"], np.where(alive, 0.0, np.nan))
    last = d[d["t"].dt.hour == 23].assign(day=lambda x: x["t"].dt.normalize())
    sm = d.assign(day=d["t"].dt.normalize()).groupby(["stn", "day"])["rn"].sum().rename("sum_rn").reset_index()
    chk = last.merge(sm, on=["stn", "day"]).query("rnday >= 0")
    qc_extra = dict(day_check_n=len(chk), day_check_abs_err_mean=float((chk["sum_rn"] - chk["rnday"]).abs().mean()),
                    day_check_within_1mm=float(((chk["sum_rn"] - chk["rnday"]).abs() <= 1.0).mean()))
    W = d.pivot_table(index="t", columns="stn", values="rn", aggfunc="max")
    W = W.reindex(pd.date_range(T0, periods=NH, freq="h"))
    return W, dict(files=nfiles, bad_lines=nbad, rows=len(d), **qc_extra)


def sgg_hourly(W: pd.DataFrame, sgg: pd.DataFrame, min_cov: float = 0.9, n_rank: int = 5) -> tuple[dict[str, np.ndarray], dict]:
    """Rolling 1/3/6/12/24h accumulations per station, then per 시군구 = nearest station with a valid value that hour."""
    sc = station_coords()
    keep = [s for s in W.columns if s in set(sc.stn) and W[s].notna().mean() >= min_cov]
    W = W[keep]
    sc = sc.set_index("stn").loc[keep]
    D = haversine_km(sgg.lat.to_numpy()[:, None], sgg.lon.to_numpy()[:, None], sc.lat.to_numpy()[None, :], sc.lon.to_numpy()[None, :])
    order = np.argsort(D, axis=1)[:, :n_rank]
    out = {}
    for w in (1, 3, 6, 12, 24):
        R = W.rolling(w, min_periods=max(1, int(np.ceil(0.75 * w)))).sum().to_numpy()   # hours x stations
        v = np.full((R.shape[0], len(sgg)), np.nan)
        for r in range(n_rank):
            v = np.where(np.isnan(v), R[:, order[:, r]], v)
        out[f"R{w}"] = v
    info = dict(n_station=len(keep), dist_med=float(np.median(D[np.arange(len(sgg)), order[:, 0]])),
                dist_p90=float(np.percentile(D[np.arange(len(sgg)), order[:, 0]], 90)), nan_frac=float(np.isnan(out["R12"]).mean()))
    return out, info
