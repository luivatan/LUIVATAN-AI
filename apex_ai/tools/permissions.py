"""Explicit permission boundaries for tool execution (Phase 74).

Phase 73's ``ToolRegistry.execute()`` runs a tool unconditionally once
resolved — that is the right place for the raw, bounded execution
guarantees (unknown name, bad arguments, and a raising handler all come
back as a normal result instead of crashing the turn), but it does not know
or care whether *this particular call* is actually authorized right now.
This module adds that layer on top, without changing anything about how
execution itself behaves.

Two independent guarantees, both required before anything the roadmap
calls an "unrestricted action" can run:

1. **Per-tool consent.** A tool with ``Tool.requires_permission=True`` (the
   default) can only run if its name appears in the caller's
   ``granted_tools`` set for this request. A read-only/side-effect-free
   tool marks itself ``requires_permission=False`` once, rather than every
   caller having to grant it every time.
2. **A hard cap on tool calls per turn.** Even a fully granted tool cannot
   be called an unbounded number of times in one model turn — a
   misbehaving or manipulated model must not be able to loop indefinitely
   or run up cost/latency without limit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apex_ai.core.logging import get_logger
from apex_ai.llm.base import ToolCall
from apex_ai.tools.base import ToolRegistry, ToolResult

log = get_logger("tools.permissions")

# A fixed safety ceiling, not a tuning knob - same precedent as
# tools.base.MAX_RESULT_CHARS. Whichever phase first constructs a real,
# live PermissionedToolExecutor can pass a different `max_tool_calls`
# explicitly if that deployment genuinely needs to.
MAX_TOOL_CALLS_PER_TURN = 8


@dataclass
class ToolCallBudget:
    """Tracks how many tool calls have been executed in one model turn.

    Pass the same instance across every call in one turn; a fresh one is
    created automatically when none is given, which only makes sense for a
    single isolated call.
    """

    limit: int = MAX_TOOL_CALLS_PER_TURN
    used: int = 0

    def remaining(self) -> int:
        return max(0, self.limit - self.used)


class PermissionedToolExecutor:
    """The permission- and budget-checked entry point real callers use
    instead of calling ``ToolRegistry.execute()`` directly."""

    def __init__(self, registry: ToolRegistry, *, max_tool_calls: int = MAX_TOOL_CALLS_PER_TURN) -> None:
        self.registry = registry
        self.max_tool_calls = max_tool_calls

    def new_budget(self) -> ToolCallBudget:
        return ToolCallBudget(limit=self.max_tool_calls)

    def schema(self, *, granted_tools: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
        """Tools to actually offer the model this turn: every tool that
        either does not require permission, or has already been granted.
        Least-privilege in the schema itself, not only at execution time —
        the model is never even told about a tool it cannot use."""
        return [
            tool.schema()
            for tool in self.registry.list()
            if not tool.requires_permission or tool.name in granted_tools
        ]

    def execute(
        self,
        call: ToolCall,
        *,
        granted_tools: frozenset[str] = frozenset(),
        budget: ToolCallBudget | None = None,
    ) -> ToolResult:
        budget = budget if budget is not None else self.new_budget()
        if budget.remaining() <= 0:
            log.warning(
                "Tool call budget exhausted (limit=%d); refusing '%s'", budget.limit, call.name
            )
            return ToolResult(
                call_id=call.id,
                name=call.name,
                content=f"Too many tool calls in this turn (limit {budget.limit}). "
                        "Answer with what is already known instead.",
                is_error=True,
            )
        tool = self.registry.get(call.name)
        if tool is not None and tool.requires_permission and call.name not in granted_tools:
            log.warning("Tool '%s' requires permission that was not granted", call.name)
            return ToolResult(
                call_id=call.id,
                name=call.name,
                content=f"The '{call.name}' tool requires explicit permission "
                        "that has not been granted for this request.",
                is_error=True,
            )
        budget.used += 1
        return self.registry.execute(call)


__all__ = ["MAX_TOOL_CALLS_PER_TURN", "PermissionedToolExecutor", "ToolCallBudget"]
