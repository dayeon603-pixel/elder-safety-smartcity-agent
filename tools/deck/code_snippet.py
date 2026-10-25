"""Render a short source excerpt as a slide-ready PNG (mentor: show only the core, not the whole file).

Usage:
    uv run --project ~/kcf/app/backend python ~/kcf/tools/deck/code_snippet.py \
        app/security/audit.py 40 58 --hl 45,52 --out ~/kcf/deck/figs/code_audit.png

The real file line numbers are kept so a judge can ask "show me line 45" and we open the same line.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from pygments import highlight
from pygments.formatters import ImageFormatter
from pygments.lexers import get_lexer_for_filename

logger = logging.getLogger("code_snippet")

MAX_LINES = 18  # more than this does not read on a slide
FONT_NAME = "Menlo"  # no Hangul glyphs: keep comments in snippets English or remove them
FONT_SIZE = 28
STYLE = "default"


def render(src: Path, start: int, end: int, hl: list[int], out: Path) -> Path:
    """Render lines [start, end] of src to a PNG with real line numbers.

    Args:
        src: Source file to excerpt.
        start: First line (1-indexed, inclusive).
        end: Last line (inclusive).
        hl: Absolute line numbers to highlight.
        out: Output PNG path.

    Returns:
        The written path.

    Raises:
        ValueError: If the range is invalid or longer than MAX_LINES.
    """
    lines = src.read_text(encoding="utf-8").splitlines()
    if not (1 <= start <= end <= len(lines)):
        raise ValueError(f"range {start}-{end} outside 1-{len(lines)}")
    if end - start + 1 > MAX_LINES:
        raise ValueError(f"{end - start + 1} lines > {MAX_LINES}: cut to the core")
    if any(ord(ch) > 0x3000 for ln in lines[start - 1 : end] for ch in ln):
        logger.warning("excerpt contains CJK characters; %s has no Hangul glyphs", FONT_NAME)
    code = "\n".join(lines[start - 1 : end]) + "\n"
    fmt = ImageFormatter(
        font_name=FONT_NAME,
        font_size=FONT_SIZE,
        style=STYLE,
        line_numbers=True,
        line_number_start=start,
        line_number_bg="#FFFFFF",
        line_number_fg="#9A9A9A",
        line_number_separator=False,
        line_pad=6,
        image_pad=24,
        hl_lines=[n - start + 1 for n in hl if start <= n <= end],
        hl_color="#E3EEF7",
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(highlight(code, get_lexer_for_filename(src.name), fmt))
    logger.info("wrote %s (%s:%d-%d)", out, src, start, end)
    return out


def main() -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("src", type=Path)
    p.add_argument("start", type=int)
    p.add_argument("end", type=int)
    p.add_argument("--hl", default="", help="comma-separated absolute line numbers to highlight")
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    hl = [int(x) for x in a.hl.split(",") if x.strip()]
    render(a.src, a.start, a.end, hl, a.out)


if __name__ == "__main__":
    main()
