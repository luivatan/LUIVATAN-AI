# Apex AI Phase 50 — Long Conversation Summaries

- **Completed:** 2026-08-29 (America/Chicago)
- **Baseline:** `75b1595` (Phase 49 memory conflict handling)
- **Scope:** "Summarize older conversation context when necessary while preserving
  important decisions and unresolved questions." Closes Section 4 (Memory &
  Personalization) except Phase 48, which remains blocked on Phase 71 (Projects).

## What was actually missing

Phase 41's short-term context (`apex_ai/memory/context.py`) already tracks exactly
what's needed to know summarization is due: `ConversationContext.dropped_turn_count`.
But "dropped" meant *dropped* — older turns beyond the live window (`APEX_HISTORY_TURNS`,
newest-first, currently 3) were simply never included again. A long conversation's
early decisions and unresolved questions were permanently invisible to generation
once enough turns accumulated, with no compression step in between.

## Design

- **Trigger and storage are the web chat controller's job, not the engine's.**
  `RagEngine` doesn't own persistent conversation storage — `apex_ai/api/chat.py`
  already does (it's the "thin controller" that persists messages). Summarization
  logic split the same way as Phase 30's query rewriting: `apex_ai/memory/
  summarization.py` is pure decision logic (what needs summarizing, what prompt to
  send) with no I/O; `chat.py` owns the actual LLM call and persistence.
- **What triggers it:** after an assistant message is persisted, check whether more
  messages have fallen out of the live window (`memory_turns * 2`, approximating
  turns→messages) than the conversation's `summarized_message_count` already covers.
  Most turns are a cheap no-op list-slice check; only the occasional turn that
  crosses the boundary pays for an LLM call.
- **What gets summarized:** the *previous* summary (if any) plus exactly the newly
  fallen-out messages — not the whole conversation from scratch every time. This
  keeps the summarization call's own input bounded regardless of how long the
  conversation eventually gets.
- **Storage:** `conversations.summary` and `conversations.summarized_message_count`
  (guarded `ALTER TABLE`, same pattern as Phase 17/46) — deliberately *not* added to
  `Conversation.to_dict()` / the public API. This is prompt-construction bookkeeping,
  not a user-facing field; `updated_at` is untouched by a summary refresh so it never
  reorders the conversation list.
- **Off by default** (`APEX_CONVERSATION_SUMMARY=0`) — the same latency tradeoff
  `APEX_QUERY_REWRITE` already accepts for the identical reason: an extra LLM call,
  gated behind an explicit opt-in rather than silently added to some turns' latency.
  A stored summary, once generated, is *always* read into the prompt regardless of
  this setting — the flag only controls whether *new* summarization work happens, not
  whether an already-computed summary gets used.
- **Prompt injection** mirrors Phase 47's `memory_text` exactly: `build_messages()`
  gained a `summary_text` parameter, rendered as its own block — "Summary of earlier
  conversation (not evidence, never cite)" — with a new system-prompt rule (9)
  extending the same never-evidence-never-cited contract instead of writing a new one.
  `RagEngine` reads it via `getattr(self.memory, "summary_text", None)`, the same
  duck-typed pattern already used for `.recent()`/`.add()` — only
  `ConversationMemoryAdapter` (the SQLite-backed web chat memory) implements it; the
  legacy JSON `ConversationMemory` (CLI/Gradio/`/query`) does not, so this phase is
  correctly scoped to the web chat experience it targets, same as Phase 41 was.

## A real bug this caught before it shipped

The first implementation called `services.active_llm()` for the summarization call —
which independently re-resolves the *configured* provider from settings, not
necessarily the same provider instance actually generating the current turn's answer.
The test suite's fixtures wire a `FakeLLM()` directly into `RagEngine`, bypassing
`services.active_llm()` entirely; running the integration test surfaced this
immediately as a `ModelNotFoundError` (no real model configured in test settings).
Fixed by threading the actual `engine.llm` used for this turn's generation through to
the summarization call instead of re-resolving a provider independently — the correct
behavior in production too: summarization should use the same provider the user's
answer just came from, not risk it diverging.

## Deliberately not changed

- No summarization for the legacy JSON `ConversationMemory` path (CLI, Gradio,
  `/query`) — see "scoped to the web chat" above.
- No UI to view/edit a conversation's summary — it's prompt-construction state, not
  a user-facing feature this phase's wording asked for (contrast with Phase 46, which
  explicitly asked for a memory-viewing UI).
- `memory_turns * 2` is an approximation (turns → messages), not an exact accounting
  of what `ConversationMemoryAdapter.recent()` actually returns after turn-pairing.
  Documented as a known simplification rather than building exact message-to-turn
  accounting for a threshold that only needs to be roughly right.

## Verification

| Check | Result |
|---|---|
| Full test suite (`python -m pytest tests/ -q`) | 257 passed, 3 skipped |
| New `tests/test_memory_summarization.py` (pure decision-logic unit tests: nothing needed while turns fit live, older turns detected, already-summarized turns not re-summarized, incremental growth, prompt construction with/without a previous summary) | 6 passed |
| New integration tests: enabled → summary populates and reaches a later turn's prompt; default (disabled) → summary never populates even across many turns | `tests/test_conversations_web.py` |
| `ruff check` on every touched file | 1 finding, confirmed pre-existing via the same `git diff` method used in Phase 47/49 (unmodified reranker-fallback line, already catalogued) — zero new findings |

## Boundaries and remaining unknowns

- No evaluation of summary *quality* — whether the LLM actually preserves decisions
  and unresolved questions well is a model-quality question, same limitation already
  documented throughout `docs/RAG_PHASE2_REPORT.md` for everything else that depends
  on a real configured model.
- The rolling-summary approach (fold new turns into the existing summary rather than
  re-summarizing from scratch) can compound small inaccuracies over a very long
  conversation, since each pass only sees the *previous summary*, not the original
  turns it was built from. Unmeasured; a real long-conversation, real-model test would
  be needed to know if this matters in practice.
- `max_tokens=300` for the summary call is a fixed constant, not derived from
  `context_char_limit` or any other configured budget — reasonable for a ~200-word
  target but not adaptive if a deployment wants longer summaries.
