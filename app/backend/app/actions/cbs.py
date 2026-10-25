"""재난문자(CBS) draft for one 시군구: a standard text and an 쉬운 말 text, both under the length limit.

Limit: 90자형 standard text by default, 157자형 for the heat-type alert texts (재난문자방송 기준 및 운영규정 별표 2).
Length is `len(text)` in characters with spaces counted, which is the conservative reading of
"한글 기준 글자 수". The text is composed from prioritised parts; optional parts (shelters) are added
only while the result still fits, so the guard can never be met by cutting a sentence in half.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from . import constants as C
from .models import CbsDraft, ShelterRef

logger = logging.getLogger("actions.cbs")


class MessageTooLongError(ValueError):
    """Even the mandatory parts of the message exceed the limit."""


@dataclass(frozen=True, slots=True)
class CbsInput:
    """Real values the draft is filled with."""

    region_name: str
    month: int
    day: int
    tmax_c: float | None
    rank: int | None
    n_cells: int
    shelters: tuple[ShelterRef, ...]
    hot: bool = True  # alerted or 체감 최고 (Tmax when unavailable) >= threshold; else '더위' not '폭염'
    show_rank: bool = True
    apparent_max_c: float | None = None  # KMA 여름철 체감온도 daily max; preferred over tmax_c in the text
    rank_text: str | None = None  # gated phrase from heat.ranktext; when set it replaces "N곳 중 M위"


def check_length(text: str, limit: int) -> None:
    """Raise MessageTooLongError unless len(text) <= limit."""
    if len(text) > limit:
        raise MessageTooLongError(f"{len(text)} chars > limit {limit}")


def limit_source(limit: int) -> str:
    """Citation for the limit in force."""
    return C.CBS_LIMIT_SOURCE if limit == C.CBS_LIMIT_CURRENT else C.CBS_LIMIT_SOURCE_LEGACY


def open_label(s: ShelterRef) -> str:
    """'야간·주말 운영' from the known flags only; unknown or closed flags add nothing."""
    parts = [name for flag, name in ((s.night_open, "야간"), (s.weekend_open, "주말")) if flag]
    return "·".join(parts) + " 운영" if parts else ""


def _shelter_phrase(s: ShelterRef) -> str:
    label = open_label(s)
    return f"{s.name}({label})" if label else s.name


def _temp_text(tmax: float | None, apparent: float | None = None) -> str:
    """'체감 최고 N℃ ' when the apparent maximum is known (the 폭염특보 quantity), else '최고 N℃ '."""
    if apparent is not None:
        return f"체감 최고 {apparent:.0f}℃ "
    return f"최고 {tmax:.0f}℃ " if tmax is not None else ""


def build_standard(inp: CbsInput, limit: int) -> str:
    """The standard-register text; falls back to a shorter guidance sentence if needed."""
    word = "폭염" if inp.hot else "더위 주의"
    head_full = f"[{inp.region_name}] {inp.month}/{inp.day} {_temp_text(inp.tmax_c, inp.apparent_max_c)}{word}."
    if inp.rank is not None and inp.show_rank:
        head_full += f" 온열질환 위험 {inp.rank_text or f'{inp.n_cells}곳 중 {inp.rank}위'}."
    head_short = f"[{inp.region_name}] {inp.month}/{inp.day} {_temp_text(inp.tmax_c, inp.apparent_max_c)}{word}."
    guides = (
        f"65세 이상은 {C.GUIDE_HOURS} 외출을 피하고 물을 자주 드세요.",
        f"65세 이상 {C.GUIDE_HOURS} 외출 자제.",
    )
    call = f"어지러우면 즉시 {C.EMERGENCY_NUMBER}."
    shelters = [f"무더위쉼터: {', '.join(_shelter_phrase(s) for s in inp.shelters[:k])}."
                for k in range(1, min(len(inp.shelters), C.SHELTERS_IN_TEXT) + 1)]
    for head in (head_full, head_short):
        for guide in guides:
            required = [head, guide, call]
            if len(" ".join(required)) <= limit:
                text = " ".join(required)
                for cand in reversed(shelters):  # most shelters that still fit
                    if len(f"{text} {cand}") <= limit:
                        text = f"{text} {cand}"
                        break
                check_length(text, limit)
                return text
    raise MessageTooLongError(f"mandatory parts exceed limit {limit} for {inp.region_name!r}")


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def sentences(text: str) -> list[str]:
    """Split on sentence-final punctuation."""
    return [s for s in _SENTENCE_END.split(text.strip()) if s]


def build_easy(inp: CbsInput, limit: int) -> str:
    """쉬운 말 version for older readers: one fact per sentence, no symbols, no jargon."""
    if inp.apparent_max_c is not None:
        temp = f"체감 온도가 {inp.apparent_max_c:.0f}도까지 오릅니다."
    else:
        temp = f"낮 기온이 {inp.tmax_c:.0f}도입니다." if inp.tmax_c is not None else ""
    base = [f"[{inp.region_name}] 오늘은 {'아주 ' if inp.hot else ''}덥습니다."]
    tail = ["낮 12시부터 오후 5시까지는 밖에 나가지 마세요.", "물을 자주 드세요."]
    call = f"어지러우면 {C.EMERGENCY_NUMBER}에 전화하세요."
    parts = [*base, temp, *tail] if temp else [*base, *tail]
    if len(" ".join([*parts, call])) > limit:  # tight limit: the temperature sentence goes first
        parts = [*base, *tail]
    required = [*parts, call]
    check_length(" ".join(required), limit)
    shelter_sentences = [
        f"가까운 시원한 곳은 {s.name}입니다." for s in inp.shelters[: C.SHELTERS_IN_TEXT]
        if len(f"가까운 시원한 곳은 {s.name}입니다.") <= C.EASY_MAX_SENTENCE_CHARS
    ]
    for cand in shelter_sentences:  # nearest first; keep the first that fits
        trial = " ".join([*parts, cand, call])
        if len(trial) <= limit:
            return trial
    return " ".join(required)


def build_cbs(inp: CbsInput, limit: int = C.CBS_LIMIT_CURRENT, shelter_note: str | None = None) -> CbsDraft:
    """Both texts plus the length facts.

    Raises:
        ValueError: if limit is not one of the known CBS limits.
        MessageTooLongError: if the mandatory parts do not fit.
    """
    if limit not in C.CBS_LIMITS:
        raise ValueError(f"limit must be one of {C.CBS_LIMITS}")
    text = build_standard(inp, limit)
    easy = build_easy(inp, limit)
    return CbsDraft(
        text=text, easy_text=easy, length=len(text), easy_length=len(easy), limit=limit,
        limit_source=limit_source(limit), shelters=list(inp.shelters), shelter_note=shelter_note,
    )
