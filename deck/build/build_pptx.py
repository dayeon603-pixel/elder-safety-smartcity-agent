"""Build deck/build/wireframe.pptx from deck/build/spec.json on the OFFICIAL template.

Template slides kept: 0 cover, 1 목차, 3 감사합니다 (graphics untouched). Body slides are the '1_비교' layout
(= duplicate of template slide 2). Order: cover, 목차, body..., 감사합니다, appendix.

Spec schema (extends tools/deck/pptx_fill.py): see build_all.py. Rendering uses pptx_fill.render_previews
(Hangul-capable preview font; the delivered .pptx is untouched).
"""
from __future__ import annotations

import copy
import json
import logging
import re
import sys
import zipfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageFont
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

sys.path.insert(0, "/Users/chloekang/kcf/tools/deck")
import pptx_fill as PF  # noqa: E402

LOG = logging.getLogger("build_pptx")
TEMPLATE = Path("/Users/chloekang/kcf/deck/official/template.pptx")
BODY_LAYOUT = "1_비교"
FONT = "Apple SD Gothic Neo"  # installed on every Mac; no other family anywhere (runs, tables, theme, layouts)
FONT_HEAD = FONT
FONT_BODY = FONT
FONT_FILE = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
FACE_INDEX = {False: 0, True: 6}  # ttc faces: 0 Regular, 6 Bold
LINE_H = 1.3  # line height / font size used by the overflow guard (Apple SD Gothic Neo is tall)
NBSP = "\u00a0"
OLD_FONTS = re.compile(r'typeface="(?:HY헤드라인M|프리젠테이션[^"]*|Freesentation[^"]*|Pretendard[^"]*|맑은 고딕|Malgun Gothic)"')
MIN_PT = {"Hero": 60, "Source": 12, "Credit": 12, "Caption": 12, "Tags": 14, "Criterion": 14, "Keyword": 19, "CardLines": 16, "BigNumSub": 16, "PhotoLabel": 17, "Text": 18}
NAVY = RGBColor(0x02, 0x2E, 0x5F)
INK = RGBColor(0x1A, 0x1A, 0x1A)
BLUE = RGBColor(0x00, 0x72, 0xB2)
GRAY = RGBColor(0x33, 0x33, 0x33)  # dark enough for a projector; no light gray text
LIGHT = RGBColor(0xEA, 0xF1, 0xFB)
BOXGRAY = RGBColor(0xE6, 0xE6, 0xE6)
MIDGRAY = RGBColor(0x6B, 0x6B, 0x6B)  # only for large baseline numbers (hero size), never for small text
TEXT_COLORS = {"navy": NAVY, "blue": BLUE, "gray": MIDGRAY, "ink": INK}
SLIDE_W_IN = 13.333
MARGIN_L = 0.5  # one left margin for tags, headline, keywords and left-aligned body text
MARGIN_R = 0.4
DPI_PREVIEW = 110


def set_font(run: Any, size: float, bold: bool = False, color: RGBColor = INK, face: str = FONT_BODY) -> None:
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = face
    rpr = run._r.get_or_add_rPr()
    ea = rpr.find(qn("a:ea"))
    if ea is None:
        ea = rpr.makeelement(qn("a:ea"), {})
        rpr.append(ea)
    ea.set("typeface", face)
    cs = rpr.find(qn("a:cs"))
    if cs is None:
        cs = rpr.makeelement(qn("a:cs"), {})
        rpr.append(cs)
    cs.set("typeface", face)
    rpr.attrib.pop("spc", None)  # character spacing 0


# ----------------------------------------------------------------------------- overflow guard (real font metrics)
GUARD: list[dict[str, Any]] = []
_FONTS: dict[tuple[bool, int], Any] = {}
_SLIDE = {"title": ""}


def _font(bold: bool, size: float) -> Any:
    key = (bold, int(round(size * 10)))
    if key not in _FONTS:
        _FONTS[key] = ImageFont.truetype(FONT_FILE, size=size * 10, index=FACE_INDEX[bold])  # 10 px per pt, scale back below
    return _FONTS[key]


def text_w_in(text: str, size: float, bold: bool) -> float:
    return _font(bold, size).getlength(text) / 10 / 72


def nb(text: str) -> str:
    """Keep numbers with their units, protocol names with versions, and 'a → b' pairs together (non-breaking spaces)."""
    t = re.sub(r"(\d)\s+(%p|%|m/s|ms|MiB|초|곳|명|건|km|시간|자형|자|년|동|대|개|분|시)", lambda m: m.group(1) + NBSP + m.group(2), text)
    t = re.sub(r"\b(CAP|FHIR|RSU|BIT|HMAC|Argon2id|SHA-256)\s+(\d)", lambda m: m.group(1) + NBSP + m.group(2), t)
    t = re.sub(r"(\d[\d.,%a-zA-Z가-힣]*)\s→\s(\d)", lambda m: m.group(1) + NBSP + "→" + NBSP + m.group(2), t)
    t = re.sub(r"(\S)\s·\s(?=\d)", lambda m: m.group(1) + NBSP + "·" + NBSP, t)
    return t


def wrap(text: str, size: float, bold: bool, width_in: float) -> tuple[int, bool]:
    """Greedy word wrap at spaces (NBSP never breaks). Returns (line count, True if an unbreakable chunk is wider than the box)."""
    n, bad = 0, False
    for para in text.split("\n"):
        cur = ""
        n += 1
        for tok in para.split(" "):
            if text_w_in(tok, size, bold) > width_in:
                bad = True
            trial = tok if not cur else cur + " " + tok
            if cur and text_w_in(trial, size, bold) > width_in:
                n += 1
                cur = tok
            else:
                cur = trial
    return n, bad


def guard_box(kind: str, lines: list[str], size: float, bold: bool, w: float, h: float, mx: float, my: float) -> None:
    need_h, n_tot, bad = 2 * my, 0, False
    for ln in lines:
        n, b = wrap(ln, size, bold, w - 2 * mx)
        n_tot += n
        bad = bad or b
    need_h += n_tot * size * LINE_H / 72
    GUARD.append({"slide": _SLIDE["title"], "kind": kind, "text": (lines[0] if lines else "")[:22], "size": size, "w": w, "h": h, "need": need_h, "ratio": need_h / h, "bad": bad, "lines": n_tot})


BOUNDS: list[str] = []


def check_bounds(prs: Any, skip: set[int]) -> None:
    """Every non-placeholder shape on every body slide (appendix and hidden backups included) must sit inside the slide margins."""
    for n, slide in enumerate(prs.slides, 1):
        if id(slide) in skip:
            continue
        for sh in slide.shapes:
            if sh.is_placeholder:
                continue
            l, t, w, h = (Emu(v).inches for v in (sh.left, sh.top, sh.width, sh.height))
            if l < 0.3 - 1e-6 or l + w > SLIDE_W_IN - 0.3 + 1e-6 or t < 0 or t + h > 7.5 + 1e-6:
                BOUNDS.append(f"slide {n} {sh.name}: left {l:.2f} right {l + w:.2f} top {t:.2f} bottom {t + h:.2f}")


def check_guard() -> None:
    worst = sorted(GUARD, key=lambda g: -g["ratio"])[:14]
    LOG.info("overflow guard: tightest boxes (needed height / box height)")
    LOG.info("%-24s %-12s %5s %5s %5s %6s  %s", "slide", "kind", "pt", "w", "h", "ratio", "text")
    for g in worst:
        LOG.info("%-24s %-12s %5.1f %5.2f %5.2f %6.2f  %s%s", g["slide"][:24], g["kind"], g["size"], g["w"], g["h"], g["ratio"], g["text"], "  [BREAKS INSIDE A PHRASE]" if g["bad"] else "")
    if BOUNDS:
        for b in BOUNDS:
            LOG.error("OUT OF BOUNDS %s", b)
        raise SystemExit(f"bounds guard failed for {len(BOUNDS)} shapes")
    fails = [g for g in GUARD if g["ratio"] > 1.0 or g["bad"]]
    if fails:
        for g in fails:
            LOG.error("OVERFLOW %s | %s | %s | %.1fpt box %.2fx%.2f needs %.2f in, %d lines%s", g["slide"], g["kind"], g["text"], g["size"], g["w"], g["h"], g["need"], g["lines"], " (unbreakable chunk wider than box)" if g["bad"] else "")
        raise SystemExit(f"overflow guard failed for {len(fails)} text boxes")


def add_text(slide: Any, x: float, y: float, w: float, h: float, lines: list[str], size: float = 18, bold: bool = False,
             color: RGBColor = INK, face: str = FONT_BODY, align: str = "l", anchor: str = "t", name: str = "Text") -> Any:
    size = max(size, MIN_PT.get(name, 18 if name == "Text" else size))
    lines = [nb(t) for t in lines]
    guard_box(name, lines, size, bold, w, h, 0.04, 0.02)
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tb.name = name
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Inches(0.04)
    tf.margin_top = tf.margin_bottom = Inches(0.02)
    tf.vertical_anchor = {"t": MSO_ANCHOR.TOP, "m": MSO_ANCHOR.MIDDLE, "b": MSO_ANCHOR.BOTTOM}[anchor]
    for i, line in enumerate(lines):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.alignment = {"l": PP_ALIGN.LEFT, "c": PP_ALIGN.CENTER, "r": PP_ALIGN.RIGHT}[align]
        run = para.add_run()
        run.text = line
        set_font(run, size, bold, color, face)
    return tb


def add_box(slide: Any, x: float, y: float, w: float, h: float, fill: RGBColor, line: RGBColor | None = None, name: str = "Box") -> Any:
    shp = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    shp.name = name
    shp.fill.solid()
    shp.fill.fore_color.rgb = fill
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line
        shp.line.width = Pt(1.0)
    shp.shadow.inherit = False
    return shp


def add_image(slide: Any, path: Path, box: tuple[float, float, float, float], align: str = "c") -> tuple[float, float, float, float]:
    x, y, w, h = box
    with Image.open(path) as im:
        iw, ih = im.size
    fit = PF.contain_fit(iw, ih, PF.Box(int(Inches(x)), int(Inches(y)), int(Inches(w)), int(Inches(h))))
    left = fit.left if align == "c" else int(Inches(x))
    pic = slide.shapes.add_picture(str(path), left, fit.top, fit.width, fit.height)
    pic.name = "Image"
    return (Emu(left).inches, Emu(fit.top).inches, Emu(fit.width).inches, Emu(fit.height).inches)


def add_table(slide: Any, x: float, y: float, w: float, rows: list[list[str]], col_w: list[float], size: float = 16, row_h: float = 0.55) -> None:
    size = max(size, 16)
    rows = [[nb(c) for c in r] for r in rows]
    for i, row in enumerate(rows):
        for j, txt in enumerate(row):
            guard_box("TableCell", [txt], size, i == 0 or j == 0, col_w[j], row_h, 0.1, 0.05)
    gt = slide.shapes.add_table(len(rows), len(rows[0]), Inches(x), Inches(y), Inches(w), Inches(row_h * len(rows)))
    gt.name = "Table"
    tbl = gt.table
    for j, cw in enumerate(col_w):
        tbl.columns[j].width = Inches(cw)
    for i, row in enumerate(rows):
        tbl.rows[i].height = Inches(row_h)
        for j, txt in enumerate(row):
            cell = tbl.cell(i, j)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            tf = cell.text_frame
            tf.word_wrap = True
            tf.text = ""
            run = tf.paragraphs[0].add_run()
            run.text = txt
            cell.margin_left = cell.margin_right = Inches(0.1)
            cell.margin_top = cell.margin_bottom = Inches(0.05)
            head = i == 0
            set_font(run, size, bold=head or j == 0, color=RGBColor(0xFF, 0xFF, 0xFF) if head else INK)


def fill_textbox(shape: Any, text: str) -> None:
    """Replace the text of the first run, keeping the template formatting."""
    para = shape.text_frame.paragraphs[0]
    runs = para.runs
    runs[0].text = text
    runs[0].font.bold = True
    for r in runs[1:]:
        r._r.getparent().remove(r._r)


def reorder(prs: Any, order: list[Any]) -> None:
    lst = prs.slides._sldIdLst
    ids = {id(s): el for s, el in zip(prs.slides, list(lst))}
    for el in list(lst):
        lst.remove(el)
    for s in order:
        lst.append(ids[id(s)])


def drop_slide(prs: Any, slide: Any) -> None:
    lst = prs.slides._sldIdLst
    for el in list(lst):
        if prs.slides.get(int(el.get("id"))) is slide:
            prs.part.drop_rel(el.get(qn("r:id")))
            lst.remove(el)
            return


def build_body(prs: Any, layout: Any, s: dict[str, Any], num: int) -> Any:
    slide = prs.slides.add_slide(layout)
    ph = {p.placeholder_format.idx: p for p in slide.placeholders}
    _SLIDE["title"] = s["title"]
    ph[14].text_frame.text = s["title"]
    for r in ph[14].text_frame.paragraphs[0].runs:
        r.font.bold = True
        r.font.name = FONT
    ph[16].text_frame.text = str(num)
    ph[16].left, ph[16].top, ph[16].width, ph[16].height = Inches(0.3), Inches(0.03), Inches(0.8), Inches(0.34)
    ph[16].text_frame.word_wrap = False
    ph[16].text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    ph[16].text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
    for r in ph[16].text_frame.paragraphs[0].runs:  # full digits inside the curved badge, 1 or 2 digits
        r.font.size, r.font.bold, r.font.name = Pt(18), True, FONT
    # corner labels (tag left under banner, criterion right)
    add_text(slide, MARGIN_L, 0.62, 6.0, 0.3, [s.get("tags", "")], 12, True, BLUE, name="Tags")
    add_text(slide, SLIDE_W_IN - MARGIN_R - 5.2, 0.62, 5.2, 0.3, [f"기준: {s['criterion']}"], 12, True, GRAY, align="r", name="Criterion")
    lay = s.get("layout", "fig")
    if s.get("headline"):
        add_text(slide, MARGIN_L, 0.92, SLIDE_W_IN - MARGIN_L - MARGIN_R, 0.7, [s["headline"]], 30, False, NAVY, FONT_HEAD, name="Headline")
    y_kw = 1.6
    for kw in s.get("keywords", []):
        add_text(slide, MARGIN_L, y_kw, SLIDE_W_IN - MARGIN_L - MARGIN_R, 0.4, [kw], 19, False, GRAY, name="Keyword")
        y_kw += 0.4
    top = 2.05 if s.get("keywords") else 1.7
    top = max(top, 1.7 + 0.4 * len(s.get("keywords", [])) * (1 if s.get("keywords") else 0)) if False else top
    for el in s.get("elements", []):
        t = el["type"]
        if t == "image":
            box = tuple(el["box"])
            used = add_image(slide, Path(el["path"]), box, el.get("align", "c"))
            if el.get("caption"):
                add_text(slide, box[0], used[1] + used[3] + 0.03, box[2], 0.5, [el["caption"]], 11.5, False, INK, name="Caption")
            if el.get("source"):
                cy = used[1] + used[3] + (0.52 if el.get("caption") else 0.03)
                add_text(slide, box[0], cy, box[2], 0.3, [el["source"]], 9.5, False, GRAY, name="Source")
        elif t == "photo":
            x, y, w, h = el["box"]
            used = add_image(slide, Path(el["path"]), (x, y, w, h))
            add_text(slide, x, y + h + 0.05, w, 0.4, [el["label"]], 17, True, NAVY, align="c", name="PhotoLabel")
        elif t == "bignum":
            x, y, w, h = el["box"]
            add_box(slide, x, y, w, h, LIGHT, None, "BigNumCard")
            dy = 0.0
            if el.get("label"):
                add_text(slide, x, y + 0.1, w, 0.5, [el["label"]], 22, True, NAVY, FONT_HEAD, "c", name="BigNumLabel")
                dy = 0.5
            add_text(slide, x, y + 0.08 + dy, w, 0.85 if el.get("size", 44) < 50 else 1.25, [el["big"]], el.get("size", 44), True, el.get("color", BLUE) if not isinstance(el.get("color"), str) else BLUE, FONT_HEAD, "c", "m", "BigNum")
            add_text(slide, x, y + dy + (0.95 if el.get("size", 44) < 50 else 1.35), w, h - 1.0 - dy, el["sub"], el.get("subsize", 15), False, INK, align="c", name="BigNumSub")
        elif t == "card":
            x, y, w, h = el["box"]
            add_box(slide, x, y, w, h, LIGHT, NAVY, "Card")
            add_text(slide, x + 0.1, y + 0.1, w - 0.2, 0.55, [el["head"]], 20, True, NAVY, FONT_HEAD, "c", name="CardHead")
            add_text(slide, x + 0.1, y + 0.8, w - 0.2, h - 0.9, el["lines"], el.get("size", 17), False, INK, align="c", name="CardLines")
        elif t == "rect":
            x, y, w, h = el["box"]
            add_box(slide, x, y, w, h, {"light": LIGHT, "navy": NAVY, "blue": BLUE, "gray": BOXGRAY}[el.get("fill", "light")], None, "Rect")
        elif t == "graybox":
            x, y, w, h = el["box"]
            b = add_box(slide, x, y, w, h, BOXGRAY, GRAY, "Placeholder")
            add_text(slide, x, y, w, h, el["lines"], 20, False, GRAY, align="c", anchor="m", name="PlaceholderText")
        elif t == "table":
            add_table(slide, el["x"], el["y"], el["w"], el["rows"], el["col_w"], el.get("size", 16), el.get("row_h", 0.55))
        elif t == "text":
            src_like = bool(el["lines"]) and el["lines"][0].startswith(("출처", "화면", "사진", "공공데이터", "공개데이터", "그림"))
            add_text(slide, el["box"][0], el["box"][1], el["box"][2], el["box"][3], el["lines"], el.get("size", 18), el.get("bold", False),
                     TEXT_COLORS.get(el.get("color", ""), NAVY if el.get("navy") else INK), FONT_HEAD if el.get("head") else FONT_BODY, el.get("align", "l"), el.get("anchor", "t"),
                     name="Source" if src_like else ("Hero" if el.get("size", 18) >= 60 else "Text"))
        else:
            raise ValueError(f"unknown element type {t}")
    if s.get("credit"):
        add_text(slide, 0.4, 6.82, 12.5, 0.5, [s["credit"]], 12, False, INK, name="Credit")
    if s.get("notes"):
        slide.notes_slide.notes_text_frame.text = s["notes"]
    return slide


def unify_fonts(path: Path) -> None:
    """Rewrite every part of the pptx so the template's missing families (HY헤드라인M, 프리젠테이션, Pretendard, 맑은 고딕) become Apple SD Gothic Neo, theme included."""
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.startswith("ppt/") and item.filename.endswith(".xml"):
                txt = OLD_FONTS.sub(f'typeface="{FONT}"', data.decode("utf-8"))
                if "/theme/" in item.filename:
                    txt = re.sub(r"<a:cs typeface=\"\"/>", f'<a:cs typeface="{FONT}"/>', txt)
                data = txt.encode("utf-8")
            zout.writestr(item, data)
    tmp.replace(path)


def build(spec_path: Path, out: Path, hide: bool = True) -> None:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    GUARD.clear()
    prs = Presentation(str(TEMPLATE))
    slides = list(prs.slides)
    cover, toc, tmpl_body, thanks = slides
    # cover
    for sh in cover.shapes:
        if sh.has_text_frame:
            t = sh.text_frame.text.strip()
            if t == "팀명 :":
                fill_textbox(sh, spec["cover"]["team"])
                sh.width = Inches(7.5)
            elif t == "작품명 :":
                fill_textbox(sh, spec["cover"]["work"])
                sh.width = Inches(11.4)
                sh.text_frame.paragraphs[0].runs[0].font.size = Pt(30)
            elif t.startswith("제8회"):
                sh.width = Inches(11.5)
    if spec["cover"]["subtitle"]:
        add_text(cover, 1.1, 5.55, 8.0, 0.7, [spec["cover"]["subtitle"]], 28, False, NAVY, FONT_HEAD, name="Subtitle")
    add_text(cover, 1.1, 6.15, 8.5, 0.5, [spec["cover"]["topic"]], 20, False, NAVY, FONT_BODY, name="Topic")
    cover.notes_slide.notes_text_frame.text = spec["cover"]["notes"]
    # toc
    for sh in toc.shapes:
        if sh.has_text_frame and sh.text_frame.text.strip().startswith("1."):
            tf = sh.text_frame
            paras = tf.paragraphs
            for para, txt in zip(paras, spec["toc"]):
                runs = para.runs
                runs[0].text = txt
                for r in runs[1:]:
                    r._r.getparent().remove(r._r)
    toc.notes_slide.notes_text_frame.text = spec["toc_notes"]
    # thanks
    for sh in thanks.shapes:
        if sh.has_text_frame and sh.text_frame.text.strip() == "팀명 :":
            fill_textbox(sh, spec["cover"]["team"])
            sh.width = Inches(7.5)
        elif sh.has_text_frame and sh.text_frame.text.strip().startswith("감사합니다"):
            sh.width = Inches(8.0)
    layout = PF.resolve_layout(prs, BODY_LAYOUT)
    main, appendix = [], []
    for i, s in enumerate(spec["slides"]):
        sl = build_body(prs, layout, s, i + 4 if s.get("appendix") else i + 3)
        if hide and s.get("hidden"):
            sl._element.set("show", "0")  # PowerPoint "슬라이드 숨기기": skipped in slide show, still in the file
        (appendix if s.get("appendix") else main).append(sl)
    drop_slide(prs, tmpl_body)
    BOUNDS.clear()
    check_bounds(prs, {id(cover), id(toc), id(thanks)})
    reorder(prs, [cover, toc, *main, thanks, *appendix])
    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out))
    unify_fonts(out)
    check_guard()
    LOG.info("wrote %s (%d slides)", out, len(prs.slides))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    here = Path(__file__).resolve().parent
    v = sys.argv[1] if len(sys.argv) > 1 else "A"
    out = here / f"wireframe_{v}.pptx"
    build(here / f"spec_{v}.json", out, hide=False)  # LibreOffice skips hidden slides in PDF export, so render previews from the unhidden build
    pdf, pngs = PF.render_previews(out, DPI_PREVIEW, font=FONT, font_file=Path(FONT_FILE))
    build(here / f"spec_{v}.json", out, hide=True)  # the delivered pptx has the backup slides hidden
    LOG.info("pdf %s, %d pngs", pdf, len(pngs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
