"""Data model for the document pipeline.

Flow: ``extraction`` produces a :class:`Document` (ordered pages),
``chunking`` turns it into :class:`Chunk` objects with rich metadata, and the
ingestion service embeds + persists those chunks.

Every chunk carries its origin (document id, name, page range, section) so
that citations never lose page information — a hard requirement for the
medical-document use case.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Page:
    number: int  # 1-based, as printed in citations
    text: str

    def is_empty(self) -> bool:
        return len(self.text.strip()) < 20


@dataclass
class Document:
    """An extracted document: identity + ordered pages + extraction stats."""

    document_id: str  # sha256 of the file bytes (duplicate-detection key)
    document_name: str  # sanitized file name shown to users
    source_path: str  # where the file lives on disk
    file_type: str  # pdf | txt | md | json | csv | tsv
    pages: list[Page] = field(default_factory=list)
    empty_pages: list[int] = field(default_factory=list)  # scanned/blank page numbers
    created_at: str = field(default_factory=utc_now_iso)

    def full_text(self) -> str:
        return "\n\n".join(page.text for page in self.pages)

    @property
    def page_count(self) -> int:
        return len(self.pages)


@dataclass
class Section:
    """A structural region of the document (heading + its paragraphs).

    ``page_start``/``page_end`` track that sections can span page breaks.
    """

    title: str
    level: int  # 1 = top-level heading, 2 = sub-heading, 0 = no heading found
    page_start: int
    page_end: int
    paragraphs: list[str] = field(default_factory=list)
    # Parallel to ``paragraphs``. Keeping text as ``list[str]`` preserves the
    # public Section shape while retaining the source page for every paragraph.
    # Older callers that construct Section directly may leave this empty; the
    # chunker then falls back to ``page_start``.
    paragraph_pages: list[int] = field(default_factory=list)


@dataclass
class Chunk:
    """The unit stored in the vector database."""

    chunk_id: str
    text: str
    document_id: str
    metadata: dict = field(default_factory=dict)
