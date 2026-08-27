"""Deterministic RAG evaluation metrics.

Honesty first: these metrics are computed, not asserted. Everything here is a
heuristic proxy — retrieval hit rates are exact, text-overlap measures are
approximations of relevance. The report always shows raw numbers so nobody
can accidentally (or deliberately) overclaim.

Per evaluation item we measure:

- source_hit        expected document appears in retrieved evidence
- page_hit          expected page appears among retrieved pages (of the
                    matched source when identifiable)
- first_hit         the TOP-1 retrieved chunk is from the expected source
- context_relevance token overlap between the expected answer and the
                    retrieved context (0..1 proxy)
- answer metrics (only when an LLM answered):
    - groundedness_proxy: fraction of answer sentences whose word overlap
      with the retrieved context exceeds a threshold
    - insufficient: the engine refused (low evidence) — counted, not punished
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_WORD = re.compile(r"\w+")
_OVERLAP_THRESHOLD = 0.15


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall((text or "").lower()))


def normalize_source(name: str) -> str:
    """Filename comparison that ignores case, extension and separators."""
    lowered = (name or "").lower().strip()
    for ext in (".pdf", ".txt", ".md", ".markdown", ".json"):
        if lowered.endswith(ext):
            lowered = lowered[: -len(ext)]
            break
    return re.sub(r"[\s_\-]+", " ", lowered)


def token_overlap(expected: str, actual: str) -> float:
    """|expected ∩ actual| / |expected| — 1.0 means every expected word found."""
    expected_tokens = _tokens(expected)
    if not expected_tokens:
        return 0.0
    return len(expected_tokens & _tokens(actual)) / len(expected_tokens)


@dataclass
class ItemMetrics:
    question: str
    expected_source: str
    expected_page: int | None
    source_hit: bool
    page_hit: bool
    first_hit: bool
    context_relevance: float
    answered: bool = False
    insufficient: bool = False
    groundedness_proxy: float | None = None
    retrieved: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "question": self.question,
            "expected_source": self.expected_source,
            "expected_page": self.expected_page,
            "source_hit": self.source_hit,
            "page_hit": self.page_hit,
            "first_hit": self.first_hit,
            "context_relevance": round(self.context_relevance, 3),
            "answered": self.answered,
            "insufficient": self.insufficient,
            "groundedness_proxy": (
                round(self.groundedness_proxy, 3)
                if self.groundedness_proxy is not None
                else None
            ),
        }


def evaluate_item(
    item: dict,
    retrieved_chunks,
    context_text: str,
    answer: str | None = None,
    insufficient: bool = False,
) -> ItemMetrics:
    """Score one evaluation item.

    retrieved_chunks: RetrievedChunk objects that were retrieved for the item.
    context_text: the assembled evidence block actually sent to the LLM.
    """
    expected_source = normalize_source(item.get("expected_source", ""))
    expected_page = item.get("expected_page")

    matched_source = False
    source_pages: set[int] = set()
    first_hit = False

    for index, chunk in enumerate(retrieved_chunks):
        chunk_source = normalize_source(chunk.metadata.get("document_name", ""))
        is_match = bool(expected_source) and (
            expected_source in chunk_source or chunk_source in expected_source
        )
        if is_match:
            matched_source = True
            page = chunk.metadata.get("page")
            if page is not None:
                source_pages.add(int(page))
            if index == 0:
                first_hit = True

    page_hit = expected_page is not None and expected_page in source_pages

    metrics = ItemMetrics(
        question=item.get("question", ""),
        expected_source=item.get("expected_source", ""),
        expected_page=expected_page,
        source_hit=matched_source,
        page_hit=page_hit,
        first_hit=first_hit,
        context_relevance=token_overlap(item.get("expected_answer", ""), context_text),
        answered=answer is not None,
        insufficient=insufficient,
    )

    if answer:
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", answer) if len(s.strip()) > 15]
        if sentences:
            grounded = sum(
                1
                for sentence in sentences
                if token_overlap(sentence, context_text) >= _OVERLAP_THRESHOLD
                or re.search(r"\[\d+\]", sentence)  # explicitly cited sentences count
            )
            metrics.groundedness_proxy = grounded / len(sentences)

    return metrics


@dataclass
class Report:
    items: list[ItemMetrics] = field(default_factory=list)

    def summary(self) -> dict:
        count = max(1, len(self.items))
        answered_items = [i for i in self.items if i.answered]
        grounded = [
            i.groundedness_proxy
            for i in answered_items
            if i.groundedness_proxy is not None
        ]
        return {
            "items": len(self.items),
            "source_hit_rate": sum(1 for i in self.items if i.source_hit) / count,
            "page_hit_rate": sum(1 for i in self.items if i.page_hit) / count,
            "first_hit_rate": sum(1 for i in self.items if i.first_hit) / count,
            "mean_context_relevance": sum(i.context_relevance for i in self.items) / count,
            "insufficient_rate": sum(1 for i in self.items if i.insufficient) / count,
            "mean_groundedness_proxy": (sum(grounded) / len(grounded)) if grounded else None,
        }

    def to_dict(self) -> dict:
        return {"summary": self.summary(), "items": [i.as_dict() for i in self.items]}
