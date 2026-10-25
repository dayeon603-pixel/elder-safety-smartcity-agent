"""CAP 1.2 XML from the JSON alert shape in `standards.build_cap`, validated against the OASIS XSD.

The XSD (app/agent/xsd/CAP-v1.2.xsd) is the official schema, shipped with the package so validation
works offline and inside the API image. `validate_xml` returns the schema's own error messages;
an empty list means the document conforms to the CAP 1.2 schema (structure, element order, types).
The schema does not check CAP's prose rules (e.g. `restriction` required when scope=Restricted);
`standards.validate_cap` keeps those as a self-check.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from lxml import etree

from . import constants as C

logger = logging.getLogger(__name__)

XSD_PATH = Path(__file__).resolve().parent / "xsd" / "CAP-v1.2.xsd"
CAP_NS = C.CAP_NAMESPACE
CAP_MEDIA_TYPE = "application/cap+xml"
MAX_SCHEMA_ERRORS = 10
# Element order the schema requires (sequence); only keys present in the JSON are written.
_ALERT_ORDER = ("identifier", "sender", "sent", "status", "msgType", "source", "scope", "restriction",
                "addresses", "code", "note", "references", "incidents")
_INFO_ORDER = ("language", "category", "event", "responseType", "urgency", "severity", "certainty", "audience",
               "eventCode", "effective", "onset", "expires", "senderName", "headline", "description", "instruction",
               "web", "contact", "parameter", "resource")
_AREA_ORDER = ("areaDesc", "polygon", "circle", "geocode", "altitude", "ceiling")


class CapXmlError(ValueError):
    """The JSON alert cannot be rendered as CAP XML (missing `alert` object or a non-scalar field)."""


def _q(name: str) -> str:
    return f"{{{CAP_NS}}}{name}"


def _append_scalars(parent: etree._Element, obj: dict[str, Any], order: tuple[str, ...]) -> None:
    for key in order:
        if key not in obj or obj[key] is None:
            continue
        values = obj[key] if isinstance(obj[key], list) else [obj[key]]
        for v in values:
            if isinstance(v, (dict, list)):
                raise CapXmlError(f"{key}: nested value not supported in this renderer")
            el = etree.SubElement(parent, _q(key))
            el.text = str(v)


def to_xml(doc: dict[str, Any]) -> bytes:
    """Render the JSON alert (`{"alert": {...}}`) as CAP 1.2 XML (UTF-8, with declaration).

    Raises:
        CapXmlError: when the document is not an alert object or a field cannot be rendered.
    """
    alert = doc.get("alert") if isinstance(doc, dict) else None
    if not isinstance(alert, dict):
        raise CapXmlError("document has no `alert` object")
    root = etree.Element(_q("alert"), nsmap={None: CAP_NS})
    _append_scalars(root, alert, _ALERT_ORDER)
    infos = alert.get("info")
    for info in infos if isinstance(infos, list) else ([infos] if isinstance(infos, dict) else []):
        info_el = etree.SubElement(root, _q("info"))
        _append_scalars(info_el, info, _INFO_ORDER)
        areas = info.get("area")
        for area in areas if isinstance(areas, list) else ([areas] if isinstance(areas, dict) else []):
            area_el = etree.SubElement(info_el, _q("area"))
            _append_scalars(area_el, area, _AREA_ORDER)
    out: bytes = etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)
    return out


@lru_cache(maxsize=1)
def schema() -> etree.XMLSchema:
    """The compiled OASIS CAP 1.2 schema (loaded once, no network)."""
    parser = etree.XMLParser(load_dtd=False, no_network=True, resolve_entities=False)
    return etree.XMLSchema(etree.parse(str(XSD_PATH), parser))


def validate_xml(xml: bytes) -> list[str]:
    """Schema errors for a CAP XML document (empty = conforms to the OASIS CAP 1.2 XSD)."""
    parser = etree.XMLParser(no_network=True, resolve_entities=False, load_dtd=False)
    try:
        tree = etree.fromstring(xml, parser)
    except etree.XMLSyntaxError as exc:
        return [f"not well-formed: {exc}"]
    xsd = schema()
    if xsd.validate(tree):
        return []
    return [str(e.message) for e in list(xsd.error_log)[:MAX_SCHEMA_ERRORS]]


__all__ = ["CAP_MEDIA_TYPE", "CapXmlError", "XSD_PATH", "schema", "to_xml", "validate_xml"]
