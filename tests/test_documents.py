from apex_documents import DocumentQueue, DocumentError, detect_heading, smart_chunks, validate_file
import pytest


def test_validation(tmp_path):
    txt = tmp_path / "note.txt"; txt.write_text("x")
    with pytest.raises(DocumentError): validate_file(txt)
    pdf = tmp_path / "empty.pdf"; pdf.write_bytes(b"")
    with pytest.raises(DocumentError): validate_file(pdf)


def test_heading_detection_and_metadata():
    assert detect_heading("1. Safety precautions") == "1. Safety precautions"
    assert detect_heading("a normal sentence") is None
    chunks = smart_chunks("INTRODUCTION\n\nThis is useful content.", "abc", "x.pdf", 4, size=20, overlap=5)
    assert chunks[0].document_id == "abc"
    assert chunks[0].page == 4
    assert chunks[0].heading == "INTRODUCTION"


def test_duplicate_detection(tmp_path):
    # Queue validates before hashing; same content has one stable document id.
    first = tmp_path / "one.pdf"; first.write_bytes(b"same")
    second = tmp_path / "two.pdf"; second.write_bytes(b"same")
    # Both are intentionally invalid PDFs, but duplicate IDs are content based.
    from apex_documents import document_id
    assert document_id(first) == document_id(second)
