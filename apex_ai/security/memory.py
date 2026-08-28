"""Fail-closed safety checks for durable long-term-memory text.

This module never returns matched values. Findings contain only stable reason
codes so credentials and sensitive text cannot leak through diagnostics.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from apex_ai.core.errors import SecurityError


class UnsafeMemoryError(SecurityError):
    """Raised when content must not cross the durable-memory boundary."""


@dataclass(frozen=True)
class MemorySafetyFinding:
    code: str


@dataclass(frozen=True)
class MemorySafetyResult:
    findings: tuple[MemorySafetyFinding, ...] = ()

    @property
    def safe(self) -> bool:
        return not self.findings

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.findings)


_PATTERN_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "labeled_credential",
        re.compile(
            r"""
            (?<![A-Za-z0-9])(?:
                pass(?:word|wd|code)|pin|api[\s_-]*key|access[\s_-]*key|
                secret(?:[\s_-]*key)?|client[\s_-]*secret|private[\s_-]*key|
                auth(?:entication|orization)?[\s_-]*token|access[\s_-]*token|
                refresh[\s_-]*token|session(?:[\s_-]*(?:id|token))?|cookie
            )\b
            \s*(?:=|:|\bis\b|\bwas\b)\s*["']?
            (?!none\b|empty\b|missing\b|redacted\b|unset\b)[^\s"']{4,}
            """,
            re.IGNORECASE | re.VERBOSE,
        ),
    ),
    (
        "authorization_credential",
        re.compile(
            r"\b(?:(?:authorization|proxy-authorization)\s*:\s*)?"
            r"(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}",
            re.IGNORECASE,
        ),
    ),
    (
        "private_key",
        re.compile(
            r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----",
            re.IGNORECASE,
        ),
    ),
    (
        "credential_url",
        re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@", re.IGNORECASE),
    ),
    (
        "jwt",
        re.compile(
            r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{8,}\."
            r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"
        ),
    ),
    (
        "provider_api_key",
        re.compile(
            r"(?<![A-Za-z0-9])(?:"
            r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}|"
            r"github_pat_[A-Za-z0-9_]{20,}|"
            r"gh[pousr]_[A-Za-z0-9]{20,}|"
            r"xox[baprs]-[A-Za-z0-9-]{16,}|"
            r"AIza[A-Za-z0-9_-]{30,}|"
            r"(?:AKIA|ASIA)[A-Z0-9]{16}"
            r")(?![A-Za-z0-9])"
        ),
    ),
    (
        "social_security_number",
        re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"),
    ),
    (
        "labeled_sensitive_identifier",
        re.compile(
            r"""
            \b(?:
                social[\s_-]*security(?:[\s_-]*number)?|ssn|
                credit[\s_-]*card(?:[\s_-]*number)?|cvv|cvc|
                bank[\s_-]*account(?:[\s_-]*number)?|routing[\s_-]*number|
                recovery[\s_-]*(?:phrase|seed)|seed[\s_-]*phrase|
                government[\s_-]*id|passport[\s_-]*number|
                driver'?s[\s_-]*license(?:[\s_-]*number)?|
                medical[\s_-]*record(?:[\s_-]*number)?|patient[\s_-]*id|
                health[\s_-]*insurance(?:[\s_-]*number)?|date[\s_-]*of[\s_-]*birth|dob
            )\b\s*(?:=|:|\bis\b|\bwas\b)\s*[^\s,;]{2,}
            """,
            re.IGNORECASE | re.VERBOSE,
        ),
    ),
    (
        "unnecessary_contact_detail",
        re.compile(
            r"\b(?:my|our)\s+(?:personal\s+)?"
            r"(?:email(?:\s+address)?|phone(?:\s+number)?|home\s+address)\s+"
            r"(?:is|was)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "personal_health_detail",
        re.compile(
            r"\b(?:my\s+(?:diagnosis|medical\s+condition|medication)\s+(?:is|was)|"
            r"i\s+(?:was\s+)?diagnosed\s+with)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "sensitive_profile_detail",
        re.compile(
            r"\b(?:my|our)\s+"
            r"(?:race|ethnicity|religion|sexual\s+orientation|political\s+affiliation)\s+"
            r"(?:is|was)\b",
            re.IGNORECASE,
        ),
    ),
)

_PAYMENT_CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_OPAQUE_TOKEN_CANDIDATE = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z0-9_+/=-]{32,256}(?![A-Za-z0-9])"
)
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_HEX = re.compile(r"^[0-9a-f]+$", re.IGNORECASE)


def _luhn_valid(digits: str) -> bool:
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    checksum = 0
    parity = len(digits) % 2
    for index, char in enumerate(digits):
        value = int(char)
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        checksum += value
    return checksum % 10 == 0


def _looks_high_entropy(value: str) -> bool:
    if _UUID.fullmatch(value) or _HEX.fullmatch(value):
        return False
    classes = sum(
        (
            any(char.islower() for char in value),
            any(char.isupper() for char in value),
            any(char.isdigit() for char in value),
            any(char in "_+/=-" for char in value),
        )
    )
    if classes < 3:
        return False
    frequencies = Counter(value)
    entropy = -sum(
        (count / len(value)) * math.log2(count / len(value))
        for count in frequencies.values()
    )
    return entropy >= 4.0


class MemorySafetyPolicy:
    """Classify durable-memory text without retaining or echoing matched values."""

    def inspect(self, content: str) -> MemorySafetyResult:
        if not isinstance(content, str):
            raise TypeError("content must be a string")

        codes = [code for code, pattern in _PATTERN_RULES if pattern.search(content)]
        if any(
            _luhn_valid(re.sub(r"\D", "", match.group(0)))
            for match in _PAYMENT_CARD_CANDIDATE.finditer(content)
        ):
            codes.append("payment_card_number")
        if any(
            _looks_high_entropy(match.group(0))
            for match in _OPAQUE_TOKEN_CANDIDATE.finditer(content)
        ):
            codes.append("high_entropy_credential")

        unique_codes = tuple(dict.fromkeys(codes))
        return MemorySafetyResult(
            tuple(MemorySafetyFinding(code) for code in unique_codes)
        )

    def require_safe(self, content: str) -> None:
        result = self.inspect(content)
        if result.safe:
            return
        reasons = ", ".join(result.reason_codes)
        raise UnsafeMemoryError(
            what="That text was not saved to long-term memory.",
            why=f"It matched the memory-safety policy ({reasons}).",
            fix=(
                "Remove credentials or unnecessary sensitive details. Keep passwords, "
                "API keys, tokens, and recovery data in a dedicated secret manager, "
                "not AI memory."
            ),
        )


__all__ = [
    "MemorySafetyFinding",
    "MemorySafetyPolicy",
    "MemorySafetyResult",
    "UnsafeMemoryError",
]
