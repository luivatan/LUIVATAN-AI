"""Vector store, duplicate detection, and embedding-compatibility tests."""

from __future__ import annotations

import pytest

from apex_ai.core.errors import DatabaseError, EmbeddingMismatchError
from apex_ai.documents.service import IngestionService
from apex_ai.vectordb import ChromaVectorStore
from tests.conftest import DATA_DIR


def test_ingest_and_search_roundtrip(settings, ingestion, store):
    result = ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf")
    assert result.status == "indexed"
    assert result.chunks > 0

    hits = store.search("fever in adults temperature", k=3)
    assert hits
    assert any("fever" in h.text.lower() for h in hits)
    assert all(h.metadata.get("page") for h in hits)


def test_duplicate_upload_is_skipped(ingestion):
    first = ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf")
    second = ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf")
    assert first.status == "indexed"
    assert second.status == "duplicate"


def test_reindex_replaces_chunks(ingestion, store):
    ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf")
    result = ingestion.reindex(
        next(d.document_id for d in ingestion.list_documents())
    )
    assert result.status == "indexed"
    assert store.count() == result.chunks


def test_delete_document_removes_all_chunks(ingestion, store):
    ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf")
    document_id = ingestion.list_documents()[0].document_id
    ingestion.remove(document_id)
    assert store.count() == 0
    assert ingestion.list_documents() == []


def test_list_documents_reports_pages_and_chunks(ingestion):
    ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf")
    docs = ingestion.list_documents()
    assert len(docs) == 1
    assert docs[0].pages == 2
    assert docs[0].chunks >= 2


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


def test_medical_heuristic_flags_non_medical_content(ingestion):
    from apex_ai.documents.service import is_likely_medical_document

    assert is_likely_medical_document("fever patient diagnosis treatment drug dose")
    assert not is_likely_medical_document("car engine spark plugs and tire pressure")

    result = ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf")
    assert result.status == "indexed"


def test_store_error_wraps_io_problems(settings, embeddings, monkeypatch, tmp_path):
    """Unusable database location -> DatabaseError, not a raw traceback."""
    import chromadb

    def broken_client(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(chromadb, "PersistentClient", broken_client)
    with pytest.raises(DatabaseError) as excinfo:
        ChromaVectorStore(settings, embeddings, collection_name="broken")
    assert "HOW TO FIX" in str(excinfo.value)
