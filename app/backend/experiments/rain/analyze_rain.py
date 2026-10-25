"""Step 2: daily-rain analysis, leave-one-episode-out (LOEO). No API keys.

HYPOTHESIS TEST, NOT A PROOF: n = number of storm episodes in ONE year (2025), not rows.
Proxy baseline = daily-total rule on Meteostat prcp (NOT the real 호우특보): the real 특보 uses 6h/12h
accumulations and 특보구역 polygons. Everything here is replaced once hourly ASOS + issued 특보 arrive.

LOOKAHEAD RISK: same-day total rain is only known at end of day, but the rescues it is scored against occur
during the day. Proxy, rain-only and ours all share this, so the *comparison* is unaffected, absolute capture is optimistic.
LOOKAHEAD RISK: LOEO prior uses rescues from episodes that happen AFTER the held-out one (not causal). The
`forward` variant (prior + fit only from earlier episodes) is reported alongside.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from sklearn.linear_model import PoissonRegressor

from common import OUT, SEED, haversine_km, load_prcp, load_stations

np.random.seed(SEED)
warnings.filterwarnings("ignore", category=FutureWarning)
PROXY_MM = 110.0   # daily proxy of 호우주의보 (12h 110 mm)
PROXY_WARN_MM = 180.0  # daily proxy of 호우경보 (12h 180 mm)
LENIENT_MM = 80.0  # sensitivity: daily totals understate 12h peaks
BUDGET_PER_DAY = (5, 10, 20, 40)
N_BOOT = 1000


def build_rain(sgg: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Long frame (sgg_cd, date, rain, rain_prev, dist_km) with nearest *reporting* station per day."""
    st = load_stations()
    P = load_prcp(list(st.id)).loc["2025-01-01":"2025-12-31"]
    cov = P.loc["2025-06-01":"2025-09-30"].notna().mean()
    keep = cov[cov >= 0.9].index
    st = st[st.id.isin(keep)].reset_index(drop=True)
    P = P[st.id].asfreq("D")
    print(f"stations with >=90% Jun-Sep prcp: {len(st)}")
    D = haversine_km(sgg.lat.to_numpy()[:, None], sgg.lon.to_numpy()[:, None], st.lat.to_numpy()[None, :], st.lon.to_numpy()[None, :])
    order = np.argsort(D, axis=1)
    vals = P.to_numpy()  # days x stations, NaN = missing
    rain = np.full((len(sgg), len(P)), np.nan)
    dist = np.full_like(rain, np.nan)
    for j in range(len(P)):  # per-day (365 iterations), vectorised over 시군구
        ok = ~np.isnan(vals[j])[order]            # sgg x rank
        first = ok.argmax(axis=1)
        has = ok.any(axis=1)
        sidx = order[np.arange(len(sgg)), first]
        rain[has, j] = vals[j, sidx[has]]
        dist[has, j] = D[np.arange(len(sgg)), sidx][has]
    r = pd.DataFrame(rain, index=sgg.sgg_cd.to_numpy(), columns=P.index)
    dd = pd.DataFrame(dist, index=sgg.sgg_cd.to_numpy(), columns=P.index)
    long = r.stack(dropna=False).rename("rain").reset_index().rename(columns={"level_0": "sgg_cd", "level_1": "date"})
    long["dist_km"] = dd.stack(dropna=False).to_numpy()
    prev = r.shift(1, axis=1).stack(dropna=False).to_numpy()
    long["rain_prev"] = prev
    return long, st.assign(n=len(st))


def vulnerability(sgg: pd.DataFrame) -> pd.DataFrame:
    v = sgg[["sgg_cd", "pop", "share65", "density"]].copy()
    v["log_pop"] = np.log(v["pop"].clip(lower=1))
    v["log_dens"] = np.log(v["density"].clip(lower=1))
    return v


def prior_share(resc: pd.DataFrame, eps: list[tuple[pd.Timestamp, pd.Timestamp]], excl: set[int], codes: np.ndarray) -> pd.Series:
    """Per-시군구 share of rescues in episodes not in `excl` (add-0.5 smoothing); sums to 1."""
    m = np.zeros(len(resc), bool)
    for i, (a, b) in enumerate(eps):
        if i not in excl:
            m |= ((resc["date"] >= a) & (resc["date"] <= b)).to_numpy()
    c = resc[m].groupby("sgg_cd").size().reindex(codes).fillna(0.0)
    c = c + 0.5
    return c / c.sum()


def make_cells(rain: pd.DataFrame, vul: pd.DataFrame, day: pd.DataFrame, a: pd.Timestamp, b: pd.Timestamp) -> pd.DataFrame:
    c = rain[(rain.date >= a) & (rain.date <= b)].merge(vul, on="sgg_cd")
    y = day[(day.date >= a) & (day.date <= b)][["sgg_cd", "date", "n"]]
    c = c.merge(y, on=["sgg_cd", "date"], how="left")
    c["n"] = c["n"].fillna(0)
    c["rain"] = c["rain"].fillna(0.0)          # no reporting station within the file that day -> treat as no signal
    c["rain_prev"] = c["rain_prev"].fillna(0.0)
    return c


FEATS = ["lrain", "lprev", "log_pop", "log_dens", "share65", "lprior"]


def add_feats(c: pd.DataFrame, prior: pd.Series) -> pd.DataFrame:
    c = c.copy()
    c["lrain"] = np.log1p(c["rain"])
    c["lprev"] = np.log1p(c["rain_prev"])
    c["lprior"] = np.log(c["sgg_cd"].map(prior).to_numpy() * len(prior))  # log(share / uniform share)
    return c


def fit_model(train_cells: list[pd.DataFrame]) -> tuple[PoissonRegressor, np.ndarray, np.ndarray]:
    X = pd.concat(train_cells)
    mu, sd = X[FEATS].mean().to_numpy(), X[FEATS].std().replace(0, 1).to_numpy()
    m = PoissonRegressor(alpha=1.0, max_iter=500)
    m.fit((X[FEATS].to_numpy() - mu) / sd, X["n"].to_numpy())
    return m, mu, sd


def capture(score: np.ndarray, y: np.ndarray, k: int) -> float:
    if y.sum() == 0:
        return float("nan")
    k = min(k, len(score))
    if k <= 0:
        return 0.0
    # deterministic tie-break by index via stable sort on -score
    idx = np.argsort(-score, kind="stable")[:k]
    return float(y[idx].sum() / y.sum())


def run(mode: str, eps, rain, vul, day, resc, codes) -> pd.DataFrame:
    """mode='loeo' (train on all other episodes) or 'forward' (train only on earlier episodes)."""
    rows = []
    for h, (a, b) in enumerate(eps):
        train = [t for t in range(len(eps)) if (t != h if mode == "loeo" else t < h)]
        if len(train) < (2 if mode == "forward" else 1):
            continue
        cells_h = make_cells(rain, vul, day, a, b)
        prior_h = prior_share(resc, eps, set(range(len(eps))) - set(train), codes)  # only training episodes
        cells_h = add_feats(cells_h, prior_h)
        tr = []
        for t in train:  # nested: prior for training ep t excludes t itself (and h)
            ta, tb = eps[t]
            others = set(train) - {t}
            if not others:
                continue
            tc = add_feats(make_cells(rain, vul, day, ta, tb), prior_share(resc, eps, set(range(len(eps))) - others, codes))
            tr.append(tc)
        if not tr:
            continue
        mdl, mu, sd = fit_model(tr)
        s_ours = mdl.predict((cells_h[FEATS].to_numpy() - mu) / sd)
        r, y = cells_h["rain"].to_numpy(), cells_h["n"].to_numpy()
        r2 = r + cells_h["rain_prev"].to_numpy()   # 2-day accumulation (today + yesterday), both known by end of day
        pr = cells_h["sgg_cd"].map(prior_h).to_numpy()
        s_prod = r * pr
        s_prod2 = r2 * pr
        s_prior = cells_h["sgg_cd"].map(prior_h).to_numpy() + 1e-9 * r
        nd = (b - a).days + 1
        k_proxy = int((r >= PROXY_MM).sum())
        out = dict(mode=mode, episode=h + 1, start=a.date(), end=b.date(), days=nd, cells=len(cells_h), rescues=int(y.sum()),
                   n_train_eps=len(train), K_proxy110=k_proxy, K_proxy180=int((r >= PROXY_WARN_MM).sum()), K_80=int((r >= LENIENT_MM).sum()),
                   rescue_cells=int((y > 0).sum()))
        for name, s in [("proxy110", (r >= PROXY_MM).astype(float) + 1e-9 * r), ("rain_only", r), ("rain2d_only", r2), ("rain_x_prior", s_prod), ("rain2d_x_prior", s_prod2),
                        ("prior_only", s_prior), ("ours", s_ours)]:
            out[f"cap_{name}@Kproxy"] = capture(s, y, k_proxy) if k_proxy > 0 else float("nan")
            out[f"cap_{name}@K80"] = capture(s, y, out["K_80"]) if out["K_80"] > 0 else float("nan")
            for q in BUDGET_PER_DAY:
                out[f"cap_{name}@{q}/d"] = capture(s, y, q * nd)
        # cluster bootstrap over 시군구 at matched budget (ranking fixed): resample sgg, recompute capture
        if k_proxy > 0 and y.sum() > 0:
            rng = np.random.default_rng(SEED + h)
            g = cells_h.groupby("sgg_cd").indices
            keys = np.array(list(g))
            diffs = []
            for _ in range(N_BOOT):
                pick = rng.choice(keys, len(keys))
                idx = np.concatenate([g[p] for p in pick])
                yy = y[idx]
                if yy.sum() == 0:
                    continue
                kk = max(1, int(round(k_proxy * len(idx) / len(y))))
                diffs.append(capture(s_ours[idx], yy, kk) - capture((r[idx] >= PROXY_MM).astype(float) + 1e-9 * r[idx], yy, kk))
            out["boot_lo_ours-proxy"], out["boot_hi_ours-proxy"] = np.percentile(diffs, [2.5, 97.5])
        rows.append(out)
        if mode == "loeo":
            cells_h = cells_h.assign(score_ours=s_ours, score_prod=s_prod, proxy=(r >= PROXY_MM))
            cells_h.to_csv(OUT / f"cells_ep{h+1}.csv", index=False)
    return pd.DataFrame(rows)


def main() -> None:
    sgg = pd.read_csv(OUT / "sgg_table.csv", dtype={"sgg_cd": str})
    day = pd.read_csv(OUT / "sgg_day.csv", dtype={"sgg_cd": str}, parse_dates=["date"])
    ev = pd.read_csv(OUT / "episodes.csv", parse_dates=["start", "end"])
    eps = list(zip(ev.start, ev.end))
    resc = day.groupby(["sgg_cd", "date"], as_index=False)["n"].sum()
    resc = resc.loc[resc.index.repeat(resc["n"].astype(int))].drop(columns="n")
    rain, st = build_rain(sgg)
    print(f"nearest-station distance km: median {rain.dist_km.median():.1f}, p90 {rain.dist_km.quantile(.9):.1f}, max {rain.dist_km.max():.1f}")
    print(f"sgg-days with no reporting station: {rain.rain.isna().mean():.2%}")
    vul = vulnerability(sgg)
    codes = sgg.sgg_cd.to_numpy()
    res = pd.concat([run("loeo", eps, rain, vul, day, resc, codes), run("forward", eps, rain, vul, day, resc, codes)])
    res.to_csv(OUT / "rain_results.csv", index=False)
    pd.set_option("display.width", 250, "display.max_columns", 60)
    for mode in ("loeo", "forward"):
        r = res[res["mode"] == mode]
        print(f"\n=== {mode}: n_episodes evaluated = {len(r)} ===")
        print(r[["episode", "start", "days", "rescues", "rescue_cells", "K_proxy110", "K_proxy180", "K_80"]].to_string(index=False))
        cols = [c for c in r.columns if c.startswith("cap_") and c.endswith("@Kproxy")]
        print("capture @ matched budget K=proxy110 alerts:")
        print(r[["episode", *cols]].round(3).to_string(index=False))
        v = r.dropna(subset=["cap_ours@Kproxy"])
        print(f"episodes with proxy K>0: {len(v)} ->", {c: round(v[c].mean(), 3) for c in cols}, "(macro mean)")
        print(f"ours > proxy in {(v['cap_ours@Kproxy'] > v['cap_proxy110@Kproxy']).sum()}/{len(v)}; ours > rain_only in {(v['cap_ours@Kproxy'] > v['cap_rain_only@Kproxy']).sum()}/{len(v)}")
        for q in BUDGET_PER_DAY:
            cc = [f"cap_{n}@{q}/d" for n in ("proxy110", "rain_only", "rain2d_only", "rain_x_prior", "rain2d_x_prior", "prior_only", "ours")]
            print(f"budget {q}/day macro mean over {r['cap_ours@%d/d' % q].notna().sum()} eps:", {c.split('_', 1)[1]: round(r[c].mean(), 3) for c in cc})
        for q in (20, 40):
            cc = [f"cap_{n}@{q}/d" for n in ("rain_only", "rain2d_only", "rain_x_prior", "rain2d_x_prior", "prior_only", "ours")]
            print(f"per-episode capture at {q} alerts/day:")
            print(r[["episode", "rescues", *cc]].round(3).to_string(index=False))
            print("wins ours vs rain_only:", int((r[f"cap_ours@{q}/d"] > r[f"cap_rain_only@{q}/d"]).sum()), "/", len(r),
                  "| rain_x_prior vs rain_only:", int((r[f"cap_rain_x_prior@{q}/d"] > r[f"cap_rain_only@{q}/d"]).sum()), "/", len(r))
        k80 = [f"cap_{n}@K80" for n in ("proxy110", "rain_only", "rain2d_only", "rain_x_prior", "rain2d_x_prior", "prior_only", "ours")]
        print("budget = #cells>=80mm:", r[["episode", *k80]].round(3).to_string(index=False))
        print("bootstrap 95% (ours-proxy, resampling 시군구):", r[["episode", "boot_lo_ours-proxy", "boot_hi_ours-proxy"]].dropna().round(3).to_string(index=False))


if __name__ == "__main__":
    main()
