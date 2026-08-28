# Apex AI Phase 41 — Conversation Context

**Audit date:** 2026-08-27 (America/Chicago)
**Roadmap scope:** Phase 41 only — bounded short-term conversation context

This phase follows the roadmap rule to inspect before modifying. It does not add
long-term memory, preference extraction, user accounts, subscriptions, or deployment.
Conversation text remains separate from documentary evidence and can never become a
citation.

## BEFORE — exact current architecture

Apex AI currently has two compatible conversation-history paths:

1. `ConversationMemory` stores up to `APEX_MEMORY_TURNS` question/answer pairs in a
   JSON file. The compatibility API, CLI, and legacy UI use this process-wide memory.
2. The browser uses `ConversationStore`, a SQLite store containing real conversations
   and messages. For each request, `ConversationMemoryAdapter` exposes completed or
   stopped prior question/answer pairs for only the selected conversation. It excludes
   the pending user message and is read-only during generation; the streaming controller
   persists the final assistant message and citation payload.

The generation path is:

```text
selected conversation
  -> recent paired turns (configured storage/retrieval limit: 8 by default)
  -> RagEngine.prepare()
  -> QueryProcessor (may use latest prior user question for a follow-up)
  -> format_history() (hard-coded latest 3 turns)
  -> prompt section labeled "context only, not evidence"
  -> configured LLM
```

The evidence path remains separate:

```text
indexed documents -> retrieval -> budgeted numbered evidence -> citations
```

Existing safeguards already working before this phase:

- browser conversations are isolated by conversation ID;
- the currently pending question is not duplicated in history;
- conversation history is explicitly labeled as non-evidence in the grounded prompt;
- citations are generated only from final document context chunks;
- persisted history is bounded for the compatibility JSON memory;
- corrupted JSON memory is backed up instead of crashing startup;
- browser history supports create/open/search/rename/delete/regenerate/stop.

Baseline focused command before Phase 41 changes:

```text
.venv/bin/python -m pytest tests/test_memory.py tests/test_conversations_web.py tests/test_engine.py -q
33 passed, 1 warning in 2.78s
```

## Gaps found

1. Prompt history is limited to three turns, but that limit is hard-coded and independent
   of configuration.
2. Prior assistant messages are clipped to 400 characters, while prior user messages are
   not character-bounded. Three maximum-size browser questions can therefore add tens of
   thousands of characters to a prompt.
3. `APEX_MEMORY_TURNS` conflates retained/retrieved history with the smaller amount that
   should be sent to a model.
4. Query follow-up expansion receives the raw recent turns and can concatenate an
   arbitrarily long prior question into a retrieval query.
5. Prior assistant answers retain old `[n]` citation markers, and compatibility memory
   also stores an engine-generated `Sources:` footer. Those stale marker numbers and
   source labels must not be mistaken for fresh documentary evidence on a later turn.
6. History formatting is repeated at context-budget and prompt-construction boundaries
   instead of producing one auditable, reusable result.
7. Developer diagnostics do not report how many history turns were included, dropped, or
   truncated.

## Incremental Phase 41 plan

- Add a dedicated, dependency-free short-term context builder.
- Keep newest complete turns under independently configurable turn, total-character, and
  per-message limits.
- Preserve both the beginning and end when a long message must be shortened.
- Remove stale generated `[n]` markers and Apex AI's legacy `Sources:` footer from prior
  assistant text so they cannot collide with the next turn's evidence numbering.
- Build history once per prepared turn and reuse one bounded result for query analysis,
  model-window accounting, and generation.
- Expose only bounded history diagnostics through the existing developer-only trace.
- Preserve all existing stores, APIs, LLM providers, RAG evidence rules, and entry points.
- Add focused boundary/integration tests, rerun the complete suite, and measure the strict
  bound rather than claim subjective memory quality.

## AFTER — implemented architecture

```text
selected JSON/SQLite conversation
  -> recent completed question/answer pairs
  -> build_conversation_context()
       - newest contiguous turn window
       - configured turn limit
       - configured per-message limit
       - configured strict total-character limit
       - beginning-and-end-preserving truncation
       - stale citation-marker and generated Sources-footer removal
  -> one immutable ConversationContext result
       ├── bounded turns -> conservative follow-up query analysis
       ├── exact text -> model-window evidence budgeting
       ├── exact text -> grounded generation prompt
       └── counts/text -> developer-only diagnostics
```

Document evidence still follows its independent path and remains the only citation
source. `use_memory=false` produces an empty conversation context. The implementation
adds no long-term memory and does not silently infer or store user preferences.

### Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `APEX_MEMORY_TURNS` | `8` | Maximum recent pairs requested from compatibility memory/adapters; also the JSON retention limit |
| `APEX_HISTORY_TURNS` | `3` | Newest complete turns eligible for one model request |
| `APEX_HISTORY_CHAR_LIMIT` | `2400` | Strict total characters of prior conversation supplied to one turn |
| `APEX_HISTORY_MESSAGE_CHAR_LIMIT` | `1000` | Strict characters retained from each prior user or assistant message |

Setting any `APEX_HISTORY_*` limit to zero disables the corresponding short-term
context. These limits are independent of the documentary `APEX_CONTEXT_CHAR_LIMIT`.

### Files changed

- `.env.example` — documents retention versus prompt-history limits.
- `README.md` — documents bounded context behavior and configuration.
- `apex_ai/config/settings.py` — environment-backed Phase 41 limits.
- `apex_ai/memory/context.py` — dedicated bounded short-term context builder and
  diagnostics.
- `apex_ai/memory/__init__.py` — exports the new small abstraction.
- `apex_ai/rag/engine.py` — builds history once, reuses it at every downstream boundary,
  accounts for its exact size, and records timing/debug information.
- `apex_ai/rag/prompts.py` — accepts exact prepared history while preserving the old
  formatting API.
- `docs/CHAT_INTERFACE_ARCHITECTURE.md` — updates the memory/evidence boundary.
- `docs/PHASE41_CONVERSATION_CONTEXT.md` — this audit and measured report.
- `tests/test_conversation_context.py` — strict limits, ordering, truncation, source-footer
  removal, opt-out, prompt reuse, and engine integration.
- `tests/test_api_ui.py` — developer diagnostics boundary.
- `tests/test_config.py` — environment configuration.

### New dependencies

**None.** The builder uses Python's standard library and existing dataclasses.

## Verification

Baseline focused suite before modification:

```text
33 passed, 1 warning in 2.78s
```

Final focused Phase 41/RAG/API suite:

```text
82 passed, 3 warnings in 7.73s
```

Final complete suite:

```text
.venv/bin/python -m pytest tests/ -q
143 passed, 3 warnings in 15.31s
```

The warnings are the existing upstream TestClient deprecation and two deliberately
exercised legacy-environment deprecations.

The tests verify that:

1. only the configured newest contiguous turn window is selected;
2. total history never exceeds its hard character limit;
3. each prior message is independently bounded;
4. a shortened message preserves both beginning and end when space permits;
5. the current pending browser question is not duplicated as history;
6. `use_memory=false` excludes all prior conversation context;
7. stale citation markers and generated legacy source footers do not re-enter later
   prompts or collide with new evidence numbering;
8. one prepared context's bounded turns and exact rendered text are reused for query
   analysis, context accounting, and model generation;
9. history remains separate from document context and cannot create citations;
10. bounded counts/text appear only through the existing configuration-gated developer
    trace; and
11. existing conversation CRUD, streaming, regeneration, RAG, API, CLI, and legacy
    interface tests continue to pass.

## Measured behavior and performance

A deliberately adversarial eight-turn fixture used 20,000-character prior user messages
and 5,000-character assistant messages. The previous hard-coded formatter produced
`61,334` history characters. With default Phase 41 limits, the exact output was `2,018`
characters, one newest turn, and two visibly shortened messages—below the configured
`2,400`-character ceiling.

Local formatting microbenchmark:

| Fixture | Before mean | After mean |
|---|---:|---:|
| Eight normal short turns (latest three formatted), 20,000 iterations | 0.903 µs | 10.338 µs |
| Eight maximum-size turns, 5,000 iterations | 4.375 µs | 131.666 µs |

The added validation is measurably slower than slicing one assistant string, but remained
below `0.14 ms` even for the adversarial fixture on this machine. This is a local
microbenchmark, not a production latency guarantee.

The isolated 19-case deterministic hashing RAG evaluation was rerun after integration.
Its measured quality fields were unchanged: source/page recall `100%`, candidate MRR
`0.944`, post-rerank MRR `0.972`, context lexical proxy `0.963`, and refusal accuracy
`100%`. Mean conversation-context preparation was `0.02 ms`; mean total RAG preparation
was `8.09 ms`. The local runtime report is
`eval/reports/eval-20260828-042613.json` and is intentionally ignored by Git.

As before, hashing embeddings verify deterministic plumbing rather than production
semantic quality. No production LLM was available, so this phase does not claim improved
subjective answer quality or production generation latency.

## Remaining problems and intentionally deferred work

- Recency is the only selection strategy. It does not yet summarize older discussions;
  that belongs to roadmap Phase 50.
- This is not long-term memory and stores no extracted preferences; those begin at Phase
  42 and require separate consent/safety design.
- Messages are retained by the existing stores as entered. Secret/sensitive-memory
  detection and confirmation are explicitly deferred to roadmap Phases 43–45.
- Middle truncation can necessarily omit a detail when a single message exceeds the
  configured budget, although both ends are retained where space permits.
- Character limits and the engine's four-characters-per-token model-window estimate are
  not exact provider tokenization.
- `ConversationStore.recent_turns()` currently reconstructs pairs from all messages in a
  selected SQLite conversation before trimming the returned list. It does not send those
  extra messages to the model, but very large histories may eventually need a bounded SQL
  query or summaries.
- There is no human benchmark proving that three turns is ideal for every corpus/model.
  The value is configurable and should be tuned from real use rather than assumed.
- The application remains a local single-user system. Multi-user isolation and
  authentication belong to roadmap Phases 51–60.

## Beginner-friendly explanation

Conversation storage and conversation context are different things. Storage lets the UI
show an old chat later. Context is the much smaller excerpt copied into the next model
request so phrases such as “what about the second one?” make sense.

Sending an entire conversation forever would eventually overflow the model window, slow
generation, and crowd out retrieved document evidence. Phase 41 therefore takes the
newest few complete exchanges, limits each message, and enforces one final hard ceiling.
The same bounded result is used everywhere in the turn, so retrieval and generation do
not silently see different histories.

Most importantly, remembered conversation is still not documentary proof. It can help
Apex AI understand what the user means, but only freshly retrieved numbered document
blocks can support factual claims and citations.
