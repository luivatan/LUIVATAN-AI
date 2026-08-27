"""Document and knowledge primitives for Apex AI.

Processing is synchronous by default but the queue API makes it safe to move
work to a worker without changing the UI contract.
"""
from __future__ import annotations
import hashlib
import queue
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ALLOWED_EXTENSIONS = {".pdf"}
MAX_FILE_BYTES = 100 * 1024 * 1024

class DocumentError(ValueError): pass

@dataclass(frozen=True)
class Chunk:
    text: str
    document_id: str
    filename: str
    page: int
    chunk_index: int
    heading: str | None = None

@dataclass
class Document:
    id: str
    filename: str
    path: Path
    size: int
    pages: int = 0
    status: str = "queued"
    error: str | None = None
    chunks: list[Chunk] = field(default_factory=list)


def validate_file(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_file(): raise DocumentError("The uploaded file could not be found.")
    if candidate.suffix.lower() not in ALLOWED_EXTENSIONS: raise DocumentError("Only PDF documents are supported.")
    if candidate.stat().st_size == 0: raise DocumentError("The uploaded document is empty.")
    if candidate.stat().st_size > MAX_FILE_BYTES: raise DocumentError("The document exceeds the 100 MB limit.")
    return candidate


def document_id(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""): digest.update(block)
    return digest.hexdigest()


def extract_pages(path: str | Path) -> list[tuple[int, str]]:
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        pages = [(number, (page.extract_text() or "").strip()) for number, page in enumerate(reader.pages, 1)]
    except Exception as exc: raise DocumentError("This PDF could not be read.") from exc
    if not any(text for _, text in pages): raise DocumentError("No readable text was found in this PDF.")
    return pages

_HEADING = re.compile(r"^(?:[A-Z][A-Z0-9 \-:,]{3,80}|(?:\d+(?:\.\d+)*[.)]?\s+)[A-Z].{2,100})$")

def detect_heading(line: str) -> str | None:
    clean = " ".join(line.split()).strip()
    return clean if _HEADING.match(clean) else None


def smart_chunks(text: str, document_id: str, filename: str, page: int, size: int = 1000, overlap: int = 150) -> list[Chunk]:
    if size <= overlap or size < 1: raise ValueError("Chunk size must be greater than overlap.")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, current, heading = [], "", None
    for paragraph in paragraphs:
        found = detect_heading(paragraph.splitlines()[0])
        if found: heading = found
        candidate = f"{current}\n\n{paragraph}".strip()
        if current and len(candidate) > size:
            chunks.append(Chunk(current, document_id, filename, page, len(chunks) + 1, heading))
            tail = current[-overlap:]
            current = f"{tail}\n\n{paragraph}".strip()
        else: current = candidate
    if current: chunks.append(Chunk(current, document_id, filename, page, len(chunks) + 1, heading))
    return chunks

class DocumentQueue:
    def __init__(self, on_update: Callable[[Document], None] | None = None):
        self.jobs: queue.Queue[Document] = queue.Queue()
        self.documents: dict[str, Document] = {}
        self.on_update = on_update
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def submit(self, path: str | Path) -> Document:
        source = validate_file(path)
        doc_id = document_id(source)
        if doc_id in self.documents: return self.documents[doc_id]
        doc = Document(doc_id, source.name, source, source.stat().st_size)
        self.documents[doc_id] = doc
        self.jobs.put(doc)
        return doc

    def _run(self):
        while True:
            doc = self.jobs.get()
            try:
                doc.status = "processing"; self._notify(doc)
                pages = extract_pages(doc.path); doc.pages = len(pages)
                doc.chunks = [chunk for page, text in pages for chunk in smart_chunks(text, doc.id, doc.filename, page)]
                doc.status = "ready"
            except DocumentError as exc: doc.status, doc.error = "failed", str(exc)
            finally: self._notify(doc); self.jobs.task_done()

    def _notify(self, doc):
        if self.on_update: self.on_update(doc)

    def list_documents(self) -> list[Document]: return sorted(self.documents.values(), key=lambda d: d.filename.lower())
    def delete(self, doc_id: str) -> bool: return self.documents.pop(doc_id, None) is not None
