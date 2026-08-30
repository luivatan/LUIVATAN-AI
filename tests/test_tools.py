"""Phase 73: the tool-calling abstraction (Tool/ToolRegistry) and the
provider-level generate_with_tools() interface. No network."""

from __future__ import annotations

import pytest

from apex_ai.llm.base import ToolCall
from apex_ai.tools.base import MAX_RESULT_CHARS, Tool, ToolRegistry


def _echo_tool(name="echo") -> Tool:
    return Tool(
        name=name,
        description="Echo back the given text.",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        handler=lambda arguments: arguments["text"],
    )


def test_register_and_list_tools_are_sorted_by_name():
    registry = ToolRegistry()
    registry.register(_echo_tool("zebra"))
    registry.register(_echo_tool("alpha"))
    assert [tool.name for tool in registry.list()] == ["alpha", "zebra"]


def test_registering_a_duplicate_name_is_rejected():
    registry = ToolRegistry()
    registry.register(_echo_tool())
    with pytest.raises(ValueError, match="already registered"):
        registry.register(_echo_tool())


def test_get_returns_none_for_an_unknown_tool():
    registry = ToolRegistry()
    assert registry.get("does-not-exist") is None


def test_schema_matches_the_openai_function_calling_shape():
    registry = ToolRegistry()
    registry.register(_echo_tool())
    schema = registry.schema()
    assert schema == [
        {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "Echo back the given text.",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            },
        }
    ]


def test_execute_runs_the_real_handler_and_returns_its_output():
    registry = ToolRegistry()
    registry.register(_echo_tool())
    result = registry.execute(ToolCall(id="call-1", name="echo", arguments_json='{"text": "hi"}'))
    assert result.call_id == "call-1"
    assert result.name == "echo"
    assert result.content == "hi"
    assert result.is_error is False


def test_execute_message_shape_matches_the_tool_role_convention():
    registry = ToolRegistry()
    registry.register(_echo_tool())
    result = registry.execute(ToolCall(id="call-1", name="echo", arguments_json='{"text": "hi"}'))
    assert result.to_message() == {
        "role": "tool",
        "tool_call_id": "call-1",
        "name": "echo",
        "content": "hi",
    }


def test_execute_a_hallucinated_tool_name_is_a_bounded_error_not_a_crash():
    registry = ToolRegistry()
    result = registry.execute(ToolCall(id="call-1", name="does-not-exist", arguments_json="{}"))
    assert result.is_error is True
    assert "does-not-exist" in result.content


def test_execute_malformed_json_arguments_is_a_bounded_error_not_a_crash():
    registry = ToolRegistry()
    registry.register(_echo_tool())
    result = registry.execute(ToolCall(id="call-1", name="echo", arguments_json="{not json"))
    assert result.is_error is True
    assert "not valid JSON" in result.content


def test_execute_non_object_json_arguments_is_a_bounded_error():
    registry = ToolRegistry()
    registry.register(_echo_tool())
    result = registry.execute(ToolCall(id="call-1", name="echo", arguments_json="[1, 2, 3]"))
    assert result.is_error is True


def test_execute_a_raising_handler_is_a_bounded_error_not_a_crash():
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="broken",
            description="Always fails.",
            parameters={"type": "object", "properties": {}},
            handler=lambda arguments: (_ for _ in ()).throw(RuntimeError("boom")),
        )
    )
    result = registry.execute(ToolCall(id="call-1", name="broken", arguments_json="{}"))
    assert result.is_error is True
    assert "boom" not in result.content  # internal exception detail is not leaked


def test_execute_truncates_an_oversized_result():
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="verbose",
            description="Returns a huge string.",
            parameters={"type": "object", "properties": {}},
            handler=lambda arguments: "x" * (MAX_RESULT_CHARS * 2),
        )
    )
    result = registry.execute(ToolCall(id="call-1", name="verbose", arguments_json="{}"))
    assert len(result.content) <= MAX_RESULT_CHARS + len("\n… (truncated)")
    assert result.content.endswith("(truncated)")
    assert result.is_error is False


def test_provider_without_tool_support_raises_a_clear_error(settings):
    from apex_ai.core.errors import ProviderError
    from apex_ai.llm.local import LocalLLMProvider

    provider = LocalLLMProvider(settings)
    assert provider.supports_tools is False
    with pytest.raises(ProviderError) as excinfo:
        provider.generate_with_tools([{"role": "user", "content": "hi"}], tools=[])
    assert "does not support tool calling" in str(excinfo.value)
