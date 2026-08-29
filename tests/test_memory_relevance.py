"""Phase 47: unit coverage for the relevance-selection helper in isolation
(no engine, no store, no LLM — the engine/prompt integration is covered
separately in test_api_ui.py and test_conversations_web.py)."""

from __future__ import annotations

from apex_ai.memory.long_term import LongTermMemory
from apex_ai.memory.relevance import (
    find_similar_memory,
    format_memory_text,
    select_relevant_memories,
)


def _memory(content: str, kind: str, when: str) -> LongTermMemory:
    return LongTermMemory(id=content[:8], kind=kind, content=content, created_at=when, updated_at=when)


def test_preferences_are_always_included_regardless_of_question_topic():
    memories = [_memory("Prefers concise answers.", "preference", "2026-01-01T00:00:00Z")]
    selected = select_relevant_memories("What is the capital of France?", memories)
    assert selected == memories


def test_preferences_are_bounded_by_max_preferences_newest_first():
    memories = [
        _memory(f"Preference {i}", "preference", f"2026-01-0{i}T00:00:00Z") for i in range(1, 6)
    ]
    selected = select_relevant_memories("anything", memories, max_preferences=2)
    assert len(selected) == 2
    assert selected == memories[:2]  # caller passes newest-first; truncation keeps that order


def test_ongoing_context_requires_keyword_overlap_with_the_question():
    relevant = _memory("Working on a Q3 budget review.", "ongoing_context", "2026-01-01T00:00:00Z")
    unrelated = _memory("Training for a marathon in October.", "ongoing_context", "2026-01-01T00:00:00Z")
    selected = select_relevant_memories("What should I include in the budget review?", [relevant, unrelated])
    assert selected == [relevant]


def test_ongoing_context_is_dropped_when_nothing_overlaps():
    memories = [_memory("Training for a marathon in October.", "ongoing_context", "2026-01-01T00:00:00Z")]
    assert select_relevant_memories("What temperature is a fever in adults?", memories) == []


def test_no_memories_returns_empty_list():
    assert select_relevant_memories("anything", []) == []


def test_format_memory_text_is_empty_for_no_memories():
    assert format_memory_text([]) == ""


def test_format_memory_text_renders_a_bullet_per_memory():
    memories = [
        _memory("Prefers concise answers.", "preference", "2026-01-01T00:00:00Z"),
        _memory("Working on a Q3 budget review.", "ongoing_context", "2026-01-01T00:00:00Z"),
    ]
    text = format_memory_text(memories)
    assert text == "- Prefers concise answers.\n- Working on a Q3 budget review."


def test_find_similar_memory_flags_a_likely_conflict():
    existing = _memory("Prefers detailed answers.", "preference", "2026-01-01T00:00:00Z")
    match = find_similar_memory("Prefers concise answers.", [existing])
    assert match is existing


def test_find_similar_memory_ignores_unrelated_content():
    existing = _memory("Training for a marathon in October.", "ongoing_context", "2026-01-01T00:00:00Z")
    assert find_similar_memory("Prefers concise answers.", [existing]) is None


def test_find_similar_memory_returns_none_for_no_existing_memories():
    assert find_similar_memory("Prefers concise answers.", []) is None


def test_find_similar_memory_picks_the_closer_of_two_matches():
    weak = _memory("Prefers answers with citations.", "preference", "2026-01-01T00:00:00Z")
    strong = _memory("Prefers detailed thorough answers.", "preference", "2026-01-01T00:00:00Z")
    match = find_similar_memory("Prefers detailed answers.", [weak, strong])
    assert match is strong
