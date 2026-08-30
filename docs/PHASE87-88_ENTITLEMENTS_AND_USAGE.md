# Apex AI Phase 87-88 — Entitlements & Usage Tracking

- **Completed:** 2026-08-30 (America/Chicago)
- **Baseline:** `4ca4dcd`, immediately following Phase 86 (Subscription Webhooks decision)
- **Scope:** Phase 87 — "Enforce plan limits on the backend." Phase 88 —
  "Track messages, storage, model usage, and other billable/limited
  resources." Documented together: enforcement and tracking are two sides
  of the same coin — `check_rate()` (Phase 81's `EntitlementService`) reads
  exactly what `UsageStore.record()` writes, so building one without the
  other would leave half a working system.

## What actually got wired, and where

Phase 81-84 built the plan/entitlement/usage architecture without
connecting it to anything live. This phase closes that gap for real, at
every place in the existing API that creates something a plan limits:

| Resource | Route | Check | Records usage? |
|---|---|---|---|
| Documents | `POST /documents/upload` | capacity, against `IngestionService.stats()["documents"]` | no (capacity, not rate) |
| Storage | `POST /documents/upload` | capacity, against a new `IngestionService.storage_bytes()` | no |
| Collections | `POST /collections` | capacity, against `CollectionStore.list()` length | no |
| Projects | `POST /projects` | capacity, against `ProjectStore.list()` length | no |
| Messages | `POST /chat/stream` (new messages only, never `regenerate`) | rate, against `UsageStore.total_this_month()` | yes, on success |

A blocked request returns **HTTP 402** with `code: "plan_limit_exceeded"`
and a human-readable reason (`EntitlementResult.reason`, e.g. "The Free
plan allows up to 3 collections; 3 are already in use.") — a new shared
`entitlement_error()` helper in `apex_ai/api/errors.py` builds this
consistently across all four routers. 402 was chosen deliberately over the
already-used 429 (Phase 58's unrelated per-IP API rate limiter) to keep
"you need to upgrade your plan" distinct from "you're calling the API too
fast" — they need different user responses.

**`IngestionService.storage_bytes(user_id)`** sums real on-disk file sizes
for every document the account owns — not a stored/cached figure, so it's
never out of sync with what's actually consuming disk space, and a
moved/deleted file (`OSError`) simply contributes nothing rather than
raising.

**Regenerate never re-checks or re-records.** `stream_chat`'s existing
control flow already guarantees this without a special case: the rate
check and the `usage.record()` call both live inside the exact branch that
only runs when `pending_user` is a genuinely new message (`payload.regenerate`
always finds an existing `pending_user` via `conversations.last_user_message()`
first, skipping that branch entirely) — regenerating an answer isn't
"sending a new message" and must not count as one.

## `GET /billing/usage` (Phase 88's "track... and report")

Recording usage without a way to see it isn't useful tracking. The new
endpoint reports every tracked resource for the caller — real live counts
for capacity resources (documents, storage, collections, projects) and
real recorded usage for rate resources (messages, tool calls this month)
— using `requested_increase=0`/`requested_amount=0`, deliberately
different from the enforcement calls' default `1`: this is "where do you
stand right now," not "can you do one more thing." A resource whose
underlying store isn't wired up (e.g. `services.collections is None`) is
omitted from the report rather than failing the whole response.

## Files

- `apex_ai/documents/service.py` — `IngestionService.storage_bytes()`
- `apex_ai/api/errors.py` — `entitlement_error()`
- `apex_ai/api/uploads.py`, `apex_ai/api/collections.py`,
  `apex_ai/api/projects.py`, `apex_ai/api/chat.py` — real enforcement (and,
  for chat, usage recording) wired in
- `apex_ai/api/billing.py`, `apex_ai/api/schemas.py` — `GET /billing/usage`,
  `EntitlementOut`, `UsageSummaryOut`
- `tests/test_api_ui.py` — enforcement, upgrade-lifts-the-limit,
  usage-recording, regenerate-doesn't-double-count, and the usage-report
  endpoint, all against real (mocked-fast) services

## Verification

| Check | Result |
|---|---|
| Full test suite (`python3 -m pytest -q`) | 530 passed, 3 skipped |
| `tests/test_api_ui.py` | 33 passed |
| `ruff check` on every touched file | only pre-existing findings (verified identical against baseline) |

## Deliberately not done in this phase

- **No tool-call usage recording.** `max_tool_calls_per_month` is checked
  and reported (`GET /billing/usage` includes it), but nothing records
  against it: `RagEngine.ask_with_tools()` (Phase 76) is still not wired
  into `/chat/stream`, so there is no live path where a tool call actually
  happens through the API yet — recording usage for an event that can't
  occur would be dead code with nothing to prove it works.
- **No self-serve plan upgrade endpoint.** Entitlements are enforced
  against whatever plan `SubscriptionStore.set_plan()` was last called
  with (an administrative action); there is still no real payment provider
  (Phase 85) for a user to upgrade through themselves.
- **No frontend UI for hitting a limit or viewing usage.** A 402 with a
  clear `error.message` already surfaces correctly through the existing
  generic error-handling path (`errorFromResponse()`'s `safeLegacyMessage`)
  with no frontend changes required; a dedicated usage dashboard or
  upgrade prompt is presentation work for whenever self-serve upgrades
  exist to point it at.
