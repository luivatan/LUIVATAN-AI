# Apex AI Phase 42 — Long-Term Memory

**Audit date:** 2026-08-28 (America/Chicago)
**Roadmap scope:** Phase 42 only — a separate persistence foundation for useful
preferences and ongoing context

The request to complete the remaining roadmap is being executed sequentially because the
roadmap explicitly requires inspection and testing before advancing. Implementing Phases
42–100 as one unreviewed rewrite would violate that rule. This document audits and scopes
Phase 42; Phases 43–100 remain separate acceptance gates.

## BEFORE — exact current architecture

Apex AI has two conversation systems, but no long-term memory system:

- `ConversationMemory` retains a bounded JSON list of recent question/answer pairs for
  compatibility entry points.
- `ConversationStore` persists complete browser conversations and messages in SQLite.
- `ConversationMemoryAdapter` exposes recent turns from one selected conversation.
- `build_conversation_context()` applies strict turn/message/total limits before recent
  history reaches query processing or generation.

These stores represent what was said in a particular conversation. There is no separate
record type or database for durable preferences such as a formatting preference, nor for
ongoing context that should survive across otherwise independent conversations.

No current component automatically extracts, confirms, stores, retrieves, or injects
long-term memories. That absence is important: silently treating arbitrary chat text as a
lasting preference would be unsafe and would prematurely combine roadmap Phases 42–47.

## Existing boundaries that must remain

1. Conversation history is not document evidence and cannot become a citation.
2. Long-term memory must not be written automatically in Phase 42; extraction begins in
   Phase 43 and confirmation in Phase 45.
3. Long-term memory must not enter model prompts yet; relevant retrieval belongs to Phase
   47.
4. A failure in this new optional subsystem must not stop document ingestion, RAG, chat,
   or existing conversation persistence.
5. Memory contents must not be logged.
6. No authentication, multi-user isolation, project memory, billing, or deployment is in
   Phase 42.

Baseline before implementation:

```text
.venv/bin/python -m pytest tests/ -q
143 passed, 3 warnings in 15.31s
```

## Incremental Phase 42 plan

- Add an independent SQLite-backed `LongTermMemoryStore`.
- Use a minimal record containing a stable ID, explicit kind, content, and timestamps.
- Accept only the two Phase 42 categories: `preference` and `ongoing_context`.
- Provide tested internal CRUD/list/count/clear operations for later phases to compose.
- Configure its path through the environment; keep runtime data ignored by Git.
- Initialize it as an optional service so failure degrades independently.
- Expose readiness diagnostics and an internal count without exposing memory content.
- Do not connect it to prompts, automatic extraction, or the normal UI yet.

## AFTER — implemented architecture

Phase 42 adds `apex_ai.memory.long_term.LongTermMemoryStore`, a small SQLite boundary
whose database is independent from all existing persistence:

```text
explicit internal caller ──> LongTermMemoryStore ──> data/long_term_memory.db

browser conversations ──> ConversationStore ──> data/conversations.db
legacy recent turns ─────> ConversationMemory ─> data/conversation_memory.json
document evidence ───────> ChromaVectorStore ──> data/chroma/
```

The schema stores only:

- a UUID record ID;
- an explicit kind constrained in Python and SQLite to `preference` or
  `ongoing_context`;
- trimmed text content; and
- UTC creation/update timestamps.

The store provides internal `create`, `get`, `list`, `update`, `delete`, `clear`, and
`count` operations. Reopening the store preserves records. Category filters and bounded
listing provide a minimal interface for later phases without guessing their policy.

`APEX_LONG_TERM_MEMORY_DB_PATH` configures the file (default
`data/long_term_memory.db`). Runtime initialization is optional and isolated: an expected
`DatabaseError` or unexpected constructor failure leaves `services.long_term_memory` as
`None`, records only a sanitized diagnostic, and continues building the established RAG
and chat services. `/health` reports only `ready` or `unavailable`, `optional: true`, and
`prompt_use: false`; it exposes neither records nor the configured filesystem path. The
startup log may report the aggregate record count but never record content.

The public package exports `LongTermMemory` and `LongTermMemoryStore`, but there is no
write endpoint or UI. This is intentional rather than an incomplete hidden feature:
Phase 43 must first define candidate extraction, Phase 44 must reject secrets, Phase 45
must add confirmation, Phase 46 must add user management, and Phase 47 must add
relevance-aware prompt retrieval.

## Interaction with existing behavior

- `RagEngine`, query processing, prompts, citations, and LLM providers were not changed.
- Sending a query still writes only to the existing bounded conversation memory where
  applicable. It does not create a long-term record.
- A seeded long-term record is not present in model messages and cannot become document
  evidence or a citation.
- Conversation/history SQLite tables and long-term-memory tables are verified to live in
  different files.
- The normal chat/RAG runtime still reaches `ready` when long-term-memory construction is
  deliberately failed in a test.

## Tradeoffs and deferred safety

SQLite is appropriate here because the requirement is local, durable, transactional
storage and the repository already uses SQLite for conversations. A new database rather
than another conversation table makes the semantic and failure boundary explicit. No new
service or network dependency is introduced, preserving offline-first behavior.

The local database is **not encrypted** and Phase 42 does not yet provide account or
project ownership. Therefore it must not be represented as a safe place for secrets or
multi-user data. Secret detection, consent, user/project isolation, conflict handling,
and summaries remain later roadmap gates. Keeping the store disconnected from automatic
writes and prompts is the safest useful foundation at this phase.

## Verification

Focused Phase 42/config/API regression run:

```text
.venv/bin/python -m pytest -q \
  tests/test_long_term_memory.py tests/test_config.py tests/test_api_ui.py
30 passed, 3 warnings in 3.78s
```

Complete regression suite after implementation:

```text
.venv/bin/python -m pytest tests/ -q
152 passed, 3 warnings in 11.30s
```

The nine added regressions cover persistence across instances, CRUD, category/content
validation, filtering/limits/clear, physical database separation, explainable corruption
errors, optional-runtime failure isolation, environment configuration, content-free
health diagnostics, no automatic extraction, and no prompt injection. The warnings are
one dependency deprecation from FastAPI/Starlette and the two intentionally tested legacy
environment-variable warnings; no test failed.
