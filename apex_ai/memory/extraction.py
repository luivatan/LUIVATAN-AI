"""Conservative, ephemeral long-term-memory candidate extraction.

Phase 43 identifies explicit preference and ongoing-context statements. It does
not persist candidates, inspect assistant output, invoke an LLM, or inject
anything into a prompt. Safety filtering and confirmation are separate stages.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Literal

MemoryKind = Literal["preference", "ongoing_context"]

MAX_MESSAGE_CHARS = 20_000
MAX_CANDIDATE_CHARS = 500
MAX_CANDIDATES = 5

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+|[\r\n]+")
_LIST_MARKER = re.compile(r"^\s*(?:(?:[-*•])|(?:\d+[.)]))\s+")
_SPACE = re.compile(r"\s+")

_PREFERENCE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "stated_preference",
        re.compile(
            r"\b(?:i|we)\s+(?:(?:really|strongly)\s+)?prefer(?:\s+that)?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "named_preference",
        re.compile(
            r"\b(?:my|our)\s+(?:(?:formatting|writing|communication|answer|response)\s+)?"
            r"preference\s+is\b",
            re.IGNORECASE,
        ),
    ),
    (
        "persistent_request",
        re.compile(r"\bplease\s+(?:always|usually|generally)\b", re.IGNORECASE),
    ),
    (
        "persistent_instruction",
        re.compile(
            r"^(?:always|usually|generally)\s+"
            r"(?:answer|respond|format|include|use|write|show|keep|avoid)\b",
            re.IGNORECASE,
        ),
    ),
)

_CONTEXT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "active_work",
        re.compile(
            r"\b(?:i(?:'m| am)|we(?:'re| are))\s+(?:currently\s+)?"
            r"(?:working|building|developing|writing|researching|planning|migrating|"
            r"implementing|maintaining)(?:\s+on)?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ongoing_project",
        re.compile(
            r"\b(?:my|our)\s+(?:current|ongoing)\s+(?:project|goal|task|work)\s+"
            r"(?:is|involves|uses|needs)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "project_context",
        re.compile(
            r"\b(?:my|our)\s+project\s+(?:is|uses|targets|involves|needs)\b",
            re.IGNORECASE,
        ),
    ),
)

_REMEMBER_RULE = re.compile(r"^(?:please\s+)?remember(?:\s+that)?\b", re.IGNORECASE)
_PREFERENCE_HINT = re.compile(
    r"\b(?:prefer|preference|always|usually|answer|response|format)\b",
    re.IGNORECASE,
)


def _normalized_key(kind: MemoryKind, content: str) -> str:
    normalized = _SPACE.sub(" ", content).strip().casefold()
    return f"{kind}\0{normalized}"


def _candidate_id(kind: MemoryKind, content: str) -> str:
    digest = hashlib.sha256(_normalized_key(kind, content).encode("utf-8")).hexdigest()
    return f"memcand_{digest[:24]}"


@dataclass(frozen=True)
class MemoryCandidate:
    """An unpersisted statement that may be useful after safety and approval."""

    id: str
    kind: MemoryKind
    content: str
    rule: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "content": self.content,
            "rule": self.rule,
        }


class MemoryCandidateExtractor:
    """Find only explicit durable-memory signals using auditable local rules."""

    def extract(
        self,
        user_message: str,
        *,
        max_candidates: int = MAX_CANDIDATES,
    ) -> list[MemoryCandidate]:
        if not isinstance(user_message, str):
            raise TypeError("user_message must be a string")
        if isinstance(max_candidates, bool) or not isinstance(max_candidates, int):
            raise TypeError("max_candidates must be an integer")
        if not 1 <= max_candidates <= MAX_CANDIDATES:
            raise ValueError(f"max_candidates must be between 1 and {MAX_CANDIDATES}")
        if len(user_message) > MAX_MESSAGE_CHARS:
            raise ValueError(
                f"user_message cannot exceed {MAX_MESSAGE_CHARS} characters"
            )

        candidates: list[MemoryCandidate] = []
        seen: set[str] = set()
        for raw_sentence in _SENTENCE_BOUNDARY.split(user_message):
            content = _LIST_MARKER.sub("", raw_sentence).strip()
            if not content or len(content) > MAX_CANDIDATE_CHARS:
                continue
            match = self._classify(content)
            if match is None:
                continue
            kind, rule = match
            key = _normalized_key(kind, content)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                MemoryCandidate(
                    id=_candidate_id(kind, content),
                    kind=kind,
                    content=content,
                    rule=rule,
                )
            )
            if len(candidates) >= max_candidates:
                break
        return candidates

    @staticmethod
    def _classify(content: str) -> tuple[MemoryKind, str] | None:
        for rule, pattern in _PREFERENCE_RULES:
            if pattern.search(content):
                return "preference", rule
        for rule, pattern in _CONTEXT_RULES:
            if pattern.search(content):
                return "ongoing_context", rule
        if _REMEMBER_RULE.search(content):
            kind: MemoryKind = (
                "preference" if _PREFERENCE_HINT.search(content) else "ongoing_context"
            )
            return kind, "explicit_remember"
        return None


__all__ = [
    "MAX_CANDIDATES",
    "MAX_CANDIDATE_CHARS",
    "MAX_MESSAGE_CHARS",
    "MemoryCandidate",
    "MemoryCandidateExtractor",
    "MemoryKind",
]
