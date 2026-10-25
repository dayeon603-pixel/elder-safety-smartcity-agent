from pathlib import Path

import pytest

import script_timer as st

SCRIPT = """# 대본
## [1 · 0:10] 제목
영점은 화재 신고를 분류합니다.
## [2 · 0:40] 문제
출동이 83% 늘었습니다. SHA256 해시를 씁니다.
총 음절 수 99
"""


def test_count_syllables_components() -> None:
    assert st.count_syllables("가나다") == 3
    assert st.count_syllables("API") == 2
    assert st.count_syllables("83") == 3  # 팔십삼
    assert st.count_syllables("2026") == 6  # 이천이십육
    assert st.count_syllables("0") == 1
    assert st.count_syllables("50%") == 2 + 1 + 3


def test_parse_and_analyse() -> None:
    secs = st.parse_script(SCRIPT)
    assert [(s.slide, s.target_s) for s in secs] == [(1, 10), (2, 40)]
    reps = st.analyse(secs, 290, 40)
    assert reps[0].syllables == 13  # 영점은화재신고를분류합니다
    assert "총" not in " ".join(secs[1].lines)
    assert reps[1].estimated_s == pytest.approx(reps[1].syllables / 290 * 60)


def test_long_sentence_flag() -> None:
    long = "가" * 41 + "."
    reps = st.analyse(st.parse_script(f"## [1 · 0:10] t\n{long}\n"), 290, 40)
    assert reps[0].longest_sentence == 41 and len(reps[0].long_sentences) == 1


def test_no_headers() -> None:
    with pytest.raises(ValueError):
        st.parse_script("just text")


def test_bad_rate() -> None:
    with pytest.raises(ValueError):
        st.analyse(st.parse_script(SCRIPT), 0, 40)


def test_fmt_and_parse_time() -> None:
    assert st.fmt_time(65) == "1:05" and st.fmt_time(-7) == "-0:07"
    assert st.parse_mmss("6:00") == 360 and st.parse_mmss("90") == 90


def test_exit_codes(tmp_path: Path) -> None:
    ok = tmp_path / "ok.md"
    ok.write_text(SCRIPT, encoding="utf-8")
    assert st.main([str(ok)]) == 0
    assert st.main([str(ok), "--max-total", "0:05"]) == 1
    assert st.main([str(tmp_path / "missing.md")]) == 2
    long = tmp_path / "long.md"
    long.write_text("## [1 · 0:10] t\n" + "가" * 50 + ".\n", encoding="utf-8")
    assert st.main([str(long)]) == 0
    assert st.main([str(long), "--strict"]) == 1


def test_over_six_minutes(tmp_path: Path) -> None:
    f = tmp_path / "big.md"
    f.write_text("## [1 · 6:00] t\n" + ("가나다라마바사. " * 300) + "\n", encoding="utf-8")
    assert st.main([str(f)]) == 1
