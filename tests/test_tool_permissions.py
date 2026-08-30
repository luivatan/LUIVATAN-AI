"""Phase 74: explicit permission boundaries and a hard cap on tool calls
per turn, layered on top of Phase 73's raw ToolRegistry.execute()."""

from __future__ import annotations

from apex_ai.llm.base import ToolCall
from apex_ai.tools.base import Tool, ToolRegistry
from apex_ai.tools.permissions import PermissionedToolExecutor, ToolCallBudget


def _tool(name="action", requires_permission=True):
    return Tool(
        name=name,
        description="A test tool.",
        parameters={"type": "object", "properties": {}},
        handler=lambda arguments: "ran",
        requires_permission=requires_permission,
    )


def _executor(*tools):
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return PermissionedToolExecutor(registry)


def test_tools_default_to_requiring_permission():
    tool = _tool()
    assert tool.requires_permission is True


def test_a_gated_tool_is_refused_without_a_grant():
    executor = _executor(_tool("action"))
    result = executor.execute(ToolCall(id="1", name="action", arguments_json="{}"))
    assert result.is_error is True
    assert "permission" in result.content


def test_a_gated_tool_runs_once_explicitly_granted():
    executor = _executor(_tool("action"))
    result = executor.execute(
        ToolCall(id="1", name="action", arguments_json="{}"),
        granted_tools=frozenset({"action"}),
    )
    assert result.is_error is False
    assert result.content == "ran"


def test_a_tool_marked_safe_runs_without_any_grant():
    executor = _executor(_tool("calculator", requires_permission=False))
    result = executor.execute(ToolCall(id="1", name="calculator", arguments_json="{}"))
    assert result.is_error is False
    assert result.content == "ran"


def test_an_unknown_tool_name_is_still_the_bounded_not_found_error():
    """The permission gate must not swallow or reshape Phase 73's own
    unknown-tool handling - it only adds a check in front of it."""
    executor = _executor()
    result = executor.execute(ToolCall(id="1", name="does-not-exist", arguments_json="{}"))
    assert result.is_error is True
    assert "does-not-exist" in result.content
    assert "permission" not in result.content


def test_schema_omits_ungranted_gated_tools():
    executor = _executor(_tool("action"), _tool("calculator", requires_permission=False))
    assert [item["function"]["name"] for item in executor.schema()] == ["calculator"]
    granted = executor.schema(granted_tools=frozenset({"action"}))
    assert sorted(item["function"]["name"] for item in granted) == ["action", "calculator"]


def test_budget_is_exhausted_after_the_configured_number_of_calls():
    executor = PermissionedToolExecutor(
        ToolRegistry(), max_tool_calls=2
    )
    executor.registry.register(_tool("calculator", requires_permission=False))
    budget = executor.new_budget()

    first = executor.execute(ToolCall(id="1", name="calculator", arguments_json="{}"), budget=budget)
    second = executor.execute(ToolCall(id="2", name="calculator", arguments_json="{}"), budget=budget)
    third = executor.execute(ToolCall(id="3", name="calculator", arguments_json="{}"), budget=budget)

    assert first.is_error is False
    assert second.is_error is False
    assert third.is_error is True
    assert "Too many tool calls" in third.content


def test_budget_is_shared_across_granted_and_safe_tools_alike():
    """The call budget applies uniformly - a granted gated tool does not get
    an unlimited allowance just because it was explicitly permitted."""
    executor = PermissionedToolExecutor(ToolRegistry(), max_tool_calls=1)
    executor.registry.register(_tool("action"))
    budget = executor.new_budget()
    granted = frozenset({"action"})

    first = executor.execute(
        ToolCall(id="1", name="action", arguments_json="{}"), granted_tools=granted, budget=budget
    )
    second = executor.execute(
        ToolCall(id="2", name="action", arguments_json="{}"), granted_tools=granted, budget=budget
    )

    assert first.is_error is False
    assert second.is_error is True


def test_a_fresh_budget_is_created_automatically_when_none_is_given():
    executor = _executor(_tool("calculator", requires_permission=False))
    result = executor.execute(ToolCall(id="1", name="calculator", arguments_json="{}"))
    assert result.is_error is False


def test_tool_call_budget_remaining_never_goes_negative():
    budget = ToolCallBudget(limit=1, used=5)
    assert budget.remaining() == 0
