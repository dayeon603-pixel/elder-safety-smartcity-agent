"""Robustness to degraded video (synthetic low light / rain / low-res+JPEG) with and without a cheap pre-filter.
Protocol: models and thresholds are fit on CLEAN lab data only (UR, nested 5-fold identical to evaluate.py: thresholds chosen on
inner OOF of the training folds); degraded videos are TEST ONLY, never used for fitting or tuning.
  ur_cv   : UR, 5 outer folds, each fold's TEST sequences replaced by their degraded versions.
  (Le2i subset dropped: shared-CPU compute limit.)
Models: rule (torso angle >60 deg for 1.5 s) and v1 logistic (W=1 s, 9 features, refractory 5 s).
Degradations are synthetic proxies, not real night/rain footage: say so in the deck.
Usage: python robust_eval.py run
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import numpy as np
from joblib import Parallel, delayed

import evaluate as E
import features as F
import optimize as O

log = logging.getLogger("robust")
RX = Path("/Users/chloekang/kcf/data/cache/extra/robust")
ROUT = O.OUT / "robust"
LEVELS = ["light_g2", "light_g3", "rain_1", "rain_2", "lowres_1", "lowres_2"]
TAGS = ["clean"] + LEVELS + [f"{l}_f" for l in LEVELS]


def det_rate(path: Path) -> float:
    z = np.load(path)
    return float(np.mean(~np.isnan(z["lm"][:, 0, 0])))


def build(ds: str, base: list[O.Seq], tag: str) -> list[O.Seq]:
    """Degraded counterpart of each clean Seq in `base` (same labels/onsets), bank for W=1 only."""
    def one(s: O.Seq):
        p = (O.UR_DIR / "landmarks" if tag == "clean" else RX / ds / tag) / f"{s.name}.npz"
        t, xy, fps, dur = O.load_landmarks(p)
        n = O.Seq(ds, s.name, s.is_fall, s.onset, s.lying, s.duration, s.aspect, t, xy)
        te, B = O.bank_stream(t, xy, 1.0, s.aspect)
        n.banks[1.0] = (te, B, O._label(te, 1.0, s.is_fall, s.onset, s.lying))
        return n, det_rate(p)

    r = Parallel(n_jobs=6)(delayed(one)(s) for s in base)
    return [a for a, _ in r], float(np.mean([b for _, b in r]))


def fit_v1(train: list[O.Seq]):
    """Fit v1 logistic on clean training seqs; threshold from inner OOF (original selection rule)."""
    oof = O.inner_oof(train, [O.BASELINE], n_jobs=5)
    (cfg, pp), _ = O.select(train, oof, "orig")
    X = np.vstack([s.banks[1.0][1][:, O.FSETS["v1"]] for s in train]).astype(np.float64)
    y = np.concatenate([s.banks[1.0][2] for s in train])
    return O.fit_model("logreg", X, y), pp


def predict_events(model, pp, S: list[O.Seq]) -> list[E.EventRes]:
    P = [model.predict_proba(s.banks[1.0][1][:, O.FSETS["v1"]].astype(np.float64))[:, 1] if len(s.banks[1.0][0]) else np.zeros(0) for s in S]
    return O.events(S, P, 1.0, pp)


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    O.set_seed()
    ROUT.mkdir(parents=True, exist_ok=True)
    UR = O.load_ds("ur")
    out: dict = {"n_ur": len(UR)}
    bank: dict = {}
    for tag in TAGS:
        if tag == "clean":
            bank[("ur", tag)] = UR
            dr = {"ur": float(np.mean([det_rate(O.UR_DIR / "landmarks" / f"{s.name}.npz") for s in UR]))}
        else:
            bank[("ur", tag)], a = build("ur", UR, tag)
            dr = {"ur": a}
        out[tag] = {"det_rate": dr}
        log.info("banked %s det %s", tag, dr)
    # ---- UR nested CV: per-fold clean model, degraded test fold
    fold = O.strat_folds(UR, O.SEED)
    res = {t: [None] * len(UR) for t in TAGS}
    for k in range(5):
        tr = [UR[i] for i in range(len(UR)) if fold[i] != k]
        ti = [i for i in range(len(UR)) if fold[i] == k]
        model, pp = fit_v1(tr)
        for t in TAGS:
            r = predict_events(model, pp, [bank[("ur", t)][i] for i in ti])
            for i, e in zip(ti, r):
                res[t][i] = e
        log.info("ur fold %d thr=%.3f", k, pp["thr"])
    for t in TAGS:
        out[t]["ur_v1"] = O.summarize(res[t], UR)
        out[t]["ur_rule"] = O.summarize(O.rule_events(bank[("ur", t)]), UR)
        log.info("%-12s ur v1  %s", t, O.fmt(out[t]["ur_v1"]))
        log.info("%-12s ur rule %s", t, O.fmt(out[t]["ur_rule"]))
    json.dump(out, open(ROUT / "robust_summary.json", "w"), indent=1, default=float)
    figure(out)


def figure(out: dict) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.family"] = "AppleGothic"
    plt.rcParams["axes.unicode_minus"] = False
    fams = [("light", "조도 저하 (감마 2→3 + 노이즈)"), ("rain", "비 (줄기 + 번짐 + 안개)"), ("lowres", "저해상도 + JPEG 압축")]
    for ds, ttl in (("ur", "UR Fall (5-겹 교차검증, 열화는 테스트에만)"),):
        fig, ax = plt.subplots(3, 3, figsize=(11, 8), sharey="row")
        for j, (fam, name) in enumerate(fams):
            tags = ["clean", f"{fam}_" + ("g2" if fam == "light" else "1"), f"{fam}_" + ("g3" if fam == "light" else "2")]
            x = [0, 1, 2]
            series = {
                "규칙 기준선": ([out[t][f"{ds}_rule"] for t in tags], "#8a8a8a", "--"),
                "v1 모델": ([out[t][f"{ds}_v1"] for t in tags], "#1f5fbf", "-"),
                "v1 + 전처리": ([out[tags[0]][f"{ds}_v1"]] + [out[t + "_f"][f"{ds}_v1"] for t in tags[1:]], "#1fa05f", "-"),
            }
            for lab, (rows, c, ls) in series.items():
                ax[1, j].plot(x, [r["sens"][0] for r in rows], ls, color=c, marker="o", label=lab)
                ax[1, j].fill_between(x, [r["sens"][1] for r in rows], [r["sens"][2] for r in rows], color=c, alpha=0.1)
                ax[2, j].plot(x, [r["fa_per_h"][0] for r in rows], ls, color=c, marker="o", label=lab)
            raw = [out[t]["det_rate"]["ur"] for t in tags]
            flt = [out[tags[0]]["det_rate"]["ur"]] + [out[t + "_f"]["det_rate"]["ur"] for t in tags[1:]]
            ax[0, j].plot(x, raw, color="#1f5fbf", marker="o", label="원본 열화")
            ax[0, j].plot(x, flt, color="#1fa05f", marker="o", label="전처리 후")
            ax[0, j].set_title(name, fontsize=10)
            for r in range(3):
                ax[r, j].set_xticks(x, ["원본", "약", "강"])
        ax[0, 0].set_ylabel("포즈 검출률 (프레임)")
        ax[1, 0].set_ylabel("낙상 포착률 (민감도)")
        ax[2, 0].set_ylabel("오경보 (건/시간)")
        ax[0, 0].set_ylim(0, 1.02)
        ax[1, 0].set_ylim(0, 1.02)
        ax[2, 0].legend(fontsize=8)
        ax[0, 0].legend(fontsize=8)
        fig.suptitle(f"{ttl}: 합성 열화 (실제 야간·강우 영상 아님)", fontsize=11)
        fig.tight_layout()
        fig.savefig(ROUT / f"robust_curve_{ds}.png", dpi=170)
        plt.close(fig)


if __name__ == "__main__":
    run() if len(sys.argv) < 2 or sys.argv[1] == "run" else sys.exit("usage: robust_eval.py run")
