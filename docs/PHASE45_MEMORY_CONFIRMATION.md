# Apex AI Phase 45 — Memory Confirmation

**Audit date:** 2026-08-28 (America/Chicago)
**Roadmap scope:** let the user approve or reject safe memory candidates before they
become long-term memory

## BEFORE — inspection at Phase 44

The system now has three intentionally separate pieces:

- `MemoryCandidateExtractor` identifies only explicit preference/ongoing-context
  statements and preserves their wording.
- `MemorySafetyPolicy` removes unsafe candidates and blocks every store create/update.
- `LongTermMemoryStore` persists safe records, but there is no user-facing caller.

The web chat's NDJSON stream currently emits metadata, provider tokens, final answer,
stop, and error events. The browser has no memory confirmation surface. The Settings
page exists but manages only browser behavior and conversations; full memory management
belongs to Phase 46.

## Phase 45 design

A candidate must not be treated as approved merely because it appeared in a user message.
Implement an explicit state transition:

```text
user message -> safe candidate -> pending proposal
                              -> APPROVE -> long-term memory
                              -> REJECT  -> no content retained
```

Requirements:

1. Persist only safe, explicit candidates in a dedicated pending-proposal table so a page
   reload or model failure does not turn confirmation into a race.
2. Pending content expires after a short fixed retention period and is never injected
   into prompts.
3. Store rejection/approval tombstones as content-free candidate hashes so the same
   sentence is not repeatedly proposed.
4. Re-run the Phase 44 safety policy at approval and perform proposal-to-memory movement
   in one SQLite transaction.
5. Add backend endpoints to list, approve, and reject pending proposals. Clients submit
   only the server-issued proposal ID; they cannot replace the content during approval.
6. Detect candidates only for new `/chat/stream` user messages—not regeneration,
   assistant output, document text, or the compatibility `/query` route.
7. Candidate or optional-memory failure must not interrupt chat generation.
8. Render an accessible confirmation card near the composer. Use text nodes for candidate
   content, clear **Remember** and **Don't save** actions, and a reminder never to save
   secrets.

This phase does not add general view/edit/delete/clear controls for confirmed memories;
that is Phase 46. It does not use confirmed memories in prompts; relevance and retrieval
remain Phase 47.

## AFTER — explicit pending-to-confirmed transition

Phase 45 adds `MemoryConfirmationService`, backed by two new tables inside the already
separate long-term-memory database:

- `pending_memories` contains only Phase 43 candidates that passed Phase 44 safety,
  including their deterministic ID, kind, verbatim content, matched rule, creation time,
  and seven-day expiry; and
- `memory_candidate_decisions` stores the content-derived candidate ID, approved/rejected
  state, optional resulting memory ID, and decision time—but never rejected content.

Pending rows are not returned by `LongTermMemoryStore.list()`/`count()` and are not
confirmed memories. Expired rows are deleted on startup and lazily before proposal/list/
decision operations. A matching decision tombstone prevents an approved or rejected
sentence from being offered repeatedly. Existing equivalent confirmed content is also
recognized and not duplicated.

Approval performs the following inside one SQLite transaction:

1. load the server-side proposal by its constrained `memcand_…` ID;
2. re-run `MemorySafetyPolicy` against the stored content;
3. insert (or reuse equivalent) confirmed memory;
4. delete pending content; and
5. write a content-free approval decision.

If a pending row has somehow become unsafe, approval deletes it, records a rejected
content-free decision, and raises `UnsafeMemoryError` without echoing the value. Rejection
deletes pending content and stores only its candidate-ID tombstone. “Don't save” does not
delete the original user message from ordinary conversation history; it guarantees that
content does not become long-term memory or remain in the pending-memory table.

## Chat, API, and browser interaction

For each new `/chat/stream` user message, the controller asks the optional confirmation
service for safe candidates. Any proposals are included in the initial genuine NDJSON
`meta` event. Regeneration, assistant output, documents, and the compatibility `/query`
endpoint do not trigger proposals. A broad optional-component boundary catches proposal
failures by exception type only and continues the real RAG generation.

The backend now exposes:

| Endpoint | Behavior |
|---|---|
| `GET /memory/candidates` | list unexpired safe proposals for page reload recovery |
| `POST /memory/candidates/{id}/approve` | atomically create confirmed memory |
| `POST /memory/candidates/{id}/reject` | delete pending content and reject it |

The client never posts candidate content back for approval, so it cannot replace the
server-reviewed text in transit. Missing/expired IDs return 404, malformed IDs return 400,
and an unavailable optional memory service returns 503 while explicitly stating that
core chat remains available.

The chat UI has an `aria-live` confirmation region above the composer. Each card is built
with DOM text nodes (not candidate-controlled HTML), identifies the kind, shows the exact
text, warns “Review first · never save secrets,” and offers **Remember** or **Don't save**.
Cards survive reload through the list endpoint, disable controls during a decision, and
show success/error feedback. At Phase 45 the success toast truthfully says prompt use is
not enabled yet.

## Preserved boundaries and tradeoffs

- Confirmed and pending memory remain absent from `RagEngine`, query processing, prompts,
  retrieved evidence, and citations. A post-approval follow-up test confirms the stored
  preference does not appear in model messages.
- Existing LLM providers, ChromaDB/document RAG, conversation persistence, streaming,
  stop/regenerate, and entry points remain unchanged.
- A model failure does not invalidate a candidate from the user's own message, and a
  candidate failure does not invalidate chat. These lifecycles are deliberately
  independent.
- Persisting a pending candidate is a limited pre-consent retention tradeoff required to
  make confirmation reliable across reloads. It stores only explicit, safety-screened
  sentences, expires them, and never mistakes them for approved memory.
- This is still a local single-user interface. Backend identity and per-user isolation are
  later roadmap phases, so this phase makes no multi-user privacy claim.

## Verification

Focused memory/API/web regression run:

```text
.venv/bin/python -m pytest -q \
  tests/test_memory_confirmation.py tests/test_memory_safety.py \
  tests/test_memory_extraction.py tests/test_long_term_memory.py \
  tests/test_conversations_web.py tests/test_api_ui.py
69 passed, 1 warning in 5.94s
```

Complete regression suite:

```text
.venv/bin/python -m pytest tests/ -q
192 passed, 3 warnings in 12.51s
```

Additional static validation:

```text
node --check apex_ai/web/static/app.js
python -m compileall -q apex_ai tests
ruff check <Phase 45 Python files>
git diff --check
all passed
```

The 13 new tests cover pending-versus-confirmed state, approval, rejection and content
removal, decision suppression, unsafe/ordinary input, equivalent-content deduplication,
expiry, approval-time safety revalidation, invalid/missing IDs, real streaming metadata,
API approve/reject/list/unavailable behavior, regeneration suppression, optional failure
isolation, safe browser rendering hooks, and the invariant that confirmed memory is not
yet injected into prompts. The three suite warnings are the unchanged dependency and
legacy-environment deprecations; no test failed.
