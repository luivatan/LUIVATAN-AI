"""Phase 76: RagEngine.ask_with_tools() - the live integration between
Phase 73/74's tool-calling abstraction and real generation."""

from __future__ import annotations

from apex_ai.llm.base import ToolCall, ToolCallResult
from apex_ai.tools import PermissionedToolExecutor, ToolRegistry, make_calculator_tool
from apex_ai.tools.base import Tool
from apex_ai.tools.permissions import MAX_TOOL_CALLS_PER_TURN

QUESTION = "What temperature counts as a fever in adults?"


class FakeToolLLM:
    """A minimal provider double that supports tool calling deterministically."""

    name = "fake-tools"
    supports_streaming = False
    supports_tools = True

    def __init__(self, tool_calls=(), final_answer="Final answer. [1]"):
        self.tool_calls = tuple(tool_calls)
        self.final_answer = final_answer
        self.final_messages = None

    def generate_with_tools(self, messages, tools, max_tokens=512, temperature=0.2):
        content = None if self.tool_calls else self.final_answer
        return ToolCallResult(content=content, tool_calls=self.tool_calls)

    def generate(self, prompt=None, *, messages=None, max_tokens=512, temperature=0.2, stop=None):
        self.final_messages = messages
        return self.final_answer

    def get_model_info(self):
        return {"provider": "fake-tools"}


def _calculator_executor():
    registry = ToolRegistry()
    registry.register(make_calculator_tool())
    return PermissionedToolExecutor(registry)


def test_ask_with_tools_falls_back_to_ask_when_provider_lacks_tool_support(engine):
    """conftest's FakeLLM has no supports_tools attribute at all - the
    honest fallback, not a degraded feature."""
    result = engine.ask_with_tools(QUESTION, tool_executor=_calculator_executor())
    assert not result.insufficient_evidence
    assert result.tool_calls_used == []


def test_ask_with_tools_falls_back_when_no_tools_are_registered(engine):
    empty_executor = PermissionedToolExecutor(ToolRegistry())
    result = engine.ask_with_tools(QUESTION, tool_executor=empty_executor)
    assert result.tool_calls_used == []


def test_ask_with_tools_finalizes_directly_when_no_tool_is_needed(engine):
    engine.llm = FakeToolLLM(tool_calls=(), final_answer="Fever in adults is 38C or higher. [1]")
    result = engine.ask_with_tools(QUESTION, tool_executor=_calculator_executor())
    assert result.tool_calls_used == []
    assert result.answer.startswith("Fever in adults is 38C or higher.")


def test_ask_with_tools_executes_a_real_tool_and_finalizes_with_the_result(engine):
    call = ToolCall(id="call-1", name="calculator", arguments_json='{"expression": "2 + 2"}')
    fake = FakeToolLLM(tool_calls=[call], final_answer="The answer is 4. [1]")
    engine.llm = fake

    result = engine.ask_with_tools(QUESTION, tool_executor=_calculator_executor())

    assert result.tool_calls_used == [
        {
            "name": "calculator",
            "arguments": '{"expression": "2 + 2"}',
            "result": "4",
            "is_error": False,
        }
    ]
    assert result.answer.startswith("The answer is 4.")
    # the follow-up generate() call must carry both the assistant's tool
    # request and the real tool result as a 'tool' role message.
    assert any(
        message.get("role") == "assistant" and message.get("tool_calls")
        for message in fake.final_messages
    )
    assert any(
        message.get("role") == "tool" and message.get("content") == "4"
        for message in fake.final_messages
    )


def test_ask_with_tools_refuses_a_tool_call_for_a_tool_that_was_never_granted(engine):
    """A model can attempt to call any tool name in the registry, granted or
    not (weaker local models especially) - the executor must still refuse
    it even though a different (safe) tool was legitimately offered."""
    registry = ToolRegistry()
    registry.register(make_calculator_tool())  # offered: requires_permission=False
    registry.register(
        Tool(
            name="action",
            description="A gated action.",
            parameters={"type": "object", "properties": {}},
            handler=lambda arguments: "ran",
        )
    )  # NOT offered: requires_permission defaults True
    executor = PermissionedToolExecutor(registry)
    call = ToolCall(id="call-1", name="action", arguments_json="{}")
    engine.llm = FakeToolLLM(tool_calls=[call], final_answer="Done. [1]")

    result = engine.ask_with_tools(QUESTION, tool_executor=executor)

    assert result.tool_calls_used[0]["is_error"] is True
    assert "permission" in result.tool_calls_used[0]["result"]


def test_ask_with_tools_call_budget_bounds_a_single_round(engine):
    many_calls = [
        ToolCall(id=f"call-{i}", name="calculator", arguments_json='{"expression": "1 + 1"}')
        for i in range(MAX_TOOL_CALLS_PER_TURN + 3)
    ]
    engine.llm = FakeToolLLM(tool_calls=many_calls, final_answer="Done. [1]")

    result = engine.ask_with_tools(QUESTION, tool_executor=_calculator_executor())

    successes = [item for item in result.tool_calls_used if not item["is_error"]]
    failures = [item for item in result.tool_calls_used if item["is_error"]]
    assert len(successes) == MAX_TOOL_CALLS_PER_TURN
    assert len(failures) == 3
    assert all("Too many tool calls" in item["result"] for item in failures)


def test_ask_with_tools_still_refuses_unsupported_evidence(engine):
    """Tool availability must not bypass the anti-hallucination evidence
    gate - an out-of-scope question is still insufficient_evidence."""
    engine.llm = FakeToolLLM(tool_calls=(), final_answer="Should never be reached.")
    result = engine.ask_with_tools(
        "What is the capital of France?", tool_executor=_calculator_executor()
    )
    assert result.insufficient_evidence
