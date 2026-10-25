"""Decision layer for idea A: turn stored OURS scores into a resource plan, and price it against B1.

Algorithms (one line each):
  * Default team allocation ("spread"): one team per region on the T regions with the highest
    stored lambda. Needs no capacity assumption; it is the headline plan.
  * Optional "coverage" mode (assumption-based, not the headline): greedy marginal gain on separable concave coverage 1-(1-p)^n, which is optimal
    for separable concave objectives under a total-count constraint (exchange argument).
  * Shelter extension: rank shelters known to be closed at night/weekend by
    region lambda x (65+ residents attributed to the shelter within 1 km), take the top k.
  * Counterfactual: same T teams, B1 alerts as the only signal (uniform over B1-alerted regions,
    ties by pop65); compare actual cases inside regions that received a team, with a paired
    7-day moving-block bootstrap CI.

Inputs are only the stored alert scores for the requested day plus static region/shelter tables, so
the plan contains nothing newer than the variant's own information set. Actual cases are attached
afterwards for evaluation and never feed the allocation.

Assumptions (no sub-region population grid or team-capacity data exists in the database):
  * Only "coverage" mode: one team can reach TEAM_REACH_65 older residents per day, so its per-team share of a region's
    risk is p = min(TEAM_REACH_65 / pop65, pop65 / (pop65 + SATURATION_C)).
  * A shelter's 65+ catchment is the region's pop65 split over its shelters, each shelter weighted
    by 1 / (number of shelters within 1 km of it), which discounts overlapping coverage.
"""

from __future__ import annotations

import heapq
import math
import random
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date as date_type
from datetime import timedelta
from typing import Any

from .ranktext import expected_cases_text

from .. import repo_heat

TEAM_REACH_65 = 8000.0  # older residents one visiting team can check per day (assumption)
SATURATION_C = 5000.0  # caps p for very small regions: p <= pop65 / (pop65 + c)
SHELTER_RADIUS_KM = 1.0
EARTH_RADIUS_KM = 6371.0088
MODE_SPREAD = "spread"
MODE_COVERAGE = "coverage"
BOOT_N = 1000
BOOT_BLOCK_DAYS = 7
BOOT_SEED = 42
CI_ALPHA = 0.05
MAN_UNIT = 10000


@dataclass(frozen=True, slots=True)
class Cell:
    """One alerted region on one day: lambda is the stored score (expected cases)."""

    code: str
    label: str
    lam: float
    pop65: int
    cases: int


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def team_share(pop65: int) -> float:
    """Per-team share p of a region's expected cases a single team reaches (0 for empty regions)."""
    if pop65 <= 0:
        return 0.0
    return min(TEAM_REACH_65 / pop65, pop65 / (pop65 + SATURATION_C), 1.0)


def coverage_gain(lam: float, pop65: int, n_teams: int) -> float:
    """Expected cases reached with n teams: lambda * (1 - (1 - p)^n)."""
    return lam * (1.0 - (1.0 - team_share(pop65)) ** n_teams)


def allocate_greedy(cells: list[Cell], teams: int) -> dict[str, int]:
    """Place `teams` one at a time on the region with the largest marginal expected gain.

    Marginal gain of the (n+1)-th team is lambda * p * (1-p)^n, decreasing in n, so the objective is
    separable and concave and greedy is exactly optimal. Ties break by region code. Teams with no
    positive gain left are not deployed, so the result may use fewer than `teams`.

    Raises:
        ValueError: if teams is negative.
    """
    if teams < 0:
        raise ValueError("teams must be >= 0")
    heap: list[tuple[float, str]] = []
    by_code = {c.code: c for c in cells}
    for c in cells:
        g = c.lam * team_share(c.pop65)
        if g > 0:
            heap.append((-g, c.code))
    heapq.heapify(heap)
    out: dict[str, int] = {}
    for _ in range(teams):
        if not heap:
            break
        _, code = heapq.heappop(heap)
        out[code] = out.get(code, 0) + 1
        c = by_code[code]
        p = team_share(c.pop65)
        heapq.heappush(heap, (-c.lam * p * (1.0 - p) ** out[code], code))
    return out


def allocate_uniform(cells: list[Cell], teams: int) -> dict[str, int]:
    """B1 has no lambda: spread teams evenly over its alerted regions, extras to larger pop65.

    With fewer teams than alerted regions the `teams` regions with the most 65+ residents get one.
    Ties by region code.
    """
    if teams < 0:
        raise ValueError("teams must be >= 0")
    if not cells:
        return {}
    order = sorted(cells, key=lambda c: (-c.pop65, c.code))
    base, extra = divmod(teams, len(order))
    out: dict[str, int] = {}
    for i, c in enumerate(order):
        n = base + (1 if i < extra else 0)
        if n > 0:
            out[c.code] = n
    return out


def reached_cases(cells: list[Cell], alloc: dict[str, int]) -> int:
    """Actual cases in regions that received at least one team."""
    return sum(c.cases for c in cells if alloc.get(c.code, 0) > 0)


def covered_cases(cells: list[Cell], alloc: dict[str, int]) -> float:
    """Actual cases weighted by the coverage the plan buys: cases * (1 - (1-p)^n) per region.

    Same coverage model the allocator optimises, evaluated on realised cases. Unlike
    `reached_cases` it does not treat a second team in a region as worthless.
    """
    return sum(c.cases * (1.0 - (1.0 - team_share(c.pop65)) ** alloc.get(c.code, 0)) for c in cells)


def allocate_spread(cells: list[Cell], teams: int) -> dict[str, int]:
    """One team per region on the `teams` regions with the highest lambda (no stacking).

    Same shape as the B1 plan (one team per region), so 'reached' is compared like for like.
    """
    if teams < 0:
        raise ValueError("teams must be >= 0")
    top = sorted((c for c in cells if c.lam > 0), key=lambda c: (-c.lam, c.code))[:teams]
    return {c.code: 1 for c in top}


def _pop_text(pop65: int) -> str:
    if pop65 >= MAN_UNIT:
        return f"{pop65 / MAN_UNIT:.1f}만 명"
    return f"{pop65:,}명"


def team_reason(c: Cell, tmax: float | None, day_word: str, apparent: float | None = None) -> str:
    """Plain-Korean justification, e.g. '예상 환자 3.2명, 65세 이상 1.8만 명, 오늘 체감 최고 34.1℃'."""
    parts = [expected_cases_text(c.lam, apparent)[0], f"65세 이상 {_pop_text(c.pop65)}"]
    if apparent is not None:
        parts.append(f"{day_word} 체감 최고 {apparent:.1f}℃")
    elif tmax is not None:
        parts.append(f"{day_word} 최고기온 {tmax:.1f}℃")
    return ", ".join(parts)


def pick_shelter_extensions(
    cells: list[Cell], shelters: list[dict[str, Any]], k: int
) -> tuple[list[dict[str, Any]], int]:
    """Choose up to k shelters to extend to night/weekend hours.

    Candidates are shelters with night_open == 0 or weekend_open == 0 in the given regions. Shelters
    that are not known-closed but have an unknown (NULL) flag are excluded and counted.

    Returns:
        (extensions in priority order, number of shelters excluded for unknown hours)
    """
    by_code = {c.code: c for c in cells}
    per_region: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in shelters:
        if s["region_code"] in by_code:
            per_region[s["region_code"]].append(s)
    scored: list[tuple[float, int, dict[str, Any], str, Cell, float]] = []
    unknown = 0
    for code, group in per_region.items():
        cell = by_code[code]
        weights = [
            1.0 / sum(1 for o in group if haversine_km(s["lat"], s["lon"], o["lat"], o["lon"]) <= SHELTER_RADIUS_KM)
            for s in group
        ]
        total_w = sum(weights)
        for s, w in zip(group, weights, strict=True):
            closed = [name for flag, name in ((s["night_open"], "야간"), (s["weekend_open"], "주말")) if flag == 0]
            if not closed:
                if s["night_open"] is None or s["weekend_open"] is None:
                    unknown += 1
                continue
            near65 = cell.pop65 * w / total_w
            scored.append((cell.lam * near65, int(s["id"]), s, "·".join(closed), cell, near65))
    scored.sort(key=lambda t: (-t[0], t[1]))
    out = [
        {
            "shelter_id": s["id"], "name": s["name"], "region_code": s["region_code"],
            "reason": (
                f"{cell.label}: 예상 환자 {cell.lam:.1f}명, 반경 1km 65세 이상 약 {round(near65):,}명(추정), "
                f"현재 {closed} 닫힘"
            ),
        }
        for _, _, s, closed, cell, near65 in scored[:k]
    ]
    return out, unknown


def block_bootstrap_ci(
    values: list[float], block_days: int = BOOT_BLOCK_DAYS, n_boot: int = BOOT_N, seed: int = BOOT_SEED
) -> tuple[float, float]:
    """95% percentile CI of the sum of per-day paired differences, resampling consecutive blocks.

    Moving blocks of `block_days` keep within-week dependence; each resample has len(values) days.
    """
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    block = min(block_days, n)
    n_blocks = math.ceil(n / block)
    rng = random.Random(seed)
    sums: list[float] = []
    for _ in range(n_boot):
        total = 0.0
        taken = 0
        for _ in range(n_blocks):
            start = rng.randrange(n - block + 1)
            chunk = values[start:start + block][: n - taken]
            total += sum(chunk)
            taken += len(chunk)
        sums.append(total)
    sums.sort()
    lo = sums[int(CI_ALPHA / 2 * (n_boot - 1))]
    hi = sums[math.ceil((1 - CI_ALPHA / 2) * (n_boot - 1))]
    return lo, hi


def _cells(rows: list[dict[str, Any]]) -> dict[str, list[Cell]]:
    by_day: dict[str, list[Cell]] = defaultdict(list)
    for r in rows:
        by_day[r["date"]].append(
            Cell(r["code"], f"{r['sido']} {r['name']}", float(r["score"]), int(r["pop65"]), int(r["cases"]))
        )
    return by_day


def _allocate(mode: str, cells: list[Cell], teams: int) -> dict[str, int]:
    if mode == MODE_SPREAD:
        return allocate_spread(cells, teams)
    if mode == MODE_COVERAGE:
        return allocate_greedy(cells, teams)
    raise ValueError(f"unknown mode {mode!r}")


def build_day_plan(
    conn: sqlite3.Connection, b1_run: int, ours_run: int, day: str, variant: str, teams: int,
    shelters: int, mode: str = MODE_SPREAD,
) -> dict[str, Any]:
    """Action plan for one day from the stored OURS scores, plus the same-budget B1 comparison.

    Spread mode ranks every stored region of the day by lambda (alerted regions are the top of that
    ranking, so extra teams beyond the alert count go to the next-highest lambda regions).
    """
    ours = _cells(repo_heat.plan_cells(conn, ours_run, day, day, alerted_only=False)).get(day, [])
    b1 = _cells(repo_heat.plan_cells(conn, b1_run, day, day)).get(day, [])
    alloc = _allocate(mode, ours, teams)
    b1_alloc = allocate_uniform(b1, teams)
    d = date_type.fromisoformat(day)
    tmax_day, day_word = (d, "오늘") if variant == "F" else (d - timedelta(days=1), "어제")
    by_code = {c.code: c for c in ours}
    wx = {code: repo_heat.station_weather(conn, code, tmax_day.isoformat()) for code in alloc}
    allocations: list[dict[str, Any]] = [
        {
            "code": code, "name": by_code[code].label, "teams": n, "expected_cases": round(by_code[code].lam, 3),
            "actual_cases": by_code[code].cases,
            "reason": team_reason(by_code[code], wx[code]["tmax"], day_word, wx[code]["apparent_max"]),
        }
        for code, n in alloc.items()
    ]
    allocations.sort(key=lambda a: (-a["teams"], -a["expected_cases"], a["code"]))
    funded = [by_code[a["code"]] for a in allocations]
    exts: list[dict[str, Any]] = []
    unknown = 0
    if funded:
        exts, unknown = pick_shelter_extensions(
            funded, repo_heat.shelters_in_regions(conn, [c.code for c in funded]), shelters
        )
    return {
        "date": day, "teams": teams, "mode": mode, "allocations": allocations, "shelter_extensions": exts,
        "unknown_hours_count": unknown,
        "compare": {"ours_reached": reached_cases(ours, alloc), "b1_reached": reached_cases(b1, b1_alloc)},
    }


def evaluate_year(
    conn: sqlite3.Connection, b1_run: int, ours_run: int, year: int, teams: int, mode: str = MODE_SPREAD
) -> dict[str, Any]:
    """Season-level 'same teams, more cases reached': OURS plan vs B1 plan with paired CI.

    Metric: actual cases in regions that received at least one team (binary reach). OURS spread =
    one team per top-lambda region; B1 = uniform over its alerted regions. CI = paired 7-day
    moving-block bootstrap of the season difference.
    """
    first, last = f"{year}-01-01", f"{year}-12-31"
    ours = _cells(repo_heat.plan_cells(conn, ours_run, first, last, alerted_only=False))
    b1 = _cells(repo_heat.plan_cells(conn, b1_run, first, last))
    per_day: list[dict[str, Any]] = []
    for day in sorted(b1):  # days B1 would act on; elsewhere B1 spends nothing, so no like-for-like budget
        o_cells, b_cells = ours.get(day, []), b1[day]
        per_day.append({
            "date": day,
            "ours_reached": reached_cases(o_cells, _allocate(mode, o_cells, teams)),
            "b1_reached": reached_cases(b_cells, allocate_uniform(b_cells, teams)),
        })
    o_tot = sum(p["ours_reached"] for p in per_day)
    b_tot = sum(p["b1_reached"] for p in per_day)
    lo, hi = block_bootstrap_ci([float(p["ours_reached"] - p["b1_reached"]) for p in per_day])
    total_cases = sum(r["n_cases"] for r in repo_heat.days(conn, b1_run, ours_run, year))
    return {
        "year": year, "teams": teams, "mode": mode, "n_days": len(per_day), "total_cases": total_cases,
        "ours_reached": o_tot, "b1_reached": b_tot, "delta": o_tot - b_tot, "ci_lo": lo, "ci_hi": hi,
        "per_day": per_day,
    }
