"""Silver re-validation with optimized picks (ours_opt). Hypotheses, not proofs. n=25 구."""
from __future__ import annotations
import json
import numpy as np
from run import *  # noqa
from ext import tvd

MAP = EXP / "silver/out/opt/promoted/map_seoul_current_vs_ours_k3918_fold2024.csv"

def main() -> None:
    act = load_act()
    res, *_ = silver(act, np.random.default_rng(SEED), MAP)
    cg = res["counts_by_gu"]; g = list(cg)
    Y = np.array([cg[x]["ped70"] for x in g], float)
    S = {"현행 노인보호구역": np.array([cg[x]["cur"] for x in g], float), "영점 선정 지점(opt)": np.array([cg[x]["ours"] for x in g], float),
         "65세 이상 인구": np.array([cg[x]["p65"] for x in g], float)}
    idx = boot_idx(25, np.random.default_rng(SEED))
    bt = {k: tvd(v[idx], Y[idx]) for k, v in S.items()}
    br = {k: spearman(v[idx], Y[idx]) for k, v in S.items()}
    o, c, p = "영점 선정 지점(opt)", "현행 노인보호구역", "65세 이상 인구"
    res["misallocation_tvd"] = {k: {"tvd": float(tvd(v, Y)), "ci": ci(bt[k])} for k, v in S.items()}
    M = res["misallocation_tvd"]
    M["paired_current_minus_ours"] = {"diff": M[c]["tvd"] - M[o]["tvd"], "ci": ci(bt[c] - bt[o])}
    M["paired_ours_minus_p65"] = {"diff": M[o]["tvd"] - M[p]["tvd"], "ci": ci(bt[o] - bt[p])}
    rt = {k: (S[k] / S[k].sum()) / (Y / Y.sum()) for k in S}
    M["ratio_per_gu"] = {k: {x: float(r) for x, r in zip(g, v)} for k, v in rt.items()}
    M["ratio_range"] = {k: [float(v.min()), float(v.max())] for k, v in rt.items()}
    res["spearman_ped70"] = {k: {"rho": float(spearman(v, Y)), "ci": ci(br[k])} for k, v in S.items()}
    res["spearman_paired"] = {"ours_minus_current": {"diff": res["spearman_ped70"][o]["rho"] - res["spearman_ped70"][c]["rho"], "ci": ci(br[o] - br[c])},
                              "ours_minus_p65": {"diff": res["spearman_ped70"][o]["rho"] - res["spearman_ped70"][p]["rho"], "ci": ci(br[o] - br[p])}}
    res["map_file"] = str(MAP)
    R = json.loads((OUT / "results.json").read_text()); R["silver_opt"] = res
    (OUT / "results.json").write_text(json.dumps(R, ensure_ascii=False, indent=1, default=float))
    print(json.dumps({k: res[k] for k in ["n_current_zones_seoul", "n_ours_picks_seoul", "n_ours_picks_outside_seoul_polygons", "spearman_ped70", "spearman_paired", "share_ped70_in_gu_ours_gt_current", "share_ped70_in_gu_ours_share_gt_current_share"]}, ensure_ascii=False))
    print(json.dumps({k: M[k] for k in M if k != "ratio_per_gu"}, ensure_ascii=False))

if __name__ == "__main__":
    main()
