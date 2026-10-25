"""How precisely a region's heat rank may be stated, gated by its own case history (EDGE_CASES.md A17).

Retraining bootstraps move an alerted region's rank by a median 42–53 places out of 250, so a bare
"N위" over-states precision for thinly observed regions. Rule: numeric rank only when the region has
≥ RANK_EXACT_MIN_CASES historical cases; a quintile band between RANK_BAND_MIN_CASES and that; below it,
"자료 부족". The same helper feeds the briefing, the CBS text and the evidence dict.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

RANK_EXACT_MIN_CASES = 50
RANK_BAND_MIN_CASES = 20
BAND_STEPS = 5  # quintiles: 상위 20/40/60/80/100%
HEAT_SATURATION_C = 36.0  # 체감 ≥ 36℃: λ under-predicts 2–5× (experiments/edge heat_ood.json) -> lower bound


@dataclass(frozen=True, slots=True)
class RankText:
    mode: str  # exact | band | insufficient | none
    text: str | None
    n_cases: int | None

    def as_dict(self) -> dict[str, str | int | None]:
        return {"mode": self.mode, "text": self.text, "basis_cases": self.n_cases}


def rank_text(rank: int | None, n_cells: int | None, n_cases: int | None) -> RankText:
    """Phrase for a rank among `n_cells` scored cells, given `n_cases` historical cases of the region."""
    if rank is None or not n_cells:
        return RankText("none", None, n_cases)
    cases = int(n_cases or 0)
    if cases >= RANK_EXACT_MIN_CASES:
        return RankText("exact", f"{n_cells}곳 중 {rank}위", cases)
    if cases >= RANK_BAND_MIN_CASES:
        step = max(1, min(BAND_STEPS, math.ceil(rank / n_cells * BAND_STEPS)))
        return RankText("band", f"{n_cells}곳 중 상위 {step * 100 // BAND_STEPS}% 안 (과거 환자 {cases}건, 순위 구간으로만 표시)", cases)
    return RankText("insufficient", f"순위 자료 부족 (과거 환자 {cases}건)", cases)


def expected_cases_text(lam: float, apparent_max_c: float | None, decimals: int = 1) -> tuple[str, bool]:
    """'예상 환자 N명' or, at saturating heat, 'N명 이상(하한)'; returns (text, is_lower_bound)."""
    lower = apparent_max_c is not None and apparent_max_c >= HEAT_SATURATION_C
    base = f"예상 환자 {lam:.{decimals}f}명"
    return (base + " 이상(하한: 극한 폭염에서 모델이 과소예측)" if lower else base), lower


__all__ = ["HEAT_SATURATION_C", "RANK_BAND_MIN_CASES", "RANK_EXACT_MIN_CASES", "RankText", "expected_cases_text", "rank_text"]
