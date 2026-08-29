# Apex AI Phase 58 — API Security

- **Completed:** 2026-08-29 (America/Chicago)
- **Baseline:** `56ddd71` (Phase 57 file security)
- **Scope:** the roadmap names three things — validation, rate limiting
  "where appropriate," and secure CORS — plus general abuse protection. This
  phase audits validation (already substantial via FastAPI/Pydantic across
  every route), and adds the two pieces that didn't exist yet: rate limiting
  and explicit, deny-by-default CORS configuration.

## What was already true (inspected, not assumed)

Every request body in the API is a Pydantic model with real field
constraints — `SignupRequest`/`LoginRequest` bound email/password length,
`IngestRequest`/`QueryRequest` require non-empty strings, feedback values are
constrained to an enum, and FastAPI's `RequestValidationError` handler
(`apex_ai/api/errors.py`) already turns any violation into a structured
`422` with per-field messages rather than a raw traceback. Upload validation
(size, type, filename) was audited and hardened in Phase 57. This phase
didn't find a gap in validation itself — the gap was in what happens *after*
a request validates: nothing stopped a client from sending an unbounded
number of valid requests.

## Rate limiting

`apex_ai/api/rate_limit.py` — a `SlidingWindowLimiter` (a per-key `deque` of
timestamps, evaluated and trimmed on every call) wrapped in a
`RateLimitMiddleware`. Design choices:

- **In-memory, not Redis.** Consistent with the project's offline-first,
  no-external-service posture (the same reasoning that chose SQLite sessions
  over a signed token scheme in Phase 52, and an in-memory `GenerationManager`
  for stream cancellation). It resets on restart, which is an accepted
  tradeoff: the threat model is a client hammering the API within one
  process's uptime, not surviving restarts or coordinating across a fleet of
  workers this single-process app doesn't have.
- **Two budgets, not one.** A general budget (`APEX_RATE_LIMIT_PER_MINUTE`,
  default 120/minute) covers ordinary API traffic; `/auth/login` and
  `/auth/signup` get a much stricter budget
  (`APEX_AUTH_RATE_LIMIT_PER_MINUTE`, default 10/minute) since they're the
  classic brute-force/credential-stuffing target and deserve a tighter leash
  than chat or document routes. `test_auth_routes_have_a_stricter_limit_than_general_traffic`
  proves the strict budget bites even when the general budget is nowhere
  close to exhausted.
- **Static assets and docs pages are exempt.** `/assets/*`, `/api/docs`,
  `/docs`, `/openapi.json` serve fixed, non-sensitive content; limiting them
  would only risk breaking a normal page load (a browser fetching CSS/JS on
  every navigation) without stopping any real abuse.
- **Keyed by client IP**, with the same caveat already documented for the
  session cookie's `Secure` flag (Phase 51-53): a reverse proxy that doesn't
  forward the real client address makes the limit coarser (everyone behind
  the proxy shares one budget), not absent — an availability tradeoff, not a
  security hole.
- **The response shape matches every other API error.** The middleware
  builds its `429` body with `apex_ai.api.errors.error_problem()`, the same
  builder every other route's error path uses, so a client parsing
  `{"error": {"code": "rate_limited", "retryable": true, ...}}` doesn't need
  a special case for rate-limit responses specifically.
- **Configurable, including off.** `APEX_RATE_LIMIT_ENABLED=0` disables it
  entirely — for a deployment that wants to defer rate limiting to a reverse
  proxy or API gateway instead of doing it in-process.

## CORS

No `CORSMiddleware` was installed before this phase — meaning cross-origin
browser requests were already blocked by default (browsers require an
explicit `Access-Control-Allow-Origin` to let a page read a cross-origin
response), but this was an *implicit* consequence of never adding CORS
support, not a *documented, deliberate, configurable* security posture. This
phase makes it explicit: `APEX_CORS_ALLOWED_ORIGINS` (comma-separated,
empty by default) controls whether `CORSMiddleware` is installed at all.
Empty means exactly what it did before this phase — no CORS headers, same-
origin only — but now it's a documented default rather than an accident of
omission, and a deployment that genuinely needs a separately-hosted frontend
can turn it on with an explicit allowlist rather than reaching for
`allow_origins=["*"]`.

## Files

- `apex_ai/api/rate_limit.py` (new) — `SlidingWindowLimiter`,
  `RateLimitMiddleware`, `install_rate_limiting()`.
- `apex_ai/api/server.py` — wires `install_rate_limiting()` and, when
  `cors_allowed_origins` is set, `CORSMiddleware` into `create_api()`, so
  both apply regardless of `include_web`.
- `apex_ai/config/settings.py` — `rate_limit_enabled`,
  `rate_limit_requests_per_minute`, `auth_rate_limit_requests_per_minute`,
  `cors_allowed_origins`, plus their `APEX_*` env var loaders.
- `tests/conftest.py` — the shared `settings` fixture sets
  `rate_limit_enabled=False`, since the full test suite legitimately makes
  far more than 120 requests against one shared `TestClient` within a single
  run; rate limiting itself is exercised by its own dedicated fixture with
  its own small, deterministic limits.
- `tests/test_api_security.py` (new) — general-limit `429`, the stricter
  auth-route budget, static-asset exemption, the disabled-by-default no-op,
  and both CORS states (absent by default, present and origin-matched when
  configured).

## Verification

| Check | Result |
|---|---|
| Full test suite (`python3 -m pytest -q`) | 306 passed, 3 skipped |
| `tests/test_api_security.py` | 6 passed |
| `ruff check` on every new/touched file | clean (the pre-existing `tests/conftest.py` baseline findings are unchanged) |

## Deliberately not done in this phase

- **No distributed/shared rate-limit store.** Correct for this application's
  single-process deployment model; a horizontally-scaled deployment would
  need a shared store (Redis, etc.) instead, which would also mean
  reintroducing exactly the external-service dependency this project has
  consistently avoided elsewhere.
- **No IP allow/deny-listing or WAF-style abuse heuristics.** Out of scope
  for "rate limiting where appropriate" — a real production deployment
  facing hostile traffic belongs behind a proper edge/WAF layer (Phase 91+,
  Production & Reliability), not reimplemented inside the application.
- **No per-account (as opposed to per-IP) rate limiting.** IP-based limiting
  catches the pre-authentication case (login/signup brute-forcing) that
  per-account limiting cannot, since an attacker guessing passwords has no
  account yet. Layering a per-account limit on top of authenticated routes
  was considered and left out as not clearly "appropriate" yet — nothing in
  the roadmap or the current threat model calls for it, and adding it
  speculatively would be exactly the kind of complexity-for-its-own-sake the
  roadmap's development rules warn against.
