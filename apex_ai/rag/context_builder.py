"""Deduplicated, structured, budget-safe evidence context construction.

Only chunks returned in ``BuiltContext.used_chunks`` may become citations.
Selection follows retrieval relevance; selected chunks are then grouped in
source/page order where useful so adjacent evidence reads coherently.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from apex_ai.core.logging import get_logger
from apex_ai.core.types import RetrievedChunk

log = get_logger("rag.context")

_WORD = re.compile(r"\w+")


@dataclass
class BuiltContext:
    text: str
    used_chunks: list[RetrievedChunk]
    char_limit: int = 0
    dropped_duplicate_ids: list[str] = field(default_factory=list)
    dropped_budget_ids: list[str] = field(default_factory=list)
    truncated_chunk_ids: list[str] = field(default_factory=list)

    @property
    def character_count(self) -> int:
        return len(self.text)

    def diagnostics(self) -> dict:
        return {
            "character_count": self.character_count,
            "character_limit": self.char_limit,
            "used_chunk_ids": [chunk.chunk_id for chunk in self.used_chunks],
            "dropped_duplicate_ids": list(self.dropped_duplicate_ids),
            "dropped_budget_ids": list(self.dropped_budget_ids),
            "truncated_chunk_ids": list(self.truncated_chunk_ids),
        }


def _normalized_text(text: str) -> str:
    return " ".join(_WORD.findall((text or "").casefold()))


def _near_duplicate(first: RetrievedChunk, second: RetrievedChunk) -> bool:
    first_text = _normalized_text(first.text)
    second_text = _normalized_text(second.text)
    if not first_text or not second_text:
        return False
    if first_text == second_text:
        return True

    # Near-duplicate suppression is deliberately limited to one document;
    # similar passages in independent sources may be useful corroboration.
    if first.metadata.get("document_id") != second.metadata.get("document_id"):
        return False
    if min(len(first_text), len(second_text)) < 100:
        return False
    first_tokens = set(first_text.split())
    second_tokens = set(second_text.split())
    # Template-like passages that differ in an ID, date, or number are not
    # duplicates: those small differences may be the exact requested fact.
    first_numeric = {token for token in first_tokens if any(char.isdigit() for char in token)}
    second_numeric = {
        token for token in second_tokens if any(char.isdigit() for char in token)
    }
    if first_numeric != second_numeric:
        return False

    union = first_tokens | second_tokens
    jaccard = len(first_tokens & second_tokens) / max(1, len(union))
    length_ratio = min(len(first_text), len(second_text)) / max(len(first_text), len(second_text))
    if jaccard < 0.92 or length_ratio < 0.80:
        return False
    # SequenceMatcher is comparatively expensive, so only run it after the
    # linear-time token/length filters identify a plausible duplicate.
    sequence = difflib.SequenceMatcher(
        None, first_text, second_text, autojunk=False
    ).ratio()
    return sequence >= 0.92


def _deduplicate(
    chunks: list[RetrievedChunk],
) -> tuple[list[RetrievedChunk], list[str]]:
    unique: list[RetrievedChunk] = []
    dropped: list[str] = []
    seen_ids: set[str] = set()
    for chunk in chunks:
        if chunk.chunk_id in seen_ids or any(_near_duplicate(chunk, prior) for prior in unique):
            dropped.append(chunk.chunk_id)
            continue
        seen_ids.add(chunk.chunk_id)
        unique.append(chunk)
    return unique, dropped


def _page_label(metadata: dict) -> str:
    page_start = metadata.get("page_start", metadata.get("page", "n/a"))
    page_end = metadata.get("page_end", page_start)
    if page_start in (None, ""):
        return "n/a"
    if page_end not in (None, "", page_start):
        return f"{page_start}-{page_end}"
    return str(page_start)


def _header(chunk: RetrievedChunk, index: int) -> str:
    metadata = chunk.metadata
    return (
        f"[{index}]\n"
        f"SOURCE: {metadata.get('document_name', metadata.get('filename', 'unknown'))}\n"
        f"PAGE: {_page_label(metadata)}\n"
        f"SECTION: {metadata.get('section') or 'n/a'}\n"
    )


def _source_order(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Group selected chunks by first source appearance, then natural order."""
    source_rank: dict[str, int] = {}
    for rank, chunk in enumerate(chunks):
        document_id = str(chunk.metadata.get("document_id", chunk.source))
        source_rank.setdefault(document_id, rank)

    return sorted(
        chunks,
        key=lambda chunk: (
            source_rank[str(chunk.metadata.get("document_id", chunk.source))],
            int(chunk.metadata.get("page_start", chunk.metadata.get("page", 0)) or 0),
            int(chunk.metadata.get("chunk_index", 0) or 0),
        ),
    )


def build_context(
    chunks: list[RetrievedChunk],
    char_limit: int = 6000,
    *,
    preserve_document_order: bool = True,
) -> BuiltContext:
    """Select and format evidence without ever exceeding ``char_limit``.

    Duplicate filtering and budget selection operate in ranked order. A chunk
    that does not fit is skipped so a later, shorter chunk may still use the
    remaining budget. If the best chunk alone is too large, it is visibly
    truncated—not silently cut—and remains the only cited source for that
    block.
    """
    char_limit = max(0, int(char_limit))
    unique, duplicate_ids = _deduplicate(chunks)
    selected: list[RetrievedChunk] = []
    budget_ids: list[str] = []
    truncated_ids: list[str] = []
    estimated = 0

    for chunk in unique:
        # Index width can alter a header by a character after reordering. Use
        # the eventual selected index and leave newline separators accounted.
        index = len(selected) + 1
        block_length = len(_header(chunk, index)) + 1 + len(chunk.text.strip()) + 1
        separator = 1 if selected else 0
        if estimated + separator + block_length <= char_limit:
            selected.append(chunk)
            estimated += separator + block_length
            continue

        if not selected and char_limit > len(_header(chunk, 1)) + len("\n[…truncated]\n"):
            selected.append(chunk)
            truncated_ids.append(chunk.chunk_id)
            # It consumes the complete budget; lower-ranked chunks cannot fit.
            estimated = char_limit
        else:
            budget_ids.append(chunk.chunk_id)

    if preserve_document_order:
        selected = _source_order(selected)

    blocks: list[str] = []
    used: list[RetrievedChunk] = []
    remaining = char_limit
    for index, chunk in enumerate(selected, start=1):
        separator_length = 1 if blocks else 0
        header = _header(chunk, index)
        suffix = "\n"
        marker = "\n[…truncated]\n"
        text = chunk.text.strip()
        available = remaining - separator_length - len(header) - 1 - len(suffix)
        if available < 0:
            budget_ids.append(chunk.chunk_id)
            continue

        used_chunk = chunk
        if len(text) > available:
            marker_room = remaining - separator_length - len(header) - 1 - len(marker)
            if marker_room <= 0:
                budget_ids.append(chunk.chunk_id)
                continue
            text = text[:marker_room].rstrip()
            block = f"{header}\n{text}{marker}"
            # ``used_chunks`` is the citation/evidence source of truth. Keep
            # only the excerpt actually supplied to the model so the evidence
            # gate and source viewer cannot rely on unseen tail text.
            used_chunk = RetrievedChunk(
                chunk_id=chunk.chunk_id,
                text=text,
                metadata=dict(chunk.metadata),
                similarity=chunk.similarity,
                retrieval_score=chunk.retrieval_score,
            )
            if chunk.chunk_id not in truncated_ids:
                truncated_ids.append(chunk.chunk_id)
        else:
            block = f"{header}\n{text}\n"

        needed = separator_length + len(block)
        if needed > remaining:  # defensive: formatting must stay budget-safe
            budget_ids.append(chunk.chunk_id)
            continue
        blocks.append(block)
        used.append(used_chunk)
        remaining -= needed

    context_text = "\n".join(blocks)
    if duplicate_ids or budget_ids:
        log.debug(
            "Context used %d/%d chunk(s); %d duplicate, %d over budget",
            len(used),
            len(chunks),
            len(duplicate_ids),
            len(budget_ids),
        )
    return BuiltContext(
        text=context_text,
        used_chunks=used,
        char_limit=char_limit,
        dropped_duplicate_ids=duplicate_ids,
        dropped_budget_ids=list(dict.fromkeys(budget_ids)),
        truncated_chunk_ids=truncated_ids,
    )
