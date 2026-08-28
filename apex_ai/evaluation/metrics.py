"""Deterministic, explicitly limited RAG evaluation metrics.

Exact retrieval/citation checks are reported separately from heuristic text
overlap. ``groundedness_proxy`` is not factuality: it only measures whether
answer sentences share meaningful words with supplied context. No metric here
is presented as human evaluation, semantic truth, or production quality.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_WORD = re.compile(r"\w+")
_CITATION = re.compile(r"\[(\d+)]")
_OVERLAP_THRESHOLD = 0.15


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall((text or "").lower()))


def normalize_source(name: str) -> str:
    """Filename comparison that ignores case, extension, and separators."""
    lowered = (name or "").lower().strip()
    for ext in (".pdf", ".txt", ".md", ".markdown", ".json"):
        if lowered.endswith(ext):
            lowered = lowered[: -len(ext)]
            break
    return re.sub(r"[\s_\-]+", " ", lowered)


def token_overlap(expected: str, actual: str) -> float:
    """Recall of expected tokens in actual text (a lexical relevance proxy)."""
    expected_tokens = _tokens(expected)
    if not expected_tokens:
        return 0.0
    return len(expected_tokens & _tokens(actual)) / len(expected_tokens)


def _expected_sources(item: dict) -> list[str]:
    raw = item.get("expected_sources")
    if raw is None:
        raw = [item.get("expected_source", "")]
    elif isinstance(raw, str):
        raw = [raw]
    return [str(source) for source in raw if str(source).strip()]


def _source_matches(actual: str, expected: str) -> bool:
    actual_norm = normalize_source(actual)
    expected_norm = normalize_source(expected)
    return bool(actual_norm and expected_norm) and actual_norm == expected_norm


def _page_contains(metadata: dict, expected_page: int) -> bool:
    start = metadata.get("page_start", metadata.get("page"))
    end = metadata.get("page_end", start)
    try:
        return int(start) <= int(expected_page) <= int(end)
    except (TypeError, ValueError):
        return False


@dataclass
class ItemMetrics:
    question: str
    expected_source: str
    expected_page: int | None
    source_hit: bool | None
    page_hit: bool | None
    first_hit: bool | None
    context_relevance: float
    category: str = "uncategorized"
    expected_sources: list[str] = field(default_factory=list)
    expected_pages: list[int] = field(default_factory=list)
    source_recall: float | None = None
    source_precision_at_k: float | None = None
    page_recall: float | None = None
    reciprocal_rank: float | None = None
    reranked_first_hit: bool | None = None
    reranked_reciprocal_rank: float | None = None
    reranker_rr_delta: float | None = None
    answered: bool = False
    insufficient: bool = False
    expected_insufficient: bool | None = None
    refusal_correct: bool | None = None
    groundedness_proxy: float | None = None
    citation_integrity: float | None = None
    citation_source_recall: float | None = None
    citation_marker_validity: float | None = None
    timings_ms: dict[str, float] = field(default_factory=dict)
    retrieved: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "question": self.question,
            "category": self.category,
            "expected_source": self.expected_source,
            "expected_sources": self.expected_sources,
            "expected_page": self.expected_page,
            "expected_pages": self.expected_pages,
            "source_hit": self.source_hit,
            "source_recall": (
                round(self.source_recall, 3) if self.source_recall is not None else None
            ),
            "source_precision_at_k": (
                round(self.source_precision_at_k, 3)
                if self.source_precision_at_k is not None
                else None
            ),
            "page_hit": self.page_hit,
            "page_recall": (
                round(self.page_recall, 3) if self.page_recall is not None else None
            ),
            "first_hit": self.first_hit,
            "reciprocal_rank": (
                round(self.reciprocal_rank, 3)
                if self.reciprocal_rank is not None
                else None
            ),
            "reranked_first_hit": self.reranked_first_hit,
            "reranked_reciprocal_rank": (
                round(self.reranked_reciprocal_rank, 3)
                if self.reranked_reciprocal_rank is not None
                else None
            ),
            "reranker_rr_delta": (
                round(self.reranker_rr_delta, 3)
                if self.reranker_rr_delta is not None
                else None
            ),
            "context_relevance": round(self.context_relevance, 3),
            "answered": self.answered,
            "insufficient": self.insufficient,
            "expected_insufficient": self.expected_insufficient,
            "refusal_correct": self.refusal_correct,
            "groundedness_proxy": (
                round(self.groundedness_proxy, 3)
                if self.groundedness_proxy is not None
                else None
            ),
            "citation_integrity": (
                round(self.citation_integrity, 3)
                if self.citation_integrity is not None
                else None
            ),
            "citation_source_recall": (
                round(self.citation_source_recall, 3)
                if self.citation_source_recall is not None
                else None
            ),
            "citation_marker_validity": (
                round(self.citation_marker_validity, 3)
                if self.citation_marker_validity is not None
                else None
            ),
            "timings_ms": {key: round(value, 3) for key, value in self.timings_ms.items()},
            "retrieved": list(self.retrieved),
        }


def evaluate_item(
    item: dict,
    retrieved_chunks,
    context_text: str,
    answer: str | None = None,
    insufficient: bool = False,
    *,
    reranked_chunks=None,
    citations=None,
    context_chunk_ids: list[str] | None = None,
    timings_ms: dict[str, float] | None = None,
) -> ItemMetrics:
    """Score one item using exact source/page IDs plus documented proxies."""
    expected_sources = _expected_sources(item)
    raw_expected_pages = item.get("expected_pages")
    if raw_expected_pages is None:
        raw_expected_pages = (
            [item["expected_page"]] if item.get("expected_page") is not None else []
        )
    elif not isinstance(raw_expected_pages, list):
        raw_expected_pages = [raw_expected_pages]
    expected_pages = [int(page) for page in raw_expected_pages]
    expected_page = item.get("expected_page")
    found_sources: set[str] = set()
    first_expected_rank: int | None = None
    matched_source_pages: set[int] = set()
    source_matching_chunks = 0

    retrieved_summary: list[dict] = []
    for index, chunk in enumerate(retrieved_chunks):
        chunk_source = chunk.metadata.get(
            "document_name", chunk.metadata.get("filename", "")
        )
        matching = [
            source for source in expected_sources if _source_matches(chunk_source, source)
        ]
        if matching:
            found_sources.update(matching)
            source_matching_chunks += 1
            if first_expected_rank is None:
                first_expected_rank = index + 1
            for page in expected_pages:
                if _page_contains(chunk.metadata, page):
                    matched_source_pages.add(page)
        retrieved_summary.append(
            {
                "rank": index + 1,
                "chunk_id": chunk.chunk_id,
                "source": chunk_source,
                "page_start": chunk.metadata.get("page_start", chunk.metadata.get("page")),
                "page_end": chunk.metadata.get(
                    "page_end", chunk.metadata.get("page_start", chunk.metadata.get("page"))
                ),
                "similarity": round(float(chunk.similarity), 6),
                "score": round(float(chunk.retrieval_score), 8),
            }
        )

    if expected_sources:
        source_recall = len(found_sources) / len(expected_sources)
        source_precision_at_k: float | None = source_matching_chunks / max(
            1, len(retrieved_chunks)
        )
        source_hit: bool | None = source_recall == 1.0
        first_hit: bool | None = first_expected_rank == 1
        reciprocal_rank: float | None = (
            1.0 / first_expected_rank if first_expected_rank is not None else 0.0
        )
    else:
        source_recall = None
        source_precision_at_k = None
        source_hit = None
        first_hit = None
        reciprocal_rank = None

    reranked_first_hit: bool | None = None
    reranked_reciprocal_rank: float | None = None
    reranker_rr_delta: float | None = None
    if expected_sources and reranked_chunks is not None:
        reranked_rank = next(
            (
                index
                for index, chunk in enumerate(reranked_chunks, start=1)
                if any(_source_matches(chunk.source, source) for source in expected_sources)
            ),
            None,
        )
        reranked_first_hit = reranked_rank == 1
        reranked_reciprocal_rank = 1.0 / reranked_rank if reranked_rank else 0.0
        reranker_rr_delta = reranked_reciprocal_rank - (reciprocal_rank or 0.0)

    page_hit: bool | None
    page_recall: float | None
    if not expected_pages:
        page_hit = None
        page_recall = None
    else:
        page_recall = len(matched_source_pages) / len(set(expected_pages))
        page_hit = page_recall == 1.0

    expected_insufficient = item.get("expected_insufficient")
    if expected_insufficient is not None:
        expected_insufficient = bool(expected_insufficient)
    refusal_correct = (
        insufficient == expected_insufficient if expected_insufficient is not None else None
    )

    metrics = ItemMetrics(
        question=item.get("question", ""),
        category=item.get("category", "uncategorized"),
        expected_source=(expected_sources[0] if expected_sources else ""),
        expected_sources=expected_sources,
        expected_page=expected_page,
        expected_pages=expected_pages,
        source_hit=source_hit,
        source_recall=source_recall,
        source_precision_at_k=source_precision_at_k,
        page_hit=page_hit,
        page_recall=page_recall,
        first_hit=first_hit,
        reciprocal_rank=reciprocal_rank,
        reranked_first_hit=reranked_first_hit,
        reranked_reciprocal_rank=reranked_reciprocal_rank,
        reranker_rr_delta=reranker_rr_delta,
        context_relevance=token_overlap(item.get("expected_answer", ""), context_text),
        answered=answer is not None,
        insufficient=insufficient,
        expected_insufficient=expected_insufficient,
        refusal_correct=refusal_correct,
        timings_ms=dict(timings_ms or {}),
        retrieved=retrieved_summary,
    )

    citation_list = list(citations or [])
    if citations is not None:
        context_ids = set(context_chunk_ids or [])
        if citation_list:
            metrics.citation_integrity = sum(
                1 for citation in citation_list if citation.chunk_id in context_ids
            ) / len(citation_list)
        elif not insufficient:
            metrics.citation_integrity = 0.0

    if answer and not insufficient:
        sentences = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+", answer)
            if len(sentence.strip()) > 15
        ]
        if sentences:
            grounded = sum(
                1
                for sentence in sentences
                if token_overlap(_CITATION.sub("", sentence), context_text)
                >= _OVERLAP_THRESHOLD
            )
            metrics.groundedness_proxy = grounded / len(sentences)

        markers = [int(marker) for marker in _CITATION.findall(answer)]
        citation_by_index = {citation.index: citation for citation in citation_list}
        valid_indices = set(citation_by_index)
        metrics.citation_marker_validity = (
            sum(1 for marker in markers if marker in valid_indices) / len(markers)
            if markers
            else 0.0
        )
        if expected_sources:
            # Source recall follows markers in the generated answer, not every
            # context citation attached to the transport payload. Otherwise an
            # uncited expected source would receive misleading credit.
            referenced = [
                citation_by_index[index]
                for index in dict.fromkeys(markers)
                if index in valid_indices
            ]
            cited_sources = {
                expected
                for expected in expected_sources
                if any(_source_matches(citation.source, expected) for citation in referenced)
            }
            metrics.citation_source_recall = len(cited_sources) / len(expected_sources)

    return metrics


def _rate(items: list[ItemMetrics], attribute: str) -> float | None:
    values = [getattr(item, attribute) for item in items]
    applicable = [value for value in values if value is not None]
    if not applicable:
        return None
    return sum(float(value) for value in applicable) / len(applicable)


def _mean(items: list[ItemMetrics], attribute: str) -> float | None:
    values = [getattr(item, attribute) for item in items]
    applicable = [float(value) for value in values if value is not None]
    return sum(applicable) / len(applicable) if applicable else None


@dataclass
class Report:
    items: list[ItemMetrics] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def summary(self) -> dict:
        count = len(self.items)
        latency_keys = sorted(
            {key for item in self.items for key in item.timings_ms}
        )
        categories: dict[str, dict] = {}
        for category in sorted({item.category for item in self.items}):
            members = [item for item in self.items if item.category == category]
            categories[category] = {
                "items": len(members),
                "source_hit_rate": _rate(members, "source_hit"),
                "mean_reciprocal_rank": _mean(members, "reciprocal_rank"),
                "reranked_mean_reciprocal_rank": _mean(
                    members, "reranked_reciprocal_rank"
                ),
                "refusal_accuracy": _rate(members, "refusal_correct"),
            }

        relevance_items = [
            item for item in self.items if item.expected_insufficient is not True
        ]
        return {
            "items": count,
            "source_hit_rate": _rate(self.items, "source_hit"),
            "mean_source_recall": _mean(self.items, "source_recall"),
            "mean_source_precision_at_k": _mean(
                self.items, "source_precision_at_k"
            ),
            "page_hit_rate": _rate(self.items, "page_hit"),
            "mean_page_recall": _mean(self.items, "page_recall"),
            "first_hit_rate": _rate(self.items, "first_hit"),
            "mean_reciprocal_rank": _mean(self.items, "reciprocal_rank"),
            "reranked_first_hit_rate": _rate(self.items, "reranked_first_hit"),
            "reranked_mean_reciprocal_rank": _mean(
                self.items, "reranked_reciprocal_rank"
            ),
            "mean_reranker_rr_delta": _mean(self.items, "reranker_rr_delta"),
            "mean_context_relevance": (
                sum(item.context_relevance for item in relevance_items)
                / len(relevance_items)
                if relevance_items
                else None
            ),
            "insufficient_rate": (
                sum(1 for item in self.items if item.insufficient) / count if count else None
            ),
            "refusal_accuracy": _rate(self.items, "refusal_correct"),
            "mean_groundedness_proxy": _mean(self.items, "groundedness_proxy"),
            "citation_integrity": _mean(self.items, "citation_integrity"),
            "citation_source_recall": _mean(self.items, "citation_source_recall"),
            "citation_marker_validity": _mean(
                self.items, "citation_marker_validity"
            ),
            "mean_latency_ms": {
                key: round(
                    sum(item.timings_ms[key] for item in self.items if key in item.timings_ms)
                    / sum(1 for item in self.items if key in item.timings_ms),
                    3,
                )
                for key in latency_keys
            },
            "categories": categories,
        }

    def to_dict(self) -> dict:
        return {
            "metadata": dict(self.metadata),
            "summary": self.summary(),
            "items": [item.as_dict() for item in self.items],
        }
