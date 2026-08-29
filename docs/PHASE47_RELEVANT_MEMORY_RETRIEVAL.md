# Apex AI Phase 47 — Relevant Memory Retrieval

- **Completed:** 2026-08-29 (America/Chicago)
- **Baseline:** `9e33047` (Phase 46 memory management)
- **Scope:** "Retrieve only memories relevant to the current request." The first
  phase where confirmed long-term memory (Phase 42/45) legitimately reaches a
  generation prompt — every earlier phase deliberately kept it out.

## Design decision: two kinds, two treatments

`preference` memories describe **how** to answer (tone, format — "keep answers
concise"). That applies to every question, not just topically similar ones, so
preferences are always included, bounded to a small recency-capped count.
`ongoing_context` memories describe **what** the user is currently doing (a project,
a task). That's only useful when the current question is actually about it, so
context items are filtered by keyword overlap with the question — an unrelated
question shouldn't see stale context about a different task. This isn't arbitrary:
it's the same kind taxonomy Phase 43's extraction rules already use
(`stated_preference` vs. ongoing-context signals), now given different retrieval
behavior because they mean different things.

Selection is deterministic keyword overlap (`apex_ai/memory/relevance.py`), not an
embedding search. `long_term.py`'s own docstring already describes the module as
"intentionally disconnected from model prompts" — reusing that same
dependency-free philosophy here, rather than wiring `services.embeddings` into the
memory module (a new dependency, a new embedding-versioning surface, and per-turn
embedding calls) for a handful of short preference/context strings, was a deliberate
choice, not an oversight. Long-term memory entries are short, explicit,
user-authored statements; keyword overlap is a reasonable, fully local, zero-latency
signal for "is this the same topic," which is what relevance means here.

## Change

- **`apex_ai/memory/relevance.py`** (new): `select_relevant_memories()` and
  `format_memory_text()`, pure functions, no I/O.
- **`apex_ai/rag/prompts.py`**: `build_messages()` gained an optional `memory_text`
  parameter. When present, it renders as its own clearly labeled block — `"User
  context (preferences/situation, not evidence, never cite)"` — before the
  conversation-history block, never merged with it or with retrieved evidence. Rule
  8 was added to the system prompt: use it for tone/relevance, never as fact, never
  cited. When `memory_text` is empty (the overwhelming majority of calls — no
  memory, or nothing selected as relevant), the prompt is byte-for-byte identical to
  before this phase.
- **`apex_ai/rag/engine.py`**: `RagEngine` gained an optional `long_term_memory`
  parameter, separate from the existing `memory` parameter (which is *short-term
  conversation history* — an unfortunately close name for a very different thing;
  the constructor docstring/comment calls this out explicitly to prevent future
  confusion). `prepare()` selects relevant memories (guarded by `use_memory` — the
  same per-request toggle that already gates short-term history — and the new
  `memory_prompt_use` setting) and stores the result on `PreparedTurn.memory_text`,
  which all three generation paths (`ask`, `ask_stream`, `debug`) pass through to
  `build_messages()`. A failure here is caught and degrades to no personalization
  (`turn.memory_text` stays `""`) rather than breaking the chat turn — matching how
  every other optional component in this app already fails (Phase 42's own stated
  design: a long-term-memory failure must not disable core chat).
- **`apex_ai/config/settings.py`**: `memory_prompt_use: bool = True`
  (`APEX_MEMORY_PROMPT_USE`). Default **on** — confirmed memories already passed
  through explicit safety screening (Phase 44) and explicit user approval (Phase 45)
  before they could exist at all, so using them for their stated purpose by default
  is honoring user intent, not a surprise. An operator who later switches to a
  remote provider and wants stricter data minimization can turn it off.
- **`apex_ai/runtime.py` / `apex_ai/api/chat.py`**: both `RagEngine` construction
  sites now pass `long_term_memory=services.long_term_memory` (the actual runtime
  wiring; everything above is inert without this).
- **`/health`**: `long_term_memory.prompt_use` now reports the real computed value
  (store present AND setting on) instead of the hardcoded `False` placeholder that
  existed since Phase 42, when this genuinely didn't exist yet.
- Corrected three now-false claims this phase made stale: the browser toast after
  approving a memory ("prompt use is not enabled yet"), and two README passages
  ("neither is read by chat generation yet" / "not prompt-connected in Phase 42").
  Leaving fixed, wrong claims in a shipped product is exactly the kind of thing the
  roadmap's ground rules forbid.

## A test that encoded the old invariant, and what replaced it

`test_phase42_does_not_extract_or_prompt_with_long_term_memory` asserted a private
memory literally never appeared in a prompt — true before this phase, intentionally
false after it for preference-kind memories. Splitting it revealed the test's
fixture (`wired_services` in `test_api_ui.py`) never wired `long_term_memory` into
its `RagEngine` at all, so the assertion was passing **vacuously**, not because the
feature correctly excluded it. Fixed with three new engine-level tests that build a
`RagEngine` with `long_term_memory` genuinely wired: a preference reaches the prompt
but never a citation; unrelated ongoing_context is filtered out; and
`memory_prompt_use=False` disables injection entirely. The equivalent
`test_conversations_web.py` test (used by real `/chat/stream` wiring, which *was*
already correctly wired) was renamed and extended rather than left describing a
behavior the phase intentionally changed.

## Deliberately not changed

- No embedding-based semantic relevance — see "Design decision" above.
- No new relevance-tuning env vars (`max_preferences`, `max_context`,
  `min_overlap`) — these are internal constants in `relevance.py` with sane
  defaults (5 preferences, 3 context items, overlap ≥ 1 word). Expanding the config
  surface for knobs nobody has asked to tune yet would be speculative complexity.
- Citations remain built exclusively from `BuiltContext.used_chunks`
  (`RagEngine._citations()`, untouched by this phase) — this is the same
  invariant enforcement Phase 35 established; Phase 47 adds a new prompt input, not
  a new citation source.

## Verification

| Check | Result |
|---|---|
| Full test suite (`python -m pytest tests/ -q`) | 244 passed, 3 skipped |
| New `tests/test_memory_relevance.py` (pure selection-logic unit tests) | 7 passed |
| New engine-level tests: preference always included + never cited, unrelated context filtered, `memory_prompt_use=False` disables injection | Included in the 244 |
| Existing test renamed/extended for the new, intentional Phase 47 behavior rather than left red or deleted | `test_memory_candidate_requires_approval_then_becomes_relevant_context` |
| `ruff check` on every touched file | 1 finding, confirmed pre-existing via `git diff` (unmodified line, already catalogued in Phase 6/9) — zero new findings from this phase's diff |

## Boundaries and remaining unknowns

- Keyword overlap is a coarse relevance signal — a context item phrased with none
  of the question's words but genuinely relevant will be missed, and one sharing
  incidental common words but genuinely unrelated could be included. This is the
  known tradeoff of choosing "no new ML dependency" over "best possible relevance";
  revisiting it with embeddings is a defensible future change if this proves
  insufficient in practice, not a defect being hidden now.
- Preferences have no per-item relevance/expiry model — every confirmed preference
  applies to every future question until deleted (Phase 46). If a user accumulates
  many contradictory preferences over time, the bounded `max_preferences` cap
  controls prompt growth but not which ones "win"; that's Phase 49's problem
  (Memory Conflict Handling), not this one's.
- No measurement of whether memory-personalized answers are actually better —
  this phase is plumbing plus a relevance filter, not an evaluation of answer
  quality; that would need a real LLM and human judgment, same limitation already
  documented for the rest of RAG evaluation (`docs/RAG_PHASE2_REPORT.md`).
