"""Hourly-rain analysis (API허브 kma_sfctm2). Leave-one-episode-out (LOEO) and forward-only; n = episodes, 1 year.

HYPOTHESIS TEST, NOT A PROOF. The baseline is an OBSERVED-accumulation proxy of the 호우특보 criterion, not the issued 특보
(wrn_met_data 403 until 활용신청 approved). Criterion (ko.wikipedia 기상특보 발표기준 table citing KMA; KMA's own page is
JS-rendered and could not be fetched): 주의보 6h>=70mm or 12h>=110mm; 경보 6h>=110mm or 12h>=180mm. Real 특보 is issued on
FORECAST ("예상될 때") for 특보구역, so an observed-accumulation rule is a nowcast: flattering on lead time, blind to forecasts.
Sensitivity: the 3h 60/90 variant suggested by the coordinator is NOT verified from a KMA source.
Episodes before 2025-06-01 (ep1, 05-16) have no hourly data -> excluded (n=7).
LOOKAHEAD RISK: hourly unit uses accumulation up to hour t to score rescues in hours t..t+2 (causal). Daily unit uses the
day's max accumulation (end-of-day knowledge) against that day's rescues (not causal; same for all methods).
LOOKAHEAD RISK: LOEO prior uses rescues of later episodes; 'forward' variant uses only earlier ones.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from sklearn.linear_model import PoissonRegressor

from analyze_rain import capture, prior_share, vulnerability
from common import OUT, SEED
from hourly_data import NH, T0, load_hourly, sgg_hourly

warnings.filterwarnings("ignore")
HOR = 3  # target horizon (hours): rescues in hours t..t+2
CRIT = {"adv": dict(r6=70, r12=110), "warn": dict(r6=110, r12=180), "adv3h": dict(r3=60, r12=110), "warn3h": dict(r3=90, r12=180)}
HFE = ["l1", "l3", "l6", "l12", "l24", "log_pop", "log_dens", "share65", "lprior"]
DFE = ["lmax3", "lmax6", "lmax12", "lday", "lprev12", "log_pop", "log_dens", "share65", "lprior"]


def fire(F: dict[str, np.ndarray], c: dict[str, float]) -> np.ndarray:
    m = np.zeros(F["R12"].shape, bool)
    for k, v in c.items():
        m |= np.nan_to_num(F["R" + k[1:]], nan=0.0) >= v
    return m


def build(sgg, day_events, hour_events):
    W, qc = load_hourly()
    F, info = sgg_hourly(W, sgg)
    codes = sgg.sgg_cd.to_numpy()
    ix = {c: i for i, c in enumerate(codes)}
    Y = np.zeros((NH, len(codes)))
    h = hour_events.copy()
    h["t"] = ((pd.to_datetime(h["date"]) - T0) / pd.Timedelta(hours=1) + h["hour"]).astype(int)
    h = h[(h.t >= 0) & (h.t < NH) & h.sgg_cd.isin(ix)]
    np.add.at(Y, (h.t.to_numpy(), h.sgg_cd.map(ix).to_numpy()), h["n"].to_numpy())
    Y3 = sum(np.vstack([Y[k:], np.zeros((k, Y.shape[1]))]) for k in range(HOR))
    return W, qc, F, info, Y, Y3, codes


def hourly_cells(F, Y3, vul, prior, t0, t1):
    t = np.arange(t0, t1)
    n = len(sgg_idx := np.arange(F["R1"].shape[1]))
    d = dict(t=np.repeat(t, n), s=np.tile(sgg_idx, len(t)), y=Y3[t0:t1].ravel())
    for w in (1, 3, 6, 12, 24):
        d[f"R{w}"] = np.nan_to_num(F[f"R{w}"][t0:t1].ravel(), nan=0.0)
        d[f"l{w}"] = np.log1p(d[f"R{w}"])
    c = pd.DataFrame(d)
    for k in ("log_pop", "log_dens", "share65"):
        c[k] = np.tile(vul[k].to_numpy(), len(t))
    c["lprior"] = np.log(np.tile(prior.to_numpy(), len(t)) * n)
    c["prior"] = np.tile(prior.to_numpy(), len(t))
    return c


def daily_cells(F, Y, vul, prior, d0, d1):
    n = F["R1"].shape[1]
    days = list(range(d0, d1 + 1))
    rows = []
    for d in days:
        a, b = d * 24, d * 24 + 24
        g = lambda w: np.nan_to_num(np.nanmax(F[f"R{w}"][a:b], axis=0), nan=0.0)  # noqa: E731
        prev = np.nan_to_num(np.nanmax(F["R12"][max(0, a - 24):a], axis=0), nan=0.0) if a else np.zeros(n)
        day_tot = np.nan_to_num(F["R24"][b - 1], nan=0.0)
        rows.append(pd.DataFrame(dict(day=d, s=np.arange(n), y=Y[a:b].sum(0), max3=g(3), max6=g(6), max12=g(12), day24=day_tot, prev12=prev)))
    c = pd.concat(rows, ignore_index=True)
    for k in ("max3", "max6", "max12", "day24", "prev12"):
        c["l" + k.replace("day24", "day")] = np.log1p(c[k])
    c["lday"] = np.log1p(c["day24"])
    for k in ("log_pop", "log_dens", "share65"):
        c[k] = np.tile(vul[k].to_numpy(), len(days))
    c["lprior"] = np.log(np.tile(prior.to_numpy(), len(days)) * n)
    c["prior"] = np.tile(prior.to_numpy(), len(days))
    return c


def fit(cells: list[pd.DataFrame], feats: list[str], ycol: str):
    X = pd.concat(cells)
    mu, sd = X[feats].mean().to_numpy(), X[feats].std().replace(0, 1).to_numpy()
    m = PoissonRegressor(alpha=1.0, max_iter=300).fit((X[feats].to_numpy() - mu) / sd, X[ycol].to_numpy())
    return m, mu, sd


def run(mode, eps, F, Y, Y3, vul, resc, codes):
    rows, store = [], {}
    for h, (a, b) in enumerate(eps):
        d0, d1 = (a - T0).days, (b - T0).days
        if d0 < 0:
            continue
        train = [t for t in range(len(eps)) if (eps[t][0] - T0).days >= 0 and (t != h if mode == "loeo" else t < h)]
        if len(train) < (2 if mode == "forward" else 1):
            continue
        ph = prior_share(resc, eps, set(range(len(eps))) - set(train), codes)
        trH, trD = [], []
        for t in train:
            oth = set(train) - {t}
            if not oth:
                continue
            pt = prior_share(resc, eps, set(range(len(eps))) - oth, codes)
            e0, e1 = (eps[t][0] - T0).days, (eps[t][1] - T0).days
            trH.append(hourly_cells(F, Y3, vul, pt, e0 * 24, e1 * 24 + 24))
            trD.append(daily_cells(F, Y, vul, pt, e0, e1))
        if not trH:
            continue
        mH, muH, sdH = fit(trH, HFE, "y")
        mD, muD, sdD = fit(trD, DFE, "y")
        ch = hourly_cells(F, Y3, vul, ph, d0 * 24, d1 * 24 + 24)
        cd = daily_cells(F, Y, vul, ph, d0, d1)
        out = dict(mode=mode, episode=h + 1, start=a.date(), end=b.date(), days=d1 - d0 + 1, rescues=int(Y[d0 * 24:d1 * 24 + 24].sum()))
        for unit, c, mdl, mu, sd, feats, ycol in (("H", ch, mH, muH, sdH, HFE, "y"), ("D", cd, mD, muD, sdD, DFE, "y")):
            y = c[ycol].to_numpy()
            ours = mdl.predict((c[feats].to_numpy() - mu) / sd)
            if unit == "H":
                r12, pr = c["R12"].to_numpy(), c["prior"].to_numpy()
                fires = {k: fire({f"R{w}": c[f"R{w}"].to_numpy() for w in (1, 3, 6, 12, 24)}, v) for k, v in CRIT.items()}
                nunits = len(c) / (24 * out["days"])
            else:
                r12, pr = c["max12"].to_numpy(), c["prior"].to_numpy()
                Fd = {"R3": c["max3"].to_numpy(), "R6": c["max6"].to_numpy(), "R12": c["max12"].to_numpy()}
                fires = {k: fire(Fd, v) for k, v in CRIT.items()}
                nunits = len(c) / out["days"]
            sc = {"proxy_adv": fires["adv"] + 1e-6 * r12, "rain_only": r12, "rain_x_prior": r12 * pr, "prior_only": pr + 1e-9 * r12, "ours": ours}
            out[f"{unit}_Kadv"], out[f"{unit}_Kwarn"], out[f"{unit}_Kadv3h"] = int(fires["adv"].sum()), int(fires["warn"].sum()), int(fires["adv3h"].sum())
            out[f"{unit}_cells_with_rescue"] = int((y > 0).sum())
            kadv = out[f"{unit}_Kadv"]
            for nm, s in sc.items():
                out[f"{unit}_{nm}@Kadv"] = capture(s, y, kadv) if kadv > 0 else np.nan
                for q in ((2, 5, 10) if unit == "H" else (5, 10, 20, 40)):
                    out[f"{unit}_{nm}@{q}/{'h' if unit == 'H' else 'd'}"] = capture(s, y, int(q * (24 if unit == 'H' else 1) * out["days"] * (1 if unit == "D" else 1) / (24 if unit == "H" else 1)))
            if kadv > 0:
                out[f"{unit}_proxy_warn@Kwarn"] = capture(fires["warn"] + 1e-6 * r12, y, out[f"{unit}_Kwarn"]) if out[f"{unit}_Kwarn"] else np.nan
            if unit == "D":
                store[h + 1] = c.assign(score_ours=ours, proxy_adv=fires["adv"], proxy_warn=fires["warn"], proxy_adv3h=fires["adv3h"])
        rows.append(out)
    return pd.DataFrame(rows), store


def main() -> None:
    sgg = pd.read_csv(OUT / "sgg_table.csv", dtype={"sgg_cd": str})
    day = pd.read_csv(OUT / "sgg_day.csv", dtype={"sgg_cd": str}, parse_dates=["date"])
    hr = pd.read_csv(OUT / "sgg_hour.csv", dtype={"sgg_cd": str})
    ev = pd.read_csv(OUT / "episodes.csv", parse_dates=["start", "end"])
    eps = list(zip(ev.start, ev.end))
    resc = day.loc[day.index.repeat(day["n"].astype(int))][["sgg_cd", "date"]]
    W, qc, F, info, Y, Y3, codes = build(sgg, day, hr)
    print("QC", qc, info)
    vul = vulnerability(sgg)
    res, store = [], {}
    for mode in ("loeo", "forward"):
        r, s = run(mode, eps, F, Y, Y3, vul, resc, codes)
        res.append(r)
        if mode == "loeo":
            store = s
    res = pd.concat(res)
    res.to_csv(OUT / "hourly_results.csv", index=False)
    for k, c in store.items():
        c.assign(date=lambda x: T0 + pd.to_timedelta(x["day"], unit="D"), sgg_cd=lambda x: codes[x["s"]]).to_csv(OUT / f"cells_daily_ep{k}.csv", index=False)
    pd.set_option("display.width", 250, "display.max_columns", 80)
    for mode in ("loeo", "forward"):
        r = res[res["mode"] == mode]
        print(f"\n=== {mode}: n episodes = {len(r)} ===")
        print(r[["episode", "start", "days", "rescues", "H_Kadv", "H_Kwarn", "H_Kadv3h", "D_Kadv", "D_Kwarn", "D_Kadv3h"]].to_string(index=False))
        for u in ("H", "D"):
            names = ("proxy_adv", "rain_only", "rain_x_prior", "prior_only", "ours")
            print(f"[{u}] capture @ matched K = #proxy-advisory cells:")
            print(r[["episode", *[f"{u}_{n}@Kadv" for n in names]]].round(3).to_string(index=False))
            v = r.dropna(subset=[f"{u}_ours@Kadv"])
            print(f"  episodes with proxy K>0: {len(v)}; macro:", {n: round(v[f"{u}_{n}@Kadv"].mean(), 3) for n in names},
                  f"ours>proxy {int((v[f'{u}_ours@Kadv'] > v[f'{u}_proxy_adv@Kadv']).sum())}/{len(v)}")
            for q in ((2, 5, 10) if u == "H" else (10, 20, 40)):
                unit = "h" if u == "H" else "d"
                print(f"  fixed {q}/{unit}: macro n={len(r)}", {n: round(r[f"{u}_{n}@{q}/{unit}"].mean(), 3) for n in names},
                      f"| ours>rain_only {int((r[f'{u}_ours@{q}/{unit}'] > r[f'{u}_rain_only@{q}/{unit}']).sum())}/{len(r)}",
                      f"| rain_x_prior>rain_only {int((r[f'{u}_rain_x_prior@{q}/{unit}'] > r[f'{u}_rain_only@{q}/{unit}']).sum())}/{len(r)}")


if __name__ == "__main__":
    main()
