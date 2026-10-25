"""Validate the 4-minute coverage model against 119 cardiac arrests (Seoul 2024, 소방청 구급활동정보).

Question: does the 구 x hour share of residents NOT within reach of an open AED track the rate of PUBLIC-place arrests?
Caveats are part of the result: event hour = dispatch hour (lags collapse), weekday unknown (blend 5:2), AED hours are a
2026 snapshot, exposure is residence-based while public arrests happen where people move, 구 x hour cells hold ~3 events.
Outcome data is used ONLY here (never to choose any parameter), so no train/test split is needed for the model itself.
"""
from __future__ import annotations

import json
import logging
import pickle
import sys

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

import w4lib as L

sys.path.insert(0, str(L.Path(__file__).parent.parent))
from aed import aed_lib as AL  # noqa: E402

log = logging.getLogger("walk4.val")
RNG = np.random.default_rng(L.SEED)


def events_2024(gus: list[str]) -> pd.DataFrame:
    ev, _ = AL.load_events(set(gus))
    ev = ev[ev.year == 2024]
    return ev


def blended_uncovered(res: dict, sc: str, radius: str, scheme: str, kind: str) -> np.ndarray:
    """[25, 24] uncovered share, weekday:weekend = 5:2 (Sat,Sun equal). Holidays folded into weekdays (unknown dates)."""
    j = res["names"].index((scheme, kind)); r = res["radii"].index(radius)
    c = res["cov"][sc][:, :, r, j, :]  # [dt, h, gu]
    den = res["denom"][j]
    wd, sat, sun = c[0], c[1], c[2]
    cov = (5 * wd + sat + sun) / 7.0 / den[None, :]
    return 1.0 - cov.T


def boot_ci(x: np.ndarray, y: np.ndarray, groups: np.ndarray, B: int = 2000) -> tuple[float, float, float]:
    """Spearman rho with 95% cluster-bootstrap CI (resample 구)."""
    rho = spearmanr(x, y).statistic
    ug = np.unique(groups); idx = {g: np.where(groups == g)[0] for g in ug}
    bs = []
    for _ in range(B):
        pick = RNG.choice(ug, len(ug), replace=True)
        ii = np.concatenate([idx[g] for g in pick])
        if np.ptp(x[ii]) == 0 or np.ptp(y[ii]) == 0:
            continue
        bs.append(spearmanr(x[ii], y[ii]).statistic)
    return float(rho), float(np.nanpercentile(bs, 2.5)), float(np.nanpercentile(bs, 97.5))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    res = pickle.load(open(L.OUT / "cov.pkl", "rb")); gus = res["gus"]
    ev = events_2024(gus)
    months = sorted(ev.gutYm.unique())
    log.info("2024 months present: %s ; rows %d", months, len(ev))
    n_months = len(months)
    pop = L.pop_by_dong().groupby("gu")[["pop_tot", "pop65"]].sum().loc[gus]
    pub = ev[ev.public]; home = ev[ev.ca & (ev.place == AL.HOME)]
    cnt = lambda d: np.array([[((d.egrcSiggCdNm == g) & (d.hour == h)).sum() for h in range(24)] for g in gus])
    Cpub, Chome = cnt(pub), cnt(home)
    out: dict = {"months": months, "n_public_ca": int(Cpub.sum()), "n_home_ca": int(Chome.sum())}
    # rate: events per 100k residents per hour-of-day per year-equivalent
    scale = 12.0 / n_months
    for kind, pc in (("ptot", pop.pop_tot), ("p65", pop.pop65)):
        pass
    results = []
    for sc in ("primary", "upper", "asreport"):
        for radius in ("v1.2_2min", "v1.2_4min", "v0.8_2min", "v0.8_4min"):
            for kind in ("ptot", "p65"):
                U = blended_uncovered(res, sc, radius, "w_main", kind)  # [gu, h]
                den = (pop.pop_tot if kind == "ptot" else pop.pop65).to_numpy()[:, None]
                Rpub = Cpub * scale / den * 1e5; Rhome = Chome * scale / den * 1e5
                g = np.repeat(np.arange(25), 24)
                row = {"scenario": sc, "radius": radius, "kind": kind}
                # (1) pooled cells, (2) within-구 (hour effect), (3) 구-level night, (4) 구-level day, (5) city-by-hour
                for name, R in (("public", Rpub), ("home_negctl", Rhome)):
                    rho, lo, hi = boot_ci(U.ravel(), R.ravel(), g)
                    row[f"cells_{name}"] = (rho, lo, hi)
                    Uw = U - U.mean(axis=0, keepdims=True); Rw = R - R.mean(axis=0, keepdims=True)  # remove hour-of-day effect
                    row[f"cells_hourdemeaned_{name}"] = boot_ci(Uw.ravel(), Rw.ravel(), g)
                    for nm, hrs in (("night", L.NIGHT), ("day", L.DAY)):
                        u = U[:, hrs].mean(axis=1); r_ = (Cpub if name == "public" else Chome)[:, hrs].sum(axis=1) * scale / den[:, 0] * 1e5
                        sr = spearmanr(u, r_)
                        row[f"gu_{nm}_{name}"] = (float(sr.statistic), float(sr.pvalue))
                city_U = (U * pop.pop_tot.to_numpy()[:, None]).sum(axis=0) / pop.pop_tot.sum()
                sr = spearmanr(city_U, Cpub.sum(axis=0)); row["city_by_hour_public"] = (float(sr.statistic), float(sr.pvalue))
                results.append(row)
    out["results"] = results
    # AED-vintage sensitivity: only AEDs whose serialSeq-date year <= 2024 (est.; field semantics unverified)
    aed = pd.read_pickle(L.OUT / "aed_snapped.pkl")
    yr = pd.to_numeric(aed.serialSeq.str.extract(r"^\d+-(\d{2})\d{4}-")[0], errors="coerce") + 2000
    out["serial_year_parse_rate"] = float(yr.notna().mean())
    out["n_aed_leq_2024_or_unparsed"] = int(((yr <= 2024) | yr.isna()).sum())
    out["Cpub_by_hour"] = Cpub.sum(axis=0).tolist(); out["Chome_by_hour"] = Chome.sum(axis=0).tolist()
    out["Cpub_by_gu"] = dict(zip(gus, Cpub.sum(axis=1).tolist()))
    out["uncovered_primary_citywide_by_hour_blended"] = (
        (blended_uncovered(res, "primary", L.PRIMARY, "w_main", "ptot") * pop.pop_tot.to_numpy()[:, None]).sum(axis=0) / pop.pop_tot.sum()).tolist()
    np.savez(L.OUT / "val_counts.npz", Cpub=Cpub, Chome=Chome)
    (L.OUT / "validation.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    log.info("saved validation.json")


if __name__ == "__main__":
    main()
