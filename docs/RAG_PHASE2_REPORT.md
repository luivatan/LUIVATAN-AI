# Apex AI RAG Phase 2 — Audit, Changes, and Measured Results

**Audit date:** 2026-08-27 (America/Chicago)
**Protected baseline:** `6e08f92` (`Build chat-first Apex AI web experience`)
**Scope:** Incremental RAG strengthening; the existing LLM providers, ChromaDB,
ingestion entry points, chat surfaces, API routes, and source viewer remain in place.

This report separates exact measurements from proxies. It does not claim factual
answer quality, production readiness, clinical validation, or semantic-model quality.

## RAG BEFORE

The starting implementation was already a real hybrid RAG system, not a basic
single-vector demo:

```text
PDF/TXT/MD/JSON
  -> extraction and cleanup into ordered Page objects
  -> heading/paragraph parsing
  -> configurable structural chunks
  -> configurable EmbeddingProvider
  -> cosine ChromaDB + in-memory BM25Plus
  -> weighted Reciprocal Rank Fusion
  -> optional cross-encoder / lexical / off reranking
  -> character-budgeted SOURCE/PAGE/SECTION context
  -> max-cosine evidence gate
  -> dedicated grounded LLM prompt
  -> citations made from context-used chunks only
```

### What was already good and was retained

- File-specific extraction preserved ordered PDF pages and rejected scanned/empty PDFs
  with an actionable OCR message.
- `EmbeddingProvider` separated embedding inference from storage. The default
  sentence-transformers provider validated local availability, supported a one-time
  cache fill when online, and failed clearly in explicit offline mode.
- ChromaDB used persistent cosine collections, normalized embeddings, stable chunk IDs,
  and embedding-model identity metadata.
- BM25Plus complemented vectors for exact terms.
- RRF correctly avoided adding incompatible cosine and BM25 raw scores.
- Reranking was configurable (`auto`, `cross_encoder`, `lexical`, `off`).
- Context blocks had source/page/section identity and citations were created only from
  blocks actually supplied to the answer model.
- Conversation history was explicitly separated from documentary evidence.
- The grounded prompt told the LLM not to invent evidence.
- Empty-index chat returned the existing upload guidance instead of crashing.

### Concrete gaps found before modification

1. A section spanning pages assigned every paragraph to `section.page_start`; page-two
   evidence could therefore cite page one.
2. Tiny-chunk merging occurred after all sections were flattened, so a short chunk could
   merge into the previous heading and inherit the wrong section.
3. Overlap could cross headings/pages and could grow a chunk beyond the configured hard
   maximum.
4. Useful metadata aliases/ranges/hashes/schema identity were absent.
5. Headings were metadata but were not included in semantic/BM25 indexing text.
6. BM25Plus's positive delta meant `score > 0` did not actually prove token overlap;
   unrelated chunks could appear as lexical hits.
7. Hyphenated IDs, dates, and versions were split into components without preserving a
   whole exact token.
8. Multi-query results were concatenated by channel before fusion. Rank positions then
   depended on earlier query-list lengths, and repeated query variants could multiply a
   channel's influence.
9. The query processor was disabled by default. In normal runtime construction,
   `quiet_llm=True` also left it without an LLM, so enabling the old flag did not produce
   useful runtime expansion.
10. Confidence was the maximum vector similarity among all fused candidates, even if
    that candidate did not enter final context. Lexical/exact support was ignored.
11. An explicitly configured unavailable cross-encoder could fail during a request.
12. Context did not remove duplicate evidence, stopped at the first oversized lower-rank
    chunk, had no model-window reserve, and its "always keep first" behavior could exceed
    a very small requested character budget.
13. Only total turn time was returned; query, semantic, lexical, fusion, reranking, and
    context time were not independently visible.
14. There was no configuration-gated candidate/context trace for developers.
15. The evaluation fixture had six direct medical questions and no category labels,
    negative cases, follow-ups, exact IDs/dates, multi-part, duplicate, long,
    multi-document, or explicit failure-path coverage.

### Baseline measurements

Using the deterministic hashing provider, two bundled documents, six original
questions, lexical reranking, and a fresh temporary Chroma index:

| Metric | Before |
|---|---:|
| Source hit rate | 100.00% |
| Page hit rate | 100.00% |
| Top-1 source hit rate | 83.33% |
| Mean context lexical-relevance proxy | 0.96875 |
| Warm retrieval/preparation mean, 120 samples | 2.970 ms |
| Warm retrieval/preparation p95, 120 samples | 3.419 ms |

These values characterize this small fixture and machine only. Hashing vectors are a
repeatable test mechanism, not evidence of production semantic quality.

## RAG AFTER

```text
extract ordered pages
  -> sections + page identity for every paragraph
  -> section-isolated, hard-bounded chunks with page ranges/schema-v2 metadata
  -> embed (section heading + exact body); store exact body for truthful viewing
  -> per-query semantic pool + exact-token-aware BM25 pool
  -> one ranked list per query/channel
  -> weighted RRF with channel weight divided across query variants
  -> optional cross-encoder with lexical fallback
  -> rerank top candidates
  -> exact/near-duplicate removal + relevance-first budget selection
  -> source/page ordering + strict model-aware context budget
  -> context-only semantic/lexical/exact evidence gate
  -> stricter grounded prompt
  -> configured LLM
  -> citations from used context, including truthful page ranges
```

The runtime activates deterministic query processing and improved retrieval
automatically. No user-facing orchestration switch is required. Optional LLM rewriting
remains off by default because it adds latency and model-dependent variability.

## FILES CHANGED

### Runtime and configuration

- `.env.example` — documents every new candidate, fusion, query, context, and debug knob.
- `apex_ai/config/settings.py` — adds environment-backed settings with bounded defaults.
- `apex_ai/runtime.py` — wires automatic deterministic query processing and lazy optional
  LLM rewriting into the existing service container.
- `apex_ai/api/server.py` — conditionally registers developer diagnostics only when
  `APEX_RAG_DEBUG=1`.

### Ingestion, metadata, and storage

- `apex_ai/documents/models.py` — retains each paragraph's page alongside public section
  text.
- `apex_ai/documents/chunking.py` — fixes page attribution, heading-safe tiny merges,
  overlap hard limits, page ranges, and schema-v2 metadata.
- `apex_ai/vectordb/chroma_store.py` — embeds heading plus body while storing exact body,
  validates dimensions as well as model name, and counts spanning pages accurately.

### Retrieval and generation

- `apex_ai/retrieval/keyword.py` — exact compound tokens, heading indexing, and real
  overlap filtering.
- `apex_ai/retrieval/pipeline.py` — independent per-query/channel lists, normalized
  weighted RRF influence, graceful channel failure, and local retrieval traces.
- `apex_ai/retrieval/reranker.py` — bounded lexical/fusion scoring and persistent
  cross-encoder-to-lexical fallback.
- `apex_ai/rag/query_processing.py` — conservative deterministic follow-up expansion and
  decomposition plus protected-term validation for optional LLM output.
- `apex_ai/rag/context_builder.py` — duplicate removal, strict budgeting, structured page
  ranges, source ordering, and context diagnostics.
- `apex_ai/rag/prompts.py` — strengthens the source-only and multi-part grounding contract.
- `apex_ai/rag/engine.py` — stage orchestration/timing, context-only evidence gating,
  failure handling, diagnostics, and page-range citations.
- `apex_ai/core/types.py` — adds optional citation `page_end` without removing old fields.
- `apex_ai/web/static/app.js` — displays a real page range in the existing source drawer.

### Evaluation, documentation, and tests

- `apex_ai/evaluation/metrics.py` — exact source/page/reciprocal-rank/citation metrics,
  labeled lexical proxies, category summaries, and latency aggregation.
- `apex_ai/evaluation/runner.py` — history fixtures, negative-gate scoring,
  multi-document ingestion, measured stage timings, and limitations metadata.
- `eval/dataset.example.jsonl` — expands from 6 direct items to 19 categorized items.
- `eval/docs/apex_operations.md`, `eval/docs/apex_finance.md` — explicitly synthetic
  evaluation evidence; they make no claims about this project or a real business.
- `tests/test_rag_phase2.py` — focused Phase 2 regressions and failure paths.
- `tests/test_api_ui.py`, `tests/test_engine.py`, `tests/test_config.py`,
  `tests/test_vectordb.py`, `tests/test_evaluation_security.py`,
  `tests/test_documents.py` — debug gating, deterministic fallback, environment wiring,
  embedding compatibility, metric calculations, and corrupted-document handling.
- `README.md` — current architecture, configuration, evaluation, limitations, and tests.
- `docs/RAG_PHASE2_REPORT.md` — this before/after audit.

## NEW DEPENDENCIES

**None.** ChromaDB, sentence-transformers, rank-bm25, FastAPI, and the established
provider abstractions were retained. No internet-required service or parallel RAG stack
was added.

## CONFIGURATION

| Variable | Default | Meaning |
|---|---:|---|
| `APEX_TOP_K` | 12 | Final fused candidate count |
| `APEX_SEMANTIC_CANDIDATES` | 16 | Vector candidates per retrieval query |
| `APEX_KEYWORD_CANDIDATES` | 16 | BM25 candidates per retrieval query |
| `APEX_VECTOR_WEIGHT` | 0.6 | Total semantic influence in RRF |
| `APEX_KEYWORD_WEIGHT` | 0.4 | Total lexical influence in RRF |
| `APEX_RRF_K` | 60 | Rank-damping constant |
| `APEX_RERANK_TOP_K` | 4 | Maximum evidence chunks before context budgeting |
| `APEX_RERANKER` | `auto` | `auto`, `cross_encoder`, `lexical`, or `off` |
| `APEX_MIN_SIMILARITY` | 0.30 | Normal semantic evidence threshold |
| `APEX_LEXICAL_SUPPORT_THRESHOLD` | 0.60 | Conservative multi-term lexical gate |
| `APEX_QUERY_PROCESSING` | 1 | Enable deterministic analysis |
| `APEX_QUERY_DECOMPOSITION` | 1 | Add variants for clearly distinct clauses |
| `APEX_MAX_QUERY_VARIANTS` | 4 | Original plus bounded additional retrieval queries |
| `APEX_QUERY_REWRITE` | 0 | Optional LLM refinement; never required for fallback |
| `APEX_CONTEXT_CHAR_LIMIT` | 6000 | Absolute configured evidence-character cap |
| `APEX_CONTEXT_TOKEN_RESERVE` | 1024 | Approximate room for prompt/history/output |
| `APEX_RAG_DEBUG` | 0 | Whether the hidden developer trace route exists |

Embedding selection remains `APEX_EMBEDDING_MODEL`; `APEX_OFFLINE=1` continues to ban
model downloads. API keys and model paths remain environment/local configuration only.

### Developer diagnostics

With `APEX_RAG_DEBUG=1`, the unlinked `POST /debug/rag` route is registered but remains
excluded from OpenAPI. It accepts `question`, `use_memory`, and `generate`; generation is
on by default so the response is from the actual configured model. Setting
`"generate": false` explicitly limits the run to preparation. The payload exposes query
variants and rewrite/decomposition decisions, per-channel candidate ranks/scores,
complete post-rerank ordering, context-selection decisions, exact final context, gate
reason, stage timings/errors, actual model response, and real context-backed sources.
It may read conversation memory for follow-up analysis but never appends a memory turn.
The route does not exist at all under normal settings, and debug fields are never added
to ordinary chat responses.

## RETRIEVAL

### Exact lexical retrieval

The tokenizer emits `xj-420` and its components `xj`, `420`; similarly it retains dates,
versions, paths, and abbreviations. BM25 indexes `section + body`, but results return the
unchanged source body. An explicit set intersection now removes BM25Plus delta-only
results with no query-token overlap.

### Multi-query fusion

For `Q` unique variants, Apex AI builds `2Q` ranked lists: one semantic and one lexical
list per variant. Each semantic list receives `vector_weight / Q`; each lexical list
receives `keyword_weight / Q`. Standard one-based RRF is:

```text
score(chunk) = sum(weight_for_list / (APEX_RRF_K + one_based_rank))
```

This uses rankings rather than incomparable raw scorer values. Duplicate chunk IDs are
collapsed while their best cosine, lexical coverage, channels, and channel ranks remain
available for gating/debugging.

### Query behavior

- A simple standalone question produces exactly one retrieval query.
- A short pronoun/continuation follow-up adds a lossless query containing the latest user
  question plus the current follow-up.
- Explicit independent clauses (`? ... ?`, `;`, or `and what/how/...`) add bounded
  deterministic subqueries.
- The original is always first and never altered.
- If optional LLM processing is enabled, output that drops a name, quoted phrase, ID,
  number, date, version, or abbreviation is rejected.

### Evidence gate

The gate examines chunks actually retained in context, not arbitrary pre-rerank
candidates. Near-threshold semantic evidence needs at least one informative lexical
corroboration; very strong semantic evidence can support a pure paraphrase. Exact
anchors and strong multi-term lexical evidence provide bounded fallback paths. No
candidate/context evidence means deterministic refusal without an LLM call.

Thresholds are heuristics and must be measured on the actual corpus. They are not
probabilities.

## CHUNKING

- Paragraph page identity survives sections that cross page boundaries.
- Chunks can state `page_start` and `page_end`; compatibility key `page` remains.
- Packing and tiny-fragment merging occur within one section only.
- Overlap occurs only at compatible page/section locations and is trimmed so
  `max_chunk_size` remains a real hard bound.
- New metadata includes stable chunk/document IDs, filename/source, file type, page
  aliases/range, section/level, index, character count, SHA-256 content fingerprint,
  UTC creation time, and chunk schema version 2.
- Section text participates in embedding/BM25 search, but stored/cited text is still the
  exact extracted body.

Existing indexed chunks remain readable through metadata fallbacks. Re-indexing is
required to regenerate old chunks with schema-v2 ranges and heading-aware embeddings;
Apex AI does not silently rewrite a user's persistent index.

## CITATIONS

Citation creation still has one source of truth: `BuiltContext.used_chunks`. The engine
cannot cite a retrieval candidate that was dropped as a duplicate, omitted for budget,
or never sent to the LLM. The compatibility `page` value remains and `page_end` is now
optional. The existing source drawer shows source, truthful page/range, section, score,
and exact supplied text.

No page hyperlink or navigation claim was added because the app does not currently
render original PDF pages. A range label is metadata display, not fake page navigation.
Developer candidate excerpts do not enter normal chat or citation payloads.

## TEST RESULTS

Final automated command:

```text
.venv/bin/python -m pytest tests/ -q
134 passed, 3 warnings in 11.40s
```

The warnings are one upstream TestClient deprecation and two intentionally asserted
legacy-environment deprecations. Static checks also passed:

```text
ruff --select E9,F63,F7,F82: passed
python -m compileall -q apex_ai tests: passed
git diff --check: passed
```

### Required scenario coverage

1. Direct factual PDF retrieval.
2. Semantic/paraphrased retrieval.
3. Exact product/record ID retrieval.
4. Exact date and number retrieval.
5. Abbreviation/version preservation.
6. Genuine multi-part decomposition.
7. Unsupported/negative evidence refusal.
8. Context-dependent follow-up expansion.
9. Multi-document source recall.
10. Duplicate context suppression.
11. Long question plus fact near the end of a longer document.
12. Multi-page source/page attribution.
13. Empty-index/non-RAG upload guidance remains unchanged.
14. Missing embedding model produces an actionable offline error.
15. Unavailable cross-encoder falls back to lexical reranking.
16. Semantic-channel failure falls back to BM25.
17. Keyword-channel failure falls back to semantic retrieval.
18. Context/model/database and empty, scanned, or corrupted-document failures remain
    bounded and actionable.
19. Developer debug route is absent for normal settings and excluded from OpenAPI when
    explicitly enabled.
20. Existing streaming, conversations, uploads, UI assets, model selection, legacy API,
    and source-viewer tests still pass.

## PERFORMANCE

### Same six-question retrieval fixture (before versus after)

The exact original six items were rerun after the changes with a fresh temporary index.
Retrieval quality did not regress on those measured fields:

| Metric | Before | After |
|---|---:|---:|
| Source hit rate | 100.00% | 100.00% |
| Page hit rate | 100.00% | 100.00% |
| Top-1 source hit rate | 83.33% | 83.33% |
| Mean context relevance proxy | 0.969 | 0.969 |

A 120-sample warm preparation loop on the same two documents/six questions measured:

| Wall-clock preparation | Before | After | Change |
|---|---:|---:|---:|
| Mean | 2.970 ms | 4.024 ms | +1.054 ms (+35.5%) |
| p95 | 3.419 ms | 4.326 ms | +0.907 ms (+26.5%) |

The increase is real and is not described as an optimization: the after path creates
per-stage traces, searches a configurable wider pool, applies stricter metadata/fusion,
and checks context duplicates/support. The absolute difference on this seven-chunk test
index is about 1.1 milliseconds; larger real corpora and real embedding models must be
measured separately.

### Expanded 19-item category fixture

Fresh-index, retrieval-only, deterministic hashing run (`APEX_MIN_SIMILARITY=0.05`, the
explicit smoke-test scale):

| Metric | Measured value |
|---|---:|
| Source hit rate (18 applicable items) | 100.00% |
| Mean source recall | 1.000 |
| Mean expected-document precision@candidate-k proxy | 0.361 |
| Page hit rate / mean expected-page recall | 100.00% / 1.000 |
| Candidate top-1 expected-source hit rate | 88.89% |
| Candidate expected-source MRR | 0.944 |
| Post-rerank top-1 expected-source hit rate | 94.44% |
| Post-rerank expected-source MRR | 0.972 |
| Mean post-minus-pre-rerank reciprocal-rank change | +0.028 |
| Mean context lexical-relevance proxy | 0.963 |
| Evidence-gate/refusal accuracy (19 labeled items) | 100.00% |
| Insufficient rate | 5.26% (the one negative item) |
| Mean query processing | 0.10 ms |
| Mean semantic retrieval | 6.22 ms |
| Mean keyword retrieval | 0.57 ms |
| Mean fusion | 0.08 ms |
| Mean reranking | 0.68 ms |
| Mean context construction | 0.26 ms |
| Mean total preparation | 8.21 ms |

The report was generated locally as `eval/reports/eval-20260828-034929.json`; reports are
runtime artifacts and intentionally ignored by Git. The precision value counts chunks
from an expected document, not passage-level human relevance. Likewise, both MRR values
and the reranker delta track the first expected **document**, so they show whether the
configured reranker moved the labeled source—not whether every passage became more
relevant. On this fixture the measured delta was positive, so lexical reranking remained
enabled.

No valid production LLM was configured for this measurement, so factual answer quality,
answer groundedness, generation latency, and answer-level citation accuracy were **not
measured**. Unit tests verify citation plumbing and metric calculation, but those are not
substituted for a production answer-quality benchmark.

## REMAINING PROBLEMS

- No built-in OCR; scanned PDFs still require OCR before ingestion.
- Complex tables, columns, figures, and extraction order remain dependent on `pypdf`.
- The real sentence-transformer model was not available in this sandbox cache. Hashing
  verifies determinism/plumbing, not semantic quality; corpus-level semantic evaluation
  remains required with the configured production embedding model.
- No valid production answer model was available for a truthful answer benchmark.
  Groundedness and citation quality need a reviewed, model-generated evaluation run.
- Deterministic decomposition intentionally handles only clear clause patterns. It will
  leave ambiguous compound questions alone rather than over-split them.
- Four characters per token is an approximation; exact provider tokenizers differ.
- Exact/semantic gate thresholds are corpus-dependent and are not calibrated
  probabilities.
- Near-duplicate detection is lexical. Paraphrased duplicate passages may both remain.
- A cross-encoder must be cached/downloaded before its effect can be evaluated; this
  run measured lexical reranking only, so no cross-encoder benefit is claimed.
- Existing indexes need explicit re-indexing for schema-v2 chunks; silent bulk migration
  was intentionally avoided.
- Developer mode is appropriate only for the current single-user local deployment. It
  is route-gated but is not a replacement for production authentication/authorization.
- Human review is still needed to score completeness, factual correctness, and whether
  each citation truly entails each answer claim.

## EDUCATIONAL EXPLANATION

RAG has two separate jobs. **Retrieval** must locate the right source passage; the
**generator** must stay inside that passage. Better prompting cannot recover evidence
that retrieval missed, and better retrieval cannot force a weak model to obey evidence.
That is why this work measures and debugs each boundary independently.

Semantic embeddings help when wording differs, while BM25 helps when spelling must be
exact. Their scores have different meanings, so adding them is invalid. RRF asks a
simpler question—"where did this chunk rank in each list?"—and combines those ranks.
Per-query lists and normalized channel weight keep decomposition from secretly changing
how much influence one retriever has.

Chunk boundaries are part of correctness, not merely performance. If overlap or a tiny
merge crosses a page/heading, the text may remain useful while its citation becomes
false. Carrying page provenance at paragraph level and prohibiting cross-heading merges
prevents that failure at its source.

Finally, a citation is not decoration. It is a reference to evidence actually provided
to the model. Building citations only from the final budgeted context creates an
auditable invariant: dropped evidence cannot become a source. The developer trace makes
query variants, channel ranks, scores, budget decisions, and gate reasons observable so
future tuning can be based on measurements rather than intuition.
