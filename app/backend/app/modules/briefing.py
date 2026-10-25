"""「오늘의 에이전트 브리핑」: one payload per 시군구 joining the heat, silver-zone and AED modules.

Read-only. Heat numbers come from the stored runs through `repo_heat` / `heat.plan` (called, not
modified); silver and AED numbers come from the loaded module tables. Texts are short Korean,
one number per line, comparisons written "A → B".
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .. import repo_heat
from ..heat import plan as heat_plan
from ..heat.plan import haversine_km
from ..heat.ranktext import expected_cases_text, rank_text
from . import repo
from .evidence import heat_evidence
from .rank import poisson_rank

VARIANT = "F"
SEOUL = repo.SEOUL
SHELTER_LIMIT = 5
SHELTERS_FOR_PLAN = 0  # shelter extensions are a separate screen; the briefing only needs the team count
PCT = 100
NOTE_AED_SEOUL_ONLY = "서울만 분석"
NOTE_SILVER_SEOUL_ONLY = "서울 지도만 분석"
NOTE_NOT_LOADED = "모듈 산출물이 적재되지 않았습니다 (make modules-load)"
NOTE_GU_MISSING = "이 구는 AED 분석표에 없습니다"


def _flag(v: int | None) -> bool | None:
    return None if v is None else bool(v)


HANGUL_FIRST, HANGUL_LAST = 0xAC00, 0xD7A3
JONGSEONG_COUNT = 28


def topic(word: str) -> str:
    """`word` + 은/는 by whether its last syllable ends in a consonant (non-Hangul endings get 는)."""
    code = ord(word[-1])
    has_final = HANGUL_FIRST <= code <= HANGUL_LAST and (code - HANGUL_FIRST) % JONGSEONG_COUNT != 0
    return word + ("은" if has_final else "는")


def _pct(x: float) -> int:
    return round(x * PCT)


def heat_section(
    conn: sqlite3.Connection, region: dict[str, Any], day: str, b1_run: int, ours_run: int, teams: int
) -> dict[str, Any]:
    """Region's standing on `day`: our rank, who alerted it, actual cases, team allocation, open shelters."""
    code = region["code"]
    score = repo_heat.cell_score(conn, ours_run, code, day)
    b1_alerted = {c["code"] for c in repo_heat.alerted_cells(conn, b1_run, day)}
    ours_alerted = {c["code"] for c in repo_heat.alerted_cells(conn, ours_run, day)}
    evaluated = score is not None
    plan = heat_plan.build_day_plan(conn, b1_run, ours_run, day, VARIANT, teams, SHELTERS_FOR_PLAN)
    mine = next((a for a in plan["allocations"] if a["code"] == code), None)
    full = repo_heat.get_region(conn, code)
    assert full is not None
    rank = repo_heat.cell_rank(conn, ours_run, day, score) if score is not None else None
    n_cells = len(ours_alerted) and int(conn.execute(
        "SELECT COUNT(*) FROM alert WHERE run_id = ? AND date = ?", (ours_run, day)).fetchone()[0])
    n_cases_hist = repo_heat.region_case_count(conn, code, f"{day[:4]}-01-01")
    rtext = rank_text(rank, n_cells or None, n_cases_hist)
    near = sorted(
        (
            {
                "name": s["name"], "dist_km": round(haversine_km(full["lat"], full["lon"], s["lat"], s["lon"]), 3),
                "night_open": _flag(s["night_open"]), "weekend_open": _flag(s["weekend_open"]),
            }
            for s in repo_heat.region_shelters(conn, code)
        ),
        key=lambda s: (s["dist_km"], s["name"]),
    )
    every = repo_heat.shelter_count(conn, code)
    return {
        "rank": rank,
        "rank_text": rtext.as_dict(),
        "evidence": {**heat_evidence(conn, full, day, score=score, rank=rank, n_cells=n_cells or None,
                                     alerted=(code in ours_alerted) if evaluated else None),
                     "rank_text": rtext.as_dict(), "rank_basis_cases": n_cases_hist},
        "alerted_b1": (code in b1_alerted) if evaluated else None,
        "alerted_ours": (code in ours_alerted) if evaluated else None,
        "cases_actual": repo_heat.cases_in_region(conn, code, day),
        "plan_allocation_for_region": {
            "teams": mine["teams"] if mine else 0, "teams_total": teams,
            "expected_cases": mine["expected_cases"] if mine else None, "reason": mine["reason"] if mine else None,
        },
        "nearby_open_shelters": {
            "coverage": "data" if every else "none",
            "items": [s for s in near if s["night_open"] or s["weekend_open"]][:SHELTER_LIMIT],
        },
    }


def silver_section(conn: sqlite3.Connection, region: dict[str, Any]) -> dict[str, Any]:
    """Zone / pick / hotspot counts for a Seoul 구; nulls with a note elsewhere or before the load."""
    empty: dict[str, Any] = {
        "current_zones_in_region": None, "our_picks_in_region": None, "hotspots_2024_in_region": None,
        "hotspots_covered_current": None, "hotspots_covered_ours": None,
        # crosswalk signal timings are not part of the module outputs, so a green-time deficit is not derivable here
        "crosswalk_green_deficit_count": None, "note": None,
    }
    if not repo.silver_loaded(conn):
        return {**empty, "note": NOTE_NOT_LOADED}
    if region["sido"] != SEOUL:
        return {**empty, "note": NOTE_SILVER_SEOUL_ONLY}
    c = repo.silver_region_counts(conn, region["code"])
    return {
        **empty, "current_zones_in_region": c["current_zones"], "our_picks_in_region": c["our_picks"],
        "hotspots_2024_in_region": c["hotspots"], "hotspots_covered_current": c["hotspots_covered_current"],
        "hotspots_covered_ours": c["hotspots_covered_ours"],
    }


def aed_section(conn: sqlite3.Connection, region: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """(section, note): the section is None outside Seoul or when the module is not loaded."""
    if not repo.aed_loaded(conn):
        return None, NOTE_NOT_LOADED
    if region["sido"] != SEOUL:
        return None, NOTE_AED_SEOUL_ONLY
    gu = repo.aed_gu_by_name(conn, region["name"])
    if gu is None:
        return None, NOTE_GU_MISSING
    events, factors = repo.aed_gu_events(conn)
    unc = poisson_rank(events, factors, gu["gu"])
    return {
        "night_open_share": gu["night_open_share"], "day_open_share": gu["day_open_share"],
        "gap_rank": repo.aed_gap_rank(conn, gu["gap_score"]), "gap_score": gu["gap_score"],
        "gap_rank_uncertainty": unc.as_dict(), "gap_rank_text": unc.text(),
        "candidates_top5": repo.aed_candidates(conn, gu["gu"]),
    }, None


def medical_section(svc: Any, region: Any, day: str) -> dict[str, Any] | None:
    """밤 의료 공백 (22:30 on `day`): open pharmacies/hospitals per 65+, ER beds; None when nothing is available."""
    if region is None:
        return None
    gap: dict[str, Any] = svc.night_gap(region, day)
    if gap.get("note") and not any(gap["fetched"].values()):
        return None
    return gap


def build_actions(
    region: dict[str, Any], heat: dict[str, Any], silver: dict[str, Any], aed: dict[str, Any] | None,
    medical: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Plain-Korean to-do lines, each with the number behind it and the inputs (`evidence`) that drove it.

    `action_id` is the stable key a reviewer's decision (POST /feedback) refers to.
    """
    name = region["name"]
    out: list[dict[str, Any]] = []
    rank = heat["rank"]
    ev = heat["evidence"]
    if ev.get("expected_cases_lambda") is not None:
        lam_text, lower = expected_cases_text(float(ev["expected_cases_lambda"]), ev.get("apparent_max_c"))
        ev["expected_cases_text"], ev["expected_cases_is_lower_bound"] = lam_text, lower
    rt = heat.get("rank_text") or {}
    rank_txt = ("이 날 평가 대상 아님" if rank is None
                else f"에이전트 순위: {rt.get('text')}" if rt.get("mode") in ("band", "insufficient") else f"에이전트 순위 {rt.get('text')}")
    if heat["alerted_ours"] and not heat["alerted_b1"]:
        out.append({"action_id": "heat.alert", "module": "heat",
                    "text": f"{topic(name)} 오늘 폭염 경보 대상입니다. 현행 기준은 빠뜨린 곳입니다.",
                    "reason": f"{rank_txt}. 65세 이상 {region['pop65']:,}명 반영.", "evidence": ev})
    elif heat["alerted_ours"] and heat["alerted_b1"]:
        out.append({"action_id": "heat.alert", "module": "heat", "text": f"{topic(name)} 현행 기준과 에이전트 모두 경보 대상입니다.",
                    "reason": rank_txt + ".", "evidence": ev})
    elif heat["alerted_b1"]:
        out.append({"action_id": "heat.alert", "module": "heat", "text": f"{topic(name)} 현행 기준만 경보 대상입니다.",
                    "reason": f"{rank_txt}. 같은 경보 수 안에서 다른 곳이 더 높습니다.", "evidence": ev})
    else:
        out.append({"action_id": "heat.alert", "module": "heat", "text": f"{topic(name)} 오늘 경보 대상이 아닙니다.",
                    "reason": rank_txt + ".", "evidence": ev})
    plan = heat["plan_allocation_for_region"]
    if plan["teams"]:
        out.append({"action_id": "heat.teams", "module": "heat",
                    "text": f"방문팀 {plan['teams']}팀 배치 (전체 {plan['teams_total']}팀 중).",
                    "reason": plan["reason"] or "예상 환자 수 기준.", "evidence": ev})
    shelters = heat["nearby_open_shelters"]["items"]
    if shelters:
        first = shelters[0]
        out.append({"action_id": "heat.shelters", "module": "heat", "text": f"야간·주말에 여는 쉼터 {len(shelters)}곳 안내.",
                    "reason": f"가장 가까운 곳: {first['name']} ({first['dist_km']}km).",
                    "evidence": {"nearest": first, "open_count": len(shelters)}})
    if silver["hotspots_2024_in_region"] is not None and (silver["our_picks_in_region"] or silver["hotspots_2024_in_region"]):
        h, a, b = silver["hotspots_2024_in_region"], silver["hotspots_covered_current"], silver["hotspots_covered_ours"]
        out.append({"action_id": "silver.check", "module": "silver",
                    "text": f"실버존 후보 {silver['our_picks_in_region']}곳을 현장 확인하세요.",
                    "reason": f"2024년 사고다발지 {h}곳 중 300m 안에 구역이 있는 곳: 현행 {a}곳 → 에이전트 {b}곳.",
                    "evidence": {"hotspots_2024": h, "covered_current": a, "covered_ours": b,
                                 "our_picks": silver["our_picks_in_region"]}})
    if aed is not None:
        unc = aed["gap_rank_uncertainty"]
        out.append({"action_id": "aed.gap", "module": "aed",
                    "text": f"밤 AED 열림 {_pct(aed['night_open_share'])}% (낮 {_pct(aed['day_open_share'])}%).",
                    "reason": f"밤 공백: {aed['gap_rank_text']}; 밤 공공장소 심정지 연 {unc['events_per_year']}건 추정.",
                    "evidence": {"night_open_share": aed["night_open_share"], "day_open_share": aed["day_open_share"],
                                 "gap_rank_uncertainty": unc, "events_per_year": unc["events_per_year"]}})
        if aed["candidates_top5"]:
            top = aed["candidates_top5"][0]
            out.append({"action_id": "aed.convert", "module": "aed", "text": f"24시간 전환 1순위: {top['org']}.",
                        "reason": f"{top['place']}. 평일 개방 {top['weekday_hours'] or '시간 정보 없음'}.",
                        "evidence": {"candidate": top}})
        else:
            out.append({"action_id": "aed.convert", "module": "aed", "text": "24시간 전환 후보 목록에 이 구는 없습니다.",
                        "reason": "구별 상위 5곳 목록은 일부 구만 담습니다.", "evidence": None})
    if medical is not None and medical.get("pharmacies_total"):
        n, per = medical["pharmacies_open_night"], medical.get("pharmacies_open_per_10k_pop65")
        stale = any((f or {}).get("stale") for f in medical["fetched"].values())
        beds = medical.get("er_beds_available_total")
        text = f"밤 22시 30분에 열린 약국 {n}곳" + (f" (65세 이상 1만 명당 {per}곳)" if per is not None else "") + "."
        reason = (f"약국 {medical['pharmacies_total']}곳 중 {n}곳 개방, 병의원 외래 {medical['hospitals_open_night']}곳 개방; "
                  f"응급의료기관 {medical['er_count']}곳" + (f", 응급실 가용 병상 {beds}" if beds is not None else "")
                  + (" · 스냅샷 자료" if stale else " · 실시간 자료") + ".")
        out.append({"action_id": "medical.gap", "module": "medical", "text": text, "reason": reason, "evidence": medical})
    return out
