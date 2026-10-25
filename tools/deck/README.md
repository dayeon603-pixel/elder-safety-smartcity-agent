# deck tools

Run with `uv run --project /Users/chloekang/kcf/app/backend python <tool> ...` (from any directory).

- `fetch_photo.py search "<query>" -n 8` / `get "File:Name.jpg" --out /Users/chloekang/kcf/deck/assets/`: Wikimedia Commons real jpeg photos only, CC0/PD/CC BY/CC BY-SA only; `get` downloads 1920px, appends to `CREDITS.md`, prints the Korean credit line.
- `script_timer.py script.md [--rate 290] [--max-total 6:00] [--strict]`: per-slide syllable/time table from `## [3 · 0:30] 제목` sections; exit 1 if over budget (`--strict` also fails on sentences over 40 chars).
- `pptx_fill.py inspect template.pptx` then `pptx_fill.py build template.pptx spec.json out.pptx [--no-render]`: fill layouts, contain-fit images, 9pt gray credit, notes, then PDF + 60 dpi PNG previews (see `examples/sample_spec.json`; `default-template out.pptx` makes a test template).
- `diagram.py graph.dot --out-dir /Users/chloekang/kcf/deck/figs`: ICASSP-style PDF + 300 dpi PNG from DOT (style in `diagram_style.dot`, mark your module with `fillcolor="@ACCENT@"`, max 7 nodes; see `examples/example_arch.dot`).

Tests: `uv run --project /Users/chloekang/kcf/app/backend python -m pytest /Users/chloekang/kcf/tools/deck/tests -q`
- `code_snippet.py <file> <start> <end> --hl 80,81 --out figs/x.png` — 핵심 코드 ≤18줄을 실제 줄번호 그대로 PNG로. 강조줄 = 우리 알고리즘의 핵심 한두 줄. 주석은 영어(Menlo엔 한글 없음).
