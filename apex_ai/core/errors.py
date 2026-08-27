"""Application-wide error hierarchy.

Every error that is *meant* to reach a user carries three fields:

- ``what``  — what happened, in plain language
- ``why``   — the likely cause
- ``fix``   — concrete steps to resolve it

Technical details go to the log; the user sees ``user_message()``.
This replaces the old pattern of dumping raw tracebacks into chat boxes.
"""

from __future__ import annotations


class ApexError(Exception):
    """Base class for all expected, explainable Apex AI errors."""

    title = "Something went wrong"

    def __init__(self, what: str, why: str = "", fix: str = "") -> None:
        self.what = what
        self.why = why
        self.fix = fix
        super().__init__(self.user_message())

    def user_message(self) -> str:
        parts = [f"{self.title}", "", f"WHAT HAPPENED:\n{self.what}"]
        if self.why:
            parts.append(f"WHY:\n{self.why}")
        if self.fix:
            parts.append(f"HOW TO FIX:\n{self.fix}")
        return "\n\n".join(parts)


class ConfigurationError(ApexError):
    title = "CONFIGURATION ERROR"


class ModelNotFoundError(ApexError):
    title = "MODEL NOT FOUND"


class EmbeddingModelNotFoundError(ApexError):
    title = "EMBEDDING MODEL NOT FOUND"


class EmbeddingMismatchError(ApexError):
    title = "EMBEDDING MODEL MISMATCH"


class DatabaseError(ApexError):
    title = "DATABASE ERROR"


class DocumentProcessingError(ApexError):
    title = "DOCUMENT PROCESSING ERROR"


class ProviderError(ApexError):
    title = "LLM PROVIDER ERROR"


class RerankerUnavailableError(ApexError):
    title = "RERANKER UNAVAILABLE"


class SecurityError(ApexError):
    title = "SECURITY ERROR"
