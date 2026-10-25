"""Render sample figures from experiments/results/<name>.json into figures/out/."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
from sklearn.metrics import roc_curve

from figures import plot_helpers as ph

logger = logging.getLogger(__name__)
RESULTS_DIR = Path(__file__).resolve().parents[1] / "experiments" / "results"
BAR_METRICS = ("auroc", "auprc", "accuracy", "f1")
BAND_BOOT = 300
BAND_GRID = 101


def latest_result(results_dir: Path) -> Path:
    """Newest experiment JSON in `results_dir` (by modification time).

    Raises:
        FileNotFoundError: if there is none (run `make exp` first).
    """
    candidates = sorted(results_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
    if not candidates:
        raise FileNotFoundError(f"no results in {results_dir}; run `make exp` first")
    return candidates[-1]


def render(result_path: Path, out_dir: Path = ph.OUT_DIR) -> list[Path]:
    """Create bar, ROC, PR, reliability and ROC-band figures; return written files."""
    res = json.loads(result_path.read_text(encoding="utf-8"))
    y = np.asarray(res["y_true"])
    meth = res["methods"]
    probs = {meth[r]["name"]: np.asarray(meth[r]["y_prob"]) for r in ("baseline", "ours")}
    written: list[Path] = []

    fig, ax = ph.new_figure()
    series = {
        meth[r]["name"]: [(meth[r]["metrics"][m]["point"], meth[r]["metrics"][m]["lo"], meth[r]["metrics"][m]["hi"])
                          for m in BAR_METRICS]
        for r in ("baseline", "ours")
    }
    ph.bar_with_ci([m.upper() for m in BAR_METRICS], series, "Score (95% CI)", ax)
    ax.set_ylim(0.5, 1.02)
    written += ph.save_figure(fig, "bar_metrics", out_dir)

    for name, fn in (("roc", ph.roc_plot), ("pr", ph.pr_plot), ("reliability", ph.reliability_plot)):
        fig, ax = ph.new_figure(2.6)
        fn(y, probs, ax=ax)
        written += ph.save_figure(fig, name, out_dir)

    rng = np.random.default_rng(res["config"]["seed"])
    grid = np.linspace(0, 1, BAND_GRID)
    p_ours = np.asarray(meth["ours"]["y_prob"])
    curves: list[np.ndarray] = []
    while len(curves) < BAND_BOOT:
        idx = rng.integers(0, len(y), len(y))
        if y[idx].min() == y[idx].max():
            continue
        fpr, tpr, _ = roc_curve(y[idx], p_ours[idx])
        curves.append(np.interp(grid, fpr, tpr))
    mean, lo, hi = ph.percentile_band(np.asarray(curves))
    fig, ax = ph.new_figure(2.6)
    ph.line_with_band(grid, mean, lo, hi, f"{meth['ours']['name']} (95% band)", 0, ax)
    ax.set(xlabel="False positive rate", ylabel="True positive rate")
    written += ph.save_figure(fig, "roc_band", out_dir)
    return written


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--result", type=Path, default=None, help="default: newest results/*.json")
    ap.add_argument("--out", type=Path, default=ph.OUT_DIR)
    args = ap.parse_args()
    result = args.result or latest_result(RESULTS_DIR)
    logger.info("rendering %s", result)
    render(result, args.out)


if __name__ == "__main__":
    main()
