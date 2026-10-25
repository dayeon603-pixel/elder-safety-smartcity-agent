"""Module C' 「AED 시간대 공백」 end-to-end run (Seoul).

PROTOCOL (fixed before any test event is touched; see out/protocol.json written first):
  data      : 소방청 구급활동정보, Seoul incident 구, 2024-01..2024-11 (daily API quota of 1000 calls capped the pull;
              2024-12 dropped because 중랑/중부 소방서 are missing for that month; other years not pulled).
  public CA : ruptSptmCdNm contains 심정지 or 호흡정지  AND  ruptOccrPlcCdNm not null AND != '집'.
  split     : temporal. train = 2024-01..08, test = 2024-09..11. No random split anywhere.
  rate model: lambda[g,h] = (1-w)*empirical + w*separable(n_g * pi_h); w picked on train-internal split
              (fit Jan-May, validate Jun-Aug, Poisson log-lik). Poisson counts, rate unit = events/month.
  AED open  : open in hour h iff open at h:30 (window [start,end)); weekday = mean(Mon..Fri); weekend = mean(Sat,Sun).
  policies  : convert B AEDs (B = 1% / 5% of Seoul AEDs) to 24h every day. Same within-구 AED picker for all
              allocation rules, so the contrast is the across-구 allocation only.
  metrics   : (1) open AEDs per km^2 in the arrest 구 at the arrest hour (primary; 'ours' optimises this),
              (2) share of arrests with <50% of the 구's AEDs open (not optimised),
              (3) share of arrests with >=1 open AED per km^2.
              Weekday and weekend schedules reported separately, never blended.
  inference : paired bootstrap over test events, stratified by month, 2000 resamples, seed 42.
BACKTESTS ARE HYPOTHESES: this is an availability analysis on a static AED snapshot, not a 4-minute-walk analysis.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import aed_lib as L  # noqa: E402

log = logging.getLogger("aed")
SEED = 42
OUT = Path(__file__).parent / "out"
TRAIN = [f"2024{m:02d}" for m in range(1, 9)]
VAL_FIT, VAL_EVAL = TRAIN[:5], TRAIN[5:]
TEST = ["202409", "202410", "202411"]
ALL_M = TRAIN + TEST
BUDGETS = {"1%": 0.01, "5%": 0.05}
W_GRID = [0.0, 0.25, 0.5, 0.75, 0.9, 1.0]
X_LIST = [0.3, 0.5]
N_BOOT = 2000
WD_SHARE = 5 / 7


def counts(ev: pd.DataFrame, gi: dict, months: list[str]) -> np.ndarray:
    e = ev[ev.public & ev.gutYm.isin(months)]
    M = np.zeros((25, 24))
    np.add.at(M, (e.egrcSiggCdNm.map(gi).to_numpy(), e.hour.to_numpy()), 1)
    return M


def rate_fit(M: np.ndarray, n_months: int, w: float) -> np.ndarray:
    """Events/month per (구,hour): (1-w)*empirical + w*separable."""
    pi = M.sum(0) / M.sum(); ng = M.sum(1)
    return ((1 - w) * M + w * ng[:, None] * pi[None, :]) / n_months


def pois_ll(y: np.ndarray, lam: np.ndarray, months: int) -> float:
    mu = np.maximum(lam * months, 1e-6)
    return float(np.sum(y * np.log(mu) - mu))


def alloc(B: int, weights: np.ndarray, caps: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Largest-remainder allocation of B units with per-bucket caps (water-filling)."""
    q = np.zeros(len(weights), int); left = B; active = caps > 0
    while left > 0 and active.any():
        w = np.where(active, weights, 0.0); w = w / w.sum()
        raw = w * left; base = np.minimum(np.floor(raw).astype(int), caps - q)
        rem = raw - np.floor(raw); rem = np.where(active & (q + base < caps), rem, -1)
        q += base; left -= base.sum()
        k = min(left, int((rem >= 0).sum()))
        if k > 0:
            order = np.lexsort((rng.random(len(rem)), -rem))[:k]
            q[order] += 1; left -= k
        active = (q < caps)
        if base.sum() == 0 and k == 0:
            break
    return q


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    OUT.mkdir(exist_ok=True)
    rng = np.random.default_rng(SEED); np.random.seed(SEED)
    G = L.seoul_gu(); gnames = list(G.gu); gi = {g: i for i, g in enumerate(gnames)}
    area = G.area_km2.to_numpy(); pop = L.seoul_pop().reindex(gnames).to_numpy()
    ev, audit = L.load_events(set(gnames)); ev = ev[ev.gutYm.isin(ALL_M)].copy()
    ev["g"] = ev.egrcSiggCdNm.map(gi)
    audit["rows_seoul_2024_01_11"] = int(len(ev)); audit["ca_all_places"] = int(ev.ca.sum())
    audit["ca_home"] = int((ev.ca & (ev.place == L.HOME)).sum()); audit["ca_place_null"] = int((ev.ca & ev.place.isna()).sum())
    audit["public_ca"] = int(ev.public.sum())
    audit["public_ca_excl_group_residence"] = int((ev.public & (ev.place != "집단거주시설")).sum())
    audit["public_place_counts"] = ev[ev.public].place.value_counts().to_dict()

    # ------------------------------------------------------------- AED
    A = L.load_aed(); A = A[A.sido.str.contains("서울", na=False)].reset_index(drop=True)
    A = A[A.gugun.isin(gi)].reset_index(drop=True)
    S = L.schedules(A); V = L.we_variants(S)
    ag = A.gugun.map(gi).to_numpy(); n_g = np.bincount(ag, minlength=25).astype(float)
    ph = S["placeholder"]
    diag = {
        "n_aed_seoul": int(len(A)),
        "wd_missing_any_day": int((~S["wd_known"]).sum()),
        "wd24_all_week_days": int(S["wd24"].sum()),
        "weekend24_both_days": int(S["we24"].sum()),
        "holiday24": int(S["hol24"].sum()),
        "placeholder_pattern_n": int(ph.sum()), "placeholder_pattern_share": float(ph.mean()),
        "share_we24_given_wd_not24": float(S["we24"][~S["wd24"]].mean()),
        "share_we24_given_wd24": float(S["we24"][S["wd24"]].mean()),
        "sat_missing": int((~S["kn_sat"]).sum()), "sun_missing": int((~S["kn_sun"]).sum()), "hol_missing": int((~S["kn_hol"]).sum()),
        "hol24_given_we24": float(S["hol24"][S["we24"]].mean()),
        "wd_daytime_only_n": int(((S["wd"][:, [2, 3, 22, 23]].sum(1) == 0) & (S["wd"][:, 10] > 0)).sum()),
    }
    m = re_m = A.serialSeq.astype(str).str.extract(r"^\d{3}-(\d{2})(\d{2})(\d{2})-")
    yr = pd.to_numeric(m[0], errors="coerce") + 2000
    diag["serialSeq_year_counts_if_date"] = yr.value_counts().sort_index().to_dict()
    (OUT / "aed_diagnostics.json").write_text(json.dumps(diag, ensure_ascii=False, indent=1, default=int))

    # ------------------------------------------------------------- descriptive (Jan-Nov pooled)
    pubM = counts(ev, gi, ALL_M)  # [25,24]
    pub = ev[ev.public].copy()
    sched_all = {"weekday": (S["wd"], np.ones(len(A), bool)), **{"weekend_" + k: v for k, v in V.items()}}
    desc = {}; hourly = {"hour": list(range(24)), "public_ca": pubM.sum(0).astype(int).tolist()}
    open_gh, share_gh = {}, {}
    for name, (sc, dm) in sched_all.items():
        og = np.zeros((25, 24)); den = np.zeros(25)
        np.add.at(og, ag[dm], sc[dm]); np.add.at(den, ag[dm], 1)
        open_gh[name] = og; share_gh[name] = og / den[:, None]
        hourly["pct_open_" + name] = (100 * og.sum(0) / den.sum()).round(2).tolist()
    pd.DataFrame(hourly).to_csv(OUT / "hourly_gap.csv", index=False)
    N = int(pubM.sum()); night_mask = np.isin(np.arange(24), L.NIGHT)
    for name, sh in share_gh.items():
        r = {}
        for X in X_LIST:
            low = (sh < X)
            k = int((pubM * low).sum()); lo, hi = L.wilson(k, N)
            r[f"share_arrests_when_open_lt_{int(X*100)}pct"] = [k / N, lo, hi, k]
        kn = int(pubM[:, night_mask].sum()); lo, hi = L.wilson(kn, N)
        r["night_share_of_arrests_22_06"] = [kn / N, lo, hi, kn]
        r["mean_pct_open_night"] = float(100 * open_gh[name][:, night_mask].sum() / (open_gh[name].shape[0] and (night_mask.sum() * (n_g if name == 'weekday' or 'W1' in name or 'W2' in name else (n_g - np.bincount(ag[ph], minlength=25))).sum())))
        r["mean_pct_open_day"] = float(100 * open_gh[name][:, ~night_mask].sum() / ((~night_mask).sum() * (n_g if name == 'weekday' or 'W1' in name or 'W2' in name else (n_g - np.bincount(ag[ph], minlength=25))).sum()))
        # arrest-weighted open share (what an arrest 'sees')
        r["arrest_weighted_pct_open_night"] = float(100 * (pubM[:, night_mask] * sh[:, night_mask]).sum() / pubM[:, night_mask].sum())
        r["arrest_weighted_pct_open_day"] = float(100 * (pubM[:, ~night_mask] * sh[:, ~night_mask]).sum() / pubM[:, ~night_mask].sum())
        desc[name] = r
    # per-구 night gap
    # ------------------------------------------------------------- rate model (train only)
    Mfit = counts(ev, gi, VAL_FIT); Mval = counts(ev, gi, VAL_EVAL)
    ll = {w: pois_ll(Mval, rate_fit(Mfit, len(VAL_FIT), w), len(VAL_EVAL)) for w in W_GRID}
    w_star = max(ll, key=ll.get)
    Mtr = counts(ev, gi, TRAIN); lam = rate_fit(Mtr, len(TRAIN), w_star)  # events/month
    # ------------------------------------------------------------- policies (train only)
    WD = S["wd"]; WE2 = V["W2_placeholder_as_weekday"][0]
    closed_mix = WD_SHARE * (1 - WD) + (1 - WD_SHARE) * (1 - WE2)
    Btot = {k: int(round(f * len(A))) for k, f in BUDGETS.items()}

    def gap_dp(lam_: np.ndarray, sc_: np.ndarray, cand_: np.ndarray, B: int) -> np.ndarray:
        """Engine variant 'gap': choose per-구 counts k_g (DP knapsack) maximising expected train arrests that fall in
        (구,hour) cells with >=50% of the 구's AEDs open (weekday/weekend-W2 mixed 5/7:2/7); within 구 take top sc."""
        f = []; order_g = []
        for g in range(25):
            idx = np.where((ag == g) & cand_)[0]; idx = idx[np.argsort(-sc_[idx], kind="stable")][: B]
            order_g.append(idx)
            cwd = np.concatenate([np.zeros((1, 24)), np.cumsum(1 - WD[idx], 0)]); cwe = np.concatenate([np.zeros((1, 24)), np.cumsum(1 - WE2[idx], 0)])
            owd = WD[ag == g].sum(0)[None, :] + cwd; owe = WE2[ag == g].sum(0)[None, :] + cwe
            f.append(((owd / n_g[g] >= 0.5) * WD_SHARE + (owe / n_g[g] >= 0.5) * (1 - WD_SHARE)) @ lam_[g])
        best = np.full((26, B + 1), -np.inf); best[0, 0] = 0; arg = np.zeros((25, B + 1), int)
        for g in range(25):
            fg = f[g]; kmax = len(fg) - 1
            for b_ in range(B + 1):
                ks = np.arange(0, min(kmax, b_) + 1)
                v = best[g, b_ - ks] + fg[ks]
                j = int(np.argmax(v)); best[g + 1, b_] = v[j]; arg[g, b_] = ks[j]
        q = np.zeros(25, int); b_ = int(np.argmax(best[25])) if False else B
        for g in range(24, -1, -1):
            q[g] = arg[g, b_]; b_ -= q[g]
        conv = np.zeros(len(A), bool)
        for g in range(25): conv[order_g[g][: q[g]]] = True
        return conv

    def build(lam_: np.ndarray, Mtr_: np.ndarray, seed_off: int) -> tuple[dict, np.ndarray]:
        rng_ = np.random.default_rng(SEED + seed_off)
        sc_ = (lam_[ag] * closed_mix).sum(1)
        cand_ = (sc_ > 0) & S["wd_any_known"]
        caps_ = np.bincount(ag[cand_], minlength=25); ng_ev_ = Mtr_.sum(1)

        def pick(q: np.ndarray) -> np.ndarray:
            conv = np.zeros(len(A), bool)
            for g in range(25):
                idx = np.where((ag == g) & cand_)[0]
                conv[idx[np.argsort(-sc_[idx], kind="stable")][: q[g]]] = True
            return conv
        out = {}
        for bk, B in Btot.items():
            P = {"현행": np.zeros(len(A), bool)}
            P["균등"] = pick(alloc(B, np.ones(25), caps_, rng_))
            P["인구비례"] = pick(alloc(B, pop, caps_, rng_))
            P["수요(시간무시)"] = pick(alloc(B, ng_ev_ + 1e-9, caps_, rng_))
            top = np.argsort(-(sc_ / area[ag]) * cand_, kind="stable")[:B]
            c = np.zeros(len(A), bool); c[top] = True; P["우리-밀도"] = c
            P["우리-공백"] = gap_dp(lam_, sc_, cand_, B)
            for k, v in P.items():
                assert v.sum() <= B and (k == "현행" or v.sum() >= B - 0), (k, v.sum(), B)
            out[bk] = P
        return out, sc_

    # inner validation (train only): fit Jan-May, evaluate Jun-Aug. Used to compare engines BEFORE the test months are read.
    lam_in = rate_fit(counts(ev, gi, VAL_FIT), len(VAL_FIT), w_star)
    pol_in, _ = build(lam_in, counts(ev, gi, VAL_FIT), 1)
    vin = ev[ev.public & ev.gutYm.isin(VAL_EVAL)]
    vg, vh = vin.g.to_numpy(), vin.hour.to_numpy()
    inner = []
    for bk, P in pol_in.items():
        for sname, sc_ in [("평일", WD), ("주말_W2", WE2)]:
            for pname, conv in P.items():
                s_post = np.maximum(sc_, conv[:, None].astype(float)); og = np.zeros((25, 24)); np.add.at(og, ag, s_post)
                inner.append({"budget": bk, "schedule": sname, "policy": pname, "dens": float((og / area[:, None])[vg, vh].mean()),
                              "lt50": float(((og / n_g[:, None])[vg, vh] < 0.5).mean()), "cnt": float(og[vg, vh].mean()), "n": len(vin)})
    pd.DataFrame(inner).to_csv(OUT / "inner_validation_train_only.csv", index=False)
    lam_out = lam
    policies, sc = build(lam, Mtr, 0)
    cand = (sc > 0) & S["wd_any_known"]
    prot = {"doc": __doc__, "seed": SEED, "train": TRAIN, "test": TEST, "w_star": w_star,
            "val_loglik": {str(k): v for k, v in ll.items()}, "budgets": Btot,
            "policy_n_converted": {bk: {k: int(v.sum()) for k, v in P.items()} for bk, P in policies.items()},
            "note": "'우리-공백' was added after the first test read showed '우리-밀도' loses on the unoptimised <50% metric; inner (train-only) validation is the clean comparison; test numbers for 우리-공백 are post-hoc.",
            "policy_hash": {bk: {k: int(np.flatnonzero(v).sum()) for k, v in P.items()} for bk, P in policies.items()}}
    (OUT / "protocol.json").write_text(json.dumps(prot, ensure_ascii=False, indent=1, default=int))
    log.info("protocol frozen: w*=%s B=%s", w_star, Btot)

    # ------------------------------------------------------------- TEST evaluation (touched only here)
    te = ev[ev.public & ev.gutYm.isin(TEST)].reset_index(drop=True)
    tg, th, tm = te.g.to_numpy(), te.hour.to_numpy(), te.gutYm.to_numpy()
    n_te = len(te)
    scheds = {"평일": (WD, False), "주말_W1(공시값)": (V["W1_as_reported"][0], False),
              "주말_W2(자리표시자=평일)": (WE2, False), "주말_W3(자리표시자=미상, 하한)": (V["W3_placeholder_unknown_dropped"][0], True)}
    bidx = [np.where(tm == mm)[0] for mm in TEST]
    brng = np.random.default_rng(SEED)
    draws = np.concatenate([brng.choice(ix, size=(N_BOOT, len(ix))) for ix in bidx], axis=1)  # stratified by month
    res = []
    nightev = np.isin(th, L.NIGHT).astype(float)
    for bk, P in policies.items():
        for sname, (sc_, w3) in scheds.items():
            vals = {}
            for pname, conv in P.items():
                s_post = np.maximum(sc_, conv[:, None].astype(float))
                og = np.zeros((25, 24)); np.add.at(og, ag, s_post)
                dens = og / area[:, None]; share = og / n_g[:, None]
                vals[pname] = {"dens": dens[tg, th], "lt50": (share[tg, th] < 0.5).astype(float), "ge1": (dens[tg, th] >= 1).astype(float),
                               "lt70": (share[tg, th] < 0.7).astype(float), "cnt": og[tg, th]}
            for mt in ["dens", "lt50", "lt70", "ge1", "cnt", "dens_night", "lt50_night", "cnt_night"]:
                base = mt.replace("_night", ""); msk = nightev if mt.endswith("_night") else np.ones(n_te)
                def agg(v, ix=None):  # mean over (resampled) events within mask
                    if ix is None:
                        return float((v * msk).sum() / msk.sum())
                    return (v[ix] * msk[ix]).sum(1) / msk[ix].sum(1)
                means = {p: agg(vals[p][base]) for p in vals}
                bsd = {p: agg(vals[p][base], draws) for p in vals}
                for pname in vals:
                    row = {"budget": bk, "schedule": sname, "metric": mt, "policy": pname, "mean": means[pname],
                           "lo": np.percentile(bsd[pname], 2.5), "hi": np.percentile(bsd[pname], 97.5), "n_events": int(msk.sum())}
                    dc = bsd[pname] - bsd["현행"]
                    row.update(vs_current=means[pname] - means["현행"], vs_current_lo=np.percentile(dc, 2.5), vs_current_hi=np.percentile(dc, 97.5))
                    for ours in ("우리-밀도", "우리-공백"):
                        if pname != ours:
                            d = bsd[ours] - bsd[pname]
                            better = (d < 0) if base.startswith("lt") else (d > 0)
                            tag = ours[-2:]
                            row.update({f"diff_{tag}": means[ours] - means[pname], f"diff_{tag}_lo": np.percentile(d, 2.5),
                                        f"diff_{tag}_hi": np.percentile(d, 97.5), f"p_{tag}_better": float(better.mean())})
                    res.append(row)
    R = pd.DataFrame(res); R.to_csv(OUT / "policy_eval.csv", index=False)
    json.dump({"audit": audit, "diag": diag, "desc": desc, "w_star": w_star, "val_ll": {str(k): v for k, v in ll.items()},
               "n_train_public": int(Mtr.sum()), "n_test_public": n_te, "budgets": Btot}, open(OUT / "summary.json", "w"), ensure_ascii=False, indent=1, default=float)

    # ------------------------------------------------------------- per-구 map CSV + candidates
    exp_year = lam * 12  # expected public arrests/yr per (구,hour)
    rows = []
    for g in range(25):
        r = {"code": G.code[g], "gu": gnames[g], "lat": G.lat[g], "lon": G.lon[g], "area_km2": round(area[g], 2), "n_aed": int(n_g[g]),
             "n_aed_24h_weekday": int(S["wd24"][ag == g].sum())}
        for nm, sh in share_gh.items():
            r[f"night_pct_open_{nm}"] = round(100 * sh[g, night_mask].mean(), 1)
        r["day_pct_open_weekday"] = round(100 * share_gh["weekday"][g, ~night_mask].mean(), 1)
        r["public_ca_per_yr_total_est"] = round(exp_year[g].sum(), 1)
        r["public_ca_per_yr_night_est"] = round(exp_year[g, night_mask].sum(), 1)
        # 밤의 공백 점수: expected night public arrests/yr x (1 - share of AEDs open), weekday schedule
        r["night_gap_score_weekday"] = round(float((exp_year[g, night_mask] * (1 - share_gh["weekday"][g, night_mask])).sum()), 2)
        r["night_gap_score_weekend_W2"] = round(float((exp_year[g, night_mask] * (1 - share_gh["weekend_W2_placeholder_as_weekday"][g, night_mask])).sum()), 2)
        r["night_gap_per_km2"] = round(r["night_gap_score_weekday"] / area[g], 3)
        for bk, P in policies.items():
            r[f"recommended_gap_{bk}"] = int(P["우리-공백"][ag == g].sum())
            r[f"recommended_density_{bk}"] = int(P["우리-밀도"][ag == g].sum())
        rows.append(r)
    pd.DataFrame(rows).sort_values("night_gap_score_weekday", ascending=False).to_csv(OUT / "seoul_gu_night_gap_map.csv", index=False, encoding="utf-8-sig")
    # candidate AEDs (B=5% ours); no manager names/phones are exported
    conv = policies["5%"]["우리-공백"]; ci = np.flatnonzero(conv)
    nz = lambda a, t: ((a[:, t] > 0).mean(1))  # noqa: E731
    cdf = pd.DataFrame({"gu": A.gugun.to_numpy()[ci], "org": A.org.to_numpy()[ci], "address": A.buildAddress.to_numpy()[ci],
                        "place_in_building": A.buildPlace.to_numpy()[ci], "lat": A.wgs84Lat.to_numpy()[ci], "lon": A.wgs84Lon.to_numpy()[ci],
                        "weekday_hours": (A.monSttTme.astype(str) + "-" + A.monEndTme.astype(str)).to_numpy()[ci],
                        "weekday_open_hours_of_24": WD[ci].sum(1).round(1), "night_open_weekday_share": WD[ci][:, night_mask].mean(1).round(2),
                        "gain_score": (sc[ci]).round(3), "serialSeq": A.serialSeq.to_numpy()[ci]})
    cdf["weekend_24h_placeholder_pattern"] = ph[ci]
    cdf["also_in_density_set"] = policies["5%"]["우리-밀도"][ci]
    cdf = cdf.sort_values(["gain_score"], ascending=False)
    cdf.to_csv(OUT / "recommended_candidates_5pct.csv", index=False, encoding="utf-8-sig")
    cdf.groupby("gu").head(5).sort_values(["gu", "gain_score"], ascending=[True, False]).to_csv(OUT / "recommended_candidates_top5_per_gu.csv", index=False, encoding="utf-8-sig")
    np.savez(OUT / "arrays.npz", pubM=pubM, lam=lam, **{f"open_{k}": v for k, v in open_gh.items()})
    log.info("done n_test=%d", n_te)


if __name__ == "__main__":
    main()
