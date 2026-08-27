"""Hybrid retrieval pipeline.

    user question(s)
        -> vector search (semantic)
        -> BM25 search  (exact keywords)
        -> Reciprocal Rank Fusion merge, deduplicated by chunk id
        -> optional reranker (reranker.py)
        -> final evidence list

Reciprocal Rank Fusion (RRF) is used instead of naive score addition because
vector similarity (0..1 cosine) and BM25 scores (unbounded) are not directly
comparable. RRF only uses *rank positions*, so it works for any scorer:

    RRF(chunk) = sum over lists of weight / (60 + rank)

The pipeline degrades gracefully: if the BM25 index is empty (no documents)
or the reranker is unavailable, the remaining stages still answer.
"""

from __future__ import annotations

from apex_ai.core.logging import get_logger, timed
from apex_ai.core.types import RetrievedChunk

log = get_logger("retrieval")

_RRF_K = 60  # standard RRF constant; dampens rank-1 dominance


def rrf_merge(
    result_lists: list[list[RetrievedChunk]], weights: list[float] | None = None
) -> list[RetrievedChunk]:
    """Fuse ranked lists into one deduplicated ranking."""
    weights = weights or [1.0] * len(result_lists)
    fused: dict[str, float] = {}
    chunks: dict[str, RetrievedChunk] = {}

    for weight, results in zip(weights, result_lists):
        for rank, chunk in enumerate(results):
            fused[chunk.chunk_id] = fused.get(chunk.chunk_id, 0.0) + weight / (_RRF_K + rank)
            existing = chunks.get(chunk.chunk_id)
            if existing is None:
                chunks[chunk.chunk_id] = RetrievedChunk(
                    chunk_id=chunk.chunk_id,
                    text=chunk.text,
                    metadata=chunk.metadata,
                    similarity=chunk.similarity,
                )
            elif chunk.similarity > existing.similarity:
                existing.similarity = chunk.similarity

    ordered_ids = sorted(fused, key=lambda cid: fused[cid], reverse=True)
    results = []
    for chunk_id in ordered_ids:
        chunk = chunks[chunk_id]
        chunk.retrieval_score = fused[chunk_id]
        results.append(chunk)
    return results


class HybridRetriever:
    def __init__(self, store, settings, keyword_index=None) -> None:
        self.store = store
        self.settings = settings
        self.keyword = keyword_index

    def retrieve(self, queries: list[str], top_k: int | None = None) -> list[RetrievedChunk]:
        """Retrieve candidates for one or more query variants.

        Input: list of query strings (the original question first, optional
        rewrites/decompositions after). Output: fused candidates, best first,
        capped at ``top_k`` (Settings.top_k by default).
        """
        top_k = top_k or self.settings.top_k
        vector_k = max(top_k, 8)
        keyword_k = max(top_k, 8)

        vector_hits: list[RetrievedChunk] = []
        keyword_hits: list[RetrievedChunk] = []
        with timed(log, f"retrieval for {len(queries)} query variant(s)"):
            for query in queries:
                if not query or not query.strip():
                    continue
                vector_hits.extend(self.store.search(query, k=vector_k))
                if self.keyword is not None:
                    keyword_hits.extend(self.keyword.search(query, k=keyword_k))

        if not vector_hits and not keyword_hits:
            return []

        weights = [self.settings.vector_weight, self.settings.keyword_weight]
        merged = rrf_merge([vector_hits, keyword_hits], weights)
        log.debug("Hybrid retrieval: %d vector + %d keyword -> %d fused candidates",
                  len(vector_hits), len(keyword_hits), len(merged))
        return merged[:top_k]
