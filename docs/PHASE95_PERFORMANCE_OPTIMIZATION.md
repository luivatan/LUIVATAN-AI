# Apex AI Phase 95 — Performance Optimization

- **Completed:** 2026-08-30 (America/Chicago)
- **Baseline:** `6e68a4a` (Phase 93 + 94, monitoring/error-tracking decisions)
- **Scope:** "Measure page speed, API latency, database performance,
  retrieval latency, and model latency. Optimize actual bottlenecks."
  Under the user's "buildable code only" scope for Section 9, this phase
  measures what can be measured from inside the codebase without a real
  deployment (retrieval/database latency, using the timing instrumentation
  already wired into `RagEngine` and `HybridRetriever`), finds a real
  bottleneck, and fixes it with real before/after numbers — not model
  inference latency, which is dominated by whichever LLM is configured
  and isn't something to "optimize" without a real model and hardware to
  measure against.

## Measuring first

`apex_ai/rag/engine.py` already records per-stage timings on every turn
(`turn.timings`: `conversation_context`, `memory_retrieval`,
`query_processing`, `retrieval`, `rerank`, `context`, `generation`,
`total`), and `HybridRetriever.retrieve_with_trace()` breaks `retrieval`
further into `semantic`, `keyword`, and `fusion`. Rather than guessing at
a bottleneck, this phase used that same instrumentation directly:
`scripts/benchmark_retrieval.py` (new) seeds a real `ChromaVectorStore`
with a configurable number of chunks via `HashingEmbeddingProvider`
(deterministic, no network/model download — the same fixture pattern
`tests/conftest.py` already uses) and times repeated real
`store.search()` and `HybridRetriever.retrieve()` calls.

## The bottleneck: a redundant full-collection fetch on every search

`ChromaVectorStore.search()` called `self.count(user_id)` before every
single query, solely to compute `k = min(k, count)`. `count()` doesn't
count — it calls `self.collection.get(where={"user_id": user_id},
include=[])` and takes `len()` of the result, which means **fetching
every one of the account's chunk IDs from Chroma just to clamp a
number**. That's a full, unbounded second round-trip on every vector
search, on top of the actual `collection.query()` call — and
`HybridRetriever.retrieve_with_trace()` calls `store.search()` once per
query variant, so a turn with query decomposition paid this cost multiple
times.

Verified directly against this Chroma version (1.5.9) before changing
anything: `collection.query(n_results=k, where=...)` already returns
fewer than `k` results — down to zero, with no error — whenever fewer
rows match, including when the collection is completely empty or the
`where` filter matches nothing. The pre-count added no correctness value;
it only added latency, and that latency scales with the account's chunk
count the exact same way the real query does, so it doesn't shrink as a
percentage of latency at scale — it stays roughly proportional.

## The fix

`apex_ai/vectordb/chroma_store.py`'s `search()` no longer calls
`count()`. It keeps the existing `document_ids == []` short-circuit,
adds an explicit `k <= 0` guard (Chroma's `query()` raises on
`n_results<=0`, a case the old code only avoided by coincidence — a
non-empty store with `count()==0` never happened, but a non-positive `k`
argument was never actually guarded against directly), and calls
`collection.query()` directly, trusting Chroma to clamp the result count
itself — which the check above confirms it already does.

## Before / after (real, measured — `scripts/benchmark_retrieval.py`)

Same machine, same run pattern, `HashingEmbeddingProvider`, isolated
temp store per run:

| Chunks | Call | Before (mean) | After (mean) | Change |
|---|---|---|---|---|
| 2,000 | `store.search()` | 15.796 ms | 9.113 ms | −42% |
| 2,000 | `HybridRetriever.retrieve()` | 21.684 ms | 15.147 ms | −30% |
| 8,000 | `store.search()` | 65.735 ms | 34.848 ms | −47% |
| 8,000 | `HybridRetriever.retrieve()` | 97.440 ms | 60.629 ms | −38% |

The gap widening in absolute terms as chunk count grows (and staying
roughly the same *proportion* of total latency at both scales) confirms
the cause: the removed call was doing real O(chunks) work identical in
shape to the query it was supposedly just "clamping" for.

This is the `retrieval` (and its `retrieval_semantic` sub-stage)
component of `turn.timings` specifically — the fix reduces exactly the
stage that instrumentation already isolates, so its effect is visible in
existing per-turn diagnostics without any new metric being added.

## Regression coverage

`tests/test_vectordb.py` gained:
- `test_search_does_not_pre_count_the_collection` — replaces
  `store.collection.get` with a function that fails the test if called,
  then asserts a real search still returns real results. This is the
  direct guard against the fixed bottleneck coming back.
- `test_search_on_an_empty_collection_returns_no_results` and
  `test_search_for_an_unknown_user_returns_no_results` — the two cases
  the old `count() == 0` early return used to handle, now covered
  directly against the new code path.
- `test_search_with_non_positive_k_returns_no_results` (parametrized,
  `k=0` and `k=-1`) — the new explicit guard that replaces the old
  code's incidental protection against `n_results<=0`.

## Files

- `apex_ai/vectordb/chroma_store.py` — `search()`: removed the `count()`
  pre-check, added an explicit `k <= 0` guard
- `scripts/benchmark_retrieval.py` (new) — reusable, real, deterministic
  retrieval benchmark; not part of the test suite (it's a measurement
  tool, not a correctness check), but committed so the before/after
  numbers above are independently reproducible
- `tests/test_vectordb.py` — 4 new tests (5 cases with parametrization)

## Verification

| Check | Result |
|---|---|
| Full test suite (`python3 -m pytest -q`) | 544 passed, 3 skipped (up from 539) |
| `tests/test_vectordb.py` | 19 passed |
| `scripts/benchmark_retrieval.py` before/after | see table above; re-verified by `git stash`-ing the fix and re-running at 8,000 chunks |
| `ruff check` on touched files | clean (the 5 remaining findings in `chroma_store.py` are pre-existing, confirmed identical against the `HEAD` baseline on lines this phase didn't touch) |

## Deliberately not done in this phase

- **No model/generation latency optimization.** `generation` timing in
  `turn.timings` is dominated by whichever LLM provider is configured
  (local `llama.cpp`, Ollama, OpenAI-compatible); "optimizing" it without
  a real production model and real hardware to measure against would be
  guesswork, not measurement.
- **No page-speed (frontend) profiling.** That needs a real browser
  session against a real deployed instance (Phase 91, declined) to
  produce real numbers rather than synthetic ones.
- **No further retrieval micro-optimization beyond the one confirmed
  bottleneck** (e.g., replacing `sorted()` with a partial
  top-k selection in `rrf_merge`/BM25 ranking) — those are real but much
  smaller effects at the corpus sizes a personal document library
  actually reaches; introducing them without a measured case that
  justifies them would be exactly the kind of unrequested complexity the
  project's own ground rules warn against.
