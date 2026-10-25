"""CBS draft: length guard under both limits, honest wording, easy-text sentence length."""

from __future__ import annotations

import pytest

from app.actions import cbs
from app.actions import constants as C
from app.actions.models import ShelterRef


def shelter(name: str, night: bool | None = True, weekend: bool | None = True, km: float = 0.5) -> ShelterRef:
    return ShelterRef(name=name, region_code="11110", distance_km=km, night_open=night, weekend_open=weekend)


def inp(**kw: object) -> cbs.CbsInput:
    base: dict[str, object] = dict(
        region_name="관악구", month=7, day=22, tmax_c=34.2, rank=3, n_cells=250,
        shelters=(shelter("가나다경로당"), shelter("라마바주민센터", False, True), shelter("사아자복지관")),
        hot=True, show_rank=True,
    )
    base.update(kw)
    return cbs.CbsInput(**base)  # type: ignore[arg-type]


def test_limits_are_the_documented_ones() -> None:
    assert C.CBS_LIMIT_CURRENT == 157 and C.CBS_LIMIT_LEGACY == 90
    assert "157" in C.CBS_LIMIT_SOURCE and "2026-06-30" in C.CBS_LIMIT_SOURCE


@pytest.mark.parametrize("limit", [90, 157])
def test_texts_never_exceed_limit(limit: int) -> None:
    d = cbs.build_cbs(inp(), limit)
    assert d.length == len(d.text) <= limit
    assert d.easy_length == len(d.easy_text) <= limit
    assert d.limit == limit


@pytest.mark.parametrize("limit", [90, 157])
def test_many_long_shelters_are_dropped_not_cut(limit: int) -> None:
    long = tuple(shelter("매우긴이름의무더위쉼터" * 3 + str(i)) for i in range(5))
    d = cbs.build_cbs(inp(shelters=long), limit)
    assert len(d.text) <= limit
    assert d.text.endswith(".") and "119" in d.text
    assert all(part in d.text for part in ("[관악구]", "7/22")) and "…" not in d.text
    # a shelter that does not fit whole is left out; any name present is complete (3 repeats + index)
    assert d.text.count("매우긴이름의무더위쉼터") % 3 == 0


def test_shelters_added_while_they_fit() -> None:
    d157 = cbs.build_cbs(inp(), 157)
    d90 = cbs.build_cbs(inp(shelters=(shelter("가나다경로당"),) * 3), 90)
    assert "가나다경로당(야간·주말 운영)" in d157.text
    assert len(d90.text) <= 90


def test_mandatory_part_too_long_raises() -> None:
    with pytest.raises(cbs.MessageTooLongError):
        cbs.build_cbs(inp(region_name="가" * 200), 157)


def test_invalid_limit_rejected() -> None:
    with pytest.raises(ValueError):
        cbs.build_cbs(inp(), 100)


def test_check_length_boundary() -> None:
    cbs.check_length("가" * 157, 157)
    with pytest.raises(cbs.MessageTooLongError):
        cbs.check_length("가" * 158, 157)


def test_no_shelter_text_has_no_shelter_clause_and_keeps_119() -> None:
    d = cbs.build_cbs(inp(shelters=()), 157, shelter_note="자료 없음")
    assert "쉼터" not in d.text and "119" in d.text and d.shelter_note == "자료 없음"
    assert "시원한 곳" not in d.easy_text


def test_not_hot_does_not_say_폭염() -> None:
    d = cbs.build_cbs(inp(hot=False, tmax_c=30.4), 157)
    assert "폭염" not in d.text and "더위 주의" in d.text and "아주" not in d.easy_text


def test_rank_shown_only_when_requested_and_missing_temperature_tolerated() -> None:
    assert "3위" in cbs.build_cbs(inp(), 157).text
    assert "250곳 중" not in cbs.build_cbs(inp(show_rank=False), 157).text
    d = cbs.build_cbs(inp(tmax_c=None), 157)
    assert "℃" not in d.text and "도입니다" not in d.easy_text


def test_easy_text_has_short_sentences_and_no_symbols() -> None:
    d = cbs.build_cbs(inp(), 157)
    parts = cbs.sentences(d.easy_text)
    assert len(parts) >= 5
    assert all(len(p) <= C.EASY_MAX_SENTENCE_CHARS for p in parts), parts
    assert "℃" not in d.easy_text and "~" not in d.easy_text and "65세" not in d.easy_text


def test_easy_shelter_sentence_skipped_when_too_long() -> None:
    d = cbs.build_cbs(inp(shelters=(shelter("아" * 40),)), 157)
    assert "아" * 40 not in d.easy_text


def test_open_label() -> None:
    assert cbs.open_label(shelter("a", True, True)) == "야간·주말 운영"
    assert cbs.open_label(shelter("a", True, False)) == "야간 운영"
    assert cbs.open_label(shelter("a", None, None)) == ""
    assert cbs.open_label(shelter("a", False, False)) == ""
