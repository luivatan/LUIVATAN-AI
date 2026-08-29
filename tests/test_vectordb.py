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
