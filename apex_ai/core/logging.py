"""Privacy-aware structured logging for Apex AI.

The console remains concise and human-readable. The rotating ``apex.log`` file is
newline-delimited JSON so operators can search stable fields without scraping prose.
Both handlers apply the same credential redaction and exception-message omission before
anything is emitted.

Application code must still avoid placing questions, prompts, answers, document text, or
document filenames in log messages. Structured fields with common private-content names
are omitted defensively, but safe logging starts at the call site.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
import traceback
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

LOG_SCHEMA_VERSION = 1

_REDACTED = "[REDACTED]"
_PRIVATE = "[PRIVATE]"
_MANAGED_HANDLER = "_apex_managed_handler"
_CONFIG_LOCK = threading.RLock()
_CONFIGURED = False
_CONFIGURED_TARGET: Path | None = None

_SECRET_KEY_PARTS = {
    "authorization",
    "credential",
    "credentials",
    "cookie",
    "password",
    "passwd",
    "secret",
}
_SECRET_KEYS = {
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "id_token",
    "token",
}
_PRIVATE_KEYS = {
    "answer",
    "chunk_text",
    "content",
    "conversation",
    "document_name",
    "document_text",
    "file_name",
    "file_path",
    "filename",
    "history",
    "input",
    "messages",
    "model_path",
    "output",
    "path",
    "prompt",
    "queries",
    "query",
    "question",
    "request_body",
    "response",
    "response_body",
    "source_text",
    "text",
}

_SECRET_NAME = (
    r"(?:[A-Za-z0-9_.-]*(?:api[_-]?key|password|passwd|secret|credential)"
    r"[A-Za-z0-9_.-]*|access[_-]?token|refresh[_-]?token|id[_-]?token|token|"
    r"authorization|cookie)"
)
_QUOTED_SECRET_RE = re.compile(
    rf"(?i)(\b{_SECRET_NAME}\b\s*[:=]\s*)([\"'])(.*?)(\2)"
)
_PLAIN_SECRET_RE = re.compile(
    rf"(?i)(\b{_SECRET_NAME}\b\s*[:=]\s*)(?:(?:Bearer|Basic)\s+)?[^\s,;}}&]+"
)
_AUTH_RE = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+")
_URL_CREDENTIAL_RE = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@")
_KNOWN_TOKEN_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{8,}|"
    r"AKIA[A-Z0-9]{16}|xox[baprs]-[A-Za-z0-9-]{8,})\b"
)
_JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)
_PEM_RE = re.compile(
    r"-----BEGIN [^-\r\n]+-----.*?-----END [^-\r\n]+-----",
    re.DOTALL,
)
_EVENT_RE = re.compile(r"[^a-z0-9_.-]+")

_STANDARD_RECORD_KEYS = frozenset(
    set(logging.makeLogRecord({}).__dict__)
    | {"asctime", "message", "event", "context"}
)


def _normalized_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_")


def _is_secret_key(key: object) -> bool:
    normalized = _normalized_key(key)
    parts = set(normalized.split("_"))
    secret_suffix = any(normalized.endswith(f"_{item}") for item in _SECRET_KEYS)
    return normalized in _SECRET_KEYS or secret_suffix or bool(parts & _SECRET_KEY_PARTS)


def _is_private_key(key: object) -> bool:
    return _normalized_key(key) in _PRIVATE_KEYS


def redact_text(value: object) -> str:
    """Redact common credential forms from arbitrary text.

    This is a final safety net, not permission to log private user or document content.
    """
    text = str(value)
    text = _PEM_RE.sub("[REDACTED PRIVATE KEY]", text)
    text = _URL_CREDENTIAL_RE.sub(r"\1[REDACTED]@", text)
    text = _QUOTED_SECRET_RE.sub(r"\1\2[REDACTED]\2", text)
    text = _PLAIN_SECRET_RE.sub(r"\1[REDACTED]", text)
    text = _AUTH_RE.sub(lambda match: f"{match.group(1)} {_REDACTED}", text)
    text = _KNOWN_TOKEN_RE.sub(_REDACTED, text)
    return _JWT_RE.sub(_REDACTED, text)


def redact_value(value: object, *, key: object = "", _depth: int = 0) -> Any:
    """Return a JSON-safe recursively redacted representation."""
    if _is_secret_key(key):
        return _REDACTED
    if _is_private_key(key):
        return _PRIVATE
    if _depth >= 8:
        return "[MAX_DEPTH]"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, Mapping):
        return {
            redact_text(item_key): redact_value(item, key=item_key, _depth=_depth + 1)
            for item_key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact_value(item, _depth=_depth + 1) for item in value]
    if isinstance(value, BaseException):
        return f"<{type(value).__module__}.{type(value).__name__}; message omitted>"
    return redact_text(value)


def preview(text: str, limit: int = 120) -> str:
    """Return content-free metadata for compatibility with the former preview helper."""
    summary = f"<private text omitted; chars={len(str(text))}>"
    return summary[: max(0, int(limit))]


def _event_name(value: object, logger_name: str = "apex") -> str:
    raw = redact_text(value).strip().casefold()
    event = _EVENT_RE.sub(".", raw).strip(".")[:96]
    if event and _REDACTED.casefold().strip("[]") not in event:
        return event
    fallback = logger_name.removeprefix("apex.").casefold()
    fallback = _EVENT_RE.sub(".", fallback).strip(".") or "application"
    return f"{fallback}.log"


def _qualified_exception_name(error_type: type[BaseException]) -> str:
    return f"{error_type.__module__}.{error_type.__name__}"


def _safe_frames(tb) -> list[dict[str, object]]:
    if tb is None:
        return []
    return [
        {
            "file": redact_text(frame.filename),
            "line": frame.lineno,
            "function": redact_text(frame.name),
        }
        for frame in traceback.extract_tb(tb)
    ]


def _exception_payload(exc_info) -> dict[str, Any] | None:
    if not exc_info:
        return None
    error_type, error, tb = exc_info
    if not isinstance(error_type, type) or not isinstance(error, BaseException):
        return None
    payload: dict[str, Any] = {
        "type": _qualified_exception_name(error_type),
        "message_omitted": True,
        "frames": _safe_frames(tb),
    }
    seen = {id(error)}
    current = error
    target = payload
    for _ in range(5):
        cause = current.__cause__
        relation = "cause"
        if cause is None and not current.__suppress_context__:
            cause = current.__context__
            relation = "context"
        if cause is None or id(cause) in seen:
            break
        seen.add(id(cause))
        nested = {
            "relation": relation,
            "type": _qualified_exception_name(type(cause)),
            "message_omitted": True,
            "frames": _safe_frames(cause.__traceback__),
        }
        target["caused_by"] = nested
        target = nested
        current = cause
    return payload


def _safe_record_message(record: logging.LogRecord) -> str:
    try:
        return redact_text(record.getMessage())
    except Exception:  # noqa: BLE001 - hostile formatter boundary
        return "A log message could not be formatted."


def _safe_exception_text(exc_info) -> str:
    payload = _exception_payload(exc_info)
    if payload is None:
        return ""
    lines = ["Exception stack (messages omitted):"]
    current = payload
    while current:
        relation = current.get("relation")
        prefix = f"{relation}: " if relation else ""
        lines.append(f"{prefix}{current['type']}")
        for frame in current.get("frames", []):
            lines.append(
                f"  at {frame['file']}:{frame['line']} in {frame['function']}"
            )
        current = current.get("caused_by")
    return "\n".join(lines)


class SensitiveDataFilter(logging.Filter):
    """Sanitize a record before either managed handler sees it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _safe_record_message(record)
        record.args = ()
        if isinstance(getattr(record, "context", None), Mapping):
            record.context = redact_value(record.context)
        if hasattr(record, "event"):
            record.event = _event_name(record.event, record.name)
        for key in set(record.__dict__) - _STANDARD_RECORD_KEYS:
            if key.startswith("_"):
                continue
            record.__dict__[key] = redact_value(record.__dict__[key], key=key)
        if record.exc_info:
            record.exc_text = _safe_exception_text(record.exc_info)
        if record.stack_info:
            record.stack_info = "Stack information omitted."
        return True


class RedactingFormatter(logging.Formatter):
    """Human-readable UTC formatter with safe exception rendering."""

    converter = time.gmtime

    def format(self, record: logging.LogRecord) -> str:
        record.msg = _safe_record_message(record)
        record.args = ()
        if record.exc_info:
            record.exc_text = _safe_exception_text(record.exc_info)
        if record.stack_info:
            record.stack_info = "Stack information omitted."
        rendered = super().format(record)
        return redact_text(rendered)


class JsonLogFormatter(logging.Formatter):
    """Render one stable JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, timezone.utc).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
        payload: dict[str, Any] = {
            "schema_version": LOG_SCHEMA_VERSION,
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "event": _event_name(getattr(record, "event", ""), record.name),
            "message": _safe_record_message(record),
            "source": {
                "module": record.module,
                "function": record.funcName,
                "line": record.lineno,
            },
        }

        context: dict[str, Any] = {}
        supplied = getattr(record, "context", None)
        if isinstance(supplied, Mapping):
            context.update(redact_value(supplied))
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_KEYS or key.startswith("_"):
                continue
            context[redact_text(key)] = redact_value(value, key=key)
        if context:
            payload["context"] = context

        exception = _exception_payload(record.exc_info)
        if exception is not None:
            payload["exception"] = exception
        if record.stack_info:
            payload["stack_omitted"] = True

        try:
            return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):  # defensive: logging must not break the app
            fallback = {
                "schema_version": LOG_SCHEMA_VERSION,
                "timestamp": timestamp,
                "level": "ERROR",
                "logger": "apex.logging",
                "event": "logging.serialization_failed",
                "message": "A structured log record could not be serialized.",
            }
            return json.dumps(fallback, separators=(",", ":"))


def _archive_legacy_log(log_path: Path) -> Path | None:
    """Move a pre-JSON Apex log aside so the active file remains valid JSON Lines."""
    if not log_path.is_file() or log_path.stat().st_size == 0:
        return None
    first_line = ""
    with log_path.open("r", encoding="utf-8", errors="replace") as source:
        for line in source:
            if line.strip():
                first_line = line
                break
    try:
        payload = json.loads(first_line)
    except (TypeError, ValueError):
        payload = None
    if isinstance(payload, dict) and payload.get("schema_version") == LOG_SCHEMA_VERSION:
        return None

    archive = log_path.with_name(f"{log_path.name}.legacy")
    index = 1
    while archive.exists():
        archive = log_path.with_name(f"{log_path.name}.legacy.{index}")
        index += 1
    log_path.replace(archive)
    return archive


def _managed_handlers(logger: logging.Logger) -> list[logging.Handler]:
    return [handler for handler in logger.handlers if getattr(handler, _MANAGED_HANDLER, False)]


def reset_logging() -> None:
    """Close handlers installed by :func:`setup_logging` (primarily for tests/reloaders)."""
    global _CONFIGURED, _CONFIGURED_TARGET
    with _CONFIG_LOCK:
        root = logging.getLogger("apex")
        for handler in _managed_handlers(root):
            root.removeHandler(handler)
            handler.close()
        _CONFIGURED = False
        _CONFIGURED_TARGET = None


def setup_logging(
    log_dir: Path,
    level: int = logging.INFO,
    *,
    force: bool = False,
) -> None:
    """Idempotently configure safe console and rotating JSON-file handlers.

    If file logging cannot be initialized, console logging remains available and startup
    continues. ``force`` is intended for test isolation and development reloaders.
    """
    global _CONFIGURED, _CONFIGURED_TARGET
    target = Path(log_dir).expanduser()
    with _CONFIG_LOCK:
        root = logging.getLogger("apex")
        existing = _managed_handlers(root)
        if existing and not force:
            for handler in existing:
                if getattr(handler, "_apex_role", "") == "console":
                    handler.setLevel(level)
            _CONFIGURED = True
            return
        if existing:
            reset_logging()

        root.setLevel(logging.DEBUG)
        root.propagate = False
        safety_filter = SensitiveDataFilter()

        console = logging.StreamHandler()
        console.setLevel(level)
        console.setFormatter(
            RedactingFormatter(
                "%(asctime)s %(levelname)-7s %(name)s | %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%SZ",
            )
        )
        console.addFilter(safety_filter)
        setattr(console, _MANAGED_HANDLER, True)
        console._apex_role = "console"  # type: ignore[attr-defined]
        root.addHandler(console)

        try:
            target.mkdir(parents=True, exist_ok=True)
            log_path = target / "apex.log"
            legacy_archive = _archive_legacy_log(log_path)
            file_handler = RotatingFileHandler(
                log_path,
                maxBytes=2_000_000,
                backupCount=3,
                encoding="utf-8",
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(JsonLogFormatter())
            file_handler.addFilter(safety_filter)
            setattr(file_handler, _MANAGED_HANDLER, True)
            file_handler._apex_role = "file"  # type: ignore[attr-defined]
            root.addHandler(file_handler)
            _CONFIGURED_TARGET = target
            if legacy_archive is not None:
                root.warning(
                    "Archived a legacy plain-text log before enabling JSON Lines",
                    extra={"event": "logging.legacy_log_archived"},
                )
        except OSError:
            root.warning(
                "File logging is unavailable; continuing with console logging.",
                extra={"event": "logging.file_unavailable"},
            )
            _CONFIGURED_TARGET = None

        for noisy in (
            "chromadb",
            "urllib3",
            "httpx",
            "httpcore",
            "sentence_transformers",
            "gradio",
            "fontTools",
            "matplotlib",
            "PIL",
        ):
            logging.getLogger(noisy).setLevel(logging.WARNING)

        _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced child logger (``ingest`` becomes ``apex.ingest``)."""
    if not name.startswith("apex"):
        name = f"apex.{name}"
    return logging.getLogger(name)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    message: str,
    *,
    exc_info: object = None,
    **fields: object,
) -> None:
    """Emit a structured event while keeping a readable console message."""
    logger.log(
        level,
        message,
        extra={"event": _event_name(event, logger.name), "context": fields},
        exc_info=exc_info,
        stacklevel=2,
    )


@contextmanager
def timed(logger: logging.Logger, label: str, level: int = logging.DEBUG):
    """Measure one operation and emit status plus monotonic duration."""
    start = time.perf_counter()
    try:
        yield
    except BaseException:
        duration_ms = round((time.perf_counter() - start) * 1000, 3)
        log_event(
            logger,
            level,
            "operation.failed",
            f"{label} failed after {duration_ms:.3f} ms",
            operation=label,
            duration_ms=duration_ms,
            status="failed",
        )
        raise
    else:
        duration_ms = round((time.perf_counter() - start) * 1000, 3)
        log_event(
            logger,
            level,
            "operation.completed",
            f"{label} completed in {duration_ms:.3f} ms",
            operation=label,
            duration_ms=duration_ms,
            status="ok",
        )


__all__ = [
    "LOG_SCHEMA_VERSION",
    "JsonLogFormatter",
    "RedactingFormatter",
    "SensitiveDataFilter",
    "get_logger",
    "log_event",
    "preview",
    "redact_text",
    "redact_value",
    "reset_logging",
    "setup_logging",
    "timed",
]
