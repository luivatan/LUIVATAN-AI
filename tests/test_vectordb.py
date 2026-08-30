"""Vector store, duplicate detection, and embedding-compatibility tests."""

from __future__ import annotations

import pytest

from apex_ai.core.errors import DatabaseError, EmbeddingMismatchError
from apex_ai.documents.service import IngestionService
from apex_ai.vectordb import ChromaVectorStore
from tests.conftest import DATA_DIR, USER

OTHER_USER = "user-2"


def test_ingest_and_search_roundtrip(settings, ingestion, store):
    result = ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf", USER)
    assert result.status == "indexed"
    assert result.chunks > 0

    hits = store.search("fever in adults temperature", USER, k=3)
    assert hits
    assert any("fever" in h.text.lower() for h in hits)
    assert all(h.metadata.get("page") for h in hits)


def test_duplicate_upload_is_skipped(ingestion):
    first = ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf", USER)
    second = ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf", USER)
    assert first.status == "indexed"
    assert second.status == "duplicate"


def test_reindex_replaces_chunks(ingestion, store):
    ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf", USER)
    result = ingestion.reindex(
        next(d.document_id for d in ingestion.list_documents(USER)), USER
    )
    assert result.status == "indexed"
    assert store.count(USER) == result.chunks


def test_delete_document_removes_all_chunks(ingestion, store):
    ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf", USER)
    document_id = ingestion.list_documents(USER)[0].document_id
    ingestion.remove(document_id, USER)
    assert store.count(USER) == 0
    assert ingestion.list_documents(USER) == []


def test_list_documents_reports_pages_and_chunks(ingestion):
    ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf", USER)
    docs = ingestion.list_documents(USER)
    assert len(docs) == 1
    assert docs[0].pages == 2
    assert docs[0].chunks >= 2


def test_documents_are_isolated_between_accounts(ingestion, store):
    """Phase 55: two accounts uploading identical bytes each get their own
    indexed copy - a global content-hash dedup would mean the second
    account's upload silently attaches to the first account's document,
    which is exactly the cross-account leak this phase closes."""
    mine = ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf", USER)
    theirs = ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf", OTHER_USER)

    assert mine.status == "indexed"
    assert theirs.status == "indexed"  # not "duplicate": dedup is per-account
    assert mine.document_id == theirs.document_id  # same bytes, same content hash

    assert len(ingestion.list_documents(USER)) == 1
    assert len(ingestion.list_documents(OTHER_USER)) == 1
    assert store.count(USER) == mine.chunks
    assert store.count(OTHER_USER) == theirs.chunks

    hits = store.search("fever in adults temperature", USER, k=5)
    assert all(h.metadata.get("user_id") == USER for h in hits)
    assert store.has_document(mine.document_id, USER)
    assert store.has_document(theirs.document_id, OTHER_USER)

    # One account's delete never touches the other's copy.
    ingestion.remove(mine.document_id, USER)
    assert ingestion.list_documents(USER) == []
    assert len(ingestion.list_documents(OTHER_USER)) == 1
    assert store.count(OTHER_USER) == theirs.chunks


def test_embedding_model_mismatch_is_detected(settings, embeddings, store):
    from apex_ai.documents.models import Chunk

    store.upsert_chunks([
        Chunk(chunk_id="a:0001", text="some text", document_id="a",
              metadata={"document_id": "a", "document_name": "a.txt"})
    ])

    class DifferentEmbeddings:
        name = "different-model-v9"

        def embed_documents(self, texts):
            return [[0.1] * 256 for _ in texts]

        def embed_query(self, text):
            return [0.1] * 256

        @property
        def dimension(self):
            return 256

    with pytest.raises(EmbeddingMismatchError):
        ChromaVectorStore(settings, DifferentEmbeddings(), collection_name="test_docs")


def test_embedding_dimension_mismatch_is_detected(settings, embeddings, store):
    from apex_ai.documents.models import Chunk

    store.upsert_chunks(
        [
            Chunk(
                chunk_id="same:0001",
                text="dimension check",
                document_id="same",
                metadata={"document_id": "same", "document_name": "same.txt"},
            )
        ]
    )

    class WrongDimension:
        name = embeddings.name
        dimension = 128

        @staticmethod
        def embed_documents(texts):
            return [[0.1] * 128 for _ in texts]

        @staticmethod
        def embed_query(text):
            return [0.1] * 128

    with pytest.raises(EmbeddingMismatchError):
        ChromaVectorStore(settings, WrongDimension(), collection_name="test_docs")


def test_medical_heuristic_flags_non_medical_content(ingestion):
    from apex_ai.documents.service import is_likely_medical_document

    assert is_likely_medical_document("fever patient diagnosis treatment drug dose")
    assert not is_likely_medical_document("car engine spark plugs and tire pressure")

    result = ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf", USER)
    assert result.status == "indexed"


def test_ingestion_enforces_the_configured_max_document_pages(settings, store, tmp_path):
    """Phase 70: IngestionService actually reads settings.max_document_pages
    (not just extract_document's own default) when ingesting a real upload."""
    from pypdf import PdfWriter

    from apex_ai.config.settings import with_overrides

    large = tmp_path / "large.pdf"
    writer = PdfWriter()
    for _ in range(10):
        writer.add_blank_page(width=200, height=200)
    with open(large, "wb") as handle:
        writer.write(handle)

    strict = with_overrides(settings, max_document_pages=5)
    ingestion = IngestionService(strict, store)

    from apex_ai.core.errors import DocumentProcessingError

    with pytest.raises(DocumentProcessingError) as excinfo:
        ingestion.ingest_path(large, USER)
    assert "exceeds the 5-page limit" in str(excinfo.value)


def test_ingestion_enforces_the_configured_max_csv_rows(settings, store, tmp_path):
    """Phase 78: IngestionService actually reads settings.max_csv_rows (not
    just extract_document's own default) when ingesting a real upload."""
    from apex_ai.config.settings import with_overrides

    large = tmp_path / "large.csv"
    rows = "\n".join(f"item{i},{i}" for i in range(10))
    large.write_text(f"name,value\n{rows}\n")

    strict = with_overrides(settings, max_csv_rows=5)
    ingestion = IngestionService(strict, store)

    from apex_ai.core.errors import DocumentProcessingError

    with pytest.raises(DocumentProcessingError) as excinfo:
        ingestion.ingest_path(large, USER)
    assert "exceeds the 5-row limit" in str(excinfo.value)


def test_csv_uploads_are_indexed_and_searchable(ingestion, store, tmp_path):
    path = tmp_path / "patients.csv"
    path.write_text("name,temperature\nAlex,38.5\nJordan,37.0\n")

    result = ingestion.ingest_path(path, USER)
    assert result.status == "indexed"
    assert result.chunks > 0

    hits = store.search("Alex temperature", USER, k=3)
    assert hits


def test_search_does_not_pre_count_the_collection(ingestion, store):
    """Phase 95: search() used to call count() (a full get() of every one of
    the account's chunk IDs) before every query just to clamp k - a real,
    measured latency cost with no correctness benefit, since Chroma's own
    query() already returns fewer than n_results when fewer rows match.
    Guard against that round-trip coming back."""
    ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf", USER)

    original_get = store.collection.get

    def _tracking_get(*args, **kwargs):
        raise AssertionError(
            "search() should not call collection.get() - it no longer needs "
            "a pre-count to clamp k"
        )

    store.collection.get = _tracking_get
    try:
        hits = store.search("fever in adults temperature", USER, k=3)
    finally:
        store.collection.get = original_get
    assert hits


def test_search_on_an_empty_collection_returns_no_results(store):
    assert store.search("anything", USER, k=5) == []


def test_search_for_an_unknown_user_returns_no_results(ingestion, store):
    ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf", USER)
    assert store.search("fever", OTHER_USER, k=5) == []


@pytest.mark.parametrize("k", [0, -1])
def test_search_with_non_positive_k_returns_no_results(ingestion, store, k):
    ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf", USER)
    assert store.search("fever", USER, k=k) == []


def test_count_failure_is_wrapped_as_actionable_database_error():
    class BrokenCollection:
        @staticmethod
        def count():
            raise RuntimeError("database unavailable")

    store = object.__new__(ChromaVectorStore)
    store.collection = BrokenCollection()
    with pytest.raises(DatabaseError) as excinfo:
        store.count()
    assert "count chunks" in excinfo.value.what
    assert "rebuild" in excinfo.value.fix


def test_store_error_wraps_io_problems(settings, embeddings, monkeypatch, tmp_path):
    """Unusable database location -> DatabaseError, not a raw traceback."""
    import chromadb

    def broken_client(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(chromadb, "PersistentClient", broken_client)
    with pytest.raises(DatabaseError) as excinfo:
        ChromaVectorStore(settings, embeddings, collection_name="broken")
    assert "HOW TO FIX" in str(excinfo.value)
