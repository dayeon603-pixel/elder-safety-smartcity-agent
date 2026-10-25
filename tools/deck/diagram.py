"""Render Graphviz DOT with an ICASSP-paper style to PDF + PNG (300 dpi).

Usage:
    diagram.py graph.dot --out-dir /path/to/figs [--accent "#CFE3F2"]

Style comes from diagram_style.dot (serif STIX, AppleGothic fallback for
Hangul, 0.8 pt black strokes, white square boxes, left-to-right). Use
fillcolor="@ACCENT@" on the one box that is "our module". Graphs with more
than 7 nodes are rejected; more than 5 logs a warning.
"""
from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
from pathlib import Path

LOG = logging.getLogger("diagram")

STYLE_PATH = Path(__file__).with_name("diagram_style.dot")
DEFAULT_ACCENT = "#CFE3F2"
ACCENT_TOKEN = "@ACCENT@"
PNG_DPI = 300
WARN_NODES = 5
MAX_NODES = 7
DOT_TIMEOUT_S = 60
OPEN_BRACE_RE = re.compile(r"\{")


class DiagramError(RuntimeError):
    """Invalid graph or Graphviz failure."""


def inject_style(source: str, style: str, accent: str) -> str:
    """Insert style statements after the first '{' and substitute the accent token."""
    match = OPEN_BRACE_RE.search(source)
    if match is None:
        raise DiagramError("DOT source has no '{'")
    styled = source[: match.end()] + "\n" + style + "\n" + source[match.end() :]
    return styled.replace(ACCENT_TOKEN, accent)


def _dot(args: list[str], source: str) -> bytes:
    try:
        res = subprocess.run(
            ["dot", *args], input=source.encode("utf-8"), capture_output=True, check=True, timeout=DOT_TIMEOUT_S
        )
    except FileNotFoundError as exc:
        raise DiagramError("graphviz 'dot' not found on PATH (brew install graphviz)") from exc
    except subprocess.TimeoutExpired as exc:
        raise DiagramError(f"dot timed out after {DOT_TIMEOUT_S}s") from exc
    except subprocess.CalledProcessError as exc:
        raise DiagramError(f"dot failed: {exc.stderr.decode(errors='replace')[:500]}") from exc
    return res.stdout


def count_nodes(styled_source: str) -> int:
    """Count nodes via `dot -Tplain` (handles any DOT syntax)."""
    plain = _dot(["-Tplain"], styled_source).decode("utf-8", errors="replace")
    return sum(1 for line in plain.splitlines() if line.startswith("node "))


def render(dot_path: Path, out_dir: Path, accent: str = DEFAULT_ACCENT) -> list[Path]:
    """Render `dot_path` to PDF and PNG in `out_dir`; returns written paths."""
    styled = inject_style(dot_path.read_text(encoding="utf-8"), STYLE_PATH.read_text(encoding="utf-8"), accent)
    n = count_nodes(styled)
    if n > MAX_NODES:
        raise DiagramError(f"{n} nodes exceeds the maximum of {MAX_NODES}; simplify the diagram")
    if n > WARN_NODES:
        LOG.warning("%d nodes; the deck guide prefers %d or fewer", n, WARN_NODES)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for fmt, extra in (("pdf", []), ("png", [f"-Gdpi={PNG_DPI}"])):
        target = out_dir / f"{dot_path.stem}.{fmt}"
        target.write_bytes(_dot([f"-T{fmt}", *extra], styled))
        written.append(target)
    return written


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("dot", type=Path)
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--accent", default=DEFAULT_ACCENT, help="fill for @ACCENT@ boxes")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_parser().parse_args(argv)
    try:
        for path in render(args.dot, args.out_dir, args.accent):
            LOG.info("wrote %s", path)
        return 0
    except (DiagramError, OSError) as exc:
        LOG.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
