"""ICASSP-style plotting helpers. All functions draw on a supplied Axes or create one."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.axes import Axes  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from numpy.typing import ArrayLike, NDArray  # noqa: E402
from sklearn.metrics import precision_recall_curve, roc_curve  # noqa: E402

logger = logging.getLogger(__name__)
logging.getLogger("fontTools").setLevel(logging.WARNING)

STYLE_PATH = Path(__file__).resolve().parent / "icassp.mplstyle"
OUT_DIR = Path(__file__).resolve().parent / "out"
EXPORT_DPI = 300
# Series 0 is the baseline (gray); series 1 is ours (single accent). GUIDE.md: 2 colors per chart.
PALETTE = ("#8C8C8C", "#0072B2", "#D55E00", "#009E73", "#E69F00", "#CC79A7")
SINGLE_COL_IN = 3.5
RELIABILITY_BINS = 10


def use_style() -> None:
    """Activate the ICASSP style globally."""
    plt.style.use(str(STYLE_PATH))


def new_figure(height_in: float = 2.4) -> tuple[Figure, Axes]:
    """Single-column figure (3.5 in wide) with one Axes."""
    use_style()
    fig, ax = plt.subplots(figsize=(SINGLE_COL_IN, height_in), layout="constrained")
    return fig, ax


def save_figure(fig: Figure, name: str, out_dir: Path = OUT_DIR) -> list[Path]:
    """Write `<name>.png` and `<name>.pdf` at 300 dpi; return the paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = [out_dir / f"{name}.{ext}" for ext in ("png", "pdf")]
    for p in paths:
        fig.savefig(p, dpi=EXPORT_DPI)
    plt.close(fig)
    logger.info("wrote %s", ", ".join(p.name for p in paths))
    return paths


def bar_with_ci(
    labels: Sequence[str],
    series: Mapping[str, Sequence[tuple[float, float, float]]],
    ylabel: str,
    ax: Axes | None = None,
) -> Axes:
    """Grouped bars. series[name][i] = (point, lo, hi) for labels[i]; error bars are the CI."""
    if ax is None:
        _, ax = new_figure()
    n = len(series)
    width = 0.8 / n
    x = np.arange(len(labels))
    for k, (name, vals) in enumerate(series.items()):
        pts = np.array([v[0] for v in vals])
        err = np.array([[v[0] - v[1] for v in vals], [v[2] - v[0] for v in vals]])
        ax.bar(x + (k - (n - 1) / 2) * width, pts, width * 0.92, yerr=err, label=name,
               color=PALETTE[k % len(PALETTE)], error_kw={"elinewidth": 0.6, "capsize": 1.5, "capthick": 0.6})
    ax.set_xticks(x, list(labels))
    ax.set_ylabel(ylabel)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=min(n, 2))
    return ax


def roc_plot(
    y_true: ArrayLike, scores: Mapping[str, ArrayLike], ax: Axes | None = None
) -> Axes:
    """ROC curves for several methods plus the chance diagonal."""
    if ax is None:
        _, ax = new_figure(2.6)
    y = np.asarray(y_true)
    for k, (name, s) in enumerate(scores.items()):
        fpr, tpr, _ = roc_curve(y, np.asarray(s))
        ax.plot(fpr, tpr, label=name, color=PALETTE[k % len(PALETTE)])
    ax.plot([0, 1], [0, 1], color="0.6", lw=0.5, ls="--")
    ax.set(xlabel="False positive rate", ylabel="True positive rate", xlim=(0, 1), ylim=(0, 1.01))
    ax.legend(loc="lower right")
    return ax


def pr_plot(y_true: ArrayLike, scores: Mapping[str, ArrayLike], ax: Axes | None = None) -> Axes:
    """Precision-recall curves with the prevalence line."""
    if ax is None:
        _, ax = new_figure(2.6)
    y = np.asarray(y_true)
    for k, (name, s) in enumerate(scores.items()):
        prec, rec, _ = precision_recall_curve(y, np.asarray(s))
        ax.plot(rec, prec, label=name, color=PALETTE[k % len(PALETTE)])
    ax.axhline(float(y.mean()), color="0.6", lw=0.5, ls="--")
    ax.set(xlabel="Recall", ylabel="Precision", xlim=(0, 1), ylim=(0, 1.02))
    ax.legend(loc="lower left")
    return ax


def reliability_plot(
    y_true: ArrayLike,
    probs: Mapping[str, ArrayLike],
    bins: int = RELIABILITY_BINS,
    ax: Axes | None = None,
) -> Axes:
    """Reliability diagram: mean predicted vs observed frequency per equal-width bin."""
    if ax is None:
        _, ax = new_figure(2.6)
    y = np.asarray(y_true)
    edges = np.linspace(0, 1, bins + 1)
    for k, (name, p_in) in enumerate(probs.items()):
        p = np.asarray(p_in)
        which = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
        xs, ys = [], []
        for b in range(bins):
            m = which == b
            if m.any():
                xs.append(float(p[m].mean()))
                ys.append(float(y[m].mean()))
        ax.plot(xs, ys, marker="o", label=name, color=PALETTE[k % len(PALETTE)])
    ax.plot([0, 1], [0, 1], color="0.6", lw=0.5, ls="--")
    ax.set(xlabel="Mean predicted probability", ylabel="Observed frequency", xlim=(0, 1), ylim=(0, 1.02))
    ax.legend(loc="upper left")
    return ax


def line_with_band(
    x: ArrayLike,
    mean: ArrayLike,
    lo: ArrayLike,
    hi: ArrayLike,
    label: str,
    color_idx: int = 0,
    ax: Axes | None = None,
) -> Axes:
    """Line with a shaded interval (e.g. 95% bootstrap band)."""
    if ax is None:
        _, ax = new_figure()
    c = PALETTE[color_idx % len(PALETTE)]
    ax.plot(np.asarray(x), np.asarray(mean), color=c, label=label)
    ax.fill_between(np.asarray(x), np.asarray(lo), np.asarray(hi), color=c, alpha=0.2, linewidth=0)
    ax.legend(loc="lower right")
    return ax


def percentile_band(curves: NDArray[np.float64], level: float = 0.95) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Mean and symmetric percentile band across rows of `curves` (n_curves, n_points)."""
    a = (1 - level) / 2
    return curves.mean(0), np.quantile(curves, a, axis=0), np.quantile(curves, 1 - a, axis=0)
