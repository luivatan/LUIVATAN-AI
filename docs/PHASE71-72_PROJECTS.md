# Apex AI Phase 71-72 — Projects & Project Instructions

- **Completed:** 2026-08-30 (America/Chicago)
- **Baseline:** `bec1de0`, immediately following Phase 70 (Large Document Handling)
- **Scope:** Phase 71 — "Create project workspaces containing conversations,
  instructions, and documents." Phase 72 — "Allow users to define
  project-specific instructions." Documented together: Phase 72's
  `instructions` column is part of Phase 71's schema from the start, and
  threading it into the prompt is a small, direct continuation of the same
  change rather than a separate one.

## Design: a project is a name + instructions + a pointer to a collection

A project does not invent a new document-association mechanism. Phase 66/67
already built exactly what "a project's documents" needs — a named
`Collection` and `IngestionService.document_ids_for_collection()` to resolve
its membership for retrieval scoping. A `Project` reuses that wholesale: it
holds only its own identity (`name`, `instructions`) plus a `collection_id`
pointer to an *existing* collection. "This project's documents" is exactly
"the documents in the collection this project points at."

Conversations reference a project the same way they already reference a
standalone collection (Phase 67): a new `conversations.project_id` column,
default `""` (no project — every conversation before this phase). When a
conversation is in a project, the project's own `collection_id` and
`instructions` take over for that conversation:

- **Retrieval scoping** — `document_ids` resolves from `project.collection_id`
  instead of the conversation's own standalone `collection_id`. A
  conversation *not* in a project keeps Phase 67's behavior unchanged.
- **Prompt instructions** (Phase 72) — `project.instructions`, when non-empty,
  is woven into the LLM prompt as its own clearly separated, never-evidence,
  never-cited block — the same pattern Phase 47's confirmed-memory block and
  Phase 50's conversation-summary block already established.

This precedence is implemented once, in `api/chat.py`'s `_resolve_scoping()`,
and used by `stream_chat` for both a persisted conversation and one lazily
created on the first message of a chat.

## What this phase adds

- **`apex_ai/projects/store.py`** — `ProjectStore`, a new small SQLite store
  following the exact `CollectionStore` pattern (Phase 66): every method
  takes `user_id` first, and a missing or mismatched owner is "not found,"
  never a distinct "forbidden." `update()` treats `None` as "leave this field
  unchanged" for each of `name`/`instructions`/`collection_id` independently,
  distinct from an explicit `""` which clears `instructions`/`collection_id`.
- **`apex_ai/memory/conversations.py`** — guarded `ALTER TABLE conversations
  ADD COLUMN project_id TEXT NOT NULL DEFAULT ''` (SQLite has no portable
  `ADD COLUMN IF NOT EXISTS`, so existing databases are checked first, same
  as every earlier additive schema change here); `create(..., project_id="")`;
  `set_project()`; `unassign_project()` (clears every conversation's
  reference to a deleted project, same precedent as
  `IngestionService.unassign_collection` — conversations are never deleted as
  a side effect of deleting their project); an optional `project_id` filter
  on `list()`. `unassign_project()` deliberately does not touch
  `updated_at` — bumping it for every affected conversation would reorder the
  user's whole conversation list as a side effect of deleting a project they
  didn't touch those conversations to cause, the same reasoning Phase 50's
  `update_summary()` already established for its own bookkeeping write.
- **`apex_ai/rag/prompts.py`** — `build_messages()` gained a keyword-only
  `project_instructions` parameter, rendered as its own block ("Project
  instructions … not evidence, never cite") ahead of the user-context and
  summary blocks, omitted entirely when empty. `SYSTEM_GROUNDED` gained rule
  10 stating instructions never override the anti-hallucination rules above
  and are never evidence.
- **`apex_ai/rag/engine.py`** — `RagEngine.ask()` and `ask_stream()` both
  gained `project_instructions`, threaded straight into `build_messages()`.
- **`apex_ai/api/projects.py`** — new CRUD router (`GET/POST /projects`,
  `GET/PATCH/DELETE /projects/{id}`), mirroring `api/collections.py`'s
  structure. `PATCH` uses a `clear_collection: bool` flag rather than
  overloading `collection_id: null`, because JSON `null` and an omitted field
  both parse to Python `None` — there is no way to distinguish "leave the
  collection unchanged" from "clear it" through the field alone. Deleting a
  project calls `conversations.unassign_project()`, the same
  never-cascade-delete precedent Phase 66 set for collections.
- **`apex_ai/api/chat.py`** — `ConversationCreate`/`ChatStreamRequest` gained
  `project_id` (validated the same way `collection_id` already is); new
  `PATCH /conversations/{id}/project`; `GET /conversations` gained an
  optional `project_id` filter; `_resolve_scoping()` implements the
  collection/instructions precedence described above and replaces the
  inline `document_ids` resolution `stream_chat` had from Phase 67.
- **`apex_ai/config/settings.py`, `.env.example`, `README.md`** —
  `projects_db_path` / `APEX_PROJECTS_DB_PATH` (default `data/projects.db`),
  same shape as Phase 66's `collections_db_path`.
- **`apex_ai/runtime.py`** — `services.projects = ProjectStore(...)`, built
  right after `services.collections`.
- **Frontend** — a new "Projects" page (sidebar nav item, `#projectList`)
  with create/rename/delete, an inline instructions textarea (saved on
  blur), and a linked-collection picker per project card, plus a "New chat"
  button that pre-selects the project for the next lazily-created
  conversation. A topbar project picker (`#conversationProject`) sits beside
  the existing collection picker; selecting a project disables the
  collection picker (with an explanatory tooltip) since the project's own
  collection governs retrieval once one is set — this mirrors the real
  backend precedence rather than presenting a control that would silently
  do nothing. A composer upload during a project-scoped conversation resolves
  its target collection from the project's `collection_id`, so an attachment
  is immediately retrievable in that same chat, the same guarantee Phase 67
  already gives a directly collection-scoped conversation.

## Files

- `apex_ai/projects/__init__.py`, `apex_ai/projects/store.py` (new)
- `apex_ai/api/projects.py` (new)
- `apex_ai/memory/conversations.py`, `apex_ai/rag/prompts.py`,
  `apex_ai/rag/engine.py`, `apex_ai/api/chat.py`, `apex_ai/api/schemas.py`,
  `apex_ai/api/server.py`, `apex_ai/config/settings.py`, `apex_ai/runtime.py`
- `apex_ai/web/templates/index.html`, `apex_ai/web/static/app.js`,
  `apex_ai/web/static/app.css`
- `.env.example`, `README.md`
- `tests/test_projects.py` (new), plus additions to
  `tests/test_conversation_context.py` and `tests/test_conversations_web.py`

## Verification

| Check | Result |
|---|---|
| Full test suite (`python3 -m pytest -q`) | 370 passed, 3 skipped |
| `tests/test_projects.py` | 13 passed |
| `tests/test_conversations_web.py` | all passed (project CRUD, project-scoped retrieval, project-instructions-in-prompt, delete-unassigns, lazy-create, project filter) |
| `node --check apex_ai/web/static/app.js` | OK |
| `ruff check` on every touched/new file | only the pre-existing `apex_ai/rag/engine.py:381` `BLE001` finding (verified unchanged from baseline — not touched by this phase) |

## Deliberately not done in this phase

- **No cascading conversation delete when a project is deleted.** Matches
  the Phase 66 collection precedent exactly: a project is an organizational
  label, not an owner of its conversations' lifecycle. Deleting it only
  clears `project_id` back to `""`.
- **No "project documents" endpoint separate from `GET /documents?collection_id=`.**
  A project's documents are exactly its linked collection's documents;
  Phase 66's existing filter already serves that with no new code.
- **No per-project retrieval-tuning knobs (top-k, thresholds, etc.).**
  Out of scope for "instructions" and "documents" as specified; every
  project shares the account's configured retrieval settings, same as every
  conversation always has.
- **No project-level conversation count on the Projects page.** Showing an
  accurate count would mean either an extra `GET /conversations?project_id=`
  request per card on every page load, or duplicating that count into the
  `Project` schema and keeping it in sync on every conversation
  create/move/delete. Neither is justified by what Phase 71 actually asks
  for; a user can already see a project's conversations by filtering the
  sidebar-adjacent conversation list once that UI is built, or via the API.
