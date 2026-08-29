"""Small, dependency-free helpers used by ingest.py.

Kept separate from ingest.py (which imports chromadb, gradio,
sentence-transformers, and requests at module load time) so these pure
functions can be unit tested without installing or loading any heavy
ML/vector-store dependencies.
"""

import hashlib
from pathlib import Path

MEDICAL_WARNING = (
    "This AI is designed for medical documents. "
    "Results may be less reliable for non-medical content."
)

MEDICAL_KEYWORDS = {
    "anatomy",
    "antibiotic",
    "blood",
    "cardiac",
    "care",
    "cell",
    "clinical",
    "diagnosis",
    "disease",
    "doctor",
    "dose",
    "drug",
    "health",
    "hospital",
    "infection",
    "injury",
    "lab",
    "medical",
    "medicine",
    "nurse",
    "patient",
    "pharmacology",
    "physician",
    "prescription",
    "symptom",
    "therapy",
    "treatment",
    "vaccine",
}


def safe_filename(filename):
    return Path(filename).name.replace("/", "_").replace("\\", "_")


def file_sha256(path):
    digest = hashlib.sha256()

    with open(path, "rb") as source_file:
        for block in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(block)

    return digest.hexdigest()


def chunk_text(text, chunk_size=1000, overlap=150):
    cleaned = " ".join(text.split())

    if not cleaned:
        return []

    chunks = []
    start = 0

    while start < len(cleaned):
        end = start + chunk_size
        chunk = cleaned[start:end].strip()

        if chunk:
            chunks.append(chunk)

        if end >= len(cleaned):
            break

        start = max(end - overlap, start + 1)

    return chunks


def is_likely_medical_document(pages):
    text = " ".join(page_text.lower() for _, page_text in pages)
    matches = sum(1 for keyword in MEDICAL_KEYWORDS if keyword in text)

    return matches >= 3


def citation_label(metadata):
    source = metadata.get("source", "unknown source")
    page = metadata.get("page")
    chunk = metadata.get("chunk")

    if page is not None:
        if chunk is not None:
            return f"{source} Page {page} Chunk {chunk}"

        return f"{source} Page {page}"

    return source
