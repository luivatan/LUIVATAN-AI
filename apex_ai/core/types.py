"""Shared dataclasses used across subsystems.

Keeping them in one low-level module avoids circular imports:
``vectordb`` produces ``RetrievedChunk`` objects that ``retrieval``, ``rag``
and the UI all consume.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RetrievedChunk:
    """A chunk that came out of the vector store (or a retrieval stage)."""

    chunk_id: str
    text: str
    metadata: dict
    similarity: float = 0.0  # cosine similarity from the vector stage (0..1)
    retrieval_score: float = 0.0  # fused/rerank score (comparable within one stage)

    @property
    def source(self) -> str:
        return self.metadata.get("document_name", self.metadata.get("source", "unknown"))

    @property
    def page(self):
        return self.metadata.get("page")

    @property
    def section(self) -> str:
        return self.metadata.get("section", "")


@dataclass
class Citation:
    """A source reference attached to an answer. Only created from chunks
    that were actually placed into the LLM context — never invented."""

    index: int
    source: str
    page: int | None
    section: str
    chunk_id: str
    text: str
    score: float
    page_end: int | None = None

    def label(self) -> str:
        if self.page is None:
            location = "no page"
        elif self.page_end not in (None, self.page):
            location = f"pages {self.page}-{self.page_end}"
        else:
            location = f"page {self.page}"
        if self.section:
            return f"[{self.index}] {self.source} — {location} — {self.section}"
        return f"[{self.index}] {self.source} — {location}"

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "source": self.source,
            "page": self.page,
            "page_end": self.page_end,
            "section": self.section,
            "chunk_id": self.chunk_id,
            "score": round(self.score, 4),
        }


@dataclass
class AnswerResult:
    """The complete outcome of one RAG turn."""

    answer: str
    citations: list[Citation] = field(default_factory=list)
    confidence: float = 0.0  # best vector similarity among used evidence
    insufficient_evidence: bool = False
    queries_used: list[str] = field(default_factory=list)
    timings: dict = field(default_factory=dict)
    # Internal audit data for evaluation; normal API serializers do not expose
    # candidate/context internals.
    context_chunk_ids: list[str] = field(default_factory=list)
    context_text: str = ""

    @property
    def sources_block(self) -> str:
        if not self.citations:
            return ""
        lines = ["Sources:"]
        lines += [f"* {c.label()}" for c in self.citations]
        return "\n".join(lines)
