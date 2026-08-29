"""Text extraction for PDF, TXT, Markdown and JSON sources.

Goals (in priority order):
1. Never lose page numbers (required for citations).
2. Preserve paragraph boundaries and headings so the chunker can work with
   structure instead of blind character windows.
3. Remove obvious extraction garbage (control characters, hyphenated
   line-breaks, repeated headers/footers, bare page-number lines).
4. Detect empty/scanned pages instead of silently indexing nothing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from apex_ai.core.errors import DocumentProcessingError
from apex_ai.core.logging import get_logger
from apex_ai.documents.models import Document, Page
from apex_ai.security.files import sanitize_filename, sha256_file

log = get_logger("extract")

SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md", ".markdown", ".json"}

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SOFT_HYPHEN = "\u00ad"
_HYPHEN_BREAK = re.compile(r"([A-Za-z])-\n([a-z])")
_SPACES = re.compile(r"[ \t\u00a0]+")
_PAGE_NUMBER_LINE = re.compile(r"^\s*(page\s*)?\d{1,4}\s*$", re.IGNORECASE)


def supported(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS


def _clean_text(raw: str) -> str:
    """Clean one page of extracted text while preserving newlines."""
    text = raw.replace(_SOFT_HYPHEN, "")
    text = _CONTROL_CHARS.sub(" ", text)
    text = _HYPHEN_BREAK.sub(r"\1\2", text)  # "exam-\nple" -> "example"
    lines = []
    for line in text.split("\n"):
        line = _SPACES.sub(" ", line).strip()
        if _PAGE_NUMBER_LINE.match(line):
            continue  # bare page numbers are extraction noise, not content
        lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _drop_repeated_lines(pages: list[str]) -> list[str]:
    """Remove header/footer lines that repeat on most pages.

    A short line (<80 chars) appearing on >= 60% of pages (and on >= 3 pages)
    is almost certainly a running header/footer, not content.
    """
    if len(pages) < 3:
        return pages
    from collections import Counter

    counts: Counter[str] = Counter()
    for page in pages:
        seen_on_page = {line.strip() for line in page.split("\n") if line.strip()}
        counts.update(seen_on_page)

    threshold = max(3, int(len(pages) * 0.6))
    repeated = {line for line, count in counts.items() if count >= threshold and len(line) < 80}
    if not repeated:
        return pages

    cleaned = []
    for page in pages:
        kept = [line for line in page.split("\n") if line.strip() not in repeated]
        cleaned.append("\n".join(kept))
    log.debug("Removed %d repeated header/footer line(s)", len(repeated))
    return cleaned


def _extract_pdf(
    path, document_id: str, name: str, max_pages: int | None = None
) -> Document:
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError as error:  # pragma: no cover
        raise DocumentProcessingError(
            what="The PDF library `pypdf` is not installed.",
            why="PDF extraction depends on the pypdf package.",
            fix="Run: pip install -r requirements.txt",
        ) from error

    try:
        reader = PdfReader(str(path))
        page_count = len(reader.pages)
    except Exception as error:
        raise DocumentProcessingError(
            what=f"The PDF `{name}` could not be opened or parsed.",
            why=f"pypdf reported: {error}",
            fix="Verify the file is a valid PDF. If it is corrupted, re-export or re-download it.",
        ) from error

    # Checked before extracting any page text (Phase 70): a pathological
    # page count is a memory/latency risk independent of file size - a PDF
    # can be well within the upload size limit and still have an enormous
    # number of pages.
    if max_pages is not None and page_count > max_pages:
        raise DocumentProcessingError(
            what=f"`{name}` has {page_count} pages, which exceeds the {max_pages}-page limit.",
            why="Extracting and indexing an extremely large document in one request risks "
                "exhausting memory and taking a very long time.",
            fix="Split the document into smaller files and upload them separately, or raise "
                "APEX_MAX_DOCUMENT_PAGES in .env if this machine can handle larger documents.",
        )

    try:
        raw_pages = [(page.extract_text() or "") for page in reader.pages]
    except Exception as error:
        raise DocumentProcessingError(
            what=f"The PDF `{name}` could not be opened or parsed.",
            why=f"pypdf reported: {error}",
            fix="Verify the file is a valid PDF. If it is corrupted, re-export or re-download it.",
        ) from error

    cleaned = _drop_repeated_lines([_clean_text(raw) for raw in raw_pages])

    pages = []
    empty_pages = []
    for number, text in enumerate(cleaned, start=1):
        page = Page(number=number, text=text)
        if page.is_empty():
            empty_pages.append(number)
        else:
            pages.append(page)

    if not pages:
        raise DocumentProcessingError(
            what=f"No readable text was found in `{name}`.",
            why="All pages are empty, or the PDF is a scan (images without a text layer).",
            fix="If this is a scanned document, run OCR on it first (for example with "
                "OCRmyPDF or Tesseract), then upload the OCR'd file.",
        )

    log.info(
        "PDF document %s: %d readable page(s), %d empty",
        document_id[:12],
        len(pages),
        len(empty_pages),
    )
    return Document(
        document_id=document_id,
        document_name=name,
        source_path=str(path),
        file_type="pdf",
        pages=pages,
        empty_pages=empty_pages,
    )


def _extract_text_like(path, document_id: str, name: str, file_type: str) -> Document:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise DocumentProcessingError(
            what=f"The file `{name}` could not be read.",
            why=str(error),
            fix="Check the file exists and is readable.",
        ) from error

    cleaned = _clean_text(raw)
    if len(cleaned.strip()) < 20:
        raise DocumentProcessingError(
            what=f"`{name}` contains (almost) no text.",
            why="The file appears to be empty.",
            fix="Upload a text file with actual content.",
        )

    return Document(
        document_id=document_id,
        document_name=name,
        source_path=str(path),
        file_type=file_type,
        pages=[Page(number=1, text=cleaned)],
    )


def _json_strings(node, collected: list[str], depth: int = 0) -> None:
    """Collect meaningful string leaves from nested JSON structures."""
    if depth > 12:
        return
    if isinstance(node, str):
        if len(node.strip()) > 1:
            collected.append(node.strip())
    elif isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str):
                collected.append(f"{key}: {value.strip()}" if len(value.strip()) > 1 else "")
            else:
                _json_strings(value, collected, depth + 1)
    elif isinstance(node, list):
        for item in node:
            _json_strings(item, collected, depth + 1)


def _extract_json(path, document_id: str, name: str) -> Document:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as error:
        raise DocumentProcessingError(
            what=f"`{name}` is not valid JSON.",
            why=f"json parser reported: {error}",
            fix="Validate the JSON file (for example with `python -m json.tool file.json`).",
        ) from error

    strings: list[str] = []
    _json_strings(data, strings)
    text = "\n\n".join(s for s in strings if s)
    if len(text.strip()) < 20:
        raise DocumentProcessingError(
            what=f"`{name}` contains no usable text content.",
            why="The JSON has no string values with content.",
            fix="Upload JSON containing text fields (for example FAQ or article dumps).",
        )

    return Document(
        document_id=document_id,
        document_name=name,
        source_path=str(path),
        file_type="json",
        pages=[Page(number=1, text=text)],
    )


def extract_document(path, max_pages: int | None = None) -> Document:
    """Extract a :class:`Document` from any supported file.

    Input: path to an existing file. ``max_pages`` (Phase 70) rejects a PDF
    with an excessive page count before any text is extracted from it - a
    file well within the upload size limit can still have a pathological
    page count.
    Output: Document with pages, page numbers preserved.
    Raises: DocumentProcessingError with a user-friendly explanation.
    """
    from pathlib import Path

    path = Path(path)
    if not path.is_file():
        raise DocumentProcessingError(
            what=f"File not found: `{path}`.",
            why="The upload did not land where the application expected it.",
            fix="Try uploading the file again.",
        )

    suffix = path.suffix.lower()
    name = sanitize_filename(path.name)
    document_id = sha256_file(path)

    if suffix == ".pdf":
        return _extract_pdf(path, document_id, name, max_pages=max_pages)
    if suffix in {".txt"}:
        return _extract_text_like(path, document_id, name, "txt")
    if suffix in {".md", ".markdown"}:
        return _extract_text_like(path, document_id, name, "md")
    if suffix == ".json":
        return _extract_json(path, document_id, name)

    raise DocumentProcessingError(
        what=f"Unsupported file type: `{suffix}`.",
        why="Apex AI supports PDF, TXT, Markdown and JSON.",
        fix="Convert the document to one of the supported formats and try again.",
    )
