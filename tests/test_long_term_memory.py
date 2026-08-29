"""Phase 42 long-term-memory storage and isolation tests."""

from __future__ import annotations

import sqlite3

import pytest

from apex_ai.core.errors import DatabaseError
from apex_ai.memory.long_term import LongTermMemoryStore

USER = "user-1"
OTHER_USER = "user-2"


def _table_names(path) -> set[str]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    return {str(row[0]) for row in rows}


def test_long_term_memory_crud_persists_across_store_instances(tmp_path):
    path = tmp_path / "long-term.db"
    store = LongTermMemoryStore(path)

    created = store.create(USER, "Prefer concise answers.", kind="preference")
    assert created.content == "Prefer concise answers."
    assert created.kind == "preference"
    assert created.created_at == created.updated_at
    assert store.count(USER) == 1

    reopened = LongTermMemoryStore(path)
    assert reopened.get(USER, created.id) == created

    updated = reopened.update(
        USER,
        created.id,
        content="Prefer concise answers with examples.",
        kind="ongoing_context",
    )
    assert updated.id == created.id
    assert updated.created_at == created.created_at
    assert updated.updated_at >= created.updated_at
    assert reopened.count(USER, kind="preference") == 0
    assert reopened.count(USER, kind="ongoing_context") == 1

    assert reopened.delete(USER, created.id)
    assert not reopened.delete(USER, created.id)
    assert reopened.get(USER, created.id) is None


def test_memory_store_validates_explicit_categories_and_content(tmp_path):
    store = LongTermMemoryStore(tmp_path / "memory.db")

    with pytest.raises(ValueError, match="Memory kind"):
        store.create(USER, "Do not accept an undeclared category.", kind="other")
    with pytest.raises(ValueError, match="cannot be empty"):
        store.create(USER, "   ", kind="preference")

    created = store.create(USER, "  Keep exact identifiers.  ", kind=" PREFERENCE ")
    assert created.kind == "preference"
    assert created.content == "Keep exact identifiers."


def test_memory_listing_filter_limit_and_clear_are_deterministic(tmp_path):
    store = LongTermMemoryStore(tmp_path / "memory.db")
    preference = store.create(USER, "Prefer tables.", kind="preference")
    context = store.create(USER, "The migration is ongoing.", kind="ongoing_context")

    assert store.list(USER, kind="preference") == [preference]
    assert store.list(USER, kind="ongoing_context") == [context]
    assert len(store.list(USER, limit=1)) == 1
    assert store.clear(USER, kind="preference") == 1
    assert store.list(USER) == [context]
    assert store.clear(USER) == 1
    assert store.count(USER) == 0


def test_memories_are_isolated_between_accounts(tmp_path):
    store = LongTermMemoryStore(tmp_path / "memory.db")
    mine = store.create(USER, "Prefer concise answers.", kind="preference")
    store.create(OTHER_USER, "Prefer detailed answers.", kind="preference")

    assert [item.id for item in store.list(USER)] == [mine.id]
    assert store.count(USER) == 1
    assert store.count(OTHER_USER) == 1
    assert store.get(OTHER_USER, mine.id) is None  # can't fetch by ID across accounts
    assert store.delete(OTHER_USER, mine.id) is False  # can't delete another account's memory
    assert store.get(USER, mine.id) == mine  # untouched by the failed cross-account delete

    assert store.clear(USER) == 1
    assert store.count(OTHER_USER) == 1  # clearing one account never touches another's


def test_long_term_memory_uses_a_database_separate_from_conversations(tmp_path):
    from apex_ai.memory.conversations import ConversationStore

    conversation_path = tmp_path / "conversations.db"
    memory_path = tmp_path / "long-term.db"
    ConversationStore(conversation_path).create(USER, "Existing conversation")
    LongTermMemoryStore(memory_path).create(
        USER, "Prefer concise answers.", kind="preference"
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
        def __init__(self, path, **kwargs):
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
    assert services.memory_confirmation is None
    assert "long_term_memory_error" in services._extras
