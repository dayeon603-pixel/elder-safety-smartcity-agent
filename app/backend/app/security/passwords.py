"""Argon2id password hashing (RFC 9106 low-memory profile)."""

from __future__ import annotations

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

ARGON2_TIME_COST = 3
ARGON2_MEMORY_KIB = 64 * 1024
ARGON2_PARALLELISM = 4
ARGON2_HASH_LEN = 32
ARGON2_SALT_LEN = 16

_hasher = PasswordHasher(
    time_cost=ARGON2_TIME_COST,
    memory_cost=ARGON2_MEMORY_KIB,
    parallelism=ARGON2_PARALLELISM,
    hash_len=ARGON2_HASH_LEN,
    salt_len=ARGON2_SALT_LEN,
    type=Type.ID,
)
# Verified against when the account does not exist, so login time does not reveal it.
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing-equalisation")


def hash_password(password: str) -> str:
    """Return a PHC-format Argon2id hash with a fresh random salt."""
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    """Constant-effort verification; a missing hash still burns one Argon2 evaluation."""
    target = stored_hash if stored_hash is not None else _DUMMY_HASH
    try:
        ok = _hasher.verify(target, password)
    except (VerificationError, InvalidHashError):
        return False
    return ok and stored_hash is not None


def needs_rehash(stored_hash: str) -> bool:
    """True if the hash was made with weaker parameters than the current profile."""
    return _hasher.check_needs_rehash(stored_hash)
