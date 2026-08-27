"""Context builder.

Turns final evidence chunks into the block format sent to the LLM::

    [1]
    SOURCE: World Health Statistics 2025
    PAGE: 42
    SECTION: Life expectancy

    <relevant text>

Two budget rules keep prompts small and relevant:

- ``char_limit`` (APEX_CONTEXT_CHAR_LIMIT) caps the total context; chunks
  that do not fit are dropped from the END (lowest ranked first), so the
  context quality degrades gracefully instead of overflowing the model.
- Chunks are never truncated mid-block silently: if a single chunk exceeds
  the remaining budget it is skipped; if it exceeds the whole budget it is
  truncated with an ellipsis marker.

The builder returns which chunks were actually used — the engine may only
cite those. This is what prevents fabricated citations.
"""

from __future__ import annotations

from dataclasses import dataclass

from apex_ai.core.logging import get_logger
from apex_ai.core.types import RetrievedChunk

log = get_logger("rag.context")


@dataclass
class BuiltContext:
    text: str
    used_chunks: list[RetrievedChunk]


def build_context(chunks: list[RetrievedChunk], char_limit: int = 6000) -> BuiltContext:
    """Assemble the evidence block under a character budget.

    Input: ranked chunks (best first). Output: formatted context + the subset
    of chunks that actually fit.
    """
    blocks: list[str] = []
    used: list[RetrievedChunk] = []
    remaining = char_limit

    for index, chunk in enumerate(chunks, start=1):
        metadata = chunk.metadata
        header = (
            f"[{index}]\n"
            f"SOURCE: {metadata.get('document_name', 'unknown')}\n"
            f"PAGE: {metadata.get('page', 'n/a')}\n"
            f"SECTION: {metadata.get('section') or 'n/a'}\n"
        )
        text = chunk.text.strip()
        block = f"{header}\n{text}\n"

        if len(block) > remaining:
            if not used:
                # Always keep at least the best chunk, truncated if needed.
                keep = max(200, remaining - len(header) - 10)
                block = f"{header}\n{text[:keep]}\n[…truncated]\n"
                blocks.append(block)
                used.append(chunk)
            break
        blocks.append(block)
        used.append(chunk)
        remaining -= len(block)

    if len(used) < len(chunks):
        log.debug("Context budget: %d of %d candidate chunk(s) fit", len(used), len(chunks))

    return BuiltContext(text="\n".join(blocks), used_chunks=used)
