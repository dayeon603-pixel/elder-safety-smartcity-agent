"""Misallocation (total variation distance) + budget-equal capture extension. Hypotheses, not proofs. 구-level, n=25."""
from __future__ import annotations
import json
import numpy as np
from run import *  # noqa

def tvd(S: np.ndarray, Y: np.ndarray) -> np.ndarray:
    """0.5*sum|S/sumS - Y/sumY| over last axis."""
    return 0.5 * np.abs(S / S.sum(-1, keepdims=True) - Y / Y.sum(-1, keepdims=True)).sum(-1)

def main() -> None:
    rng = np.random.default_rng(SEED)
    R = json.loads((OUT / "results.json").read_text())
    # ---------- silver
    cg = R["silver"]["counts_by_gu"]; g = list(cg)
    Y = np.array([cg[x]["ped70"] for x in g], float)
    S = {"현행 노인보호구역": np.array([cg[x]["cur"] for x in g], float), "영점 선정 지점": np.array([cg[x]["ours"] for x in g], float),
         "65세 이상 인구": np.array([cg[x]["p65"] for x in g], float)}
    idx = boot_idx(25, rng)
    bt = {k: tvd(v[idx], Y[idx]) for k, v in S.items()}
    out_s = {k: {"tvd": float(tvd(v, Y)), "ci": ci(bt[k])} for k, v in S.items()}
    out_s["paired_current_minus_ours"] = {"diff": out_s["현행 노인보호구역"]["tvd"] - out_s["영점 선정 지점"]["tvd"], "ci": ci(bt["현행 노인보호구역"] - bt["영점 선정 지점"])}
    out_s["paired_ours_minus_p65"] = {"diff": out_s["영점 선정 지점"]["tvd"] - out_s["65세 이상 인구"]["tvd"], "ci": ci(bt["영점 선정 지점"] - bt["65세 이상 인구"])}
    ratio = {k: (S[k] / S[k].sum()) / (Y / Y.sum()) for k in S}
    out_s["ratio_per_gu"] = {k: {x: float(r) for x, r in zip(g, v)} for k, v in ratio.items()}
    out_s["ratio_range"] = {k: [float(v.min()), float(v.max())] for k, v in ratio.items()}
    R["silver"]["misallocation_tvd"] = {"def": "1/2 * sum_구 |signal share - 70+ pedestrian 119 call share|; 0 = same distribution; interpretable as share of the signal placed in the wrong 구", "n_boot": N_BOOT, "seed": SEED, **out_s}
    # ---------- heat
    act = load_act()
    _, sig, Yh, gus = heat(act, np.random.default_rng(SEED))
    names = {"인구": "인구 (기준선)", "65세 이상 인구": "65세 이상 인구 (기준선)", "B1 경보일수 F": "특보 규칙 B1 경보일수 (F)",
             "B1 경보일수 O": "특보 규칙 B1 경보일수 (O)", "영점 λ F": "영점 예측 λ 합 (F)", "영점 λ O": "영점 예측 λ 합 (O)"}
    idx = boot_idx(25, np.random.default_rng(SEED))
    bi = (idx[:, :, None] * 4 + np.arange(4)).reshape(N_BOOT, -1)
    yf = Yh.reshape(-1); H: dict = {"def": "TVD between signal share and 119 온열손상 share; pooled = 100 구-month cells (구-cluster bootstrap), season = 25 구 totals", "pooled100": {}, "season25": {}}
    bp, bs_ = {}, {}
    for k, nm in names.items():
        Sm = sig[nm]; sf = Sm.reshape(-1)
        bp[k] = tvd(sf[bi], yf[bi]); H["pooled100"][k] = {"tvd": float(tvd(sf, yf)), "ci": ci(bp[k])}
        ss, ys = Sm.sum(1), Yh.sum(1)
        bs_[k] = tvd(ss[idx], ys[idx]); H["season25"][k] = {"tvd": float(tvd(ss, ys)), "ci": ci(bs_[k])}
    for tag, bb, d in [("pooled100", bp, H["pooled100"]), ("season25", bs_, H["season25"])]:
        d["paired"] = {}
        for a, b in [("영점 λ F", "B1 경보일수 F"), ("영점 λ O", "B1 경보일수 O"), ("영점 λ F", "인구"), ("영점 λ F", "65세 이상 인구"),
                     ("영점 λ O", "인구"), ("영점 λ O", "65세 이상 인구")]:
            d["paired"][f"{b} - {a}"] = {"diff": d[b]["tvd"] - d[a]["tvd"], "ci": ci(bb[b] - bb[a])}
    # budget-equal capture: per month, k = #구 with >=1 B1 alert day (same variant)
    cap: dict = {"def": "per month, alert the same number of 구 as B1 alerted that month (구 with >=1 alert day), ranked by each signal; value = share of that period's 119 온열손상 in alerted 구-months; boundary ties split fractionally"}
    for v in "FO":
        b1 = sig[f"특보 규칙 B1 경보일수 ({v})"]; km = (b1 > 0).sum(0)
        cap[v] = {"k_per_month": dict(zip(MONTHS, km.astype(int).tolist()))}
        cands = {"인구": sig["인구 (기준선)"], "65세 이상 인구": sig["65세 이상 인구 (기준선)"], f"B1 경보일수 {v}": b1, f"영점 λ {v}": sig[f"영점 예측 λ 합 ({v})"]}
        bc = {}
        for k, Sm in cands.items():
            def cap_fn(ii: np.ndarray) -> np.ndarray:  # ii: (..., 25) index arrays
                num = 0; den = 0
                for j in range(4):
                    s = Sm[ii, j]; y = Yh[ii, j]
                    num = num + topk_share(s, y, int(km[j])) * y.sum(-1); den = den + y.sum(-1)
                return num / den
            pt = float(cap_fn(np.arange(25)[None, :])[0]); bc[k] = cap_fn(idx)
            cap[v][k] = {"capture": pt, "ci": ci(bc[k])}
        cap[v]["paired_ours_minus"] = {k: {"diff": cap[v][f"영점 λ {v}"]["capture"] - cap[v][k]["capture"], "ci": ci(bc[f"영점 λ {v}"] - bc[k])}
                                       for k in cands if not k.startswith("영점")}
        cap[v]["random_expected"] = float(sum(km[j] / 25 * Yh[:, j].sum() for j in range(4)) / Yh.sum())
        cap[v]["note"] = "k=25 in a month means everyone is alerted, capture 100% by construction (non-informative)"
    H["budget_equal_capture"] = cap
    R["heat"]["misallocation_tvd"] = H
    (OUT / "results.json").write_text(json.dumps(R, ensure_ascii=False, indent=1, default=float))
    print(json.dumps({"silver": {k: out_s[k] for k in out_s if k != "ratio_per_gu"}, "heat": H}, ensure_ascii=False, indent=0))

if __name__ == "__main__":
    main()
