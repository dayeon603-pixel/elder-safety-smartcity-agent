"""Gate on field sets + the deck figure '실험실 -> CCTV 시점 -> 실제 영상'. Train on UR only (clean), no tuning on MCFD/CCTVF."""
from __future__ import annotations

import json
import logging
import pickle

import numpy as np

import field_eval as FE
import gate_eval as G
import optimize as O

log = logging.getLogger("fg")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    O.set_seed()
    MC, CC = pickle.load(open(FE.FOUT / "field_banks.pkl", "rb"))
    UR = O.load_ds("ur")
    allt = MC + CC
    cut = len(MC)
    oof, Pt = G.cache_split(UR, allt)
    out: dict = {}
    for g in (None, 0.1):
        r, thr = G.apply_gate(UR, oof, allt, Pt, g)
        key = f"v1_gate={g}"
        out[key] = {"thr": thr, "mcfd": FE.breakdowns(key, r[:cut], MC), "cctvf": FE.breakdowns(key, r[cut:], CC)}
        for k in ("mcfd", "cctvf"):
            a = out[key][k]["all"]
            a["fa_poisson"] = G.poisson_ci(a["fa"], a["neg_hours"])
            log.info("%s %s sens %.3f [%.3f,%.3f] FA/h %.1f poisson[%.1f,%.1f] F1 %.3f lat %.2f (tp %d/%d, fa %d/%.3fh)", key, k, *a["sens"], a["fa_per_h"][0], a["fa_poisson"][1], a["fa_poisson"][2], a["f1"][0], a["lat_med"][0], a["tp"], a["n_fall"], a["fa"], a["neg_hours"])
    rr = O.rule_events(allt)
    out["rule"] = {"mcfd": FE.breakdowns("rule", rr[:cut], MC), "cctvf": FE.breakdowns("rule", rr[cut:], CC)}
    json.dump(out, open(FE.FOUT / "field_gate_summary.json", "w"), indent=1, default=float)
    fig(out)


def fig(out: dict) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.family"] = "AppleGothic"
    plt.rcParams["axes.unicode_minus"] = False
    gate = json.load(open(O.OUT / "gate" / "gate_summary.json"))
    summ = json.load(open(O.OUT / "opt_summary.json"))
    groups = [("실험실\n(UR Fall, 연출)", {"규칙 기준선": summ["ur_cv:rule"], "v1 모델": gate["ur_cv:gate=None"], "v1 + 하강 게이트": gate["ur_cv:gate=0.1"]}),
              ("CCTV 시점\n(MCFD 8대, 연출)", {"규칙 기준선": out["rule"]["mcfd"]["all"], "v1 모델": out["v1_gate=None"]["mcfd"]["all"], "v1 + 하강 게이트": out["v1_gate=0.1"]["mcfd"]["all"]}),
              ("실제 CCTV 영상\n(클립 단위, 분류 라벨)", {"규칙 기준선": out["rule"]["cctvf"]["all"], "v1 모델": out["v1_gate=None"]["cctvf"]["all"], "v1 + 하강 게이트": out["v1_gate=0.1"]["cctvf"]["all"]})]
    cols = {"규칙 기준선": "#8a8a8a", "v1 모델": "#7aa6e8", "v1 + 하강 게이트": "#1f5fbf"}
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.4))
    for j, key, ttl in ((0, "sens", "낙상 포착률 (민감도)"), (1, "fa_per_h", "오경보 (건/시간)")):
        for gi, (gname, d) in enumerate(groups):
            for si, (sn, r) in enumerate(d.items()):
                v, lo, hi = r[key]
                x = gi + (si - 1) * 0.26
                ax[j].bar(x, v, 0.24, color=cols[sn], label=sn if gi == 0 else None)
                ax[j].errorbar(x, v, yerr=[[max(v - lo, 0)], [max(hi - v, 0)]], color="k", lw=0.9, capsize=2)
        ax[j].set_xticks(range(3), [g[0] for g in groups], fontsize=9)
        ax[j].set_title(ttl)
    ax[0].set_ylim(0, 1.05)
    ax[0].legend(fontsize=8, loc="upper right")
    fig.suptitle("같은 모델, 현장에 가까워질수록: 학습은 UR 연출 영상만, 시점·영상은 한 번도 튜닝하지 않음 (95% 신뢰구간)", fontsize=10)
    fig.tight_layout()
    fig.savefig(FE.FOUT / "field_ladder.png", dpi=170)
    plt.close(fig)


if __name__ == "__main__":
    main()
