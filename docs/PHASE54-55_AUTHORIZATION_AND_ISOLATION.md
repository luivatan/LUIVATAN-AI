# Apex AI Phases 54–55 — Authorization Enforcement and User Data Isolation

- **Completed:** 2026-08-29 (America/Chicago)
- **Baseline:** `f320127` (Phase 51–53, end of the accounts/authentication piece)
- **Scope:** Phase 54 (route-level authorization) and Phase 55 (per-user data
  isolation) land together for the same reason Phase 51–53 did: enforcing
  authorization *is* wiring ownership through the data layer — there is no
  meaningful middle state where routes require a session but the stores behind
  them can't tell one account's rows from another's. **Document/vector
  isolation (ChromaDB, BM25, uploads) is explicitly deferred — see below.**

## What "isolation" means here

Phase 51–53 added real accounts with no enforcement: every route was reachable
by anyone, and every store had no concept of an owner. This phase closes that
gap for the two stores that are pure per-account application data —
conversations and long-term memory — using one rule applied everywhere:

**Every store method takes `user_id` as its first argument, checks it at the
row level, and treats a missing or mismatched owner as "not found," never a
distinct "forbidden."** Not just the top-level `get`/`list`/`delete` — every
method, including internal helpers like `_owns()`, `recent_turns()`,
`summary_state()`, and the memory-candidate approve/reject flow. A pattern
where routes check ownership once "at the boundary" and stores trust the
`conversation_id`/`memory_id` they're given afterward was considered and
rejected: it only takes one call site added later without going through that
boundary to reopen the hole, and there's no way to verify from the store's own
tests that such a boundary check is actually in place everywhere it needs to
be. Threading `user_id` through the store means every store-level test can
independently prove isolation, which is what `test_memories_are_isolated_between_accounts`,
`test_pending_proposals_are_isolated_between_accounts`, and
`test_rejecting_a_candidate_does_not_suppress_it_for_other_accounts` do.

## Design decisions

### `ALTER TABLE ... ADD COLUMN`, not a rebuild

Same guarded-migration pattern established in Phase 17/46/50: check
`PRAGMA table_info()` for the column before adding it, since SQLite has no
`ADD COLUMN IF NOT EXISTS`. Existing `conversations`, `long_term_memories`,
and `pending_memories` rows survive the upgrade with `user_id=''` until
backfilled — no destructive rebuild, no data loss for anyone running an
existing installation.

### The one exception: `memory_candidate_decisions` needed a real rebuild

Every other table's migration is additive. `memory_candidate_decisions` (the
Phase 45 dedup table keyed by a *content-derived* candidate ID — see Phase 43)
couldn't just gain a `user_id` column, because its primary key was
`candidate_id` alone. Two different accounts independently saying "I prefer
concise answers." produce the *same* candidate ID (it's derived from content,
not a random UUID) — under the old single-column PK, one account rejecting
that phrase would silently suppress the same proposal for every other
account. The migration detects the old single-column PK via
`PRAGMA table_info()`, and when found, rebuilds the table with a composite
`PRIMARY KEY (candidate_id, user_id)`, copying existing rows forward as
default-account-owned. `test_rejecting_a_candidate_does_not_suppress_it_for_other_accounts`
is the regression test that would have caught this if it had shipped wrong.

### `backfill_owner(user_id)` — idempotent, called once at startup

Both `ConversationStore` and `LongTermMemoryStore` gained a
`backfill_owner(user_id) -> int` method: `UPDATE ... SET user_id=? WHERE
user_id=''`. It's how pre-Phase-55 rows (and freshly-migrated rows from the
`memory_candidate_decisions` rebuild) get assigned to the auto-provisioned
default local account, so an existing installation's history isn't orphaned
by the upgrade. Called once from `runtime.py` (long-term memory) and
`api/server.py` (conversations — that store is constructed there, not in
`runtime.py`), each guarded by `if services.default_local_user is not None`.

### `clear()` used to be a global wipe — now it's scoped

Before this phase, `ConversationStore.clear()` and
`LongTermMemoryStore.clear()` had no `user_id` parameter and deleted every
row in the table, for every account, unconditionally. That's not a
data-isolation nuance — it was a live bug the moment a second account existed
(`DELETE /conversations` or `DELETE /memory` from any one account would wipe
everyone's data). Requiring `user_id` as the first argument fixes it as a
side effect of the same signature change every other method got.

### Auth as an availability signal, not just an identity check

While wiring `Depends(require_user)` into `/memory`'s routes, a real
regression surfaced in `test_memory_management_degrades_when_optional_store_is_unavailable`:
a deployment where `AuthService` itself failed to construct (a genuine
startup failure, captured in `services.startup_error`) started returning
`401 Sign in to continue` instead of `503 Long-term memory is unavailable`,
because the auth dependency ran — and failed — before the route body ever got
a chance to report the real problem. `services.auth is None` only happens
when the whole auth subsystem never came up (see `runtime.py`'s startup
boundary), not when a legitimate user simply isn't signed in yet, so
`require_user` now distinguishes the two: `auth is None` → `503
auth_unavailable`; no session and no auto-login fallback → `401
authentication_required`. Telling a caller to "sign in" when the service
itself is broken would have been actively misleading.

### `/query` stays ungated

The single-account compatibility endpoint (`/query`, and the CLI/Gradio
surfaces behind it) uses the singleton `RagEngine` built once at startup and
bound to `services.default_local_user.id`. Gating `/query` behind
`Depends(require_user)` would add an auth check without adding real per-request
isolation — the underlying engine is still one account's engine regardless of
who's asking — so it was left as-is. This is documented, not silently
skipped: `test_phase42_query_endpoint_does_not_trigger_memory_extraction` and
its siblings in `test_api_ui.py` exercise `/query` against the same
`wired_services.default_local_user.id`-scoped store to prove the compatibility
surface still behaves correctly under the new signatures.

## Files

- `apex_ai/memory/conversations.py` — every method takes `user_id` first;
  new `backfill_owner()`; new `_owns()` ownership-check helper used
  internally; `clear()` fixed from global to scoped.
- `apex_ai/memory/long_term.py` — same treatment for
  `long_term_memories`/`pending_memories`/`memory_candidate_decisions`; the
  composite-PK migration described above; new `backfill_owner()`.
- `apex_ai/memory/confirmation.py` — `MemoryConfirmationService` methods take
  `user_id` and pass it straight through to the store.
- `apex_ai/api/memory.py`, `apex_ai/api/chat.py` — every route gained
  `user=Depends(require_user)` and threads `user.id` into every store call,
  including inside `stream_chat`'s streaming-response closure.
- `apex_ai/api/auth.py` — `require_user` now returns `503` when the auth
  subsystem itself is unavailable, distinct from `401` for "not signed in."
- `apex_ai/api/server.py` — backfills conversation ownership at startup;
  `DELETE /conversations` now requires a user and scopes the delete.
- `apex_ai/rag/engine.py` — `RagEngine.__init__` gained `user_id: str = ""`;
  confirmed-memory retrieval in `prepare()` now reads `self.long_term_memory.list(self.user_id, ...)`
  instead of every account's memory.
- `apex_ai/runtime.py` — backfills long-term-memory ownership at startup; the
  singleton engine behind `/query` is constructed with
  `user_id=services.default_local_user.id`.

## Verification

| Check | Result |
|---|---|
| Full test suite (`python3 -m pytest -q`) | 293 passed, 3 skipped |
| `tests/test_long_term_memory.py` (incl. new `test_memories_are_isolated_between_accounts`) | 7 passed |
| `tests/test_memory_confirmation.py` (incl. new isolation + cross-account-reject tests) | 9 passed |
| `tests/test_memory_safety.py` | 19 passed |
| `tests/test_memory_extraction.py` | 7 passed |
| `tests/test_api_ui.py` | 18 passed, 3 skipped |
| `tests/test_conversations_web.py` | 23 passed |
| `tests/test_error_handling.py` | 9 passed |
| `ruff check` on every new/touched file | 1 pre-existing finding (`BLE001` on an untouched line in `apex_ai/rag/engine.py`, unchanged by this diff); everything else clean |

## Deliberately not done in this phase

- **Document/vector isolation.** ChromaDB collections/metadata, the BM25
  index, the upload directory, and `IngestionService` all remain global
  across every account. This is a materially larger, separate piece of work
  — it needs a decision between per-user Chroma collections vs. metadata
  filtering, per-user upload directories, and retrieval-pipeline changes to
  thread `user_id` through hybrid search — and was scoped out of Phase 54/55
  rather than rushed. README and this doc call it out explicitly (see
  Limitations) so the gap is documented, not silently shipped.
- **Phase 56 (Project Isolation)** stays blocked on Phase 71 (Projects), same
  as Phase 48 — there is still no project/workspace data model anywhere in
  the codebase to isolate.
- **No rate limiting on `/auth/login` or any other route** — still Phase 58
  (API Security).

## Boundaries and remaining unknowns

- A user with a valid session can still retrieve and cite every document ever
  uploaded to the instance, regardless of who uploaded it — the isolation
  built in this phase does not cover documents. This is the single most
  important caveat carried forward from this phase; see README's Limitations
  section.
- `default_local_user` is created once at startup and does not rotate; every
  unauthenticated request on an `auto_login_local=True` deployment shares that
  one account's conversations and memory, by design (that's the whole point
  of the Phase 51 "single default account" decision) — it is not a bug that
  two browser tabs on the same machine, neither signed in, see the same
  conversation list.
