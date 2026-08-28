"""Phase 41 short-term conversation-context boundaries and integration."""

from __future__ import annotations

from dataclasses import replace

from apex_ai.memory.context import build_conversation_context
from apex_ai.rag.prompts import build_messages, format_history
from apex_ai.rag.query_processing import QueryProcessor
from tests.conftest import FakeLLM


def test_context_keeps_a_contiguous_window_of_newest_turns():
    history = [
        {"user": f"question-{index}", "assistant": f"answer-{index}"}
        for index in range(6)
    ]
    context = build_conversation_context(
        history,
        max_turns=3,
        char_limit=1000,
        message_char_limit=100,
    )
    assert [turn["user"] for turn in context.turns] == [
        "question-3",
        "question-4",
        "question-5",
    ]
    assert context.dropped_turn_count == 3
    assert context.character_count <= context.char_limit
    assert context.text.index("question-3") < context.text.index("question-5")


def test_oversized_newest_turn_keeps_both_message_ends_under_hard_limit():
    history = [
        {
            "user": "USER-START " + "u" * 600 + " USER-END-ID-991",
            "assistant": "ASSISTANT-START " + "a" * 600 + " ASSISTANT-END-447",
        }
    ]
    context = build_conversation_context(
        history,
        max_turns=3,
        char_limit=240,
        message_char_limit=1000,
    )
    assert len(context.text) <= 240
    assert "USER-START" in context.text and "USER-END-ID-991" in context.text
    assert "ASSISTANT-START" in context.text and "ASSISTANT-END-447" in context.text
    assert context.text.count("…[truncated]…") == 2
    assert context.truncated_message_count == 2


def test_boundary_matrix_never_exceeds_configured_total():
    history = [
        {"user": "u" * 503, "assistant": "a" * 607}
        for _ in range(5)
    ]
    for char_limit in range(0, 301, 7):
        for message_limit in (0, 1, 15, 16, 17, 18, 50, 100, 500, 1000):
            context = build_conversation_context(
                history,
                max_turns=3,
                char_limit=char_limit,
                message_char_limit=message_limit,
            )
            assert len(context.text) <= char_limit
            assert len(context.turns) <= 3
            assert context.text == "\n\n".join(
                f"User: {turn['user']}\nAssistant: {turn['assistant']}"
                for turn in context.turns
            )


def test_generated_legacy_source_footer_is_not_conversation_context():
    context = build_conversation_context(
        [
            {
                "user": "What is the threshold?",
                "assistant": (
                    "The threshold is 38 C. [1]\n\nSources:\n"
                    "* [1] guide.pdf — page 2 — Fever"
                ),
            }
        ],
        max_turns=3,
        char_limit=1000,
        message_char_limit=500,
    )
    assert "The threshold is 38 C" in context.text
    assert "[1]" not in context.text
    assert "Sources:" not in context.text
    assert "guide.pdf" not in context.text
    assert context.stripped_source_footer_count == 1
    assert context.stripped_citation_marker_count == 1


def test_zero_limits_disable_short_term_context_cleanly():
    context = build_conversation_context(
        [{"user": "question", "assistant": "answer"}],
        max_turns=0,
        char_limit=0,
        message_char_limit=0,
    )
    assert context.text == ""
    assert context.turns == []
    assert context.dropped_turn_count == 1
    message_disabled = build_conversation_context(
        [{"user": "question", "assistant": "answer"}],
        max_turns=1,
        char_limit=100,
        message_char_limit=0,
    )
    assert message_disabled.turns == []
    assert format_history([], max_turns=0, char_limit=0) == "(no previous conversation)"


def test_prompt_uses_the_exact_prepared_history_instead_of_reformatting_raw_turns():
    messages = build_messages(
        "current question",
        "[1]\nSOURCE: guide.pdf\nevidence",
        [{"user": "RAW HISTORY MUST NOT APPEAR", "assistant": "raw answer"}],
        history_text="User: bounded question\nAssistant: bounded answer",
    )
    prompt = messages[-1]["content"]
    assert "bounded question" in prompt
    assert "RAW HISTORY MUST NOT APPEAR" not in prompt
    assert "context only, not evidence" in prompt


def test_use_memory_false_excludes_all_conversation_context(engine):
    engine.memory.add("prior question", "prior answer")
    turn = engine.prepare(
        "What temperature counts as a fever in adults?",
        use_memory=False,
    )
    assert turn.history == []
    assert turn.conversation_context.text == ""
    assert turn.conversation_context.input_turn_count == 0


def test_engine_reuses_bounded_history_for_query_and_generation(engine):
    engine.settings = replace(
        engine.settings,
        history_turns=1,
        history_char_limit=280,
        history_message_char_limit=180,
    )
    engine.query_processor = QueryProcessor(enabled=True, llm_rewrite=False)
    engine.memory.add("OLD QUESTION SHOULD DROP", "old answer")
    engine.memory.add(
        "Mira Chen " + "middle " * 80 + " APX-447",
        "The approval record " + "detail " * 80 + " DATE-2026-04-17",
    )

    turn = engine.prepare("When was it approved?")
    assert turn.conversation_context.character_count <= 280
    assert len(turn.history) == 1
    assert "OLD QUESTION SHOULD DROP" not in turn.conversation_context.text
    assert "Mira Chen" in turn.conversation_context.text
    assert "APX-447" in turn.conversation_context.text
    assert "APX-447" in turn.queries[1]
    diagnostics = turn.diagnostics()["conversation_context"]
    assert "conversation_context" in turn.timings
    assert diagnostics["included_turn_count"] == 1
    assert diagnostics["dropped_turn_count"] == 1

    result = engine.ask("What temperature counts as a fever in adults?")
    assert result.citations
    model_prompt = FakeLLM.last_messages[-1]["content"]
    assert turn.conversation_context.text in model_prompt
    assert "OLD QUESTION SHOULD DROP" not in model_prompt
