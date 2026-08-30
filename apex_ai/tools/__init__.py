"""Tool-calling abstraction (Phase 73): a safe way for the model to invoke
real, deterministic Python functions instead of guessing."""

from apex_ai.tools.base import Tool, ToolCall, ToolRegistry, ToolResult
from apex_ai.tools.calculator import make_calculator_tool
from apex_ai.tools.data_stats import make_data_stats_tool
from apex_ai.tools.permissions import PermissionedToolExecutor, ToolCallBudget


def build_default_registry() -> ToolRegistry:
    """The registry of every built-in tool Apex AI ships (Phase 76)."""
    registry = ToolRegistry()
    registry.register(make_calculator_tool())
    registry.register(make_data_stats_tool())
    return registry


__all__ = [
    "PermissionedToolExecutor",
    "Tool",
    "ToolCall",
    "ToolCallBudget",
    "ToolRegistry",
    "ToolResult",
    "build_default_registry",
    "make_calculator_tool",
    "make_data_stats_tool",
]
