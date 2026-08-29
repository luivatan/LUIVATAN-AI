"""Hybrid semantic + lexical retrieval with weighted Reciprocal Rank Fusion.

Each query variant is searched independently in both channels.  The resulting
ranked lists are fused with weighted RRF rather than concatenated first; this
avoids making a document's score depend on how many candidates happened to be
returned for an earlier sub-query.

RRF deliberately combines *ranks*, not incompatible raw scales (cosine versus
BM25).  Channel weights are divided across query variants, so decomposing a
question cannot multiply one channel's total influence.  The original query is
always one of the variants.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from apex_ai.core.logging import get_logger, log_event
from apex_ai.core.types import RetrievedChunk
from apex_ai.retrieval.keyword import BM25Index

log = get_logger("retrieval.pipeline")


@dataclass
class RetrievalTrace:
    """Developer diagnostics for one retrieval run (never stored globally)."""

    queries: list[str]
    semantic_counts: list[int] = field(default_factory=list)
    keyword_counts: list[int] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "queries": list(self.queries),
            "semantic_counts": list(self.semantic_counts),
            "keyword_counts": list(self.keyword_counts),
            "timings_ms": dict(self.timings_ms),
            "errors": list(self.errors),
            "candidates": list(self.candidates),
        }


@dataclass
class RetrievalRun:
    chunks: list[RetrievedChunk]
    trace: RetrievalTrace


def _copy_for_channel(
    chunk: RetrievedChunk,
    *,
    channel: str,
    rank: int,
    query_index: int,
) -> RetrievedChunk:
    metadata = dict(chunk.metadata)
    metadata["_retrieval_channel"] = channel
    metadata["_retrieval_rank"] = rank
    metadata["_retrieval_query_index"] = query_index
    return RetrievedChunk(
        chunk_id=chunk.chunk_id,
        text=chunk.text,
        metadata=metadata,
        similarity=chunk.similarity,
        retrieval_score=chunk.retrieval_score,
    )


def rrf_merge(
    result_lists: list[list[RetrievedChunk]],
    weights: list[float] | None = None,
    rrf_k: int = 60,
) -> list[RetrievedChunk]:
    """Weighted Reciprocal Rank Fusion, deduplicated by stable chunk ID.

    Standard RRF contribution is ``weight / (rrf_k + rank)`` with rank
    starting at one. Raw cosine/BM25 scores remain available on each chunk for
    diagnostics and evidence gating, but are not mixed numerically.
    """
    weights = weights or [1.0] * len(result_lists)
    scores: dict[str, float] = {}
    best: dict[str, RetrievedChunk] = {}
    channels: dict[str, set[str]] = {}
    ranks: dict[str, dict[str, int]] = {}
    lexical_coverage: dict[str, float] = {}
    keyword_scores: dict[str, float] = {}

    for result_list, weight in zip(result_lists, weights):
        for rank, chunk in enumerate(result_list, start=1):
            chunk_id = chunk.chunk_id
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (rrf_k + rank)
            channel = str(chunk.metadata.get("_retrieval_channel", "unknown"))
            channels.setdefault(chunk_id, set()).add(channel)
            channel_ranks = ranks.setdefault(chunk_id, {})
            channel_ranks[channel] = min(rank, channel_ranks.get(channel, rank))
            lexical_coverage[chunk_id] = max(
                lexical_coverage.get(chunk_id, 0.0),
                float(chunk.metadata.get("_lexical_coverage", 0.0)),
            )
            keyword_scores[chunk_id] = max(
                keyword_scores.get(chunk_id, 0.0),
                float(chunk.metadata.get("_keyword_score", 0.0)),
            )

            previous = best.get(chunk_id)
            if previous is None or chunk.similarity > previous.similarity:
                best[chunk_id] = RetrievedChunk(
                    chunk_id=chunk.chunk_id,
                    text=chunk.text,
                    metadata=dict(chunk.metadata),
                    similarity=chunk.similarity,
                    retrieval_score=chunk.retrieval_score,
                )
            elif chunk.metadata.get("_lexical_coverage", 0) > previous.metadata.get(
                "_lexical_coverage", 0
            ):
                # Keep vector similarity from the prior winner while retaining
                # the strongest lexical diagnostics from a duplicate BM25 hit.
                previous.metadata.update(
                    {
                        "_lexical_coverage": chunk.metadata.get("_lexical_coverage", 0),
                        "_keyword_score": chunk.metadata.get("_keyword_score", 0),
                    }
                )

    ordered_ids = sorted(scores, key=lambda item: scores[item], reverse=True)
    merged: list[RetrievedChunk] = []
    for chunk_id in ordered_ids:
        chunk = best[chunk_id]
        chunk.retrieval_score = scores[chunk_id]
        chunk.metadata["_retrieval_channels"] = sorted(channels.get(chunk_id, set()))
        chunk.metadata["_channel_ranks"] = ranks.get(chunk_id, {})
        chunk.metadata["_lexical_coverage"] = lexical_coverage.get(chunk_id, 0.0)
        chunk.metadata["_keyword_score"] = keyword_scores.get(chunk_id, 0.0)
        merged.append(chunk)
    return merged


class HybridRetriever:
    def __init__(self, vector_store, settings, keyword_index: BM25Index | None = None) -> None:
        self.store = vector_store
        self.settings = settings
        self.keyword = keyword_index or BM25Index(vector_store)

    def retrieve(
        self, queries: list[str], user_id: str, top_k: int | None = None
    ) -> list[RetrievedChunk]:
        """Compatibility API returning only fused chunks."""
        return self.retrieve_with_trace(queries, user_id, top_k=top_k).chunks

    def retrieve_with_trace(
        self,
        queries: list[str],
        user_id: str,
        top_k: int | None = None,
        *,
        include_debug: bool = False,
    ) -> RetrievalRun:
        """Run both channels per query and return fused chunks plus diagnostics.

        A query-time failure in one channel does not discard results from the
        other. If embedding inference fails, exact BM25 retrieval can still
        answer; if BM25 fails, semantic Chroma retrieval can still answer.
        """
        started = time.perf_counter()
        candidate_limit = max(1, top_k or self.settings.top_k)
        # Channel pools are independently configurable. They need not be at
        # least the final fused limit: a smaller pool is a valid latency/recall
        # tradeoff, while their union can still fill the fused result set.
        semantic_limit = max(
            1, int(getattr(self.settings, "semantic_candidate_k", candidate_limit))
        )
        keyword_limit = max(
            1, int(getattr(self.settings, "keyword_candidate_k", candidate_limit))
        )
        clean_queries = list(dict.fromkeys(q.strip() for q in queries if q and q.strip()))
        trace = RetrievalTrace(queries=clean_queries)
        if not clean_queries:
            trace.timings_ms = {"total": 0.0, "semantic": 0.0, "keyword": 0.0, "fusion": 0.0}
            return RetrievalRun([], trace)

        semantic_lists: list[list[RetrievedChunk]] = []
        keyword_lists: list[list[RetrievedChunk]] = []
        semantic_elapsed = 0.0
        keyword_elapsed = 0.0

        for query_index, query in enumerate(clean_queries):
            stage_start = time.perf_counter()
            try:
                vector_hits = self.store.search(query, user_id, k=semantic_limit)
            except Exception as error:  # one channel may degrade independently
                vector_hits = []
                trace.errors.append(f"semantic: {type(error).__name__}: {error}")
                log.warning(
                    "Semantic retrieval failed; continuing with BM25 (error_type=%s)",
                    type(error).__name__,
                )
            semantic_elapsed += time.perf_counter() - stage_start
            vector_hits = [
                _copy_for_channel(
                    chunk, channel="semantic", rank=rank, query_index=query_index
                )
                for rank, chunk in enumerate(vector_hits, start=1)
            ]
            semantic_lists.append(vector_hits)
            trace.semantic_counts.append(len(vector_hits))

            stage_start = time.perf_counter()
            try:
                keyword_hits = self.keyword.search(query, user_id, k=keyword_limit)
            except Exception as error:  # optional lexical channel must not break RAG
                keyword_hits = []
                trace.errors.append(f"keyword: {type(error).__name__}: {error}")
                log.warning(
                    "Keyword retrieval failed; continuing with vectors (error_type=%s)",
                    type(error).__name__,
                )
            keyword_elapsed += time.perf_counter() - stage_start
            keyword_hits = [
                _copy_for_channel(
                    chunk, channel="keyword", rank=rank, query_index=query_index
                )
                for rank, chunk in enumerate(keyword_hits, start=1)
            ]
            keyword_lists.append(keyword_hits)
            trace.keyword_counts.append(len(keyword_hits))

        # Every query variant receives an equal slice of each channel's
        # configured weight. This makes query decomposition stable and avoids
        # double-counting chunks repeated across variants.
        query_count = len(clean_queries)
        result_lists: list[list[RetrievedChunk]] = []
        weights: list[float] = []
        for semantic, keyword in zip(semantic_lists, keyword_lists):
            result_lists.extend([semantic, keyword])
            weights.extend(
                [
                    self.settings.vector_weight / query_count,
                    self.settings.keyword_weight / query_count,
                ]
            )

        fusion_start = time.perf_counter()
        fused = rrf_merge(result_lists, weights, self.settings.rrf_k)[:candidate_limit]
        fusion_elapsed = time.perf_counter() - fusion_start
        trace.timings_ms = {
            "semantic": round(semantic_elapsed * 1000, 3),
            "keyword": round(keyword_elapsed * 1000, 3),
            "fusion": round(fusion_elapsed * 1000, 3),
            "total": round((time.perf_counter() - started) * 1000, 3),
        }
        if include_debug:
            trace.candidates = [
                {
                    "rank": rank,
                    "chunk_id": chunk.chunk_id,
                    "source": chunk.source,
                    "page_start": chunk.metadata.get("page_start", chunk.page),
                    "page_end": chunk.metadata.get("page_end", chunk.page),
                    "section": chunk.section,
                    "semantic_similarity": round(float(chunk.similarity), 6),
                    "fusion_score": round(float(chunk.retrieval_score), 8),
                    "lexical_coverage": round(
                        float(chunk.metadata.get("_lexical_coverage", 0.0)), 6
                    ),
                    "channels": chunk.metadata.get("_retrieval_channels", []),
                    "channel_ranks": chunk.metadata.get("_channel_ranks", {}),
                    "excerpt": chunk.text[:240],
                }
                for rank, chunk in enumerate(fused, start=1)
            ]
        log_event(
            log,
            logging.DEBUG,
            "retrieval.completed",
            "Hybrid retrieval completed",
            query_variant_count=query_count,
            fused_candidate_count=len(fused),
            duration_ms=trace.timings_ms["total"],
        )
        return RetrievalRun(fused, trace)
