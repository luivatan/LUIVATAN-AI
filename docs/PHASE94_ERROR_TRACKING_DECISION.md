# Apex AI Phase 94 — Error Tracking (Decision)

- **Decided:** 2026-08-30 (America/Chicago)
- **Baseline:** `61a3ebc` (Phase 91, production deployment decision),
  alongside Phase 93 (monitoring decision)
- **Roadmap scope:** "Connect production error tracking and remove
  sensitive data from error reports."
- **Decision:** declined for real execution this pass — documented here
  instead, following the pattern used for Phases 75, 85, 86, 89, 90, and 91.

## Why this can't be done for real here

"Connect production error tracking" describes wiring this application to
a real third-party error-tracking service (Sentry, Rollbar, Bugsnag,
etc.) so exceptions raised in production are captured, grouped, and
alerted on centrally. That needs a real account and a real DSN/API key —
credentials this environment doesn't have and must not invent. Wiring in
an SDK configured with a placeholder or fake DSN would either silently
no-op (giving a false impression the integration works) or error out —
neither is real error tracking, and both would misrepresent this phase
as done when it isn't.

This is the same reasoning as Phase 93 (monitoring): the *destination*
for the reports is an external service this session cannot create an
account for or authenticate against.

## What already exists that real error tracking would build on

The half of this phase's scope that *is* pure engineering — "remove
sensitive data from error reports" — was already built before this
phase, independent of having a real tracking service to send reports to:

- **`apex_ai/core/errors.py`**'s `sanitize_public_text()` strips, before
  any error text reaches a user-facing surface or log:
  - URLs (replaced with `<configured endpoint>`),
  - local filesystem paths, both POSIX and Windows-style (`<local path>`),
  - credential-shaped key/value pairs (`api_key=...`, `password=...`,
    `token=...`, etc. → `[redacted]`),
  - `Authorization: Bearer ...` headers (`Bearer [redacted]`),
  - recognizable API key/token formats (OpenAI-style `sk-...`, GitHub
    `gh_*`, JWTs) via `_TOKEN_RE`, regardless of what key name they were
    under,
  - any text matching a traceback or `SomeError`/`SomeException` pattern
    is replaced wholesale with a generic diagnostic-omitted message
    rather than partially redacted, since a raw traceback can leak
    arbitrary internal detail no regex list can fully anticipate.
- **`ApexError`** (same file) enforces this at the exception-class level:
  `public_message()` always sanitizes; only a separate, explicitly
  trusted-diagnostics path retains the raw `why`.
- **`apex_ai/core/logging.py`** applies "the same credential redaction
  and exception-message omission" (per its own module docstring) to
  *every* log line, both console and the rotating `apex.log` JSON file —
  not just to errors that happen to flow through `ApexError`. It also
  explicitly omits structured fields with common private-content names
  (questions, prompts, answers, document text/filenames) by convention,
  stated directly in that module's docstring as a call-site discipline.

In other words: the sanitization a real error-tracking SDK would need
applied to every event *before* transmission already exists and already
runs on every error this application produces — connecting a real
tracking service later is an additive step (send the same
already-sanitized payload externally too), not a prerequisite piece of
missing engineering.

## What would make this real

1. Create a real account with an error-tracking provider and obtain a
   real DSN/API key.
2. Add the provider's SDK as a dependency, initialize it from a new
   `APEX_ERROR_TRACKING_DSN` setting (unset by default, so local/dev runs
   never send anywhere), and hook it into the existing exception paths
   (`ApexError` handling in `apex_ai/api/errors.py`, and any unhandled
   exception at the FastAPI app boundary).
3. Route reports through `sanitize_public_text()` (or an equivalent
   payload-scrubbing step) before they leave the process, so the same
   redaction guarantee already enforced for logs and public error
   messages also holds for whatever reaches the third-party service.
4. Verify with a real triggered error in a real deployed environment
   that a report actually arrives at the provider and contains no
   secrets, paths, or tokens — the same "provably real" bar Phase 92
   held backups to.

## Deliberately not done in this phase

- No error-tracking SDK dependency added, and no DSN/API key setting
  added to `.env.example` — there is nothing real to point it at yet.
- No fabricated "error tracking connected" claim anywhere in the docs,
  README, or health endpoint.
