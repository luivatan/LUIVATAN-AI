"""A safe abstraction for tools the model can call (Phase 73).

A ``Tool`` wraps one real, deterministic Python callable - never an LLM
call pretending to be a tool, and never a simulated result. ``ToolRegistry``
is the boundary between "the model asked for X with these arguments" and
"X actually ran": it never lets a hallucinated tool name, malformed
arguments, or a raising handler escape as an unhandled exception or an
unbounded response - a failure always comes back as a normal, bounded
``ToolResult`` the calling conversation can show or feed back to the model,
the same way a citation-free answer is a normal outcome rather than a crash
elsewhere in this codebase.

This module deliberately does not decide *which* tools exist, does not wire
itself into ``RagEngine``/chat, and does not enforce *permissions* (Phase
74) - it is only the reusable execution boundary those later phases build
on.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from apex_ai.core.logging import get_logger
from apex_ai.llm.base import ToolCall

log = get_logger("tools")

# A runaway or verbose tool must not blow up the prompt budget any more than
# retrieved evidence is allowed to (see APEX_CONTEXT_CHAR_LIMIT) - this is a
# fixed, generous safety cap, not a tuning knob.
MAX_RESULT_CHARS = 4000


@dataclass(frozen=True)
class ToolResult:
    """The bounded outcome of one tool call - always safe to show the model
    or the user, whether the tool succeeded or not."""

    call_id: str
    name: str
    content: str
    is_error: bool = False

    def to_message(self) -> dict[str, str]:
        """The ``tool`` role message shape OpenAI-compatible chat APIs (and
        Ollama's) expect when feeding a result back for a follow-up turn."""
        return {"role": "tool", "tool_call_id": self.call_id, "name": self.name, "content": self.content}


@dataclass(frozen=True)
class Tool:
    """One callable the model may invoke.

    ``parameters`` is a JSON Schema object (``{"type": "object",
    "properties": {...}, "required": [...]}``) describing the arguments -
    passed to the provider verbatim as part of its function schema.
    ``handler`` receives the parsed arguments dict and returns a string; it
    must be a real, deterministic (or at least side-effect-bounded)
    operation, never another LLM call standing in for "the tool".
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], str]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """A named collection of tools, and the only place a tool call is
    actually executed."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"A tool named '{tool.name}' is already registered.")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list(self) -> list[Tool]:
        return sorted(self._tools.values(), key=lambda tool: tool.name)

    def schema(self) -> list[dict[str, Any]]:
        """The OpenAI-style tool list to pass to
        ``LLMProvider.generate_with_tools``."""
        return [tool.schema() for tool in self.list()]

    def execute(self, call: ToolCall) -> ToolResult:
        """Run one tool call. Never raises: an unknown tool name, malformed
        JSON arguments, or an exception inside the handler all come back as
        an ``is_error=True`` result instead of propagating - a model
        hallucinating a tool name or bad arguments is an expected, not
        exceptional, outcome for this boundary."""
        tool = self.get(call.name)
        if tool is None:
            return ToolResult(
                call_id=call.id,
                name=call.name,
                content=f"No tool named '{call.name}' is available.",
                is_error=True,
            )
        try:
            arguments = json.loads(call.arguments_json or "{}")
            if not isinstance(arguments, dict):
                raise TypeError("tool arguments must be a JSON object")
        except (json.JSONDecodeError, TypeError) as error:
            log.warning("Tool call '%s' had unparseable arguments: %s", call.name, error)
            return ToolResult(
                call_id=call.id,
                name=call.name,
                content=f"The arguments given to '{call.name}' were not valid JSON.",
                is_error=True,
            )
        try:
            output = tool.handler(arguments)
        except Exception as error:  # noqa: BLE001 - a raising tool must not crash the turn
            log.warning(
                "Tool '%s' raised %s during execution", call.name, type(error).__name__
            )
            return ToolResult(
                call_id=call.id,
                name=call.name,
                content=f"The '{call.name}' tool could not complete this request.",
                is_error=True,
            )
        text = str(output)
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + "\n… (truncated)"
        return ToolResult(call_id=call.id, name=call.name, content=text)


__all__ = ["MAX_RESULT_CHARS", "Tool", "ToolCall", "ToolRegistry", "ToolResult"]
