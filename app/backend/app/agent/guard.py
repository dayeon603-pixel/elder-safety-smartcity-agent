"""Guardrails: JSON-schema subset validator, context binding and numeric grounding."""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from . import constants as C

JsonSchema = Mapping[str, Any]
_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,), "array": (list,), "string": (str,), "boolean": (bool,),
    "integer": (int,), "number": (int, float),
}
# A number, optionally negative when the sign is not glued to a preceding digit ("2025-07-22"
# yields 2025, 7, 22; " -3.2" yields -3.2). Digits glued to letters/dots are not numbers.
_NUM_RE = re.compile(r"(?<![A-Za-z0-9.])(?:(?<![0-9])-)?[0-9]+(?:\.[0-9]+)?")
_COMMA_RE = re.compile(r"(?<=[0-9]),(?=[0-9]{3})")
# Only single-digit list markers at a line start ("3. "); "38.2℃" or "12) ..." stay numbers.
_LIST_MARKER_RE = re.compile(r"^[ \t]*[1-9][.)][ \t]+", re.MULTILINE)
_HANGUL_RE = re.compile(r"[가-힣]")
_LITERAL_RES = tuple(re.compile(rf"(?<![0-9A-Za-z.]){re.escape(lit)}(?![0-9A-Za-z.])") for lit in C.ALLOWED_LITERALS)
BOUND_KEYS = ("code", "date", "lat", "lon", "est_speed", "torso_angle")
# Keys whose numbers are never data claims a briefing may cite: coordinates, latencies, identifiers.
EXCLUDED_EVIDENCE_KEYS = frozenset({"lat", "lon", "ms", "http", "image_sha256", "receipt_id", "cap_id",
                                    "bundle_id", "run_id", "model", "sgg_code", "tel", "hpid", "at",
                                    "beds_updated_at"})


def validate_schema(value: Any, schema: JsonSchema, path: str = "$") -> list[str]:
    """Validate `value` against the JSON-schema subset used by the tool registry.

    Supports type, enum, required, properties, additionalProperties=False, items, minimum,
    maximum, minLength, maxLength, pattern, minItems. Returns human-readable errors.
    """
    errors: list[str] = []
    typ = schema.get("type")
    if typ is not None:
        ok = isinstance(value, _TYPES[typ]) and not (typ in ("integer", "number") and isinstance(value, bool))
        if not ok:
            return [f"{path}: expected {typ}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: must be one of {list(schema['enum'])}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if len(value) > schema.get("maxLength", len(value)):
            errors.append(f"{path}: longer than {schema['maxLength']}")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            errors.append(f"{path}: does not match {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum {schema['maximum']}")
    if isinstance(value, dict):
        props: Mapping[str, JsonSchema] = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{path}.{req}: required")
        for key, sub in value.items():
            if key in props:
                errors.extend(validate_schema(sub, props[key], f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}.{key}: unexpected property")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "items" in schema:
            for i, item in enumerate(value):
                errors.extend(validate_schema(item, schema["items"], f"{path}[{i}]"))
    return errors


def bind_context(args: Any, ctx_vals: Mapping[str, Any], path: str = "$") -> tuple[Any, list[str]]:
    """Overwrite any context-owned field (code, date, lat, ...) with the trusted context value.

    A model must not redirect an alert or invent a measurement. A context-owned key the context
    does not have (e.g. est_speed absent) is removed. Returns (bound args, correction notes).
    """
    notes: list[str] = []
    if isinstance(args, dict):
        out: dict[str, Any] = {}
        if "kind" in args and "code" in args:  # an event object: fill measurements the model left out
            for key in BOUND_KEYS:
                if key not in args and ctx_vals.get(key) is not None:
                    out[key] = ctx_vals[key]
                    notes.append(f"{path}.{key}: filled from context")
        for key, val in args.items():
            if key == "kind" and "kind" in ctx_vals:
                if val != ctx_vals["kind"]:
                    notes.append(f"{path}.kind: {val!r} -> {ctx_vals['kind']!r}")
                out[key] = ctx_vals["kind"]
            elif key in BOUND_KEYS:
                trusted = ctx_vals.get(key)
                if trusted is None:
                    notes.append(f"{path}.{key}: removed (not in context)")
                    continue
                if val != trusted:
                    notes.append(f"{path}.{key}: {val!r} -> {trusted!r}")
                out[key] = trusted
            else:
                sub, sub_notes = bind_context(val, ctx_vals, f"{path}.{key}")
                out[key] = sub
                notes.extend(sub_notes)
        return out, notes
    if isinstance(args, list):
        items = [bind_context(v, ctx_vals, f"{path}[{i}]") for i, v in enumerate(args)]
        return [v for v, _ in items], [n for _, ns in items for n in ns]
    return args, notes


def strip_noise(text: str) -> str:
    """Remove ids, ISO timestamps and allowed fixed literals so their digits are not read as data."""
    text = _LIST_MARKER_RE.sub("", text)
    for rx in (*C.NOISE_RES, *_LITERAL_RES):
        text = rx.sub(" ", text)
    return _COMMA_RE.sub("", text)


def extract_numbers(text: str) -> list[tuple[str, str]]:
    """Numbers in text as (literal, following 1 char) after noise stripping."""
    clean = strip_noise(text)
    return [(m.group(0), clean[m.end(): m.end() + 1]) for m in _NUM_RE.finditer(clean)]


def collect_evidence(obj: Any, out: list[float] | None = None) -> list[float]:
    """Every data number in a JSON-like tree (numeric values and numbers inside strings).

    Values under EXCLUDED_EVIDENCE_KEYS (coordinates, latencies, identifiers) are skipped, so a
    briefing cannot launder a fabricated figure through, say, a step's millisecond count.
    """
    acc: list[float] = [] if out is None else out
    if isinstance(obj, bool) or obj is None:
        return acc
    if isinstance(obj, (int, float)):
        acc.append(float(obj))
    elif isinstance(obj, str):
        acc.extend(float(lit) for lit, _ in extract_numbers(obj))
    elif isinstance(obj, Mapping):
        for k, v in obj.items():
            if k not in EXCLUDED_EVIDENCE_KEYS:
                collect_evidence(v, acc)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            collect_evidence(v, acc)
    return acc


def _matches(literal: str, unit: str, evidence: list[float]) -> bool:
    try:
        target = Decimal(literal)
    except InvalidOperation:
        return False
    places = Decimal(1).scaleb(-(len(literal.split(".")[1]) if "." in literal else 0))
    # "%" must come from a fraction (x100) and "만" from a count (x1e-4): a bare rank 61 does not
    # justify "61%". Unitless literals match evidence values exactly (after rounding).
    scales = [100.0] if unit == "%" else [1e-4] if unit == "만" else [1.0]
    for v in evidence:
        for s in scales:
            cand = Decimal(repr(v * s)) if s == 1.0 else Decimal(repr(round(v * s, 9)))
            if cand.quantize(places, rounding=ROUND_HALF_UP) == target:
                return True
    return False


def check_grounding(text: str, evidence: list[float]) -> list[str]:
    """Numbers in `text` that no evidence number explains (equal after rounding; % needs x100, 만 x1e-4)."""
    return [lit for lit, unit in extract_numbers(text) if not _matches(lit, unit, evidence)]


def check_briefing(text: str, evidence: list[float], must_mention: tuple[str, ...] = ()) -> list[str]:
    """All reasons a briefing is not acceptable; empty list means pass."""
    reasons: list[str] = []
    if not text.strip():
        return ["empty"]
    if not _HANGUL_RE.search(text):
        reasons.append("not Korean")
    if len(text) > C.MAX_BRIEFING_CHARS:
        reasons.append(f"longer than {C.MAX_BRIEFING_CHARS} chars")
    reasons.extend(f"missing required word: {w}" for w in must_mention if w not in text)
    reasons.extend(f"banned term: {t}" for t in C.BANNED_BRIEFING_TERMS if t in text)
    reasons.extend(f"ungrounded number: {n}" for n in check_grounding(text, evidence))
    return reasons
