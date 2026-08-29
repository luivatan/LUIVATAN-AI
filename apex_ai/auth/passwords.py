"""Phase 51/53 — password hashing.

Argon2id via ``argon2-cffi`` (OWASP's current recommended default for new
applications). No custom cryptography, no plaintext, no reversible encoding —
this module's entire job is "call the vetted library correctly," matching the
roadmap's explicit instruction to use "a proven authentication provider or
secure password hashing."
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 256  # bound the hasher's own input; not a strength rule

_hasher = PasswordHasher()


def validate_password_strength(password: str) -> str:
    """Minimal, honest baseline: a length floor, nothing pretending to be a
    real strength meter. Returns the password unchanged so call sites can
    chain this, or raises ``ValueError`` with a user-facing reason."""
    if not isinstance(password, str):
        raise TypeError("password must be a string")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at most {MAX_PASSWORD_LENGTH} characters.")
    return password


def hash_password(password: str) -> str:
    validate_password_strength(password)
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Never raises on a wrong password or a malformed/legacy hash — both are
    just "not a match," not an application error."""
    if not password or not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


__all__ = [
    "MAX_PASSWORD_LENGTH",
    "MIN_PASSWORD_LENGTH",
    "hash_password",
    "validate_password_strength",
    "verify_password",
]
