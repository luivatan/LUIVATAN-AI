# Apex AI Phases 54–55 — Authorization Enforcement and User Data Isolation

- **Completed:** 2026-08-29 (America/Chicago)
- **Baseline:** `f320127` (Phase 51–53, end of the accounts/authentication piece)
- **Scope:** Phase 54 (route-level authorization) and Phase 55 (per-user data
  isolation) land together for the same reason Phase 51–53 did: enforcing
  authorization *is* wiring ownership through the data layer — there is no
  meaningful middle state where routes require a session but the stores behind
  them can't tell one account's rows from another's. Phase 55's own wording
  ("conversations, documents, projects, or memories") covers four things;
  this pass delivers three of them in full — **conversations, long-term
  memory, and documents (vector store, keyword index, upload directory)** —
  and explicitly leaves projects out, because no project/workspace data model
  exists anywhere in the codebase yet to isolate (same blocker as Phase 48,
  resolved only once Phase 71 exists).

## What "isolation" means here

Phase 51–53 added real accounts with no enforcement: every route was reachable
by anyone, and every store had no concept of an owner. This phase closes that
gap using one rule applied everywhere, across every store that holds
per-account data:

**Every store method takes `user_id` as an argument, checks it at the row
level, and treats a missing or mismatched owner as "not found," never a
distinct "forbidden."** Not just the top-level `get`/`list`/`delete` — every
method, including internal helpers like `_owns()`, `recent_turns()`,
`summary_state()`, the memory-candidate approve/reject flow, and (for
documents) `has_document()`/`search()`/`get_all_chunks()`. A pattern where
routes check ownership once "at the boundary" and stores trust the
`conversation_id`/`memory_id`/`document_id` they're given afterward was
considered and rejected: it only takes one call site added later without
going through that boundary to reopen the hole, and there's no way to verify
from the store's own tests that such a boundary check is actually in place
everywhere it needs to be. Threading `user_id` through the store means every
store-level test can independently prove isolation — see the isolation tests
listed in Verification below.

## Design decisions — conversations & long-term memory

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

## Design decisions — documents (vector store, keyword index, uploads)

Documents needed more than a `user_id` column, because unlike conversations
and memory, retrieval is a *shared-collection search* problem, not a
row-lookup problem: one Chroma collection and one in-memory BM25 index sit
underneath every account's queries.

### Metadata filtering, not collection-per-user

Every chunk gets a `user_id` field in its Chroma metadata at ingestion time.
`ChromaVectorStore.search()`, `.get_all_chunks()`, `.list_documents()`,
`.has_document()`, and `.delete_document()` all pass `where={"user_id":
user_id}` (combined with `document_id` via `$and` where relevant) instead of
querying unfiltered. A collection-per-account design was considered — it
would give a harder physical boundary — but metadata filtering keeps the
existing embedding-compatibility bookkeeping (one collection, one recorded
embedding model/dimension) intact and needed no schema migration tooling
beyond what a rolling metadata `update()` already does. `count(user_id=None)`
is the one deliberately optional signature in this phase: passing `None`
returns the whole instance's total, used only by system-wide diagnostics
(`/health`, `/app-config`) — every content-returning method stays required.

### Dedup moved from global to per-account, on purpose

Before this phase, `IngestionService` deduplicated by content hash alone:
two different accounts uploading byte-identical files would have the second
upload silently treated as "already indexed" — meaning the second account's
upload attaches to the *first* account's document and, depending on the
code path, could read or manage a file it never uploaded. Deduplication is
now scoped to `(document_id, user_id)`: `has_document(document_id, user_id)`
only says yes if *this* account already has that content indexed, so two
accounts with the same file each get their own independent copy —
`test_documents_are_isolated_between_accounts` proves this explicitly
(`status="indexed"` for both, never `"duplicate"` across accounts). The
storage cost of not deduplicating across accounts is an accepted tradeoff:
correct isolation over storage efficiency, same call this codebase already
made for chunk IDs (below) and for `memory_candidate_decisions`.

### Chunk IDs had to become account-scoped too

Chunk IDs were purely content-derived (`{document_id}:{seq}`). Once dedup
stopped being global, two accounts ingesting the same file would produce
*identical* chunk IDs — and since Chroma's `upsert()` treats a repeated ID as
an in-place update, the second account's ingest would silently overwrite the
first account's row, re-tagging it with the second account's `user_id` and
orphaning the first account's access to a chunk they thought they owned.
`IngestionService.ingest_path()` now rewrites each chunk's ID to
`{user_id}:{document_id}:{seq}` (and updates the chunk's own
`metadata["chunk_id"]` to match) right after content-only chunking, keeping
`documents/chunking.py` itself completely account-agnostic — ownership is
applied by the ingestion service, not baked into the content pipeline.

### BM25: one cached sub-index per account, not one global index

`BM25Index` used to build a single in-memory index over every chunk in the
store, rebuilt whenever `store.version` changed. It now builds and caches one
sub-index per `user_id`, built from `store.get_all_chunks(user_id)`. Every
cached sub-index still shares one staleness check — any account's
ingest/delete bumps `store.version`, which clears every cached account's
index — so the invalidation semantics are unchanged, just fanned out per
account. `test_bm25_index_is_isolated_between_accounts` proves a keyword hit
for one account's document never surfaces when a different account searches
the same term.

### Per-account upload directories

New uploads land in `settings.upload_dir/{user_id}/{filename}` instead of a
single shared `uploads/` directory, using the same collision-safe
content-hash-suffix logic that already existed (now scoped inside each
account's own subdirectory). Files from before this phase keep their
existing flat path in the registry — physical files are not moved during the
migration, only newly-fixed ownership metadata and future ingests use the
per-account layout; the registry's stored `path` remains the source of truth
either way, so nothing already indexed became unreachable.

### Registry: composite `(document_id, user_id)` key

`IngestionService`'s JSON registry (`document_registry.json`) was keyed by
content-hash `document_id` alone. It's now keyed by `(document_id, user_id)`,
so the same content can have one entry per account that uploaded it.
`DocumentInfo` gained a `user_id` field, defaulting to `""` so an existing
registry file still loads before `backfill_owner()` runs.

### `backfill_owner(user_id)` everywhere a store gained an owner column

`ConversationStore`, `LongTermMemoryStore`, `ChromaVectorStore`, and
`IngestionService` all gained an idempotent `backfill_owner(user_id) -> int`
that assigns any pre-Phase-55 row/chunk/registry-entry with no owner yet to
the given account — always the auto-provisioned default local account, run
once at startup (`runtime.py` for long-term memory and the document store/
ingestion service; `api/server.py` for conversations, since
`ConversationStore` is constructed there). An existing installation's
history, memories, and indexed documents all survive the upgrade instead of
silently becoming unreachable once every read starts filtering by owner.

### `/query` stays ungated, on purpose

The single-account compatibility endpoint (`/query`, and the CLI/Gradio/
evaluation-runner surfaces behind it) uses the singleton `RagEngine` built
once at startup and bound to `services.default_local_user.id` for every
operation — retrieval included, now that `self.store.count(self.user_id)`
and `self.retriever.retrieve_with_trace(..., self.user_id)` back the
"no indexed documents" checks and the actual retrieval call. Gating `/query`
behind `Depends(require_user)` would add an auth check without adding real
per-request isolation — the underlying engine is still one account's engine
regardless of who's asking — so it was left as-is. `/documents/ingest`,
by contrast, *is* gated now (it manipulates real per-account state the same
way `/documents/upload` does), which is the one place this phase's document
work changed a route's authorization that Phase 51–53's "not yet done" note
had originally scoped as unchanged.

## Files

- `apex_ai/memory/conversations.py` — every method takes `user_id` first;
  new `backfill_owner()`; new `_owns()` ownership-check helper used
  internally; `clear()` fixed from global to scoped.
- `apex_ai/memory/long_term.py` — same treatment for
  `long_term_memories`/`pending_memories`/`memory_candidate_decisions`; the
  composite-PK migration described above; new `backfill_owner()`.
- `apex_ai/memory/confirmation.py` — `MemoryConfirmationService` methods take
  `user_id` and pass it straight through to the store.
- `apex_ai/vectordb/chroma_store.py` — `search()`, `get_all_chunks()`,
  `has_document()`, `delete_document()`, `list_documents()` all require
  `user_id`; `count(user_id=None)` stays optional for system-wide
  diagnostics only; new `backfill_owner()`.
- `apex_ai/retrieval/keyword.py` (`BM25Index`) — rebuilt around a per-account
  `_UserIndex` cache instead of one global index.
- `apex_ai/retrieval/pipeline.py` (`HybridRetriever`) — `retrieve()`/
  `retrieve_with_trace()` require `user_id`, threaded into both channels.
- `apex_ai/documents/service.py` (`IngestionService`) — `ingest_path()`,
  `reindex()`, `remove()`, `list_documents()` require `user_id`;
  `stats(user_id=None)` mirrors the store's optional-for-diagnostics pattern;
  per-account upload subdirectory; composite-keyed registry; chunk-ID
  rewriting; new `backfill_owner()`.
- `apex_ai/rag/engine.py` — `self.user_id` (added in the memory half of this
  phase) now also backs document retrieval: `self.store.count(self.user_id)`
  gates the "no indexed documents" short-circuits in `ask()`/`ask_stream()`/
  `debug()`, and `self.retriever.retrieve_with_trace(..., self.user_id)`
  scopes the actual search.
- `apex_ai/api/memory.py`, `apex_ai/api/chat.py`, `apex_ai/api/uploads.py` —
  every route gained `user=Depends(require_user)` and threads `user.id` into
  every store call, including inside `stream_chat`'s streaming-response
  closure.
- `apex_ai/api/auth.py` — `require_user` now returns `503` when the auth
  subsystem itself is unavailable, distinct from `401` for "not signed in."
- `apex_ai/api/server.py` — backfills conversation ownership at startup;
  `/documents`, `/documents/ingest`, `DELETE /documents/{id}`, and
  `DELETE /conversations` all require a user and scope their store calls.
- `apex_ai/runtime.py` — backfills long-term-memory, document-store, and
  ingestion-registry ownership at startup; the singleton engine behind
  `/query` is constructed with `user_id=services.default_local_user.id`.
- `apex_ai/ui/gradio_app.py`, `apex_ai/evaluation/runner.py` — both are
  single-account tools by the same precedent as `/query`; every
  `ingestion.*`/`store.*` call now threads the default local account's id
  (a small `_user_id(services)` helper in `gradio_app.py`).

## Verification

| Check | Result |
|---|---|
| Full test suite (`python3 -m pytest -q`) | 296 passed, 3 skipped (the 3 skips are Gradio UI tests skipped because the optional `gradio` package isn't installed in this environment — unrelated to this phase) |
| `tests/test_long_term_memory.py` (incl. `test_memories_are_isolated_between_accounts`) | 7 passed |
| `tests/test_memory_confirmation.py` (incl. isolation + cross-account-reject tests) | 9 passed |
| `tests/test_memory_safety.py` | 19 passed |
| `tests/test_memory_extraction.py` | 7 passed |
| `tests/test_vectordb.py` (incl. `test_documents_are_isolated_between_accounts`) | 11 passed |
| `tests/test_retrieval.py` (incl. `test_bm25_index_is_isolated_between_accounts`) | 9 passed |
| `tests/test_rag_phase2.py`, `tests/test_engine.py` | all passing |
| `tests/test_api_ui.py` | 18 passed, 3 skipped |
| `tests/test_conversations_web.py` (incl. end-to-end `test_uploaded_documents_are_isolated_between_accounts`, two real accounts through the real HTTP API) | 24 passed |
| `tests/test_error_handling.py` | 9 passed |
| `ruff check` on every new/touched file | 28 pre-existing findings across the whole touched set, byte-identical in rule and count to the pre-Phase-54/55 baseline on the same files (verified by diffing a baseline snapshot); the only finding this phase introduced (`RUF023`, an unsorted new `__slots__` tuple) was fixed |

## Deliberately not done in this phase

- **Phase 56 (Project Isolation)** stays blocked on Phase 71 (Projects), same
  as Phase 48 — there is still no project/workspace data model anywhere in
  the codebase to isolate. Once Phase 71 adds one, it will need the same
  `user_id`-required treatment documented here.
- **No rate limiting on `/auth/login`, `/documents/upload`, or any other
  route** — still Phase 58 (API Security).
- **Existing pre-Phase-55 upload files are not physically moved** into the
  new per-account subdirectory layout; only their ownership metadata is
  backfilled. See "Per-account upload directories" above for why this is
  safe (the registry's stored path is the source of truth either way).

## Boundaries and remaining unknowns

- `/query` is a deliberate, documented exception to per-request isolation —
  see "`/query` stays ungated, on purpose" above. Do not expose it on a
  deployment that expects per-caller document/memory isolation.
- Two accounts uploading byte-identical files now each pay the full
  embedding/storage cost of their own copy (no cross-account dedup). For a
  personal/small-team deployment this is negligible; a deployment expecting
  many accounts to share large, identical corpora would want a future,
  explicitly-opt-in sharing mechanism rather than reviving global dedup.
- `default_local_user` is created once at startup and does not rotate; every
  unauthenticated request on an `auto_login_local=True` deployment shares
  that one account's conversations, memory, and documents, by design (that's
  the whole point of the Phase 51 "single default account" decision) — it is
  not a bug that two browser tabs on the same machine, neither signed in,
  see the same document library.
