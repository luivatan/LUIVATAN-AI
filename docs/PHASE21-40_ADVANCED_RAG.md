# Apex AI Section 3 — Advanced RAG (Phases 21–40)

- **Reviewed:** 2026-08-29 (America/Chicago)
- **Baseline:** `0ff27f6` (Section 2, Phases 11–20)
- **Finding:** every phase in this section is already satisfied by prior work —
  primarily `docs/RAG_PHASE2_REPORT.md` (commits `bb0ccc7`/`961f6ea`, predating this
  session) — with no code gap found on inspection. This doc is the phase-by-phase
  mapping `CONTRIBUTING.md` asks for when a phase needs no new diff, so the
  100-phase roadmap has a real "done, here's why" record for this section instead of
  silence.

## Phase-by-phase mapping

| Phase | Ask | Where it's satisfied |
|---|---|---|
| 21 — RAG Audit | Document the pipeline | `docs/RAG_PHASE2_REPORT.md` §"RAG BEFORE"/"RAG AFTER" is exactly this audit |
| 22 — Better PDF Extraction | Preserve page info | §"CHUNKING": paragraph-level page identity survives sections spanning page boundaries |
| 23 — Better Chunking | Meaningful chunks, headings, context | §"CHUNKING": section-isolated, heading-safe merges, hard overlap bounds |
| 24 — Metadata | doc ID, filename, page, section, chunk ID/index | §"CHUNKING" metadata list; `apex_ai/documents/chunking.py` schema v2 |
| 25 — Embedding Abstraction | Swappable embedding layer | Predates this report (`EmbeddingProvider`); retained, not rebuilt |
| 26 — Vector Retrieval | Multiple candidates, not one | `APEX_SEMANTIC_CANDIDATES` pool per query (§CONFIGURATION) |
| 27 — Keyword Retrieval | BM25 for exact terms | §"RETRIEVAL" → "Exact lexical retrieval": BM25Plus, exact compound tokens |
| 28 — Hybrid Retrieval | Documented fusion strategy | §"Multi-query fusion": weighted, per-query-normalized RRF, formula given |
| 29 — Reranking | Optional compatible reranker | `APEX_RERANKER=auto/cross_encoder/lexical/off`; persistent fallback on failure |
| 30 — Query Rewriting | Resolve follow-ups, preserve exact terms | §"Query behavior": deterministic follow-up expansion; optional LLM path rejects dropped exact terms |
| 31 — Query Decomposition | Subquestion retrieval for multi-part | §"Query behavior": bounded deterministic subqueries on clear clause boundaries |
| 32 — Context Builder | Dedup, prioritize, budget | §"CHUNKING"/context builder: dedup, strict char budget + token reserve |
| 33 — Relevance Filtering | Don't answer from weak evidence | §"Evidence gate": semantic+lexical corroboration required near threshold |
| 34 — Grounded Prompting | Answer from evidence, admit gaps | `apex_ai/rag/prompts.py` source-only contract (§FILES CHANGED) |
| 35 — Citation Pipeline | Citations only from real metadata | §"CITATIONS": built only from `BuiltContext.used_chunks` |
| 36 — Source Viewer | Connect citations to doc/page | §"CITATIONS": source drawer shows source/page-range/section/score/exact text. No in-app PDF-page jump exists (architecture doesn't render original PDFs yet) — the phase's own "when the architecture supports it" qualifier, honestly stated rather than faked |
| 37 — RAG Debug Mode | Dev-only retrieval trace | §"Developer diagnostics": `APEX_RAG_DEBUG=1`-gated `/debug/rag`, OpenAPI-hidden |
| 38 — RAG Evaluation Dataset | Direct/semantic/exact/multi-part/no-answer tests | `eval/dataset.example.jsonl`: 19 categorized items |
| 39 — Retrieval Metrics | Precision, recall, groundedness, citation accuracy, latency | `apex_ai/evaluation/metrics.py`; §"Expanded 19-item category fixture" |
| 40 — RAG Performance | Measure and optimize bottlenecks | §"PERFORMANCE": before/after measured per-stage timing. Reported honestly as a **regression** (+1.1ms mean) traded for correctness (traces, wider pools, stricter gating) — not dressed up as an optimization it wasn't |

## Why no new work was needed here

Section 2's phases each got individually audited against their exact wording and,
where a real gap existed (tables, syntax highlighting, feedback), got real new code.
Section 3 got the same treatment: every phase's specific ask was checked against
`RAG_PHASE2_REPORT.md` and the current `apex_ai/rag/`, `apex_ai/retrieval/`,
`apex_ai/evaluation/` modules, and the current test suite (232 passed, 3 skipped,
including `tests/test_rag_phase2.py`'s focused regressions). Nothing was found
missing. Padding this section with restated or duplicate work would violate the same
principle that produced real diffs in Section 2 — don't add complexity or process
merely to have a commit per phase number.

## Carried-forward limitations (not new findings, restated for visibility)

These are already documented in `RAG_PHASE2_REPORT.md`'s "REMAINING PROBLEMS" and
remain true; they are not silently dropped, just not re-litigated:

- No built-in OCR for scanned PDFs.
- The real sentence-transformer embedding model and a real production LLM were not
  available when that report's measurements were taken — hashing embeddings verify
  plumbing/determinism, not semantic quality, and no answer-level groundedness
  benchmark has been run. This remains true in this session too (no model files are
  present in this container).
- Retrieval/evidence-gate thresholds are heuristic and corpus-dependent, not
  calibrated probabilities.
- Near-duplicate detection is lexical; paraphrased duplicate passages can both survive.
- A cross-encoder reranker's real benefit is unverified (only lexical reranking was
  measured; no cross-encoder model was cached in that environment).

None of these block later roadmap sections — they're prerequisites for a future
*real-model evaluation* pass (which needs actual downloaded models this sandbox
doesn't have), not for continuing to Section 4.

## Verification

| Check | Result |
|---|---|
| Full test suite (`python -m pytest tests/ -q`) | 232 passed, 3 skipped (no change — this section made no code changes) |
| Manual re-read of `apex_ai/rag/`, `apex_ai/retrieval/`, `apex_ai/evaluation/` against each phase's wording | No gap found |
