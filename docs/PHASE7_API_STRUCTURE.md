# Apex AI Phase 7 — API Structure

- **Completed:** 2026-08-29 (America/Chicago)
- **Baseline:** `0cd5da1` (Phase 6 logging foundation)
- **Scope:** audit the existing FastAPI surface for consistency, validation, and
  predictable responses; close the gaps found without changing route paths, request
  shapes, or success/error payload contents.

## Audit findings

The Phase 5/6 foundations already gave the API most of what this phase asks for:

- routes are organized by domain into routers (`chat`, `memory`, `uploads`) plus a
  small set of top-level routes in `server.py`;
- every request body is a `pydantic.BaseModel` with `Field` constraints (min/max
  length), so invalid input already produces structured `422` responses;
- `install_error_handlers` (Phase 5) already normalizes every error path — `APIError`
  and plain `HTTPException` alike — into one `{"detail", "error": {"code", "message",
  "retryable", "fields"?}}` shape with appropriate status codes (400/404/409/413/415/
  422/429/500/502/503/504); and
- `GET /conversations` was already bounded (`ConversationStore.list(limit=100)`).

The one real gap: **no route declared a response schema.** Every handler returned a
bare `dict`, so `/api/docs` (Swagger UI) and `/openapi.json` had no record of what a
successful response actually contains, and FastAPI could not catch a handler that
drifted from its own contract.

## Change

Added `apex_ai/api/schemas.py`: one `pydantic.BaseModel` per existing response shape
(`ConversationOut`, `MessageOut`, `ConversationDetailOut`, `DocumentOut`,
`ModelEntryOut`, `IngestOut`/`UploadOut`, `HealthOut`, `AppConfigOut`, `QueryOut`,
memory-candidate and simple confirmation shapes). Every field mirrors the producing
dataclass (`Conversation.to_dict()`, `Message.to_dict()`, `DocumentInfo.as_dict()`,
`ModelEntry`, `IngestResult`, `PendingMemory.to_dict()`) exactly — no field was
renamed, added, or dropped from what clients already receive.

Wired `response_model=` onto every non-streaming route in `chat.py`, `memory.py`,
`uploads.py`, and `server.py`. `/chat/stream` and the developer-only `/debug/rag`
route are unchanged: the former is `StreamingResponse` (NDJSON, not a single JSON
body — its per-event shapes are documented in
[`CHAT_INTERFACE_ARCHITECTURE.md`](CHAT_INTERFACE_ARCHITECTURE.md) instead), and the
latter is deliberately excluded from the schema (`include_in_schema=False`) as a
gated developer trace, not a public contract.

`/health` uses `response_model_exclude_none=True` because `documents`, `chunks`, and
`startup_error` are only meaningful conditionally (not-ready services have no
document/chunk counts; a healthy service has no startup error). Declaring them
`Optional[...] = None` plus `exclude_none` keeps the exact existing behavior — the
keys are **absent**, not `null` — while still documenting their shape when present.
A regression test (`test_health_omits_stats_when_not_ready`) pins this.

## A bug this caught before it shipped

Building `MemoryCandidateOut` from the wrong source (the extraction-time
`MemoryCandidate.to_dict()` in `memory/extraction.py`, which only has
`id/kind/content/rule`) rather than the actual response source (`PendingMemory.to_dict()`
in `memory/long_term.py`, which also has `created_at`/`expires_at`) would have made
`response_model` **silently strip** those two fields from every `GET
/memory/candidates` response — a real behavior change disguised as a docs-only
change. `test_memory_candidate_requires_approval_and_is_not_prompted` (existing,
Phase 43/45) caught the mismatch immediately via an exact-equality assertion against
the same proposal returned in the chat-stream `meta` event. Fixed by adding the two
missing fields to the schema; this is why every schema in this phase was built by
reading the actual producing dataclass rather than guessing from route usage.

## Deliberately not changed

- `DELETE /documents/{document_id}` keeps returning `{"message": "<human text>"}`
  (not e.g. `{"deleted": true}`) — that is the existing, working contract
  (`IngestionService.remove()`), and nothing depends on changing its shape.
- No URL versioning (`/api/v1/...`) or route renaming — the app is still
  single-user/local (Phase 51+ has not run yet), and renaming routes now would only
  create churn for the one existing web client with no present benefit.
- No behavior change to error raising (`HTTPException` vs `APIError`) inside
  `chat.py`/`memory.py` — Phase 5's exception handlers already normalize both into
  the same public shape, so standardizing the raise style would be a pure style change
  with no observable effect; left for a future pass if it ever causes real confusion.

## Verification

| Check | Result |
|---|---|
| Full test suite (`python -m pytest tests/ -q`) | 229 passed, 3 skipped |
| New Phase 7 regression tests (`test_health_omits_stats_when_not_ready`, `test_openapi_documents_response_schemas`) | Included in the 229 |
| Ruff (`apex_ai/api/`, `tests/test_api_ui.py`) | All checks passed |
| Manual diff review of every schema field against its producing dataclass | Done for all 13 response models |

## Boundaries and remaining unknowns

- Response models validate and document *outgoing* shape; they do not add new
  input validation beyond what Phase 5/6 already established.
- Authentication/authorization headers, rate limiting, and CORS are out of scope for
  this phase — they belong to Phase 54/58 once real multi-user accounts exist.
- `/query` and `/documents/ingest` remain explicitly local-automation endpoints (no
  path traversal beyond existing `sanitize_filename`/`ensure_within` checks, no new
  network exposure implied).
