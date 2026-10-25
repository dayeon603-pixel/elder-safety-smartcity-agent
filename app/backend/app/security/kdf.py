"""HKDF-SHA256 (RFC 5869) for deriving purpose-bound subkeys from the master secret."""

from __future__ import annotations

import hashlib
import hmac

HASH_LEN = hashlib.sha256().digest_size
MAX_OUTPUT_LEN = 255 * HASH_LEN
DEFAULT_SALT = b"kcf-hkdf-salt-v1"
DEFAULT_KEY_LEN = 32


def derive_key(
    ikm: bytes, info: bytes, length: int = DEFAULT_KEY_LEN, salt: bytes = DEFAULT_SALT
) -> bytes:
    """HKDF-Extract-then-Expand with HMAC-SHA256.

    Args:
        ikm: input keying material (the master secret).
        info: context label; different labels give independent keys.
        length: output length in bytes (1..255*32).
        salt: non-secret salt for the extract step.

    Raises:
        ValueError: if `length` is out of range.
    """
    if not 0 < length <= MAX_OUTPUT_LEN:
        raise ValueError("length out of range")
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    okm = b""
    block = b""
    counter = 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        okm += block
        counter += 1
    return okm[:length]
