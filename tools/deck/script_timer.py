"""Estimate speaking time of a markdown presentation script.

Slide sections start with ``## [3 · 0:30] 제목`` (slide number, target m:ss).
Syllables = Hangul syllables + 2 per Latin word + approximate spoken digits.

Usage:
    script_timer.py script.md [--rate 290] [--max-total 6:00] [--max-sentence 40] [--strict]
Exit code 1 if the estimated total exceeds the budget (or, with --strict,
if any sentence is over the length limit).
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

LOG = logging.getLogger("script_timer")

DEFAULT_RATE_SPM = 290.0
DEFAULT_MAX_TOTAL_S = 360
DEFAULT_MAX_SENTENCE = 40
LATIN_WORD_SYLLABLES = 2
PERCENT_SYLLABLES = 3  # 퍼센트
SECONDS_PER_MINUTE = 60

HEADER_RE = re.compile(r"^##\s*\[\s*(\d+)\s*[·•,|\-]\s*(\d+):(\d{2})\s*\]\s*(.*?)\s*$")
HANGUL_RE = re.compile(r"[가-힣]")
LATIN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9'\-]*")
DIGITS_RE = re.compile(r"\d+")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+|\n+")
SKIP_LINE_RE = re.compile(r"^\s*(#|>|\||```|<!--|---\s*$|(\*\*)?총\s*음절)")
MD_NOISE_RE = re.compile(r"[*_`]+|\[[^\]]*\]\([^)]*\)")


@dataclass
class Section:
    """One slide section of the script."""

    slide: int
    target_s: int
    title: str
    lines: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SectionReport:
    slide: int
    title: str
    target_s: int
    syllables: int
    estimated_s: float
    longest_sentence: int
    long_sentences: tuple[str, ...]


def spoken_digit_syllables(digits: str) -> int:
    """Approximate Korean spoken length of a digit run (e.g. 83 -> 팔십삼 = 3)."""
    n = len(digits)
    if n > 1 and digits.startswith("0"):
        return n  # read digit by digit
    places = sum(1 for i, d in enumerate(digits) if d != "0" and (n - 1 - i) in (1, 2, 3))
    return n + places


def count_syllables(text: str) -> int:
    """Count spoken syllables: Hangul + Latin words (x2) + digits + percent signs."""
    clean = MD_NOISE_RE.sub(" ", text)
    total = len(HANGUL_RE.findall(clean))
    total += LATIN_WORD_SYLLABLES * len(LATIN_WORD_RE.findall(clean))
    # digits inside Latin tokens (e.g. SHA256) are read too; count all digit runs
    total += sum(spoken_digit_syllables(m) for m in DIGITS_RE.findall(clean))
    total += PERCENT_SYLLABLES * clean.count("%")
    return total


def split_sentences(text: str) -> list[str]:
    """Split on sentence terminators and newlines; drop empties."""
    return [s.strip() for s in SENTENCE_SPLIT_RE.split(text) if s and s.strip()]


def sentence_length(sentence: str) -> int:
    """Length in characters (spaces included, terminal punctuation excluded)."""
    return len(MD_NOISE_RE.sub("", sentence).strip().rstrip(".!?。！？"))


def parse_script(text: str) -> list[Section]:
    """Parse markdown into slide sections. Raises ValueError if none found."""
    sections: list[Section] = []
    current: Section | None = None
    for raw in text.splitlines():
        m = HEADER_RE.match(raw)
        if m:
            current = Section(int(m[1]), int(m[2]) * SECONDS_PER_MINUTE + int(m[3]), m[4])
            sections.append(current)
            continue
        if current is None or SKIP_LINE_RE.match(raw):
            if raw.startswith("## ") and current is not None:
                current = None  # unrelated H2 ends the slide section
            continue
        if raw.strip():
            current.lines.append(raw.strip())
    if not sections:
        raise ValueError("no '## [N · m:ss] title' headers found")
    return sections


def analyse(sections: list[Section], rate_spm: float, max_sentence: int) -> list[SectionReport]:
    """Compute per-section reports."""
    if rate_spm <= 0:
        raise ValueError("rate must be positive")
    reports: list[SectionReport] = []
    for sec in sections:
        body = "\n".join(sec.lines)
        sentences = split_sentences(body)
        lengths = [sentence_length(s) for s in sentences]
        syl = count_syllables(body)
        reports.append(
            SectionReport(
                slide=sec.slide,
                title=sec.title,
                target_s=sec.target_s,
                syllables=syl,
                estimated_s=syl / rate_spm * SECONDS_PER_MINUTE,
                longest_sentence=max(lengths, default=0),
                long_sentences=tuple(s for s, n in zip(sentences, lengths) if n > max_sentence),
            )
        )
    return reports


def fmt_time(seconds: float) -> str:
    """Format seconds as m:ss (signed for deltas)."""
    sign = "-" if seconds < 0 else ""
    s = int(round(abs(seconds)))
    return f"{sign}{s // 60}:{s % 60:02d}"


def parse_mmss(value: str) -> int:
    """Parse 'm:ss' or plain seconds."""
    if ":" in value:
        m, s = value.split(":", 1)
        return int(m) * SECONDS_PER_MINUTE + int(s)
    return int(value)


def render_table(reports: list[SectionReport]) -> list[str]:
    """Render the report table as text lines."""
    lines = [f"{'slide':>5} {'target':>7} {'est':>7} {'delta':>7} {'syl':>5} {'longest':>7}  title"]
    for r in reports:
        lines.append(
            f"{r.slide:>5} {fmt_time(r.target_s):>7} {fmt_time(r.estimated_s):>7} "
            f"{fmt_time(r.estimated_s - r.target_s):>7} {r.syllables:>5} {r.longest_sentence:>7}  {r.title}"
        )
    tt = sum(r.target_s for r in reports)
    te = sum(r.estimated_s for r in reports)
    ts = sum(r.syllables for r in reports)
    lines.append(
        f"{'total':>5} {fmt_time(tt):>7} {fmt_time(te):>7} {fmt_time(te - tt):>7} {ts:>5} "
        f"{max((r.longest_sentence for r in reports), default=0):>7}"
    )
    return lines


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("script", type=Path)
    p.add_argument("--rate", type=float, default=DEFAULT_RATE_SPM, help="syllables per minute")
    p.add_argument("--max-total", default=fmt_time(DEFAULT_MAX_TOTAL_S), help="budget m:ss")
    p.add_argument("--max-sentence", type=int, default=DEFAULT_MAX_SENTENCE, help="chars")
    p.add_argument("--strict", action="store_true", help="also fail on over-long sentences")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_parser().parse_args(argv)
    try:
        sections = parse_script(args.script.read_text(encoding="utf-8"))
        reports = analyse(sections, args.rate, args.max_sentence)
        budget_s = parse_mmss(args.max_total)
    except (OSError, ValueError) as exc:
        LOG.error("%s", exc)
        return 2
    for line in render_table(reports):
        LOG.info("%s", line)
    flagged = [(r.slide, s) for r in reports for s in r.long_sentences]
    for slide, sent in flagged:
        LOG.warning("slide %d: sentence over %d chars (%d): %s", slide, args.max_sentence, sentence_length(sent), sent)
    total = sum(r.estimated_s for r in reports)
    over = total > budget_s
    if over:
        LOG.error("OVER BUDGET: %s > %s", fmt_time(total), fmt_time(budget_s))
    else:
        LOG.info("within budget: %s <= %s", fmt_time(total), fmt_time(budget_s))
    return 1 if over or (args.strict and flagged) else 0


if __name__ == "__main__":
    sys.exit(main())
