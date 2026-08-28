"""Phase 42 long-term-memory storage and isolation tests."""

from __future__ import annotations

import sqlite3

import pytest

from apex_ai.core.errors import DatabaseError
from apex_ai.memory.long_term import LongTermMemoryStore


def _table_names(path) -> set[str]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    return {str(row[0]) for row in rows}


def test_long_term_memory_crud_persists_across_store_instances(tmp_path):
    path = tmp_path / "long-term.db"
    store = LongTermMemoryStore(path)

    created = store.create("Prefer concise answers.", kind="preference")
    assert created.content == "Prefer concise answers."
    assert created.kind == "preference"
    assert created.created_at == created.updated_at
    assert store.count() == 1

    reopened = LongTermMemoryStore(path)
    assert reopened.get(created.id) == created

    updated = reopened.update(
        created.id,
        content="Prefer concise answers with examples.",
        kind="ongoing_context",
    )
    assert updated.id == created.id
    assert updated.created_at == created.created_at
    assert updated.updated_at >= created.updated_at
    assert reopened.count(kind="preference") == 0
    assert reopened.count(kind="ongoing_context") == 1

    assert reopened.delete(created.id)
    assert not reopened.delete(created.id)
    assert reopened.get(created.id) is None


def test_memory_store_validates_explicit_categories_and_content(tmp_path):
    store = LongTermMemoryStore(tmp_path / "memory.db")

    with pytest.raises(ValueError, match="Memory kind"):
        store.create("Do not accept an undeclared category.", kind="other")
    with pytest.raises(ValueError, match="cannot be empty"):
        store.create("   ", kind="preference")

    created = store.create("  Keep exact identifiers.  ", kind=" PREFERENCE ")
    assert created.kind == "preference"
    assert created.content == "Keep exact identifiers."


def test_memory_listing_filter_limit_and_clear_are_deterministic(tmp_path):
    store = LongTermMemoryStore(tmp_path / "memory.db")
    preference = store.create("Prefer tables.", kind="preference")
    context = store.create("The migration is ongoing.", kind="ongoing_context")

    assert store.list(kind="preference") == [preference]
    assert store.list(kind="ongoing_context") == [context]
    assert len(store.list(limit=1)) == 1
    assert store.clear(kind="preference") == 1
    assert store.list() == [context]
    assert store.clear() == 1
    assert store.count() == 0


def test_long_term_memory_uses_a_database_separate_from_conversations(tmp_path):
    from apex_ai.memory.conversations import ConversationStore

    conversation_path = tmp_path / "conversations.db"
    memory_path = tmp_path / "long-term.db"
    ConversationStore(conversation_path).create("Existing conversation")
    LongTermMemoryStore(memory_path).create(
        "Prefer concise answers.", kind="preference"
    )

    assert conversation_path != memory_path
    assert "long_term_memories" not in _table_names(conversation_path)
    assert "long_term_memories" in _table_names(memory_path)
    assert "conversations" not in _table_names(memory_path)


def test_corrupt_memory_database_raises_explainable_database_error(tmp_path):
    path = tmp_path / "memory.db"
    path.write_bytes(b"this is not sqlite")

    with pytest.raises(DatabaseError) as excinfo:
        LongTermMemoryStore(path)

    message = excinfo.value.user_message()
    assert "WHAT HAPPENED" in message
    assert "HOW TO FIX" in message
    assert "separate stores" in message


def test_optional_memory_failure_does_not_break_core_runtime(settings, monkeypatch):
    from apex_ai import runtime
    from apex_ai.embeddings.hashing import HashingEmbeddingProvider

    class BrokenMemoryStore:
        def __init__(self, path):
            raise OSError("simulated optional-store failure")

    monkeypatch.setattr(runtime, "LongTermMemoryStore", BrokenMemoryStore)
    services = runtime.build_services(
        settings,
        embedding_factory=lambda unused_settings: HashingEmbeddingProvider(),
    )

    assert services.ready
    assert services.engine is not None
    assert services.ingestion is not None
    assert services.long_term_memory is None
    assert "long_term_memory_error" in services._extras
