import hashlib

import pytest

from rag_utils import (
    chunk_text,
    citation_label,
    file_sha256,
    is_likely_medical_document,
    safe_filename,
)


def test_safe_filename_strips_directory_components():
    assert safe_filename("report.pdf") == "report.pdf"
    assert safe_filename("some/dir/report.pdf") == "report.pdf"


def test_safe_filename_replaces_backslashes():
    # On POSIX, a backslash is a legal filename character (not a path
    # separator), so Path(...).name keeps it; it is then swapped for "_".
    assert safe_filename("some\\dir\\report.pdf") == "some_dir_report.pdf"


def test_safe_filename_blocks_path_traversal():
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("../../../report.pdf") == "report.pdf"


def test_file_sha256_matches_hashlib(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_bytes(b"hello world" * 1000)

    expected = hashlib.sha256(file_path.read_bytes()).hexdigest()

    assert file_sha256(file_path) == expected


def test_chunk_text_empty_returns_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n\t  ") == []


def test_chunk_text_short_text_is_single_chunk():
    text = "This is a short sentence."
    chunks = chunk_text(text, chunk_size=1000, overlap=150)

    assert chunks == [text]


def test_chunk_text_splits_long_text_with_overlap():
    text = "a" * 2500
    chunks = chunk_text(text, chunk_size=1000, overlap=150)

    assert len(chunks) == 3
    assert all(len(chunk) <= 1000 for chunk in chunks)
    # Overlap means the tail of one chunk reappears at the head of the next.
    assert chunks[0][-150:] == chunks[1][:150]


def test_chunk_text_collapses_whitespace():
    text = "line one\n\n  line   two\t\tline three"
    chunks = chunk_text(text, chunk_size=1000, overlap=150)

    assert chunks == ["line one line two line three"]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("The patient was given a dose of antibiotic for the infection.", True),
        ("The quarterly sales report exceeded expectations this year.", False),
    ],
)
def test_is_likely_medical_document(text, expected):
    pages = [(1, text)]

    assert is_likely_medical_document(pages) == expected


def test_citation_label_with_page_and_chunk():
    metadata = {"source": "guide.pdf", "page": 3, "chunk": 2}

    assert citation_label(metadata) == "guide.pdf Page 3 Chunk 2"


def test_citation_label_with_page_only():
    metadata = {"source": "guide.pdf", "page": 3}

    assert citation_label(metadata) == "guide.pdf Page 3"


def test_citation_label_with_no_page():
    metadata = {"source": "guide.pdf"}

    assert citation_label(metadata) == "guide.pdf"


def test_citation_label_missing_source_defaults():
    assert citation_label({}) == "unknown source"
