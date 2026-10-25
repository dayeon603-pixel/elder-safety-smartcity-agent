"""How stable is "영점 순위 N위" under training-set resampling? (variant F, test year 2025)

Refits the OURS unit model (same HGB_PARAMS, same features as experiments/heat/evaluate.py) on B bootstrap
resamples of the training rows (2019-2024), predicts the 2025 season, and records for every region-day its
rank among the 250 regions. Reports, on the demo day and over the season:
  * for regions ranked inside the day's B1 budget K_d by the original model, the 95 % interval of their rank;
  * the share of the original top-K set that stays inside the top K in a resample (membership stability);
  * the same for the top 25 % (the cut-off /actions uses before writing a rank into a 재난문자).
Bootstrap of rows (iid) is optimistic about day-to-day dependence; it measures model-fit noise only.
Backtests are hypotheses, not proofs.

Run: uv run python experiments/edge/rank_stability_model.py [--boot 10] [--day 2025-07-22]
"""

from __future__ import annotations

import argparse
import logging
from typing import Any
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "heat"))
from edge_common import OUT, PANEL, SEED, dump, set_seed  # noqa: E402

import evaluate as E  # noqa: E402  (experiments/heat/evaluate.py: same model, same features)

logger = logging.getLogger("edge.rank_model")
VARIANT = "F"
TEST_YEAR = 2025
RANK_SHARE_CBS = 0.25  # actions/constants.RANK_TEXT_TOP_SHARE


def ranks_per_day(te: pd.DataFrame, score: np.ndarray) -> np.ndarray:
    """1 = highest score within each day; NaN scores rank last."""
    s = np.where(np.isfinite(score), score, -np.inf)
    df = pd.DataFrame({"date": te.date.to_numpy(), "s": s, "unit": te.unit_id.to_numpy()})
    ranked: np.ndarray = np.asarray(df.groupby("date").s.rank(ascending=False, method="first").to_numpy(), dtype=int)
    return ranked


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--boot", type=int, default=10)
    ap.add_argument("--day", default="2025-07-22")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    set_seed()
    t0 = time.time()
    P = E.use_family(pd.read_csv(PANEL, parse_dates=["date"]), "at").sort_values(["date", "unit_id"]).reset_index(drop=True)
    feats = E.UNIT_FEATS[VARIANT]
    key = E.KEY_T[VARIANT]
    tr = P[(P.year < TEST_YEAR) & P[key].notna()].reset_index(drop=True)
    te = P[P.year == TEST_YEAR].reset_index(drop=True)
    assert tr.date.max() < te.date.min()
    E.set_seed(SEED)
    base = E.fit_hgb(tr[feats], tr.y.to_numpy())
    base_rank = ranks_per_day(te, base.predict(te[feats]))
    logger.info("base fit done (%.0fs), n_train=%d", time.time() - t0, len(tr))

    k_d = te.groupby("date")[E.RULES[VARIANT]["B1"]].transform("sum").to_numpy().astype(int)
    n_day = te.groupby("date").unit_id.transform("size").to_numpy()
    in_topk = base_rank <= k_d
    in_top25 = base_rank <= np.floor(RANK_SHARE_CBS * n_day)
    rng = np.random.default_rng(SEED)
    boot_ranks = np.empty((args.boot, len(te)), dtype=int)
    for b in range(args.boot):
        idx = rng.integers(0, len(tr), size=len(tr))
        E.set_seed(SEED + b + 1)
        m = E.fit_hgb(tr.iloc[idx][feats], tr.y.to_numpy()[idx])
        boot_ranks[b] = ranks_per_day(te, m.predict(te[feats]))
        logger.info("boot %d/%d (%.0fs)", b + 1, args.boot, time.time() - t0)

    lo, hi = np.percentile(boot_ranks, [2.5, 97.5], axis=0)
    width = hi - lo
    stay_topk = (boot_ranks <= k_d[None, :]).mean(0)
    stay_top25 = (boot_ranks <= np.floor(RANK_SHARE_CBS * n_day)[None, :]).mean(0)
    day = te.date.dt.strftime("%Y-%m-%d").to_numpy()
    sel_day = day == args.day

    def summary(mask: np.ndarray) -> dict[str, Any]:
        if not mask.any():
            return {"n": 0}
        return {
            "n_region_days": int(mask.sum()),
            "rank_ci_width_median": float(np.median(width[mask])),
            "rank_ci_width_p90": float(np.percentile(width[mask], 90)),
            "share_stay_in_topk_mean": float(stay_topk[mask].mean()),
            "share_region_days_stay_topk_ge_0_8": float((stay_topk[mask] >= 0.8).mean()),
            "share_stay_in_top25pct_mean": float(stay_top25[mask].mean()),
        }

    out = {
        "variant": VARIANT, "test_year": TEST_YEAR, "n_boot": args.boot, "n_train_rows": int(len(tr)),
        "hgb_params": E.HGB_PARAMS, "features": feats,
        "season_topk": summary(in_topk & (k_d > 0)),
        "season_top25pct": summary(in_top25),
        "season_all": summary(np.ones(len(te), bool)),
        "demo_day": args.day,
        "demo_day_k": int(k_d[sel_day][0]) if sel_day.any() else None,
        "demo_day_topk": summary(sel_day & in_topk),
        "demo_day_rank_table": [
            {"unit_id": int(u), "sgg": f"{s} {g}", "rank": int(r), "ci": [int(a), int(b)], "stay_topk": float(st)}
            for u, s, g, r, a, b, st in sorted(
                zip(te.unit_id[sel_day & in_topk], te.sido[sel_day & in_topk], te.sgg[sel_day & in_topk], base_rank[sel_day & in_topk],
                    lo[sel_day & in_topk], hi[sel_day & in_topk], stay_topk[sel_day & in_topk], strict=True),
                key=lambda t: t[3])
        ][:40],
        "runtime_s": time.time() - t0,
    }
    dump(out, OUT / "rank_stability_model.json")


if __name__ == "__main__":
    main()
