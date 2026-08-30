"""Retry-with-backoff for transient provider failures (Phase 80).

Only genuinely transient failures are retried: a connection error, a
request timeout, or an HTTP 429/5xx server-side response. A definitively
non-transient failure (401 unauthorized, 404 not found, a malformed
request - anything in the 4xx range other than 429) is never retried:
retrying it wastes time and risks hiding a real configuration problem
behind repeated identical failures instead of surfacing it.

Retries only ever cover establishing the response (the ``requests.post``
call itself, plus the status-code check) - never token iteration. A
stream that has already yielded tokens to the caller must never be
retried transparently: the caller would see duplicated or corrupted
output. Since every provider calls this once, before any streamed content
is read, retrying here is safe for both streaming and non-streaming calls.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

import requests

from apex_ai.core.logging import get_logger

log = get_logger("llm.retry")

T = TypeVar("T")

RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def is_retryable(error: Exception) -> bool:
    """Whether ``error`` looks like a transient failure worth retrying."""
    if isinstance(error, requests.HTTPError):
        response = error.response
        return response is not None and response.status_code in RETRYABLE_STATUS_CODES
    return isinstance(error, (requests.ConnectionError, requests.Timeout))


def call_with_retries(
    func: Callable[[], T],
    *,
    max_attempts: int = 3,
    base_delay_seconds: float = 0.5,
    provider_name: str = "provider",
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call ``func()``, retrying with exponential backoff on a transient
    failure (see :func:`is_retryable`).

    Re-raises the triggering ``requests.RequestException`` unchanged once
    attempts are exhausted, or immediately for a non-retryable failure -
    the caller's existing error-wrapping logic is untouched either way.
    ``max_attempts=1`` disables retries entirely (a single attempt, no
    backoff), useful for fail-fast deployments or tests.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return func()
        except requests.RequestException as error:
            if attempt >= max_attempts or not is_retryable(error):
                raise
            delay = base_delay_seconds * (2 ** (attempt - 1))
            log.warning(
                "%s request failed (attempt %d/%d); retrying in %.1fs (error_type=%s)",
                provider_name,
                attempt,
                max_attempts,
                delay,
                type(error).__name__,
            )
            sleep(delay)


__all__ = ["RETRYABLE_STATUS_CODES", "call_with_retries", "is_retryable"]
