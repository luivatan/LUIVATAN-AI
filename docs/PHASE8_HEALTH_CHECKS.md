# Apex AI Phase 8 — Health Checks

- **Completed:** 2026-08-29 (America/Chicago)
- **Baseline:** `7babb46` (Phase 7 API structure)
- **Scope:** make `/health` a genuine, live per-component check instead of a report
  of state captured once at startup.

## Audit findings

`/health` already existed and reported `app`, `provider`, `model`, `embedding_model`,
`long_term_memory`, and (when ready) document/chunk counts. Two real gaps:

1. **`ready` was frozen at startup.** `ApexServices.ready` is `store is not None and
   engine is not None` — true the moment construction succeeds, never re-evaluated.
   If the ChromaDB directory became unreadable, got deleted, or ran out of disk space
   *after* startup, `/health` would keep reporting `ready: true` until something else
   (a real chat request) hit the failure.
2. **No status-code signal.** `/health` always returned `200`, even when
   `ready: false` was in the body. A monitor, container orchestrator, or uptime check
   that only looks at the HTTP status code (the common case) could not tell healthy
   from broken without parsing JSON.

There was no "AI service" component check at all — `provider`/`model` only echoed
configuration, never anything checked live.

## Change

- **Live database probe.** `/health` now calls `store.count()` on every request — a
  real read through the same ChromaDB handle the RAG engine uses — and reports
  `database: {"status": "ok" | "unavailable", "detail": <exception type or reason>}`.
  A failure here does not crash the health route; it downgrades `ready` and is logged
  (`health.database_probe_failed`, exception type only — no message/content, per the
  Phase 6 logging policy).
- **Honest LLM/AI-service status.** `llm: {"configured": bool, "provider": str,
  "note": "..."}` reports whether a model is *configured* (mirrors what `/app-config`
  already computed for `model`, now shared via one `_configured_model_name()` helper
  instead of two copies of the same if/elif chain). It deliberately does **not** claim
  to have verified connectivity — a real reachability check would mean a network call
  (or a paid API request, for OpenAI-compatible providers) on every health poll, which
  is exactly the kind of fake status the roadmap's ground rules forbid. The existing
  design already validates the provider at question time with a specific, actionable
  error; the `note` field says this explicitly so the field can't be misread as a
  live ping.
- **Status code reflects health.** `/health` returns `200` when `ready` (services
  present *and* the live database probe just succeeded) and `503` otherwise, using
  the same `503`/`service_unavailable` convention Phase 5 already established for
  "not ready" everywhere else in the API. `/app-config` (the endpoint the web UI
  actually polls to render its own not-ready banner) is unchanged and still always
  returns `200` — it needs to succeed precisely when the app isn't ready, so the UI
  can show *why*.

## Deliberately not changed

- `services.ready` itself (the property gating `/documents`, `/documents/ingest`,
  `/query`, `/debug/rag`, uploads) still reflects only startup construction, not a
  live DB probe. Adding a live probe to every request on every route would add
  latency and a new failure mode to paths that don't need it; `/health` is the right
  single place for this check, and Phase 93 (Monitoring) is where periodic
  probing/alerting on it belongs.
- No separate liveness vs. readiness routes (`/health/live`, `/health/ready`). Apex
  is a single-process app; one endpoint with an accurate live `ready` and per-component
  detail covers what a container healthcheck or uptime monitor needs.
- No live check for the embedding model. It loads once, eagerly, at startup (offline
  after the model is cached); a failure there already surfaces as `startup_error`
  and there is no ongoing external dependency to re-probe.

## Verification

| Check | Result |
|---|---|
| Full test suite (`python -m pytest tests/ -q`) | 231 passed, 3 skipped |
| New Phase 8 tests: not-ready → `503` with correct body, live DB-failure probe, LLM status field | Included in the 231 |
| Ruff (`apex_ai/api/`, `tests/test_api_ui.py`) | All checks passed |

## Boundaries and remaining unknowns

- The database probe is synchronous and adds one extra `store.count()` call per
  `/health` request; for ChromaDB's local persistent client this is inexpensive, but
  it is not free, and a health endpoint polled very frequently should account for
  that.
- `llm.configured` does not verify the file exists (llama.cpp), the server is running
  (Ollama), or the API key is valid (OpenAI-compatible) — only that a model
  name/path is set. Making this check meaningfully live per provider without adding
  network calls to a health endpoint is unresolved; flagged here rather than faked.
- No historical uptime, alerting, or external monitoring integration — that is
  Phase 93.
