"""Consistent, non-diagnostic error responses for Apex API clients."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from apex_ai.core.errors import (
    UNEXPECTED_ERROR_MESSAGE,
    ApexError,
    ConfigurationError,
    DatabaseError,
    DocumentProcessingError,
    EmbeddingMismatchError,
    EmbeddingModelNotFoundError,
    ModelNotFoundError,
    ProviderError,
    RerankerUnavailableError,
    SecurityError,
    sanitize_public_text,
)
from apex_ai.core.logging import get_logger, log_event

log = get_logger("api.errors")

_DEFAULT_MESSAGES = {
    400: "The request could not be processed.",
    401: "Authentication is required for this request.",
    403: "This request is not allowed.",
    404: "The requested resource was not found.",
    405: "That method is not supported for this resource.",
    409: "The request conflicts with the current application state.",
    413: "The uploaded content is too large.",
    415: "The uploaded content type is not supported.",
    422: "Check the submitted fields and try again.",
    429: "Too many requests were received. Try again later.",
    500: UNEXPECTED_ERROR_MESSAGE,
    502: "The configured AI provider could not complete the request. Try again.",
    503: "Apex AI is temporarily unavailable. Try again or review Settings.",
    504: "A dependent service took too long to respond. Try again.",
}

_STATUS_CODES = {
    400: "invalid_request",
    401: "authentication_required",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "upload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    502: "provider_unavailable",
    503: "service_unavailable",
    504: "gateway_timeout",
}


def _default_message(status_code: int) -> str:
    return _DEFAULT_MESSAGES.get(status_code, "The request could not be completed.")


def _default_code(status_code: int) -> str:
    return _STATUS_CODES.get(status_code, "request_failed")


def _is_retryable(status_code: int) -> bool:
    return status_code in {429, 502, 503, 504}


def _apex_status(error: ApexError) -> int:
    if isinstance(error, ProviderError):
        return 502
    if isinstance(
        error,
        (
            ConfigurationError,
            DatabaseError,
            EmbeddingModelNotFoundError,
            RerankerUnavailableError,
        ),
    ):
        return 503
    if isinstance(error, EmbeddingMismatchError):
        return 409
    if isinstance(error, (DocumentProcessingError, ModelNotFoundError, SecurityError)):
        return 400
    return 500


def error_problem(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    fields: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Build the stable public error object used by HTTP and stream transports."""
    problem: dict[str, Any] = {
        "code": code,
        "message": message,
        "retryable": retryable,
    }
    if fields:
        problem["fields"] = fields
    return problem


def problem_from_apex(error: ApexError) -> dict[str, Any]:
    """Convert an expected application error without exposing chained details."""
    return error_problem(
        error.code,
        error.public_message(),
        retryable=error.retryable,
    )


def internal_error_problem() -> dict[str, Any]:
    """Return the generic representation for an unexpected internal failure."""
    return error_problem("internal_error", UNEXPECTED_ERROR_MESSAGE)


class APIError(HTTPException):
    """An explicitly public HTTP error with a stable code and retry hint."""

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        code: str | None = None,
        retryable: bool | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.code = code or _default_code(status_code)
        self.message = message
        self.retryable = _is_retryable(status_code) if retryable is None else retryable
        super().__init__(status_code=status_code, detail=message, headers=headers)

    @classmethod
    def from_apex(
        cls,
        error: ApexError,
        *,
        status_code: int | None = None,
    ) -> APIError:
        return cls(
            status_code or _apex_status(error),
            error.public_message(),
            code=error.code,
            retryable=error.retryable,
        )


def service_not_ready_error() -> APIError:
    """Return the shared failure for routes that require initialized services."""
    return APIError(
        503,
        "Apex AI is not ready. Open Settings to review the configuration.",
        code="service_not_ready",
        retryable=True,
    )


def _response(
    status_code: int,
    problem: dict[str, Any],
    *,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    # ``detail`` remains during the compatibility window for existing API
    # clients. New clients should consume the structured ``error`` object.
    return JSONResponse(
        status_code=status_code,
        content={"detail": problem["message"], "error": problem},
        headers=dict(headers or {}),
    )


async def _http_exception_handler(
    _request: Request,
    error: StarletteHTTPException,
) -> JSONResponse:
    if isinstance(error, APIError):
        problem = error_problem(
            error.code,
            sanitize_public_text(error.message),
            retryable=error.retryable,
        )
    else:
        message = (
            sanitize_public_text(error.detail)
            if error.status_code < 500 and isinstance(error.detail, str)
            else _default_message(error.status_code)
        )
        problem = error_problem(
            _default_code(error.status_code),
            message,
            retryable=_is_retryable(error.status_code),
        )
    return _response(error.status_code, problem, headers=error.headers)


async def _validation_exception_handler(
    _request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    fields = []
    for issue in error.errors()[:20]:
        location = ".".join(str(part) for part in issue.get("loc", ()) if part != "body")
        fields.append(
            {
                "field": location or "request",
                "message": sanitize_public_text(issue.get("msg") or "Invalid value."),
                "code": str(issue.get("type") or "invalid"),
            }
        )
    problem = error_problem(
        "validation_error",
        _default_message(422),
        fields=fields,
    )
    return _response(422, problem)


async def _apex_exception_handler(_request: Request, error: ApexError) -> JSONResponse:
    status_code = _apex_status(error)
    return _response(status_code, problem_from_apex(error))


async def _unexpected_exception_handler(request: Request, error: Exception) -> JSONResponse:
    log_event(
        log,
        logging.ERROR,
        "api.request_failed",
        "Unhandled API request failure",
        exc_info=(type(error), error, error.__traceback__),
        method=request.method,
        http_path=request.url.path,
    )
    return _response(500, internal_error_problem())


def install_error_handlers(app: FastAPI) -> None:
    """Install the shared handlers on one FastAPI application."""
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(ApexError, _apex_exception_handler)
    app.add_exception_handler(Exception, _unexpected_exception_handler)


__all__ = [
    "APIError",
    "error_problem",
    "install_error_handlers",
    "internal_error_problem",
    "problem_from_apex",
    "service_not_ready_error",
]
