"""Phase 6 tests for structured, privacy-aware application logging."""

from __future__ import annotations

import io
import json
import logging
import sys
from pathlib import Path

from apex_ai.core.logging import (
    JsonLogFormatter,
    RedactingFormatter,
    SensitiveDataFilter,
    get_logger,
    log_event,
    preview,
    redact_text,
    redact_value,
    reset_logging,
    setup_logging,
    timed,
)

_PRIVATE_TEXT = "Patient Alice takes medicine 20 mg every morning."
_SECRET = "logging-secret-canary-91ad"


def _managed_handlers() -> list[logging.Handler]:
    return [
        handler
        for handler in logging.getLogger("apex").handlers
        if getattr(handler, "_apex_managed_handler", False)
    ]


def test_text_redaction_covers_common_credential_forms():
    openai_like = "sk-" + "testcredential123456"
    github_like = "ghp_" + "testcredential123456"
    jwt_like = "eyJ" + "abcdefghijk.abcdefghijkl.abcdefghijk"
    source = "\n".join(
        [
            f"APEX_OPENAI_API_KEY={_SECRET}",
            f'password: "{_SECRET} with spaces"',
            f"Authorization: Bearer {_SECRET}",
            f"authorization=Basic {_SECRET}",
            f"https://operator:{_SECRET}@internal.example/v1",
            openai_like,
            github_like,
            jwt_like,
            "-----BEGIN PRIVATE KEY-----\nprivate-material\n-----END PRIVATE KEY-----",
        ]
    )

    redacted = redact_text(source)

    for value in (
        _SECRET,
        "private-material",
        openai_like,
        github_like,
        jwt_like,
    ):
        assert value not in redacted
    assert redacted.count("[REDACTED]") >= 7
    assert "internal.example/v1" in redacted


def test_structured_values_redact_secret_and_private_field_names_recursively():
    payload = redact_value(
        {
            "api_key": _SECRET,
            "APEX_OPENAI_API_KEY": _SECRET,
            "question": _PRIVATE_TEXT,
            "safe_count": 3,
            "nested": {
                "password": _SECRET,
                "note": f"token={_SECRET}",
                "status": "ready",
            },
        }
    )

    assert payload == {
        "api_key": "[REDACTED]",
        "APEX_OPENAI_API_KEY": "[REDACTED]",
        "question": "[PRIVATE]",
        "safe_count": 3,
        "nested": {
            "password": "[REDACTED]",
            "note": "token=[REDACTED]",
            "status": "ready",
        },
    }


def test_preview_is_content_free_and_bounded():
    summary = preview(_PRIVATE_TEXT, limit=80)

    assert _PRIVATE_TEXT not in summary
    assert "private text omitted" in summary
    assert f"chars={len(_PRIVATE_TEXT)}" in summary
    assert len(summary) <= 80


def test_json_formatter_emits_stable_fields_and_redacted_context():
    record = logging.LogRecord(
        name="apex.test",
        level=logging.WARNING,
        pathname=__file__,
        lineno=100,
        msg="Provider failed with password=%s",
        args=(_SECRET,),
        exc_info=None,
    )
    record.event = "provider.request_failed"
    record.context = {
        "provider": "local",
        "question": _PRIVATE_TEXT,
        "access_token": _SECRET,
        "attempt": 2,
    }

    payload = json.loads(JsonLogFormatter().format(record))

    assert payload["schema_version"] == 1
    assert payload["timestamp"].endswith("Z")
    assert payload["level"] == "WARNING"
    assert payload["logger"] == "apex.test"
    assert payload["event"] == "provider.request_failed"
    assert payload["message"] == "Provider failed with password=[REDACTED]"
    assert payload["context"] == {
        "provider": "local",
        "question": "[PRIVATE]",
        "access_token": "[REDACTED]",
        "attempt": 2,
    }
    assert payload["source"]["line"] == 100


def test_exception_formatter_keeps_stack_shape_but_omits_exception_message():
    try:
        raise RuntimeError(f"{_PRIVATE_TEXT} password={_SECRET}")
    except RuntimeError:
        record = logging.LogRecord(
            name="apex.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=130,
            msg="Generation failed",
            args=(),
            exc_info=sys.exc_info(),
        )

    assert SensitiveDataFilter().filter(record)
    human = RedactingFormatter("%(levelname)s %(name)s | %(message)s").format(record)
    structured = json.loads(JsonLogFormatter().format(record))
    rendered = human + json.dumps(structured)

    assert "RuntimeError" in rendered
    assert "message_omitted" in rendered
    assert _PRIVATE_TEXT not in rendered
    assert _SECRET not in rendered
    assert structured["exception"]["frames"]
    assert structured["exception"]["message_omitted"] is True


def test_query_processing_logs_counts_without_question_or_protected_terms():
    from apex_ai.rag.query_processing import QueryProcessor

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = get_logger("rag.query")
    previous_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        processor = QueryProcessor(enabled=True, decompose=True, max_subqueries=2)
        processor.expand(f"Compare {_PRIVATE_TEXT}; explain patient ID PRIVATE-42")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    output = stream.getvalue()
    assert "Prepared" in output
    assert _PRIVATE_TEXT not in output
    assert "PRIVATE-42" not in output


def test_timed_emits_measured_duration_and_status_fields():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    handler.addFilter(SensitiveDataFilter())
    logger = get_logger("timing.test")
    previous_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        with timed(logger, "test operation", level=logging.INFO):
            pass
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    payload = json.loads(stream.getvalue())
    assert payload["event"] == "operation.completed"
    assert payload["context"]["operation"] == "test operation"
    assert payload["context"]["status"] == "ok"
    assert payload["context"]["duration_ms"] >= 0


def test_setup_logging_writes_json_without_duplicates_or_private_values(tmp_path: Path):
    reset_logging()
    try:
        setup_logging(tmp_path, level=logging.CRITICAL, force=True)
        setup_logging(tmp_path, level=logging.CRITICAL)
        assert len(_managed_handlers()) == 2

        logger = get_logger("phase6")
        log_event(
            logger,
            logging.INFO,
            "phase6.ready",
            f"Service ready; api_key={_SECRET}",
            question=_PRIVATE_TEXT,
            document_count=2,
        )
        try:
            raise ValueError(f"{_PRIVATE_TEXT} token={_SECRET}")
        except ValueError:
            log_event(
                logger,
                logging.ERROR,
                "phase6.failed",
                "A controlled test operation failed",
                exc_info=True,
                operation="test",
            )
    finally:
        reset_logging()

    lines = (tmp_path / "apex.log").read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    rendered = "\n".join(lines)

    assert [record["event"] for record in records] == ["phase6.ready", "phase6.failed"]
    assert records[0]["context"]["question"] == "[PRIVATE]"
    assert records[0]["context"]["document_count"] == 2
    assert records[1]["exception"]["type"] == "builtins.ValueError"
    assert _PRIVATE_TEXT not in rendered
    assert _SECRET not in rendered


def test_runtime_failure_log_omits_private_exception_message(settings):
    from apex_ai.runtime import build_services

    def fail_embedding(_settings):
        raise RuntimeError(f"{_PRIVATE_TEXT} password={_SECRET}")

    reset_logging()
    try:
        setup_logging(settings.log_dir, level=logging.CRITICAL, force=True)
        services = build_services(settings, embedding_factory=fail_embedding)
    finally:
        reset_logging()

    rendered = (settings.log_dir / "apex.log").read_text(encoding="utf-8")
    records = [json.loads(line) for line in rendered.splitlines()]
    failure = next(record for record in records if record["event"] == "runtime.startup_failed")
    assert services.ready is False
    assert failure["exception"]["type"] == "builtins.RuntimeError"
    assert _PRIVATE_TEXT not in rendered
    assert _SECRET not in rendered


def test_setup_archives_plain_legacy_log_before_writing_json(tmp_path: Path):
    legacy = tmp_path / "apex.log"
    legacy.write_text("2025-01-01 INFO apex | old plain record\n", encoding="utf-8")

    reset_logging()
    try:
        setup_logging(tmp_path, level=logging.CRITICAL, force=True)
    finally:
        reset_logging()

    archives = list(tmp_path.glob("apex.log.legacy*"))
    assert len(archives) == 1
    assert "old plain record" in archives[0].read_text(encoding="utf-8")
    records = [
        json.loads(line)
        for line in legacy.read_text(encoding="utf-8").splitlines()
    ]
    assert records[0]["event"] == "logging.legacy_log_archived"


def test_file_logging_failure_degrades_to_console(monkeypatch, tmp_path: Path):
    from apex_ai.core import logging as logging_module

    class BrokenFileHandler:
        def __init__(self, *args, **kwargs):
            del args, kwargs
            raise OSError("private filesystem detail")

    reset_logging()
    monkeypatch.setattr(logging_module, "RotatingFileHandler", BrokenFileHandler)
    try:
        setup_logging(tmp_path, level=logging.CRITICAL, force=True)
        handlers = _managed_handlers()
        assert len(handlers) == 1
        assert handlers[0]._apex_role == "console"  # type: ignore[attr-defined]
    finally:
        reset_logging()
