"""Structure-aware chunking.

The old code did this::

    cleaned = " ".join(text.split())          # destroy all structure
    chunk = cleaned[start:start + chunk_size] # cut mid-sentence

This module instead walks the document's natural structure:

    heading -> section -> paragraphs -> logical chunk

and only ever splits inside a paragraph when that paragraph alone exceeds the
maximum chunk size — and then at sentence boundaries.

All sizes are configurable (Settings.chunk_size / chunk_overlap /
min_chunk_size / max_chunk_size) so they can be tuned per corpus instead of
hardcoded.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from apex_ai.core.logging import get_logger
from apex_ai.documents.models import Chunk, Document, Section, utc_now_iso

log = get_logger("chunk")

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_NUMBERED_HEADING = re.compile(r"^\s*(\d+(\.\d+)*)([.)])?\s+\S.*$")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"(\[])")


def _looks_like_heading(line: str, next_line: str, markdown: bool) -> int:
    """Return a heading level (1-6) if ``line`` looks like a heading, else 0.

    Heuristics, in order of confidence:
    1. Markdown ``#`` prefixes (only for md/txt sources where it is common).
    2. Numbered headings: "3", "3.1", "3.1.2 Treatment".
    3. ALL-CAPS short lines.
    4. Short Title-Case-ish lines without terminal punctuation, followed by
       a blank line or a clearly longer paragraph line.
    """
    if not line or len(line) > 120:
        return 0

    if markdown:
        match = _MD_HEADING.match(line)
        if match:
            return len(match.group(1))

    if _NUMBERED_HEADING.match(line):
        prefix = _NUMBERED_HEADING.match(line).group(1)
        has_dot_structure = "." in prefix
        single_digit_paren = len(prefix) == 1 and line.strip()[len(prefix)] in ")."
        if has_dot_structure or single_digit_paren:
            depth = prefix.count(".") + 1
            return min(depth + 1, 6)
        return 0  # e.g. "20 minutes" — a data line, not a heading

    stripped = line.strip()
    words = stripped.split()
    if (
        1 <= len(words) <= 10
        and len(stripped) >= 3
        and stripped == stripped.upper()
        and any(c.isalpha() for c in stripped)
        and not stripped.endswith((".", "!", "?", ",", ";", ":"))
    ):
        return 2 if len(words) > 1 else 3

    ends_sentence = stripped.endswith((".", "!", "?", ",", ";", ":"))
    # A blank next line — or an identical repeated line (some extractors emit
    # headings twice) — both indicate a heading boundary.
    next_blank = (
        not next_line.strip() if next_line is not None else True
    ) or next_line.strip() == line.strip()
    if (
        2 <= len(words) <= 12
        and not ends_sentence
        and stripped[0].isupper()
        and (next_blank or len(next_line) > len(stripped) * 1.5)
    ):
        return 3

    return 0


_BULLET = re.compile(r"^\s*([•▪‣◦*]|[-–]\s+)\s*")


def _split_paragraphs(page_text: str) -> list[str]:
    """Group extracted lines into paragraphs.

    PDF extraction usually returns one line per *visual* line, so we use two
    signals to decide where paragraphs start:

    - a blank line always starts a new paragraph;
    - a line ending in terminal punctuation, followed by a line that starts
      with an uppercase letter / digit / bullet, almost certainly ends a
      paragraph in the original document.

    Wrapped sentences (previous line has no terminal punctuation) stay merged,
    which is what keeps sentences intact.
    """
    paragraphs: list[str] = []
    current: list[str] = []
    lines = [line.strip() for line in page_text.split("\n")]

    def close():
        if current:
            paragraphs.append(" ".join(current))
            current.clear()

    for index, line in enumerate(lines):
        if not line:
            close()
            continue

        if _BULLET.match(line) and current:
            close()  # a bullet always starts a new paragraph

        current.append(line)
        ends_sentence = line.endswith((".", "!", "?"))
        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        next_starts_new = (
            not next_line
            or next_line[:1].isupper()
            or next_line[:1].isdigit()
            or bool(_BULLET.match(next_line))
        )
        if ends_sentence and next_starts_new:
            close()

    close()
    return paragraphs


def parse_sections(document: Document) -> list[Section]:
    """Build an ordered list of sections from the document's pages.

    Sections can span page breaks: a section stays open until the next
    heading appears, and ``page_end`` tracks the last page that contributed
    text to it. Body lines between headings are grouped into paragraphs with
    :func:`_split_paragraphs`.
    """
    markdown = document.file_type in {"md", "markdown", "txt"}
    sections: list[Section] = []
    current: Section | None = None
    body_run: list[str] = []

    def flush_run(page_number: int) -> None:
        nonlocal body_run
        if body_run and current is not None:
            paragraphs = _split_paragraphs("\n".join(body_run))
            current.paragraphs.extend(paragraphs)
            current.paragraph_pages.extend([page_number] * len(paragraphs))
            current.page_end = max(current.page_end, page_number)
        body_run = []

    for page in document.pages:
        lines = page.text.split("\n")
        for index, line in enumerate(lines):
            next_line = lines[index + 1] if index + 1 < len(lines) else ""
            level = _looks_like_heading(line, next_line, markdown)
            if level and line.strip():
                flush_run(page.number)
                current = Section(
                    title=line.strip().lstrip("#").strip(),
                    level=level,
                    page_start=page.number,
                    page_end=page.number,
                )
                sections.append(current)
                continue

            if current is None:
                current = Section(
                    title=document.document_name,
                    level=0,
                    page_start=page.number,
                    page_end=page.number,
                )
                sections.append(current)

            if line.strip():
                body_run.append(line)

        flush_run(page.number)

    return [s for s in sections if any(p.strip() for p in s.paragraphs)]


@dataclass
class _Piece:
    """A paragraph (or sentence) available for packing into chunks.

    Page ranges are explicit because a section—and occasionally one packed
    chunk—can legitimately cross a PDF page boundary. A single ``page`` value
    silently attributed all such text to the section's first page.
    """

    text: str
    page_start: int
    page_end: int
    section: str
    section_level: int


def _hard_split(text: str, max_size: int) -> list[str]:
    """Split an oversized piece at sentence boundaries, then word boundaries."""
    if len(text) <= max_size:
        return [text]
    parts: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(text):
        while len(sentence) > max_size:
            cut = sentence.rfind(" ", 0, max_size)
            cut = cut if cut > max_size // 2 else max_size
            parts.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if sentence.strip():
            parts.append(sentence.strip())
    return parts


class Chunker:
    """Turns a Document into embeddable Chunks.

    Parameters come from Settings so nothing is hardcoded:

    - ``chunk_size``      target characters per chunk (soft limit)
    - ``max_chunk_size``  hard limit; pieces larger than this get split
    - ``min_chunk_size``  chunks smaller than this are merged into a neighbour
    - ``chunk_overlap``   characters of trailing context repeated in the next
                          chunk, aligned to sentence boundaries
    """

    def __init__(self, settings) -> None:
        self.chunk_size = max(100, settings.chunk_size)
        self.max_chunk_size = max(self.chunk_size, settings.max_chunk_size)
        self.min_chunk_size = max(0, settings.min_chunk_size)
        self.overlap = max(0, min(settings.chunk_overlap, self.chunk_size // 2))

    def build_chunks(self, document: Document) -> list[Chunk]:
        sections = parse_sections(document)
        pieces_by_section: list[list[_Piece]] = []

        for section in sections:
            pieces: list[_Piece] = []
            for paragraph_index, paragraph in enumerate(section.paragraphs):
                page_number = (
                    section.paragraph_pages[paragraph_index]
                    if paragraph_index < len(section.paragraph_pages)
                    else section.page_start
                )
                paragraph = paragraph.strip()
                if not paragraph:
                    continue
                for part in _hard_split(paragraph, self.max_chunk_size):
                    pieces.append(
                        _Piece(
                            text=part,
                            page_start=page_number,
                            page_end=page_number,
                            section=section.title,
                            section_level=section.level,
                        )
                    )
            if pieces:
                pieces_by_section.append(pieces)

        if not pieces_by_section:
            return []

        # Pack and merge each section independently. Running the tiny-chunk
        # merge over the flattened list used to combine the final fragment of
        # one heading with the first chunk under the next heading, making the
        # SECTION citation false.
        packed: list[_Piece] = []
        for pieces in pieces_by_section:
            packed.extend(self._merge_tiny(self._pack(pieces)))
        return self._to_chunks(document, packed)

    # -- internals -------------------------------------------------------

    def _pack(self, pieces: list[_Piece]) -> list[_Piece]:
        """Greedily pack pieces up to ``chunk_size`` and retain page ranges."""
        packed: list[_Piece] = []
        buffer: list[str] = []
        buffer_len = 0
        buffer_page_start = pieces[0].page_start
        buffer_page_end = pieces[0].page_end
        buffer_section = pieces[0].section
        buffer_section_level = pieces[0].section_level

        def flush() -> None:
            nonlocal buffer, buffer_len
            if buffer:
                packed.append(
                    _Piece(
                        text="\n\n".join(buffer),
                        page_start=buffer_page_start,
                        page_end=buffer_page_end,
                        section=buffer_section,
                        section_level=buffer_section_level,
                    )
                )
                buffer, buffer_len = [], 0

        for piece in pieces:
            if buffer and buffer_len + len(piece.text) + 2 > self.chunk_size:
                flush()
            if not buffer:
                buffer_page_start = piece.page_start
                buffer_page_end = piece.page_end
                buffer_section = piece.section
                buffer_section_level = piece.section_level
            else:
                buffer_page_end = max(buffer_page_end, piece.page_end)
            buffer.append(piece.text)
            buffer_len += len(piece.text) + 2
        flush()
        return packed

    def _merge_tiny(self, chunks: list[_Piece]) -> list[_Piece]:
        """Merge undersized neighbours without crossing a section or hard limit.

        A fragment is retained when no truthful, size-safe merge is possible;
        exceeding ``max_chunk_size`` is worse than keeping one small chunk.
        """
        if self.min_chunk_size <= 0 or len(chunks) <= 1:
            return chunks

        merged: list[_Piece] = []
        for piece in chunks:
            can_merge_back = (
                merged
                and len(piece.text) < self.min_chunk_size
                and merged[-1].section == piece.section
                and len(merged[-1].text) + len(piece.text) + 2 <= self.max_chunk_size
            )
            if can_merge_back:
                previous = merged[-1]
                merged[-1] = _Piece(
                    text=previous.text + "\n\n" + piece.text,
                    page_start=min(previous.page_start, piece.page_start),
                    page_end=max(previous.page_end, piece.page_end),
                    section=previous.section,
                    section_level=previous.section_level,
                )
            else:
                merged.append(piece)

        # The first fragment has no previous neighbour. Merge it forward only
        # when the section and hard-size constraints both remain valid.
        if (
            len(merged) > 1
            and len(merged[0].text) < self.min_chunk_size
            and merged[0].section == merged[1].section
            and len(merged[0].text) + len(merged[1].text) + 2 <= self.max_chunk_size
        ):
            first, second = merged[0], merged[1]
            merged[1] = _Piece(
                text=first.text + "\n\n" + second.text,
                page_start=min(first.page_start, second.page_start),
                page_end=max(first.page_end, second.page_end),
                section=first.section,
                section_level=first.section_level,
            )
            merged.pop(0)
        return merged

    def _overlap_tail(self, text: str) -> str:
        """Return ~``self.overlap`` trailing characters, snapped to a
        sentence boundary so the overlap never cuts a sentence in half."""
        if self.overlap <= 0 or len(text) <= self.overlap:
            return ""
        tail = text[-self.overlap :]
        cut = min(
            (pos for pos in (tail.find(". "), tail.find("! "), tail.find("? ")) if pos != -1),
            default=None,
        )
        if cut is not None:
            tail = tail[cut + 1 :]
        return tail.strip()

    def _to_chunks(self, document: Document, packed: list[_Piece]) -> list[Chunk]:
        chunks: list[Chunk] = []
        created = utc_now_iso()
        previous: _Piece | None = None

        for index, piece in enumerate(packed, start=1):
            text = piece.text
            # Overlap is useful within one page/section, but carrying a tail
            # across a page or heading makes citation locations ambiguous.
            same_location = (
                previous is not None
                and previous.section == piece.section
                and previous.page_end == piece.page_start
            )
            if same_location:
                tail = self._overlap_tail(previous.text)
                available = self.max_chunk_size - len(text) - 2
                if tail and available > 0:
                    if len(tail) > available:
                        tail = tail[-available:]
                        first_space = tail.find(" ")
                        if first_space >= 0:
                            tail = tail[first_space + 1 :]
                    if tail and not text.startswith(tail):
                        text = f"{tail}\n\n{text}"
            previous = piece

            chunk_id = f"{document.document_id}:{index:04d}"
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    text=text,
                    document_id=document.document_id,
                    metadata={
                        "chunk_id": chunk_id,
                        "document_id": document.document_id,
                        "document_name": document.document_name,
                        "filename": document.document_name,
                        "source": document.source_path,
                        "file_type": document.file_type,
                        # ``page`` stays for API compatibility. The explicit
                        # range prevents a spanning chunk from pretending all
                        # evidence came from one page.
                        "page": piece.page_start,
                        "page_number": piece.page_start,
                        "page_start": piece.page_start,
                        "page_end": piece.page_end,
                        "section": piece.section[:200],
                        "section_level": piece.section_level,
                        "chunk_index": index,
                        "character_count": len(text),
                        "content_sha256": content_hash,
                        "chunk_schema_version": 2,
                        "created_at": created,
                    },
                )
            )

        log.info("Chunked %s into %d chunk(s)", document.document_name, len(chunks))
        return chunks
