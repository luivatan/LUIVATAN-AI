"""Application-wide error hierarchy.

Expected errors carry plain-language context and a stable machine-readable code.
Technical causes may be retained for trusted diagnostics, while normal UI/API
surfaces use :meth:`ApexError.public_message` and never render chained exception
text or tracebacks.
"""

from __future__ import annotations

import re

UNEXPECTED_ERROR_MESSAGE = (
    "Apex AI encountered an unexpected error. Try again. If the problem continues, "
    "review the application logs."
)

_PUBLIC_DIAGNOSTIC_MESSAGE = (
    "The operation could not be completed; diagnostic details were omitted."
)
_DIAGNOSTIC_RE = re.compile(
    r"(?i:\btraceback\b)|\b[A-Z][A-Za-z0-9_.]*(?:Error|Exception)\b"
)
_URL_RE = re.compile(r"(?i)\b(?:https?|ftp)://[^\s`\"'<>]+")
_WINDOWS_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:[A-Za-z]:[\\/]|\\\\)[^\s`\"'<>|]+"
)
_POSIX_PATH_RE = re.compile(r"(?<![:A-Za-z0-9_])(?:~?/|\.\.?/)[^\s`\"'<>|]+")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[ _-]?key|access[ _-]?token|token|password|secret|authorization)\b"
    r"(\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_TOKEN_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{8,}|"
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})\b"
)


def sanitize_public_text(value: str) -> str:
    """Remove common diagnostic, credential, endpoint, and local-path disclosures."""
    text = str(value)
    if _DIAGNOSTIC_RE.search(text):
        return _PUBLIC_DIAGNOSTIC_MESSAGE
    text = _URL_RE.sub("<configured endpoint>", text)
    text = _WINDOWS_PATH_RE.sub("<local path>", text)
    text = _POSIX_PATH_RE.sub("<local path>", text)
    text = _SECRET_ASSIGNMENT_RE.sub(r"\1\2[redacted]", text)
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    return _TOKEN_RE.sub("[redacted]", text)


class ApexError(Exception):
    """Base class for expected, explainable Apex AI failures."""

    title = "Something went wrong"
    code = "apex_error"
    retryable = False

    def __init__(self, what: str, why: str = "", fix: str = "") -> None:
        self.what = what
        self.why = why
        self.fix = fix
        super().__init__(self.user_message())

    def _format_message(self, *, include_why: bool, sanitize: bool = False) -> str:
        project = sanitize_public_text if sanitize else str
        parts = [self.title, f"WHAT HAPPENED:\n{project(self.what)}"]
        if include_why and self.why:
            parts.append(f"WHY:\n{project(self.why)}")
        if self.fix:
            parts.append(f"HOW TO FIX:\n{project(self.fix)}")
        return "\n\n".join(parts)

    def user_message(self) -> str:
        """Return the established detailed message used by trusted local tools."""
        return self._format_message(include_why=True)

    def public_message(self) -> str:
        """Return an actionable message without diagnostic WHY text or local details."""
        return self._format_message(include_why=False, sanitize=True)

    def public_problem(self) -> dict[str, object]:
        """Return the transport-neutral public representation of this error."""
        return {
            "code": self.code,
            "message": self.public_message(),
            "retryable": self.retryable,
        }


class ConfigurationError(ApexError):
    title = "CONFIGURATION ERROR"
    code = "configuration_error"


class ModelNotFoundError(ApexError):
    title = "MODEL NOT FOUND"
    code = "model_not_found"


class EmbeddingModelNotFoundError(ApexError):
    title = "EMBEDDING MODEL NOT FOUND"
    code = "embedding_model_not_found"


class EmbeddingMismatchError(ApexError):
    title = "EMBEDDING MODEL MISMATCH"
    code = "embedding_mismatch"


class DatabaseError(ApexError):
    title = "DATABASE ERROR"
    code = "database_error"
    retryable = True


class DocumentProcessingError(ApexError):
    title = "DOCUMENT PROCESSING ERROR"
    code = "document_processing_error"


class ProviderError(ApexError):
    title = "LLM PROVIDER ERROR"
    code = "provider_error"
    retryable = True


class RerankerUnavailableError(ApexError):
    title = "RERANKER UNAVAILABLE"
    code = "reranker_unavailable"
    retryable = True


class SecurityError(ApexError):
    title = "SECURITY ERROR"
    code = "security_error"
