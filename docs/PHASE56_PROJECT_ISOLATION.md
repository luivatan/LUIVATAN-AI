# Apex AI Phase 56 — Project Isolation

- **Reviewed:** 2026-08-29 (America/Chicago)
- **Baseline:** `e035226` (Phase 54/55 completed — conversations, memory, and
  documents all isolated per account)
- **Finding:** genuinely blocked on the same forward dependency as Phase 48.
  Documented rather than built around, per the roadmap's own no-fake-features
  rule.

## The ask and why it can't be honestly built yet

Phase 56 asks to "ensure project data cannot leak between unrelated
projects." That presupposes the same **Projects** data model Phase 48
(Section 4, Project Memory) was blocked on: a way to group conversations and
documents under a named workspace. Re-checked directly for this phase rather
than assumed carried-over: a fresh search of the codebase (`project_id`,
`class Project`, `CREATE TABLE ... project`) still finds nothing outside
Phase 48's own documentation of the gap. Phase 54/55's account-isolation work
(conversations, long-term memory, and now documents — see
[`docs/PHASE54-55_AUTHORIZATION_AND_ISOLATION.md`](PHASE54-55_AUTHORIZATION_AND_ISOLATION.md))
did not add a project concept either; it isolates by *account*, which is a
different grouping than "project" and doesn't substitute for it.

The roadmap itself schedules building the project data model as **Phase 71 —
Projects** (Section 7). Phase 56 sits in Section 5 (Security), ahead of that,
which means "project isolation" is asking to secure a boundary that doesn't
exist yet.

## What would happen if this were built anyway

There is nothing to isolate without a `projects` table and a way to assign a
conversation or document to one. Adding isolation logic for a container that
doesn't exist would be exactly the kind of ungrounded, speculative feature
the roadmap's own development rule forbids. Worse than Phase 48's case: at
least Phase 48 could be described in terms of an existing store
(`long_term_memories`) gaining a hypothetical extra filter column. Phase 56
has no existing store to describe at all — "project isolation" has no
referent yet.

## What this phase does instead

Nothing is implemented. When Phase 71 (Projects) lands with a real `projects`
table, revisit this phase directly and give it the same `user_id`-required
treatment Phase 54/55 gave conversations, long-term memory, and documents:
every project-scoped store method should require both a `user_id` (who) and
a `project_id` (which project) and treat a missing/mismatched owner-or-project
as "not found," never "forbidden" — same rationale, same pattern, one more
dimension.

## Verification

| Check | Result |
|---|---|
| Full test suite (`python3 -m pytest -q`) | 296 passed, 3 skipped (no code change) |
| Searched the codebase for any existing project/workspace concept | None found — confirmed genuinely absent, not just under-documented |
