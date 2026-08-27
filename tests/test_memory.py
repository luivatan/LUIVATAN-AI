"""Conversation memory: persistence, limits, corruption recovery."""

from __future__ import annotations

from apex_ai.memory.conversation import ConversationMemory


def test_roundtrip(tmp_path):
    memory = ConversationMemory(tmp_path / "m.json", limit=5)
    memory.add("hello", "hi there")
    reloaded = ConversationMemory(tmp_path / "m.json", limit=5)
    assert reloaded.turns[0]["user"] == "hello"


def test_limit_keeps_recent_turns(tmp_path):
    memory = ConversationMemory(tmp_path / "m.json", limit=3)
    for i in range(6):
        memory.add(f"q{i}", f"a{i}")
    assert [t["user"] for t in memory.turns] == ["q3", "q4", "q5"]


def test_as_messages_alternates_roles(tmp_path):
    memory = ConversationMemory(tmp_path / "m.json", limit=4)
    memory.add("q1", "a1")
    roles = [m["role"] for m in memory.as_messages()]
    assert roles == ["user", "assistant"]


def test_corrupted_file_is_backed_up_not_fatal(tmp_path):
    path = tmp_path / "m.json"
    path.write_text("{ not valid json !!!", encoding="utf-8")
    memory = ConversationMemory(path, limit=4)
    assert memory.turns == []
    assert path.with_suffix(".corrupt.bak").exists()
    memory.add("fresh", "start")  # and it keeps working
    assert ConversationMemory(path, limit=4).turns[-1]["user"] == "fresh"


def test_clear(tmp_path):
    memory = ConversationMemory(tmp_path / "m.json", limit=4)
    memory.add("q", "a")
    memory.clear()
    assert memory.turns == []
    assert ConversationMemory(tmp_path / "m.json", limit=4).turns == []


def test_display_formats_turns(tmp_path):
    memory = ConversationMemory(tmp_path / "m.json", limit=4)
    memory.add("What is fever?", "A raised body temperature.")
    text = memory.display()
    assert "User: What is fever?" in text
    assert "Assistant: A raised body temperature." in text
