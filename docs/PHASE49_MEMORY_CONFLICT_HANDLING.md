# Apex AI Phase 49 — Memory Conflict Handling

- **Completed:** 2026-08-29 (America/Chicago)
- **Baseline:** `523268d` (Phase 48 deferral doc)
- **Scope:** "Detect outdated or conflicting memories and handle them safely."

## What already existed vs. the real gap

`LongTermMemoryStore.propose_candidate`/`approve_candidate` (Phase 42) already
handle **exact** duplicates: a casefold/whitespace-normalized identical statement
reuses the existing memory instead of creating a second row. The real gap was
**near**-duplicates that actually conflict — "prefers concise answers" confirmed,
then later "prefers detailed answers" proposed. Same kind, clearly about the same
thing, clearly contradictory, and the exact-match check does nothing for it (the
strings aren't equal).

## "Handle them safely" — the design choice

The safest handling of a detected conflict is **not resolving it automatically**.
Silently overwriting the old memory could discard something the user still wants;
silently keeping both without saying anything hides a real contradiction from the
user who is in the best position to judge which one is current. So this phase is
detection plus a clear warning, not auto-resolution:

- A conflict is detected server-side (`MemoryConfirmationService.find_conflict`)
  whenever a pending candidate is proposed or listed, by finding the most
  keyword-similar **existing confirmed memory of the same kind** via
  `find_similar_memory()` in `apex_ai/memory/relevance.py` (same keyword-overlap
  approach Phase 47 already established — reused, not reinvented).
- It's surfaced as a `conflicts_with` field on the candidate payload, both in the
  `/chat/stream` "meta" event (where the confirmation card already appears, Phase
  45) and in `GET /memory/candidates`.
- The confirmation card now shows *"May conflict with a saved memory: `<old
  content>`"* alongside the existing Remember/Don't save buttons.
- Approving still just adds the new memory — nothing is deleted or overwritten.
  Resolving a real conflict (deleting the stale one) reuses the Phase 46
  memory-management UI that already exists for exactly this: the user reads the
  warning, approves if they still want the new memory saved, and deletes the old
  one from Settings → Memory if it's actually stale. No new resolution UI was
  built because Phase 46 already is one.

## Verification

| Check | Result |
|---|---|
| Full test suite (`python -m pytest tests/ -q`) | 249 passed, 3 skipped |
| New unit tests: conflict flagged for near-duplicate content, ignored for unrelated content, no existing memories → no conflict, picks the closer of two candidate matches | `tests/test_memory_relevance.py` |
| New integration test: a real conflict surfaces identically through both the chat-stream event and `GET /memory/candidates`, approving does not touch the existing memory | `tests/test_conversations_web.py::test_memory_candidate_flags_a_conflict_with_an_existing_memory` |
| `ruff check` on every touched file | All checks passed |
| `node --check apex_ai/web/static/app.js` | Passes |

## Boundaries and remaining unknowns

- Keyword overlap uses plain word tokens, not stemming — "prefer" and "prefers"
  are different tokens and won't overlap on that word alone. This was caught
  directly while writing the integration test (an initial version used "Prefers
  detailed answers." as the seeded conflicting memory and the conflict silently
  failed to trigger); the fixed test uses "I prefer detailed answers." to match the
  extractor's actual un-stemmed output. This is the same class of limitation
  already documented for Phase 47's relevance filtering — accepted for the same
  reason (no new ML/stemming dependency for short preference strings), not hidden.
- No conflict detection for `ongoing_context` beyond the same overlap mechanism —
  two overlapping but non-contradictory context notes (e.g. two different aspects
  of the same project) could be flagged as a "conflict" when they're not really
  contradictory, just related. The UI wording ("may conflict") is deliberately
  hedged rather than asserting a definite contradiction, for exactly this reason.
- Detection runs on every candidate proposal/listing, scanning all confirmed
  memories of the matching kind — fine at the scale this app operates at (a
  personal, single-user memory list), not evaluated for a very large memory count.
