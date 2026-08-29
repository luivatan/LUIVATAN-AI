# Apex AI Phase 48 — Project Memory

- **Reviewed:** 2026-08-29 (America/Chicago)
- **Baseline:** `ba2efa7` (Phase 47 relevant memory retrieval)
- **Finding:** genuinely blocked on a forward dependency. Documented rather than
  built around, per the roadmap's own no-fake-features rule.

## The ask and why it can't be honestly built yet

Phase 48 asks for "project-specific context containing project instructions,
conversations, and documents." That presupposes a **Projects** data model — a way
to group a set of conversations and a set of documents under one named workspace
with its own instructions. That data model does not exist anywhere in Apex AI today:

- `apex_ai/memory/conversations.py`'s `conversations` table has no `project_id` or
  equivalent grouping column — every conversation is global.
- `apex_ai/documents/service.py`'s document registry is one flat, global collection
  — there is no concept of a document belonging to one project versus another.
- There is no `projects` table, no project API routes, no project UI, anywhere in
  the codebase.

The roadmap itself schedules building this data model as **Phase 71 — Projects**
(Section 7, "Projects, Agents & AI Features"), with Phase 72 adding project
instructions on top of it. Phase 48 sits in Section 4 (Memory), ahead of that, which
means "project memory" is asking to scope long-term memory to a container that the
roadmap's own later phase hasn't built yet.

## What would happen if this were built anyway

Adding a speculative `project_id` column to `long_term_memories` now, with no
`projects` table to reference and no UI to assign a memory to a project, would
produce exactly the kind of half-finished, ungrounded feature the roadmap's own
development rule explicitly forbids ("Do not create fake features... Never add
complexity merely because a technology is popular"). It would also very likely need
to be redesigned once Phase 71 defines what a project actually is structurally
(does a conversation belong to exactly one project? can projects nest? are
documents shared across projects or owned by one?) — questions Phase 71 has to
answer regardless of anything built here first.

## What this phase does instead

Nothing is implemented. This is the honest record of why, so a future session
doesn't have to re-discover the same forward dependency, and so "Phase 48: skipped"
doesn't read as an oversight. When Phase 71 (Projects) lands with a real
`projects` table and API, revisit this phase directly: at minimum, scope
`LongTermMemoryStore.list()`/relevant-memory retrieval (Phase 47) to an optional
project filter, following the same "reuse the existing store, add the missing
grouping key" pattern Phase 46 already used for memory CRUD.

## Verification

| Check | Result |
|---|---|
| Full test suite (`python -m pytest tests/ -q`) | 244 passed, 3 skipped (no code change) |
| Searched the codebase for any existing project/workspace concept | None found — confirmed genuinely absent, not just under-documented |
