# Apex AI Phase 43 — Memory Candidate Extraction

**Audit date:** 2026-08-28 (America/Chicago)
**Roadmap scope:** identify useful memory candidates without automatically storing every
message

## BEFORE — inspection at Phase 42

Phase 42 introduced an independent `LongTermMemoryStore` for explicitly supplied
`preference` and `ongoing_context` records. It is intentionally disconnected from chat,
prompts, and the UI. Current user messages flow through either `RagEngine.ask()` or the
`/chat/stream` controller, and neither path inspects a message for durable memory.

That boundary must remain intact. Treating every user message as memory would retain
questions, one-off requests, document text, and potentially sensitive values. Calling an
LLM for every turn would also add nondeterminism, latency, possible remote disclosure,
and an internet/provider dependency to a capability that can begin conservatively.

## Phase 43 design

Implement a transparent, deterministic `MemoryCandidateExtractor` that:

1. considers only explicit linguistic signals of a durable formatting/interaction
   preference or ongoing work context;
2. preserves the candidate sentence exactly except for surrounding whitespace/list
   markers, so identifiers, versions, dates, and numbers are not rewritten;
3. labels each candidate as `preference` or `ongoing_context` and records which explicit
   rule matched;
4. deduplicates equivalent matches and imposes strict per-candidate and per-message
   bounds;
5. returns ephemeral `MemoryCandidate` values only; and
6. never calls `LongTermMemoryStore`, an LLM, an embedding model, or the network.

The extractor will be available through the runtime service container for composition by
later phases, but Phase 43 will not call it automatically from chat and will not add an
API/UI write path. Phase 44 must add secret/sensitive-data rejection before candidate
extraction can be exposed to normal users, and Phase 45 must define confirmation before a
candidate can become a stored memory.

## Non-goals

- No automatic persistence or prompt injection.
- No claim that deterministic patterns understand arbitrary prose.
- No secret detection yet; that is the immediate next roadmap gate.
- No user/project ownership, relevance ranking, management UI, or summaries.

## AFTER — implemented candidate pipeline

`apex_ai.memory.extraction` now provides:

- immutable `MemoryCandidate` values with a deterministic content-derived ID, explicit
  kind, verbatim content, and matched rule name;
- `MemoryCandidateExtractor.extract()` with no model or persistence dependency;
- conservative rules for directly stated/named preferences, persistent requests and
  instructions, active work, ongoing/project context, and explicit “remember” requests;
- deterministic case/whitespace-aware deduplication; and
- hard limits of 20,000 input characters, 500 characters per candidate, and five
  candidates per call. Oversized candidate sentences are omitted rather than truncated,
  because truncating a qualification could reverse or distort its meaning.

`build_services()` creates one stateless extractor and makes it available as
`services.memory_extractor`. No chat controller or RAG component invokes it. This makes
the capability composable without prematurely crossing the safety/consent boundary.

Example behavior (shown as classification, not as persisted state):

```text
“I prefer concise Markdown tables for API v2.7 IDs.”
  -> preference / stated_preference

“We're currently migrating project ACME-104 to PostgreSQL 16.”
  -> ongoing_context / active_work

“What does the indexed document say about fever?”
  -> no candidate
```

The candidate text remains verbatim, including `v2.7`, `ACME-104`, and `PostgreSQL 16`.
The deterministic ID hashes a normalized kind/content key and does not require a database.
Equivalent candidates in one extraction result are returned once.

## Interactions and boundaries

- Long-term memory remains at zero records when extraction is called unless some future,
  explicit caller separately writes a record.
- A candidate-like normal chat request was sent through the real API/RAG path in a test;
  the independent memory store remained empty.
- Existing conversation history still persists normally and remains the only history
  supplied to the current prompt path.
- Document retrieval, citations, providers, and query processing were not changed.
- The extractor examines only text explicitly passed as `user_message`; it never scans
  assistant output, stored conversations, documents, or files.

## Tradeoffs

Rules are intentionally narrower than an LLM classifier. They are fast, offline,
auditable, and do not disclose text to a remote provider, but they will miss implicit
preferences and unusual phrasing. That is safer than broad retention at this stage. The
matched rule is retained so later confirmation UI can explain why a statement was
suggested rather than presenting an opaque score.

Phase 43's raw extractor can recognize an explicit “remember” sentence containing data
that should not be retained. It is not exposed or automatically called. Phase 44 must add
a fail-closed safety screen before any candidate reaches confirmation or persistence.

## Verification

Focused extraction/storage/API regression run:

```text
.venv/bin/python -m pytest -q \
  tests/test_memory_extraction.py tests/test_long_term_memory.py tests/test_api_ui.py
26 passed, 1 warning in 4.45s
```

The eight new tests cover exact-term preservation, preference/context classification,
ordinary-message rejection, explicit/persistent rules, deduplication and limits,
non-truncating bounds, zero-write extraction, runtime wiring, and a candidate-like chat
request that leaves the store empty.

Complete regression suite after implementation:

```text
.venv/bin/python -m pytest tests/ -q
160 passed, 3 warnings in 10.72s
```

The warnings are the same dependency deprecation and two intentionally exercised legacy
environment-variable warnings present before this phase; no test failed.
