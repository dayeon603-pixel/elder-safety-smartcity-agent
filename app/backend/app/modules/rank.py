"""Rank uncertainty for small-count league tables (AED 밤 공백 순위 among 25 구).

The gap score of a 구 is `events_per_year × (1 − night_open_share)`; with 12–33 events per year the exact
rank is noise (EDGE_CASES.md C: 95% rank-interval width median 19 of 25). A Poisson bootstrap resamples
each 구's yearly events, recomputes the scores and ranks, and reports set membership (probability of
being in the top K) plus a rank interval instead of a point rank. Deterministic (fixed seed).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SEED = 42
N_BOOT = 4000
TOP_K = 5
CI = 0.95
MEMBERSHIP_THRESHOLD = 0.5  # "상위 5구 안" is asserted only when the bootstrap probability reaches this


@dataclass(frozen=True, slots=True)
class RankUncertainty:
    rank_point: int
    rank_lo: int
    rank_hi: int
    top_k_probability: float
    events_per_year: float
    n_units: int
    top_k: int

    def text(self, unit: str = "구") -> str:
        """Plain Korean: set membership first, the interval as the fact behind it."""
        pct = round(self.top_k_probability * 100)
        if self.top_k_probability >= MEMBERSHIP_THRESHOLD:
            return f"{self.n_units}개 {unit} 중 상위 {self.top_k}{unit} 안 (확률 {pct}%, 순위 구간 {self.rank_lo}~{self.rank_hi}위)"
        return f"{self.n_units}개 {unit} 중 상위 {self.top_k}{unit} 밖일 가능성이 큼 (안에 들 확률 {pct}%, 순위 구간 {self.rank_lo}~{self.rank_hi}위)"

    def as_dict(self) -> dict[str, float | int | list[int]]:
        return {"rank_point": self.rank_point, "rank_interval": [self.rank_lo, self.rank_hi],
                "top_k": self.top_k, "top_k_probability": round(self.top_k_probability, 3),
                "events_per_year": round(self.events_per_year, 1), "n_units": self.n_units}


def poisson_rank(events: dict[str, float], factors: dict[str, float], target: str, *, top_k: int = TOP_K,
                 n_boot: int = N_BOOT, seed: int = SEED) -> RankUncertainty:
    """Rank uncertainty of `target` when score_u = events_u × factor_u and events_u ~ Poisson(events_u).

    Raises:
        KeyError: target is not in `events`.
        ValueError: negative events or empty table.
    """
    names = sorted(events)
    if not names:
        raise ValueError("empty table")
    lam = np.array([float(events[n]) for n in names])
    fac = np.array([float(factors.get(n, 1.0)) for n in names])
    if (lam < 0).any():
        raise ValueError("events must be non-negative")
    if target not in events:
        raise KeyError(target)
    idx = names.index(target)
    rng = np.random.default_rng(seed)
    draws = rng.poisson(lam, size=(n_boot, len(names))) * fac  # (B, U) scores
    # rank 1 = largest score; ties share the best rank (1 + number strictly greater)
    target_scores = draws[:, idx][:, None]
    ranks = 1 + (draws > target_scores).sum(axis=1)
    point = 1 + int((lam * fac > lam[idx] * fac[idx]).sum())
    lo, hi = np.quantile(ranks, [(1 - CI) / 2, 1 - (1 - CI) / 2])
    return RankUncertainty(point, int(np.floor(lo)), int(np.ceil(hi)), float((ranks <= top_k).mean()),
                           float(lam[idx]), len(names), top_k)


__all__ = ["MEMBERSHIP_THRESHOLD", "N_BOOT", "RankUncertainty", "TOP_K", "poisson_rank"]
