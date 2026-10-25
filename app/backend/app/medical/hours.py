"""요일별 운영시간 (dutyTime{1..8}{s,c}) -> is it open at a given moment, incl. 야간·휴일.

Day index: 1 = Monday ... 7 = Sunday, 8 = 공휴일 (the API's convention). A closing time at or before the
opening time means the place runs past midnight ("0900"–"0200"); "2400" closes at the end of the day.
Holidays: fixed national holidays plus an explicit table of lunar-calendar holidays and 대체공휴일 for
2024–2026 (listed below; verify against the official 관공서의 공휴일 notice before relying on another year).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from . import constants as C

logger = logging.getLogger(__name__)

MINUTES_PER_DAY = 1440
FIXED_HOLIDAYS: frozenset[tuple[int, int]] = frozenset({
    (1, 1), (3, 1), (5, 5), (6, 6), (8, 15), (10, 3), (10, 9), (12, 25),
})
# Lunar-calendar holidays and substitute holidays (대체공휴일); explicit per year. Verify each new year.
MOVABLE_HOLIDAYS: frozenset[date] = frozenset({
    # 2024
    date(2024, 2, 9), date(2024, 2, 10), date(2024, 2, 11), date(2024, 2, 12), date(2024, 4, 10), date(2024, 5, 6),
    date(2024, 5, 15), date(2024, 9, 16), date(2024, 9, 17), date(2024, 9, 18), date(2024, 10, 1),
    # 2025
    date(2025, 1, 27), date(2025, 1, 28), date(2025, 1, 29), date(2025, 1, 30), date(2025, 3, 3), date(2025, 5, 6),
    date(2025, 6, 3), date(2025, 10, 6), date(2025, 10, 7), date(2025, 10, 8),
    # 2026
    date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18), date(2026, 3, 2), date(2026, 5, 24), date(2026, 5, 25),
    date(2026, 6, 3), date(2026, 8, 17), date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 26), date(2026, 10, 5),
})


HOLIDAY_TABLE_END_YEAR = max(d.year for d in MOVABLE_HOLIDAYS)
HOLIDAY_TABLE_START_YEAR = min(d.year for d in MOVABLE_HOLIDAYS)
_warned_years: set[int] = set()


def holiday_table_covers(d: date) -> bool:
    """False for years outside the explicit 설/추석/대체공휴일 table (fixed-date holidays still apply)."""
    return HOLIDAY_TABLE_START_YEAR <= d.year <= HOLIDAY_TABLE_END_YEAR


def is_holiday(d: date) -> bool:
    if not holiday_table_covers(d) and d.year not in _warned_years:
        _warned_years.add(d.year)
        logger.warning("holiday table covers %d-%d; %d has fixed-date holidays only (update medical/hours.py)",
                       HOLIDAY_TABLE_START_YEAR, HOLIDAY_TABLE_END_YEAR, d.year)
    return (d.month, d.day) in FIXED_HOLIDAYS or d in MOVABLE_HOLIDAYS


def day_index(d: date) -> int:
    """1..7 = Mon..Sun, 8 = 공휴일 (a holiday wins over the weekday)."""
    return C.HOLIDAY_INDEX if is_holiday(d) else d.weekday() + 1


def parse_hhmm(raw: str | None) -> int | None:
    """'0930' -> 570 minutes; '2400' -> 1440; anything else -> None."""
    if raw is None:
        return None
    s = raw.strip()
    if len(s) != 4 or not s.isdigit():
        return None
    h, m = int(s[:2]), int(s[2:])
    if h == 24 and m == 0:
        return MINUTES_PER_DAY
    if h > 23 or m > 59:
        return None
    return h * 60 + m


def hours_for(item: dict[str, Any], idx: int) -> tuple[int, int] | None:
    """(open, close) minutes for day index `idx`, or None when that day has no hours."""
    s, c = parse_hhmm(item.get(f"dutyTime{idx}s")), parse_hhmm(item.get(f"dutyTime{idx}c"))
    if s is None or c is None:
        return None
    return s, c


def has_any_hours(item: dict[str, Any]) -> bool:
    return any(hours_for(item, i) is not None for i in range(1, C.HOLIDAY_INDEX + 1))


def _covers(span: tuple[int, int], minute: int, *, spill: bool) -> bool:
    """`spill`: evaluate the previous day's span for the part that runs past midnight."""
    s, c = span
    if c <= s:  # runs past midnight
        return minute < c if spill else minute >= s
    return False if spill else s <= minute < c


def is_open(item: dict[str, Any], at: datetime) -> bool | None:
    """True/False, or None when the record has no usable hours at all.

    A holiday uses dutyTime8*; when a place lists no holiday hours it is closed that day (the API's
    convention: a missing day = 휴무). The previous day's past-midnight hours also count.
    """
    if not has_any_hours(item):
        return None
    minute = at.hour * 60 + at.minute
    today = hours_for(item, day_index(at.date()))
    if today is not None and _covers(today, minute, spill=False):
        return True
    yesterday = hours_for(item, day_index(at.date() - timedelta(days=1)))
    return yesterday is not None and _covers(yesterday, minute, spill=True)


def split_open(items: list[dict[str, Any]], at: datetime) -> tuple[list[dict[str, Any]], int, int]:
    """(open items, closed count, unknown count) at `at`."""
    open_items: list[dict[str, Any]] = []
    closed = unknown = 0
    for it in items:
        state = is_open(it, at)
        if state is None:
            unknown += 1
        elif state:
            open_items.append(it)
        else:
            closed += 1
    return open_items, closed, unknown


def is_night(at: datetime) -> bool:
    return at.hour >= C.NIGHT_START_H or at.hour < C.NIGHT_END_H


__all__ = ["FIXED_HOLIDAYS", "HOLIDAY_TABLE_END_YEAR", "MOVABLE_HOLIDAYS", "day_index", "holiday_table_covers", "has_any_hours", "hours_for", "is_holiday", "is_night",
           "is_open", "parse_hhmm", "split_open"]
