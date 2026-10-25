from pathlib import Path

import pytest

import diagram as dg

STYLE = dg.STYLE_PATH.read_text(encoding="utf-8")


def test_inject_style_and_accent() -> None:
    out = dg.inject_style('digraph g { a [fillcolor="@ACCENT@"]; }', "rankdir=LR;", "#123456")
    assert out.index("rankdir=LR;") < out.index("a [")
    assert "#123456" in out and "@ACCENT@" not in out


def test_inject_requires_brace() -> None:
    with pytest.raises(dg.DiagramError):
        dg.inject_style("nothing", "x", "y")


def test_style_matches_spec() -> None:
    for token in ("rankdir=LR", "penwidth=0.8", "STIX", "AppleGothic", "white"):
        assert token in STYLE
    assert "rounded" not in STYLE


def test_render_example(tmp_path: Path) -> None:
    dot = tmp_path / "g.dot"
    dot.write_text("digraph g { a -> b -> c; }", encoding="utf-8")
    pdf, png = dg.render(dot, tmp_path / "out")
    assert pdf.read_bytes().startswith(b"%PDF") and png.read_bytes().startswith(b"\x89PNG")


def test_too_many_nodes(tmp_path: Path) -> None:
    dot = tmp_path / "big.dot"
    dot.write_text("digraph g { a->b->c->d->e->f->g->h; }", encoding="utf-8")
    with pytest.raises(dg.DiagramError):
        dg.render(dot, tmp_path / "out")


def test_bad_dot(tmp_path: Path) -> None:
    dot = tmp_path / "bad.dot"
    dot.write_text("digraph g { a -> ; ", encoding="utf-8")
    assert dg.main([str(dot), "--out-dir", str(tmp_path / "o")]) == 1
