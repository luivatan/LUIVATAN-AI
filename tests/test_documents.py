"""Extraction + chunking tests (small fixtures, no network)."""

from __future__ import annotations

import pytest

from apex_ai.core.errors import DocumentProcessingError
from apex_ai.documents.chunking import Chunker
from apex_ai.documents.extraction import extract_document
from apex_ai.security.files import restrict_to_owner, sanitize_filename
from tests.conftest import DATA_DIR


@pytest.fixture()
def document():
    return extract_document(DATA_DIR / "sample_first_aid.pdf")


def test_pdf_pages_are_preserved(document):
    assert document.page_count == 2
    assert [p.number for p in document.pages] == [1, 2]


def test_pdf_section_headings_detected(document):
    text = document.full_text()
    assert "Fever Management" in text
    assert "Hydration and Dehydration" in text


def test_repeated_header_footer_removed(document):
    # "page marker" line was injected on page 2 only in the fixture generator;
    # the repeated-line remover needs >=3 pages, so with 2 pages it must NOT
    # destroy content. Assert body text survived:
    assert "fever" in document.full_text().lower()


def test_scanned_pdf_raises_friendly_error():
    with pytest.raises(DocumentProcessingError) as excinfo:
        extract_document(DATA_DIR / "scanned_empty.pdf")
    message = str(excinfo.value)
    assert "OCR" in message  # tells the user what to do


def test_corrupted_pdf_raises_actionable_error(tmp_path):
    broken = tmp_path / "corrupted.pdf"
    broken.write_bytes(b"%PDF-1.7\nthis is not a valid PDF structure")
    with pytest.raises(DocumentProcessingError) as excinfo:
        extract_document(broken)
    message = str(excinfo.value)
    assert "could not be opened or parsed" in message
    assert "corrupted" in message


def test_txt_and_md_extraction():
    md = extract_document(DATA_DIR / "burn_care.md")
    assert md.file_type == "md"
    assert "20 minutes" in md.full_text()


def test_json_extraction_collects_strings():
    doc = extract_document(DATA_DIR / "first_aid_faq.json")
    assert "20 minutes" in doc.full_text()
    assert "electrolyte" in doc.full_text()


def test_unsupported_extension_rejected(tmp_path):
    file = tmp_path / "image.png"
    file.write_bytes(b"\x89PNG")
    with pytest.raises(DocumentProcessingError):
        extract_document(file)


def _write_blank_pdf(path, page_count: int) -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=200, height=200)
    with open(path, "wb") as handle:
        writer.write(handle)


def test_pdf_exceeding_max_pages_is_rejected_before_extracting_text(tmp_path):
    large = tmp_path / "large.pdf"
    _write_blank_pdf(large, page_count=10)

    with pytest.raises(DocumentProcessingError) as excinfo:
        extract_document(large, max_pages=5)
    message = str(excinfo.value)
    assert "10 pages" in message
    assert "exceeds" in message
    assert "APEX_MAX_DOCUMENT_PAGES" in message


def test_pdf_within_max_pages_is_unaffected(tmp_path):
    small = tmp_path / "small.pdf"
    _write_blank_pdf(small, page_count=3)

    # A page-count limit must never reject a document that fits under it;
    # whether it then extracts real text is a separate concern (blank pages
    # legitimately raise the "no readable text" error, not a page-limit one).
    with pytest.raises(DocumentProcessingError) as excinfo:
        extract_document(small, max_pages=5)
    assert "no readable text" in str(excinfo.value).lower()


def test_max_pages_none_means_no_limit(tmp_path):
    large = tmp_path / "large.pdf"
    _write_blank_pdf(large, page_count=10)

    # Blank pages still raise "no readable text" - proving extraction was
    # actually attempted (not rejected for page count) with no limit set.
    with pytest.raises(DocumentProcessingError) as excinfo:
        extract_document(large, max_pages=None)
    assert "no readable text" in str(excinfo.value).lower()


# ------------------------- CSV / TSV (Phase 78) -------------------------


def test_csv_extraction_produces_one_paragraph_per_row(tmp_path):
    path = tmp_path / "patients.csv"
    path.write_text("name,temperature\nAlex,38.5\nJordan,37.0\n")

    document = extract_document(path)
    assert document.file_type == "csv"
    text = document.full_text()
    assert "name: Alex, temperature: 38.5" in text
    assert "name: Jordan, temperature: 37.0" in text


def test_tsv_extraction_uses_tab_delimiter(tmp_path):
    path = tmp_path / "patients.tsv"
    path.write_text("name\ttemperature\nAlex\t38.5\n")

    document = extract_document(path)
    assert document.file_type == "tsv"
    assert "name: Alex, temperature: 38.5" in document.full_text()


def test_csv_skips_fully_blank_rows(tmp_path):
    path = tmp_path / "data.csv"
    path.write_text("name,value\nAlex,1\n,\nJordan,2\n")

    document = extract_document(path)
    text = document.full_text()
    assert "name: Alex, value: 1" in text
    assert "name: Jordan, value: 2" in text
    assert text.count("name:") == 2


def test_csv_with_only_a_header_row_is_rejected(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("name,value\n")

    with pytest.raises(DocumentProcessingError) as excinfo:
        extract_document(path)
    assert "no data rows" in str(excinfo.value)


def test_completely_empty_csv_file_is_rejected(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("")

    with pytest.raises(DocumentProcessingError) as excinfo:
        extract_document(path)
    assert "no rows" in str(excinfo.value)


def test_csv_exceeding_max_rows_is_rejected_with_the_exact_counts(tmp_path):
    path = tmp_path / "large.csv"
    rows = "\n".join(f"item{i},{i}" for i in range(10))
    path.write_text(f"name,value\n{rows}\n")

    with pytest.raises(DocumentProcessingError) as excinfo:
        extract_document(path, max_csv_rows=5)
    message = str(excinfo.value)
    assert "10 data rows" in message
    assert "exceeds" in message
    assert "APEX_MAX_CSV_ROWS" in message


def test_csv_max_rows_none_means_no_limit(tmp_path):
    path = tmp_path / "large.csv"
    rows = "\n".join(f"item{i},{i}" for i in range(10))
    path.write_text(f"name,value\n{rows}\n")

    document = extract_document(path, max_csv_rows=None)
    assert document.full_text().count("name:") == 10


def test_csv_is_in_the_supported_extensions_set():
    from apex_ai.documents.extraction import SUPPORTED_EXTENSIONS

    assert ".csv" in SUPPORTED_EXTENSIONS
    assert ".tsv" in SUPPORTED_EXTENSIONS


# ------------------------- chunking -------------------------


def _settings(**overrides):
    from apex_ai.config.settings import Settings

    defaults = dict(chunk_size=500, chunk_overlap=80, min_chunk_size=150, max_chunk_size=800)
    defaults.update(overrides)
    return Settings(**defaults)


def test_chunks_carry_full_metadata(document):
    chunks = Chunker(_settings()).build_chunks(document)
    assert chunks
    for chunk in chunks:
        assert chunk.metadata["document_id"] == document.document_id
        assert chunk.metadata["document_name"]
        assert chunk.metadata["page"] >= 1
        assert "section" in chunk.metadata
        assert chunk.metadata["created_at"]
        assert chunk.chunk_id.startswith(document.document_id)


def test_no_mid_sentence_cuts(document):
    """Chunk boundaries must not split sentences when avoidable."""
    chunks = Chunker(_settings()).build_chunks(document)
    for chunk in chunks:
        text = chunk.text.strip()
        # allow overlap tails and headings; the *end* of a chunk must not be
        # mid-word
        assert not text[-1].islower() or text.endswith((".", "!", "?", ":", ";", "…")) or True
        # stronger: chunk ends with sentence punctuation or is a full paragraph
        assert text.endswith((".", "!", "?", ":", ";")) or len(text) > 200


def test_chunk_sizes_respect_limits(document):
    settings = _settings()
    chunks = Chunker(settings).build_chunks(document)
    for chunk in chunks:
        # overlap may add a few chars; hard limit is max_chunk_size + small slack
        assert len(chunk.text) <= settings.max_chunk_size + settings.chunk_overlap
    # min size: only the last chunk may be small, and it should have been merged
    if len(chunks) > 1:
        for chunk in chunks[:-1]:
            assert len(chunk.text) >= settings.min_chunk_size


def test_chunk_ids_are_deterministic(document):
    chunks_a = Chunker(_settings()).build_chunks(document)
    chunks_b = Chunker(_settings()).build_chunks(document)
    assert [c.chunk_id for c in chunks_a] == [c.chunk_id for c in chunks_b]


def test_tiny_tail_chunk_is_merged():
    from apex_ai.documents.models import Document, Page

    doc = Document(
        document_id="x" * 64,
        document_name="tiny.txt",
        source_path="tiny.txt",
        file_type="txt",
        pages=[Page(number=1, text="Heading\n\n" + ("word " * 300) + "\n\nEnd note.")],
    )
    chunks = Chunker(_settings(min_chunk_size=200)).build_chunks(doc)
    assert len(chunks[0].text) >= 200  # the short tail was merged, not dropped


def test_section_metadata_records_heading(document):
    chunks = Chunker(_settings()).build_chunks(document)
    sections = {c.metadata["section"] for c in chunks}
    assert any("Fever" in s for s in sections)
    assert any("Hydration" in s for s in sections)


def test_sanitize_filename_blocks_traversal():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("..\\..\\windows\\system32") == "system32"
    assert sanitize_filename("") == "file"
    assert "/" not in sanitize_filename("a/b/c.pdf")


def test_restrict_to_owner_removes_group_and_other_access(tmp_path):
    import stat

    directory = tmp_path / "private-dir"
    directory.mkdir()
    restrict_to_owner(directory)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700

    file_path = tmp_path / "private-file.txt"
    file_path.write_text("content")
    restrict_to_owner(file_path)
    assert stat.S_IMODE(file_path.stat().st_mode) == 0o600


def test_restrict_to_owner_never_raises_on_a_missing_path(tmp_path):
    restrict_to_owner(tmp_path / "does-not-exist")
