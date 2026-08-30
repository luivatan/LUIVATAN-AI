# Apex AI Phase 93 — Monitoring (Decision)

- **Decided:** 2026-08-30 (America/Chicago)
- **Baseline:** `61a3ebc` (Phase 91, production deployment decision)
- **Roadmap scope:** "Monitor uptime, API failures, model failures,
  database failures, and major application errors."
- **Decision:** declined for real execution this pass — documented here
  instead, following the pattern used for Phases 75, 85, 86, 89, 90, and 91.

## Why this can't be done for real here

"Monitor" means a real, continuously-running external system watching
this application over time — an uptime checker pinging a real public
URL on a schedule, a metrics backend (Prometheus/Grafana, Datadog,
Better Uptime, etc.) collecting and alerting on real time-series data.
None of that can exist inside this sandbox:

- There is no long-lived public deployment to monitor yet (Phase 91,
  declined for the same reason: no real hosting target).
- A real monitoring backend needs a real account and credentials
  (an API key, a webhook URL for alerts) that this environment doesn't
  have and shouldn't fabricate.
- "Uptime" is meaningless to measure from inside the same process that
  would be reporting on itself — a real uptime monitor is external by
  definition.

Standing up a monitoring *integration* against nothing (no real target,
no real account) would produce code that has never actually monitored
anything — the same kind of unverifiable, effectively fake artifact the
roadmap's ground rules warn against.

## What already exists that real monitoring would build on

This wasn't blocked on external monitoring — the application already
exposes what a monitor needs to check:

- **`GET /health`** (`apex_ai/api/server.py`) is exactly the endpoint a
  real uptime/monitoring service would poll. It already reports:
  - overall `ready` state,
  - vector database reachability (a real `services.store.count()` probe,
    not a hardcoded "ok"),
  - LLM provider *configuration* status (explicitly not a live
    connectivity check on every poll — see the docstring on
    `_llm_status()`, which states plainly that faking a "connected"
    result would violate the same no-fake-status rule),
  - embedding model and long-term memory status.
- **Structured JSON logging** (`apex_ai/core/logging.py`) already writes
  every request/error to a rotating `apex.log` file as newline-delimited
  JSON with a stable schema (`LOG_SCHEMA_VERSION`), which is what a real
  log-shipping agent (Vector, Filebeat, a Datadog agent) would tail and
  forward to a monitoring backend — no changes needed on the log-emitting
  side to plug one in.
- **`log_event()`** already includes structured, queryable fields (event
  name, severity) rather than free-text messages, which is what makes
  log-based alerting rules (e.g. "alert on any `health.database_probe_failed`
  event") possible without new instrumentation.

## What would make this real

1. Deploy the application somewhere with a real public URL (Phase 91).
2. Point a real uptime service at `GET /health` on a schedule (e.g. every
   1-5 minutes), alerting on non-`ready` responses or timeouts.
3. Ship `apex.log` to a real log aggregator/metrics backend and define
   alert rules on the structured events already being emitted (database
   probe failures, LLM provider errors, unhandled exceptions).
4. Define real on-call/alerting destinations (email, Slack, PagerDuty)
   — a business decision about who gets paged, not an engineering one.

## Deliberately not done in this phase

- No monitoring-service SDK, API client, or webhook integration wired in
  — there is no real account to configure it against.
- No fabricated "monitoring dashboard" or invented uptime metrics.
- No changes claiming a false monitoring guarantee anywhere in the docs
  or README.
