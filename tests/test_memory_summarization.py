"""Phase 50: unit coverage for summarization decision logic in isolation
(no engine, no store, no LLM — end-to-end wiring is covered separately in
test_conversations_web.py)."""

from __future__ import annotations

from apex_ai.memory.conversations import Message
from apex_ai.memory.summarization import build_summary_messages, turns_needing_summary


def _message(role: str, content: str, when: str) -> Message:
    return Message(
        id=content[:8], conversation_id="c1", role=role, content=content,
        citations=(), status="complete", created_at=when,
    )


def _conversation(turn_count: int) -> list[Message]:
    messages = []
    for i in range(turn_count):
        messages.append(_message("user", f"Question {i}", f"2026-01-01T00:00:{i:02d}Z"))
        messages.append(_message("assistant", f"Answer {i}", f"2026-01-01T00:00:{i:02d}Z"))
    return messages


def test_nothing_needs_summarizing_while_everything_fits_in_the_live_window():
    messages = _conversation(3)  # 6 messages
    assert turns_needing_summary(messages, already_summarized_count=0, keep_live_messages=8) is None


def test_older_messages_beyond_the_live_window_need_summarizing():
    messages = _conversation(6)  # 12 messages
    pending = turns_needing_summary(messages, already_summarized_count=0, keep_live_messages=8)
    assert pending is not None
    assert pending.through_message_count == 4  # 12 - 8 kept live = 4 fell out
    assert "Question 0" in pending.turns_text
    assert "Answer 1" in pending.turns_text
    assert "Question 2" not in pending.turns_text  # still inside the live window


def test_already_summarized_messages_are_not_resummarized():
    messages = _conversation(6)  # 12 messages, boundary at 4
    pending = turns_needing_summary(messages, already_summarized_count=4, keep_live_messages=8)
    assert pending is None  # nothing NEW has fallen out since the last summary


def test_summarization_advances_incrementally_as_the_conversation_grows():
    messages = _conversation(8)  # 16 messages, boundary at 8
    pending = turns_needing_summary(messages, already_summarized_count=4, keep_live_messages=8)
    assert pending is not None
    assert pending.through_message_count == 8
    assert "Question 0" not in pending.turns_text  # already covered by the prior summary
    assert "Question 2" in pending.turns_text  # newly fallen out this time


def test_build_summary_messages_includes_previous_summary_when_present():
    messages = build_summary_messages("Earlier: discussed pricing.", "User: what about support?\nAssistant: yes, 24/7.")
    assert messages[0]["role"] == "system"
    assert "Earlier: discussed pricing." in messages[1]["content"]
    assert "what about support?" in messages[1]["content"]


def test_build_summary_messages_omits_empty_previous_summary():
    messages = build_summary_messages("", "User: hello\nAssistant: hi")
    assert "Existing summary" not in messages[1]["content"]
