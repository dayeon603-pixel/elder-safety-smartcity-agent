"""Minimal DOCX (WordprocessingML) writer with the standard library only: paragraphs and one two-column table.

Produces a valid OOXML package ([Content_Types].xml, _rels/.rels, word/document.xml) that Word, LibreOffice
and Pages open. No external dependency, no network, deterministic bytes for the same input.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Sequence
from xml.sax.saxutils import escape

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/></Relationships>'
)
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
FONT = "Malgun Gothic"
FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)  # deterministic archives
TITLE_HALF_POINTS = 32  # 16 pt
BODY_HALF_POINTS = 22  # 11 pt


def _run(text: str, *, bold: bool = False, size: int = BODY_HALF_POINTS) -> str:
    rpr = (f'<w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:eastAsia="{FONT}"/>'
           f'{"<w:b/>" if bold else ""}<w:sz w:val="{size}"/></w:rPr>')
    return f'<w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _p(text: str, *, bold: bool = False, size: int = BODY_HALF_POINTS, center: bool = False) -> str:
    ppr = '<w:pPr><w:jc w:val="center"/></w:pPr>' if center else ""
    return f"<w:p>{ppr}{_run(text, bold=bold, size=size)}</w:p>"


def _table(rows: Sequence[tuple[str, str]]) -> str:
    def cell(text: str, bold: bool) -> str:
        return f'<w:tc><w:tcPr><w:tcW w:w="0" w:type="auto"/></w:tcPr>{_p(text, bold=bold)}</w:tc>'

    body = "".join(f"<w:tr>{cell(k, True)}{cell(v, False)}</w:tr>" for k, v in rows)
    borders = "".join(f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="999999"/>'
                      for side in ("top", "left", "bottom", "right", "insideH", "insideV"))
    return f'<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/><w:tblBorders>{borders}</w:tblBorders></w:tblPr>{body}</w:tbl>'


def build_docx(title: str, meta: Sequence[tuple[str, str]], paragraphs: Sequence[str],
               evidence: Sequence[tuple[str, str]], footer: str) -> bytes:
    """One-page letter: centred title, meta table (수신/발신/일자...), body paragraphs, evidence table, footer."""
    parts = [_p(title, bold=True, size=TITLE_HALF_POINTS, center=True), _p("")]
    parts.append(_table(meta))
    parts.append(_p(""))
    parts.extend(_p(t) for t in paragraphs)
    parts.append(_p(""))
    parts.append(_p("근거 (시스템 산출값과 출처)", bold=True))
    parts.append(_table(evidence))
    parts.append(_p(""))
    parts.append(_p(footer))
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{W_NS}"><w:body>'
                + "".join(parts) + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr></w:body></w:document>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in (("[Content_Types].xml", CONTENT_TYPES), ("_rels/.rels", RELS), ("word/document.xml", document)):
            info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data.encode("utf-8"))
    return buf.getvalue()


__all__ = ["DOCX_MEDIA_TYPE", "build_docx"]
