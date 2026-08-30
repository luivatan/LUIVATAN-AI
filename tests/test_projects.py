"""Phase 71/72: project workspaces and project-instructions prompt threading."""

from __future__ import annotations

import pytest

from apex_ai.memory.conversations import ConversationStore
from apex_ai.projects.store import ProjectStore
from tests.conftest import USER, FakeLLM

OTHER_USER = "user-2"


# ---------------- ProjectStore ----------------


def test_project_crud_and_persistence(tmp_path):
    path = tmp_path / "projects.db"
    store = ProjectStore(path)

    created = store.create(USER, "  Research  ", "Be concise.", "collection-1")
    assert created.name == "Research"
    assert created.instructions == "Be concise."
    assert created.collection_id == "collection-1"

    reopened = ProjectStore(path)
    assert reopened.get(USER, created.id) == created

    updated = reopened.update(USER, created.id, name="Renamed", instructions="Be terse.")
    assert updated.id == created.id
    assert updated.name == "Renamed"
    assert updated.instructions == "Be terse."
    assert updated.collection_id == "collection-1"  # untouched field stays as-is

    assert [p.id for p in reopened.list(USER)] == [created.id]
    assert reopened.delete(USER, created.id)
    assert not reopened.delete(USER, created.id)
    assert reopened.get(USER, created.id) is None


def test_project_name_is_validated(tmp_path):
    store = ProjectStore(tmp_path / "projects.db")
    with pytest.raises(ValueError, match="cannot be empty"):
        store.create(USER, "   ")
    created = store.create(USER, "Real name")
    with pytest.raises(ValueError, match="cannot be empty"):
        store.update(USER, created.id, name="")


def test_project_update_field_semantics_none_means_unchanged(tmp_path):
    store = ProjectStore(tmp_path / "projects.db")
    created = store.create(USER, "Scoped", collection_id="work")

    # Updating only the name must leave instructions/collection_id untouched.
    renamed = store.update(USER, created.id, name="Renamed")
    assert renamed.collection_id == "work"
    assert renamed.instructions == ""

    # An explicit empty string clears the collection - distinct from
    # omitting the field entirely (which leaves it unchanged, above).
    cleared = store.update(USER, created.id, collection_id="")
    assert cleared.collection_id == ""
    assert cleared.name == "Renamed"  # untouched by this update


def test_projects_are_isolated_between_accounts(tmp_path):
    store = ProjectStore(tmp_path / "projects.db")
    mine = store.create(USER, "Mine")
    store.create(OTHER_USER, "Theirs")

    assert [p.id for p in store.list(USER)] == [mine.id]
    assert store.get(OTHER_USER, mine.id) is None
    with pytest.raises(KeyError):
        store.update(OTHER_USER, mine.id, name="Hijacked")
    assert store.delete(OTHER_USER, mine.id) is False
    assert store.get(USER, mine.id) == mine  # untouched by the failed cross-account ops


def test_updating_a_missing_project_raises_key_error(tmp_path):
    store = ProjectStore(tmp_path / "projects.db")
    with pytest.raises(KeyError):
        store.update(USER, "does-not-exist", name="New name")


# ---------------- Conversation <-> project ----------------


def test_conversation_project_id_round_trips(tmp_path):
    store = ConversationStore(tmp_path / "conversations.db")
    conversation = store.create(USER, project_id="project-1")
    assert conversation.project_id == "project-1"
    assert store.get(USER, conversation.id).project_id == "project-1"


def test_set_project_moves_a_conversation_in_and_out(tmp_path):
    store = ConversationStore(tmp_path / "conversations.db")
    conversation = store.create(USER)
    assert conversation.project_id == ""

    moved = store.set_project(USER, conversation.id, "project-1")
    assert moved.project_id == "project-1"

    moved_out = store.set_project(USER, conversation.id, "")
    assert moved_out.project_id == ""


def test_set_project_on_a_missing_conversation_raises_key_error(tmp_path):
    store = ConversationStore(tmp_path / "conversations.db")
    with pytest.raises(KeyError):
        store.set_project(USER, "does-not-exist", "project-1")


def test_unassign_project_clears_every_reference_but_keeps_conversations(tmp_path):
    store = ConversationStore(tmp_path / "conversations.db")
    a = store.create(USER, project_id="deleted-me")
    b = store.create(USER, project_id="deleted-me")
    c = store.create(USER, project_id="keep-me")

    changed = store.unassign_project(USER, "deleted-me")

    assert changed == 2
    assert store.get(USER, a.id).project_id == ""
    assert store.get(USER, b.id).project_id == ""
    assert store.get(USER, c.id).project_id == "keep-me"


def test_list_conversations_filters_by_project_including_unassigned(tmp_path):
    store = ConversationStore(tmp_path / "conversations.db")
    store.create(USER, title="in project", project_id="work")
    store.create(USER, title="unassigned", project_id="")

    assert len(store.list(USER)) == 2  # None (default) = no filter
    assert len(store.list(USER, project_id="work")) == 1
    assert len(store.list(USER, project_id="")) == 1
    assert len(store.list(USER, project_id="nonexistent")) == 0


# ---------------- Phase 72: project instructions reach generation ----------------


def test_engine_ask_threads_project_instructions_into_the_prompt(engine):
    engine.ask(
        "What temperature counts as a fever in adults?",
        project_instructions="Always answer in ALL CAPS.",
    )
    user_message = next(
        message["content"] for message in FakeLLM.last_messages if message["role"] == "user"
    )
    assert "Always answer in ALL CAPS." in user_message


def test_engine_ask_stream_threads_project_instructions_into_the_prompt(engine):
    list(
        engine.ask_stream(
            "What temperature counts as a fever in adults?",
            project_instructions="Reply only in French.",
        )
    )
    user_message = next(
        message["content"] for message in FakeLLM.last_messages if message["role"] == "user"
    )
    assert "Reply only in French." in user_message


def test_engine_ask_without_project_instructions_omits_the_block(engine):
    engine.ask("What temperature counts as a fever in adults?")
    user_message = next(
        message["content"] for message in FakeLLM.last_messages if message["role"] == "user"
    )
    assert "Project instructions" not in user_message
