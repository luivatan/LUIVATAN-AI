"""Phase 66/67: document collections and knowledge-base-scoped retrieval."""

from __future__ import annotations

import pytest

from apex_ai.documents.collections import CollectionStore
from tests.conftest import DATA_DIR, USER

OTHER_USER = "user-2"


# ---------------- CollectionStore ----------------


def test_collection_crud_and_persistence(tmp_path):
    path = tmp_path / "collections.db"
    store = CollectionStore(path)

    created = store.create(USER, "  Medical research  ")
    assert created.name == "Medical research"

    reopened = CollectionStore(path)
    assert reopened.get(USER, created.id) == created

    renamed = reopened.rename(USER, created.id, "Renamed collection")
    assert renamed.id == created.id
    assert renamed.name == "Renamed collection"

    assert [c.id for c in reopened.list(USER)] == [created.id]
    assert reopened.delete(USER, created.id)
    assert not reopened.delete(USER, created.id)
    assert reopened.get(USER, created.id) is None


def test_collection_name_is_validated(tmp_path):
    store = CollectionStore(tmp_path / "collections.db")
    with pytest.raises(ValueError, match="cannot be empty"):
        store.create(USER, "   ")
    created = store.create(USER, "Real name")
    with pytest.raises(ValueError, match="cannot be empty"):
        store.rename(USER, created.id, "")


def test_collections_are_isolated_between_accounts(tmp_path):
    store = CollectionStore(tmp_path / "collections.db")
    mine = store.create(USER, "Mine")
    store.create(OTHER_USER, "Theirs")

    assert [c.id for c in store.list(USER)] == [mine.id]
    assert store.get(OTHER_USER, mine.id) is None
    with pytest.raises(KeyError):
        store.rename(OTHER_USER, mine.id, "Hijacked")
    assert store.delete(OTHER_USER, mine.id) is False
    assert store.get(USER, mine.id) == mine  # untouched by the failed cross-account ops


def test_renaming_a_missing_collection_raises_key_error(tmp_path):
    store = CollectionStore(tmp_path / "collections.db")
    with pytest.raises(KeyError):
        store.rename(USER, "does-not-exist", "New name")


# ---------------- IngestionService collection assignment ----------------


def test_ingest_assigns_a_collection_and_reindex_preserves_it(ingestion, store):
    result = ingestion.ingest_path(
        DATA_DIR / "sample_first_aid.pdf", USER, collection_id="collection-1"
    )
    assert result.status == "indexed"

    docs = ingestion.list_documents(USER, "collection-1")
    assert len(docs) == 1
    assert docs[0].document_id == result.document_id

    reindexed = ingestion.reindex(result.document_id, USER)
    assert reindexed.status == "indexed"
    assert ingestion.list_documents(USER, "collection-1")[0].document_id == result.document_id
    assert ingestion.list_documents(USER, "")  == []  # not left uncategorized


def test_list_documents_filters_by_collection_including_uncategorized(ingestion):
    ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf", USER, collection_id="work")
    ingestion.ingest_path(DATA_DIR / "burn_care.md", USER, collection_id="")

    assert len(ingestion.list_documents(USER)) == 2  # None = no filter
    assert len(ingestion.list_documents(USER, "work")) == 1
    assert len(ingestion.list_documents(USER, "")) == 1
    assert len(ingestion.list_documents(USER, "nonexistent")) == 0


def test_move_to_collection_is_a_pure_registry_update(ingestion, store):
    result = ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf", USER)
    before_chunks = store.count(USER)

    moved = ingestion.move_to_collection(result.document_id, USER, "new-collection")
    assert moved.collection_id == "new-collection"
    assert ingestion.list_documents(USER, "new-collection")[0].document_id == result.document_id
    assert store.count(USER) == before_chunks  # no re-embedding happened

    unassigned = ingestion.move_to_collection(result.document_id, USER, "")
    assert unassigned.collection_id == ""


def test_move_to_collection_on_a_missing_document_raises_key_error(ingestion):
    with pytest.raises(KeyError):
        ingestion.move_to_collection("does-not-exist", USER, "some-collection")


def test_unassign_collection_clears_every_reference_but_keeps_documents(ingestion):
    a = ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf", USER, collection_id="deleted-me")
    b = ingestion.ingest_path(DATA_DIR / "burn_care.md", USER, collection_id="deleted-me")

    changed = ingestion.unassign_collection(USER, "deleted-me")

    assert changed == 2
    assert ingestion.list_documents(USER, "deleted-me") == []
    remaining_ids = {d.document_id for d in ingestion.list_documents(USER, "")}
    assert remaining_ids == {a.document_id, b.document_id}


def test_document_ids_for_collection_resolves_membership(ingestion):
    result = ingestion.ingest_path(
        DATA_DIR / "sample_first_aid.pdf", USER, collection_id="work"
    )
    assert ingestion.document_ids_for_collection(USER, "work") == [result.document_id]
    assert ingestion.document_ids_for_collection(USER, "empty-collection") == []


# ---------------- retrieval scoped by document_ids ----------------


def test_search_scoped_to_document_ids_excludes_other_documents(ingestion, store):
    first = ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf", USER)
    ingestion.ingest_path(DATA_DIR / "burn_care.md", USER)

    unscoped = store.search("fever burns treatment", USER, k=10)
    scoped = store.search(
        "fever burns treatment", USER, k=10, document_ids=[first.document_id]
    )

    assert scoped  # the scoped document itself is still found
    assert all(chunk.metadata["document_id"] == first.document_id for chunk in scoped)
    assert len(unscoped) >= len(scoped)


def test_search_scoped_to_an_empty_collection_returns_nothing(ingestion, store):
    ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf", USER)

    assert store.search("fever", USER, k=10, document_ids=[]) == []


def test_bm25_search_scoped_to_document_ids_excludes_other_documents(ingestion, store):
    from apex_ai.retrieval.keyword import BM25Index

    first = ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf", USER)
    ingestion.ingest_path(DATA_DIR / "burn_care.md", USER)
    index = BM25Index(store)

    scoped = index.search("fever", USER, k=10, document_ids=[first.document_id])
    assert all(chunk.metadata["document_id"] == first.document_id for chunk in scoped)


def test_engine_ask_respects_document_ids_scoping(engine, ingestion):
    """End-to-end: a question that a document could otherwise answer must
    come back unsupported when the engine is asked with document_ids
    restricted to exclude every document (an empty scoped collection), and
    must still succeed when scoped to the document that actually answers it."""
    burn_care = ingestion.ingest_path(DATA_DIR / "burn_care.md", USER)

    unsupported = engine.ask("How should burns be cooled?", document_ids=[])
    assert unsupported.insufficient_evidence

    supported = engine.ask("How should burns be cooled?", document_ids=[burn_care.document_id])
    assert not supported.insufficient_evidence
    assert supported.citations
