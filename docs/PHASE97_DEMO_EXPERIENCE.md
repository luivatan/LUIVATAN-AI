# Apex AI Phase 97 — Demo Experience

- **Completed:** 2026-08-30 (America/Chicago)
- **Baseline:** `c659cbb` (Phase 96, landing page)
- **Scope:** "Create a short product demonstration showing: chat → upload
  document → ask question → grounded answer → sources."

## Design: a repeatable seed, and a script grounded in already-verified facts

Two deliverables, matching the two different jobs a demo needs:

1. **`scripts/seed_demo.py`** (new) — a real, idempotent CLI script that
   ingests three of the four documents already used by Phase 2's
   evaluation harness (`eval/docs/sample_first_aid.pdf`,
   `apex_operations.md`, `apex_finance.md`) into a real "Demo: Apex
   Research" collection on the default local account, using the real
   `build_services()` path — the same embedding model and LLM a real
   deployment uses, not a stand-in. Re-running it is safe: it reuses an
   existing "Demo: Apex Research" collection instead of creating a
   duplicate, and re-ingesting an already-indexed document is detected
   and skipped by Apex's own duplicate protection (SHA-256 document
   IDs), so nothing is silently re-embedded.

2. **`docs/DEMO_SCRIPT.md`** (new) — the written walkthrough. Every
   question in it is a real, already-verified fixture from
   `eval/dataset.example.jsonl` (Phase 2), so the "grounded answer" step
   isn't a guess about what the model will say — it's a fact the
   evaluation harness already confirmed is retrievable and correct
   against these exact documents.

## Why one document is deliberately left out of the seed

`eval/docs/burn_care.md` is excluded from `DEMO_DOC_NAMES` on purpose. The
roadmap's scope is explicit about the sequence: *chat → **upload
document** → ask question → grounded answer → sources.* A demo that only
ever shows pre-loaded documents skips the "upload" step the roadmap
actually asks for. `docs/DEMO_SCRIPT.md`'s walkthrough uploads
`burn_care.md` live (it's short — indexes in a couple of seconds) and
then asks the one fixture question grounded in it ("How long should a
burn be cooled with running water?" → *20 minutes*), so the demo shows
the real upload pipeline, not just retrieval against something already
sitting in the database.

## Also demonstrated, and why each step is in there

- **Exact-match retrieval** (APX-447 identifier + date) — shows the
  keyword channel of hybrid retrieval, not just semantic similarity.
- **A multi-document question** (release owner + 2025 revenue, across
  two different files) — shows evidence fusion across sources with
  separate attribution, not a single-document lookup.
- **A question the documents cannot answer** ("lunar greenhouse oxygen
  quota") — the single most important moment in the script: it shows the
  evidence gate declining to answer rather than hallucinating, which is
  the entire trust proposition the landing page (Phase 96) and this
  product's grounding design are built around. Skipping this step would
  make the demo prove less than the product actually does.

## Verification

Since `scripts/seed_demo.py` is a thin CLI wrapper around already-tested
library code (`CollectionStore.create`/`.list`,
`IngestionService.ingest_path`) — the same "CLI wrapper, verified by a
real end-to-end run rather than a new unit test" pattern Phase 92 used
for `scripts/backup.py`/`scripts/restore.py` — this phase verifies it the
same way: a real, scripted end-to-end run rather than a new pytest
module for logic that's already covered elsewhere.

| Check | Result |
|---|---|
| Full test suite (`python3 -m pytest -q`) | 553 passed, 3 skipped (unchanged — no library code was modified) |
| Real end-to-end run (`HashingEmbeddingProvider`, no network) | first run: all 3 documents `INDEXED`; second run: all 3 correctly reported `DUPLICATE`, same collection ID reused (not duplicated) |
| Real retrieval check against the seeded collection | "What temperature counts as a fever in adults?" retrieved a chunk containing "38" (the fixture's expected fact) via `HybridRetriever`, scoped to the seeded collection's document IDs |
| `ruff check scripts/seed_demo.py` | clean |
| `chmod +x` on the script | done, consistent with the project's other CLI scripts |

## Files

- `scripts/seed_demo.py` (new)
- `docs/DEMO_SCRIPT.md` (new)
- `README.md` — new "Demo" section

## Deliberately not done in this phase

- **No recorded video.** A recording is a presentation artifact, not
  code — and this session cannot produce a real screen recording that
  demonstrates a real model's real inference. `docs/DEMO_SCRIPT.md` is
  written so a person can film one themselves against a real deployment.
- **No scripted/mocked model output.** Every expected answer in
  `docs/DEMO_SCRIPT.md` is a real fact from the source documents that
  the evaluation harness already confirmed is retrievable — but the
  document itself says plainly that exact model wording will vary by
  which LLM provider is configured, rather than presenting a fake
  verbatim transcript.
- **No separate demo-only build or environment** — the demo runs against
  the exact same application code as everything else in this project;
  there is no "demo mode" flag that changes behavior, which would risk
  the demo showing something the real product doesn't actually do.
