# Apex AI Phase 46 — Memory Management

- **Completed:** 2026-08-29 (America/Chicago)
- **Baseline:** `f8fa749` (Section 3, Phases 21–40)
- **Scope:** "Create a settings area where users can view, delete, or clear memories."

## Audit findings

`LongTermMemoryStore` (Phase 42) already had full CRUD — `list()`, `get()`, `delete()`,
`clear()` — because Phase 42 built the storage boundary generally, not narrowly for the
candidate-confirmation flow. The only real gaps were that nothing exposed this CRUD
over HTTP, and Settings had no UI for it. This phase is almost entirely a thin new
layer over already-correct, already-tested storage code — not a new storage design.

## Change

- **API** (`apex_ai/api/memory.py`): `GET /memory` (optional `?kind=` filter),
  `DELETE /memory/{id}`, `DELETE /memory` (optional `?kind=` filter for "clear only
  preferences" style use, though the UI only exposes "clear all"). These call
  `services.long_term_memory` directly — they do **not** go through
  `MemoryConfirmationService` or the candidate/proposal tables, because confirmed
  memories aren't proposals anymore; conflating the two paths would have been the
  wrong abstraction. Same `503 memory_unavailable` degradation as the existing
  candidate routes when the optional store failed to initialize.
- **Settings UI**: a new "Memory" section lists every confirmed memory (kind badge +
  content + delete button) and a "Clear all memory" action, both behind the existing
  `confirmAction()` confirmation modal used elsewhere for destructive actions. Loads
  on demand when the Settings view opens (`showView("settings") → loadMemories()`),
  matching how the Documents view already lazy-loads.

## Deliberately not changed

- No edit/update UI, even though `LongTermMemoryStore.update()` already exists — the
  phase asks for "view, delete, or clear," not edit. Exposing `update()` later is a
  small addition if a real need shows up; adding it now would be scope beyond the ask.
- No pagination on `GET /memory` — reuses the store's existing `limit=100` default,
  consistent with how `GET /conversations` was already bounded (Phase 7 found that
  gap didn't need re-fixing; same reasoning applies here).

## Verification

| Check | Result |
|---|---|
| Full test suite (`python -m pytest tests/ -q`) | 234 passed, 3 skipped |
| New tests: list (with `?kind=` filter), delete (200 then 404 on repeat), clear-all, and 503 degradation when the store is unavailable | Included in the 234 |
| `node --check apex_ai/web/static/app.js` | Passes |
| `ruff check` on touched files | All checks passed |
