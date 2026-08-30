"""Phase 80: retry-with-backoff for transient provider failures. Every
test controls (or disables) sleeping - no real waiting, no real network."""

from __future__ import annotations

import requests

from apex_ai.llm.retry import call_with_retries, is_retryable


def _http_error(status_code: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status_code
    return requests.HTTPError(response=response)


def test_is_retryable_classifies_connection_and_timeout_errors():
    assert is_retryable(requests.ConnectionError("boom")) is True
    assert is_retryable(requests.Timeout("boom")) is True


def test_is_retryable_classifies_http_status_codes():
    for status in (429, 500, 502, 503, 504):
        assert is_retryable(_http_error(status)) is True
    for status in (400, 401, 403, 404, 422):
        assert is_retryable(_http_error(status)) is False


def test_is_retryable_rejects_unrelated_exceptions():
    assert is_retryable(ValueError("not a request error")) is False


def test_call_with_retries_succeeds_on_first_attempt_without_sleeping():
    sleeps = []
    calls = {"count": 0}

    def func():
        calls["count"] += 1
        return "ok"

    result = call_with_retries(func, sleep=sleeps.append)

    assert result == "ok"
    assert calls["count"] == 1
    assert sleeps == []


def test_call_with_retries_retries_a_transient_failure_then_succeeds():
    sleeps = []
    attempts = {"count": 0}

    def func():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise requests.ConnectionError("transient")
        return "recovered"

    result = call_with_retries(func, max_attempts=5, base_delay_seconds=1.0, sleep=sleeps.append)

    assert result == "recovered"
    assert attempts["count"] == 3
    assert sleeps == [1.0, 2.0]  # exponential backoff: 1.0 * 2**0, 1.0 * 2**1


def test_call_with_retries_gives_up_after_max_attempts():
    sleeps = []
    attempts = {"count": 0}

    def func():
        attempts["count"] += 1
        raise requests.ConnectionError("always fails")

    try:
        call_with_retries(func, max_attempts=3, base_delay_seconds=0.1, sleep=sleeps.append)
        raise AssertionError("expected the final ConnectionError to propagate")
    except requests.ConnectionError:
        pass

    assert attempts["count"] == 3
    assert len(sleeps) == 2  # slept between attempts 1->2 and 2->3, not after the last failure


def test_call_with_retries_does_not_retry_a_non_retryable_http_error():
    sleeps = []
    attempts = {"count": 0}

    def func():
        attempts["count"] += 1
        raise _http_error(404)

    try:
        call_with_retries(func, max_attempts=5, sleep=sleeps.append)
        raise AssertionError("expected the 404 to propagate immediately")
    except requests.HTTPError as error:
        assert error.response.status_code == 404

    assert attempts["count"] == 1  # never retried
    assert sleeps == []


def test_call_with_retries_does_not_retry_a_non_request_exception():
    """Only requests.RequestException subclasses are ever retried - a
    genuine programming error must never be silently retried/hidden."""
    attempts = {"count": 0}

    def func():
        attempts["count"] += 1
        raise ValueError("not a network error")

    try:
        call_with_retries(func, max_attempts=5, sleep=lambda _: None)
        raise AssertionError("expected ValueError to propagate")
    except ValueError:
        pass

    assert attempts["count"] == 1


def test_max_attempts_one_disables_retries():
    attempts = {"count": 0}

    def func():
        attempts["count"] += 1
        raise requests.ConnectionError("fails")

    try:
        call_with_retries(func, max_attempts=1, sleep=lambda _: None)
        raise AssertionError("expected the error to propagate on the first attempt")
    except requests.ConnectionError:
        pass

    assert attempts["count"] == 1
