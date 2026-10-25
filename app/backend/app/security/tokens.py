"""Short-lived HMAC-SHA256 signed tokens: base64url(payload).base64url(mac)."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
from typing import Any

TOKEN_VERSION = 1
MAX_TOKEN_LEN = 1024
_B64URL_RE = re.compile(r"[A-Za-z0-9_-]*")


class TokenError(Exception):
    """Token is malformed, forged, or otherwise unusable."""


class ExpiredTokenError(TokenError):
    """Token signature is valid but it is past its expiry."""


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    """Strict unpadded base64url: alphabet only, and the input must be the canonical encoding.

    Re-encoding and comparing rejects non-zero trailing bits, which lenient decoders
    silently drop (two different strings would otherwise decode to the same bytes).
    """
    if not _B64URL_RE.fullmatch(text):
        raise TokenError("bad base64")
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as exc:
        raise TokenError("bad base64") from exc
    if _b64e(raw) != text:
        raise TokenError("non-canonical base64")
    return raw


def _mac(secret: bytes, signing_input: bytes) -> bytes:
    return hmac.new(secret, signing_input, hashlib.sha256).digest()


def issue_token(
    secret: bytes, user_id: int, role: str, ttl_s: int, now: float | None = None
) -> str:
    """Sign a token for `user_id` valid for `ttl_s` seconds."""
    issued = int(time.time() if now is None else now)
    payload = {"v": TOKEN_VERSION, "sub": user_id, "role": role, "iat": issued, "exp": issued + ttl_s}
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return f"{body}.{_b64e(_mac(secret, body.encode('ascii')))}"


def verify_token(secret: bytes, token: str, now: float | None = None) -> dict[str, Any]:
    """Return the payload of a valid, unexpired token.

    The MAC is checked (constant time) before the payload is parsed.

    Raises:
        ExpiredTokenError: valid signature, past expiry.
        TokenError: any other defect.
    """
    if not token or len(token) > MAX_TOKEN_LEN or token.count(".") != 1:
        raise TokenError("malformed token")
    body, sig = token.split(".")
    if not hmac.compare_digest(_b64d(sig), _mac(secret, body.encode("ascii", "replace"))):
        raise TokenError("bad signature")
    try:
        payload = json.loads(_b64d(body))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise TokenError("bad payload") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("v") != TOKEN_VERSION
        or not isinstance(payload.get("sub"), int)
        or not isinstance(payload.get("exp"), int)
    ):
        raise TokenError("bad claims")
    if (time.time() if now is None else now) >= payload["exp"]:
        raise ExpiredTokenError("token expired")
    return payload
