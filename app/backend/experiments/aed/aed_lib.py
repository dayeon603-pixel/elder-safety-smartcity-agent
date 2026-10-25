"""Loaders + schedule math for module C' (AED 시간대 공백).

Units: events are 시군구 x hour (소방청 구급활동정보 gutHh = dispatch hour, a proxy for the collapse hour).
All counts are rates/shares; no money. Responses from the API are untrusted: only parsed as JSON.
"""
from __future__ import annotations

import glob
import gzip
import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/chloekang/kcf/data/cache/extra")
ACT_DIR = ROOT / "ems/api/act"
AED_DIR = ROOT / "ems/api/aed"
BOUND = ROOT / "boundaries/skorea_municipalities_geo_simple.json"
POP_CSV = ROOT / "mois_pop/sgg_1100000000_202312.csv"

CA_SYMPTOMS = {"심정지", "호흡정지"}
HOME = "집"
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun", "hol"]
HOURS = np.arange(24)
MID = HOURS * 100 + 30  # an AED counts as open in hour h if open at h:30
NIGHT = [22, 23, 0, 1, 2, 3, 4, 5]  # 22-06


# ---------------------------------------------------------------- geography
def seoul_gu() -> pd.DataFrame:
    """25 Seoul 구: code, name, area_km2, centroid. Area from a local equirectangular projection (error < 0.5%)."""
    j = json.loads(BOUND.read_text(encoding="utf-8"))
    rows = []
    for f in j["features"]:
        p = f["properties"]
        if not str(p["code"]).startswith("11"):
            continue
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        area = 0.0; cx = cy = wsum = 0.0
        for poly in polys:
            for k, ring in enumerate(poly):
                a = np.asarray(ring, float)
                lat0 = np.deg2rad(a[:, 1].mean())
                x = np.deg2rad(a[:, 0]) * 6371.0088 * np.cos(lat0); y = np.deg2rad(a[:, 1]) * 6371.0088
                s = 0.5 * np.sum(x[:-1] * y[1:] - x[1:] * y[:-1])
                sign = 1.0 if k == 0 else -1.0  # holes subtract
                area += sign * abs(s)
                if k == 0:
                    cx += a[:, 0].mean() * abs(s); cy += a[:, 1].mean() * abs(s); wsum += abs(s)
        rows.append({"code": p["code"], "gu": p["name"], "area_km2": area, "lon": cx / wsum, "lat": cy / wsum})
    df = pd.DataFrame(rows).sort_values("code").reset_index(drop=True)
    assert len(df) == 25, len(df)
    return df


def seoul_pop() -> pd.Series:
    """Resident population by 구 (MOIS 2023-12, known at end of train window)."""
    df = pd.read_csv(POP_CSV, encoding="cp949")
    out = {}
    for _, r in df.iterrows():
        m = re.search(r"\s(\S+구)\s*\(", str(r.iloc[0]))
        if m:
            out[m.group(1)] = float(str(r.iloc[1]).replace(",", ""))
    return pd.Series(out)


# ---------------------------------------------------------------- events
def load_events(gu_names: set[str]) -> tuple[pd.DataFrame, dict]:
    """All cached Seoul 구급활동 rows (incident 구 in Seoul) + audit counts."""
    frames = []
    n_files = 0
    for f in sorted(glob.glob(str(ACT_DIR / "*.json.gz"))):
        with gzip.open(f, "rt", encoding="utf-8") as g:
            d = json.load(g)
        it = d.get("items", [])
        if not isinstance(it, list) or not it:
            continue
        n_files += 1
        frames.append(pd.DataFrame(it)[["egrcSidoCdNm", "egrcSiggCdNm", "gutHh", "gutYm", "ruptSptmCdNm",
                                         "ruptOccrPlcCdNm", "rsacGutFsttOgidNm", "sptMvmnDtc"]])
    raw = pd.concat(frames, ignore_index=True)
    audit = {"files": n_files, "rows_pulled_all_sido": int(len(raw))}
    df = raw[(raw.egrcSidoCdNm == "서울특별시") & raw.egrcSiggCdNm.isin(gu_names)].copy()
    audit["rows_seoul_incident"] = int(len(df))
    df["year"] = df.gutYm.str[:4].astype(int)
    h = pd.to_numeric(df.gutHh, errors="coerce")
    audit["hour_missing"] = int(h.isna().sum())
    df["hour"] = h
    df = df[h.notna()].copy(); df["hour"] = df.hour.astype(int)
    sy = df.ruptSptmCdNm.fillna("").str.split(",")
    df["ca"] = sy.map(lambda xs: any(x.strip() in CA_SYMPTOMS for x in xs))
    df["ca_sim"] = sy.map(lambda xs: any(x.strip() == "심정지" for x in xs))
    df["place"] = df.ruptOccrPlcCdNm
    df["public"] = df.ca & df.place.notna() & (df.place != HOME)
    # de-duplicate nothing: no record id exists (ops cannot be joined); document instead.
    return df, audit


# ---------------------------------------------------------------- AED
def _t(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return np.nan
    return v if 0 <= v <= 2400 and v % 100 < 60 else np.nan


def load_aed() -> pd.DataFrame:
    rows = []
    for f in sorted(glob.glob(str(AED_DIR / "full_*.json.gz"))):
        with gzip.open(f, "rt", encoding="utf-8") as g:
            j = json.load(g)
        it = j["response"]["body"]["items"]
        it = it["item"] if isinstance(it, dict) else it
        rows += it if isinstance(it, list) else [it]
    df = pd.DataFrame(rows)
    return df


def open_matrix(df: pd.DataFrame, day: str) -> tuple[np.ndarray, np.ndarray]:
    """(open[n,24] bool, known[n] bool) for one weekday key. Window [s,e): open in hour h if open at h:30.
    Overnight windows (s>e) wrap. s==e or unparseable -> unknown (not open)."""
    s = df[f"{day}SttTme"].map(_t).to_numpy()[:, None]
    e = df[f"{day}EndTme"].map(_t).to_numpy()[:, None]
    known = (~np.isnan(s) & ~np.isnan(e) & (s != e))[:, 0]
    m = MID[None, :]
    with np.errstate(invalid="ignore"):
        day_ = (s < e) & (s <= m) & (m < e)
        over = (s > e) & ((m >= s) | (m < e))
    return (day_ | over) & known[:, None], known


def schedules(df: pd.DataFrame) -> dict:
    """Per-AED open fraction by hour for weekday (Mon-Fri mean) and weekend (Sat,Sun mean) + placeholder flag."""
    om, kn = {}, {}
    for d in DAYS:
        om[d], kn[d] = open_matrix(df, d)
    wd = np.mean([om[d] for d in DAYS[:5]], axis=0)
    we = np.mean([om["sat"], om["sun"]], axis=0)
    hol = om["hol"].astype(float)

    def full(day: str) -> np.ndarray:
        s = df[f"{day}SttTme"].map(_t).to_numpy(); e = df[f"{day}EndTme"].map(_t).to_numpy()
        return (s == 0) & (e == 2400)

    wd24 = np.all([full(d) for d in DAYS[:5]], axis=0)
    we24 = full("sat") & full("sun")
    hol24 = full("hol")
    placeholder = (~wd24) & we24  # weekday not 24h but weekend 24h
    wd_known = np.all([kn[d] for d in DAYS[:5]], axis=0)
    return {"wd": wd, "we": we, "hol": hol, "wd24": wd24, "we24": we24, "hol24": hol24, "placeholder": placeholder,
            "wd_known": wd_known, "kn_sat": kn["sat"], "kn_sun": kn["sun"], "kn_hol": kn["hol"],
            "wd_any_known": np.any([kn[d] for d in DAYS[:5]], axis=0)}


def we_variants(S: dict) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Weekend schedule under three treatments of the suspicious 0000-2400 placeholder.
    returns name -> (open_fraction[n,24], counted_in_denominator[n])."""
    ph = S["placeholder"]
    n = len(ph)
    W1 = S["we"]
    W2 = np.where(ph[:, None], S["wd"], S["we"])  # unknown -> assume weekday hours
    W3 = np.where(ph[:, None], 0.0, S["we"])      # unknown -> not counted open (lower bound), and dropped from denominator
    return {"W1_as_reported": (W1, np.ones(n, bool)), "W2_placeholder_as_weekday": (W2, np.ones(n, bool)),
            "W3_placeholder_unknown_dropped": (W3, ~ph)}


# ---------------------------------------------------------------- stats
def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"),) * 2
    p = k / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h
