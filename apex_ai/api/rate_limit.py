"""In-memory API rate limiting (Phase 58).

Offline-first, single-process design: a per-client sliding window kept in
memory, no Redis or external service. It resets on restart, which is an
accepted tradeoff - the threat model here is a client hammering the API
within one process's uptime (brute-forcing a password, scripting abusive
traffic), not surviving restarts or coordinating across a fleet of workers.

Static assets and the OpenAPI/Swagger pages are exempt: they serve fixed,
non-sensitive content and rate-limiting them would only risk breaking a
normal page load, not stopping abuse.
"""

from __future__ import annotations

import time
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from apex_ai.api.errors import error_problem

_EXEMPT_PREFIXES = ("/assets", "/api/docs", "/docs", "/openapi.json")

# The classic brute-force / credential-stuffing target: a much tighter
# budget than the rest of the API.
_STRICT_PATHS = frozenset({"/auth/login", "/auth/signup"})


class SlidingWindowLimiter:
    """Fixed-capacity sliding window per key, evaluated on each call."""

    def __init__(self, *, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits.setdefault(key, deque())
        cutoff = now - self.window_seconds
        while hits and hits[0] < cutoff:
            hits.popleft()
        if len(hits) >= self.max_requests:
            return False
        hits.append(now)
        return True


def _client_key(request: Request) -> str:
    """Best-effort client identity for rate-limiting purposes only - not an
    authentication signal. A deployment behind a reverse proxy that does not
    forward the real client address will rate-limit by the proxy's address
    instead of per real client; that is an availability tradeoff, not a
    security one (the limit still applies, just coarsely)."""
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        general_limiter: SlidingWindowLimiter,
        strict_limiter: SlidingWindowLimiter,
    ) -> None:
        super().__init__(app)
        self.general_limiter = general_limiter
        self.strict_limiter = strict_limiter

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith(_EXEMPT_PREFIXES):
            return await call_next(request)

        limiter = self.strict_limiter if path in _STRICT_PATHS else self.general_limiter
        key = _client_key(request)
        if not limiter.allow(key):
            problem = error_problem(
                "rate_limited",
                "Too many requests. Slow down and try again shortly.",
                retryable=True,
            )
            return JSONResponse(
                status_code=429,
                content={"detail": problem["message"], "error": problem},
                headers={"Retry-After": str(int(limiter.window_seconds))},
            )
        return await call_next(request)


def install_rate_limiting(app, settings) -> None:
    """Attach the middleware if enabled. A no-op otherwise, so a deployment
    that wants to disable it (or defer to a reverse proxy's own limiter)
    can, via APEX_RATE_LIMIT_ENABLED=0."""
    if not settings.rate_limit_enabled:
        return
    app.add_middleware(
        RateLimitMiddleware,
        general_limiter=SlidingWindowLimiter(
            max_requests=settings.rate_limit_requests_per_minute,
            window_seconds=60.0,
        ),
        strict_limiter=SlidingWindowLimiter(
            max_requests=settings.auth_rate_limit_requests_per_minute,
            window_seconds=60.0,
        ),
    )


__all__ = ["RateLimitMiddleware", "SlidingWindowLimiter", "install_rate_limiting"]
