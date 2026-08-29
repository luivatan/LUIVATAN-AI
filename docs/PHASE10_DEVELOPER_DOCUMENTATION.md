# Apex AI Phase 10 — Developer Documentation

- **Completed:** 2026-08-29 (America/Chicago)
- **Baseline:** `7cd5c83` (Phase 9 testing foundation)
- **Scope:** the roadmap asks to document setup, environment variables, architecture,
  commands, and development workflow in beginner-friendly language. This closes
  Section 1 (Foundation & Project Audit).

## Audit findings

Setup, environment variables, and commands were already well documented in
`README.md` (installation, model setup, running, a full configuration table,
ingestion, evaluation, testing, troubleshooting). Architecture had an unusually
thorough dedicated document, `docs/PHASE2_ARCHITECTURE_MAP.md`. Two real gaps:

1. **No development-workflow document.** `git log` shows a clear, consistently
   followed convention — one numbered roadmap phase per unit of work, inspect before
   changing, test before moving on, write a `docs/PHASEN_*.md` for each phase with
   real code changes — but nothing wrote that convention down. A new contributor (or
   a future agent session starting cold) would have to reverse-engineer it from git
   history and by reading several phase docs back to back.
2. **The architecture map had gone stale in specific, checkable places.** It is
   explicitly pinned to its Phase 2 baseline commit (by design — it's a snapshot),
   but three of its claims were flatly contradicted by later, completed phases: "no
   CI workflow exists" (Phase 9 added one), "provider readiness... not represented
   accurately in the... health API" (Phase 8 fixed this for the database check), and
   the API map's `/health` row not mentioning what Phases 7–8 added. A document that
   confidently asserts something false is worse than one that says "unknown."

## Change

- **`CONTRIBUTING.md`** (new, repo root): a setup walkthrough that explains *why*
  each step matters (what a venv is for, why `.env` is gitignored, why tests don't
  need a real model) for a genuine beginner, a command reference for daily work, an
  explicit write-up of the roadmap-phase workflow (inspect → implement the smallest
  real fix → test → document → commit), and a "where things live" table pointing to
  the existing architecture/audit/chat-interface docs instead of duplicating them.
- **`docs/PHASE2_ARCHITECTURE_MAP.md`**: added section 19, "Amendments after later
  phases" — a small table correcting the specific claims Phases 8 and 9 superseded,
  each with a pointer to the phase that changed it, plus one inline correction in the
  deployment section's "Not present" list. The rest of the document (trust
  boundaries, data schemas, security posture, invariants) was re-read against the
  current codebase and remains accurate; it was deliberately not rewritten wholesale
  — a full rewrite would have discarded a detailed, still-correct snapshot to fix
  three sentences.
- **`README.md`**: added a pointer to `CONTRIBUTING.md` from the Development section.

## Deliberately not changed

- No rewrite of `docs/PHASE2_ARCHITECTURE_MAP.md`'s baseline commit, diagrams, or
  the bulk of its content — see above.
- No new architecture diagrams beyond what section 19's table needed — the existing
  Mermaid diagrams in the Phase 2 map already cover system context, process
  entry points, runtime composition, RAG flow, and ingestion flow, and remain
  accurate.
- No API reference doc beyond what Phase 7 already delivered (`/openapi.json` /
  `/api/docs`, which are now accurate per-route contracts) — generating a static
  copy of that would just be a second, driftable copy of the same information.

## Verification

| Check | Result |
|---|---|
| Full test suite (`python -m pytest tests/ -q`) | 231 passed, 3 skipped (docs-only phase; unchanged) |
| Every relative link added in `CONTRIBUTING.md` and the README pointer | Manually checked against actual file paths/anchors |
| `docs/PHASE2_ARCHITECTURE_MAP.md` section 19 claims | Verified against the actual Phase 7/8/9 diffs, not against those phases' own docs alone |

## Boundaries and remaining unknowns

- "Beginner-friendly" is a judgment call, not a measured property — this was not
  validated against an actual first-time contributor.
- `CONTRIBUTING.md` documents the workflow this repository has followed so far; it
  is not a promise that every future phase will look identical (e.g., a phase that
  turns out to need no code change should still get a short doc explaining why, not
  be forced into an unnecessary diff).
- No auto-generated architecture diagrams or API docs pipeline — everything here is
  hand-maintained prose/Mermaid, with the staleness risk that implies (mitigated by
  section 19's precedent of amending rather than silently trusting old claims).
