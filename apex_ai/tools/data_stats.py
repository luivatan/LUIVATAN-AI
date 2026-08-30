"""A reliable statistics tool (Phase 76) — exact aggregation over a list of
numbers instead of asking the LLM to guess a sum, mean, or median."""

from __future__ import annotations

import statistics

from apex_ai.tools.base import Tool

MAX_NUMBERS = 10_000

_OPERATIONS = {
    "sum": sum,
    "mean": statistics.mean,
    "median": statistics.median,
    "min": min,
    "max": max,
    "count": len,
    "stdev": statistics.stdev,
}


def _clean_numbers(raw) -> list[float]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("'numbers' must be a non-empty list of numbers.")
    if len(raw) > MAX_NUMBERS:
        raise ValueError(f"Too many numbers (limit {MAX_NUMBERS}).")
    numbers: list[float] = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise TypeError("Every item in 'numbers' must be a number.")
        numbers.append(item)
    return numbers


def _handler(arguments: dict) -> str:
    operation = str(arguments.get("operation", "")).strip().lower()
    if operation not in _OPERATIONS:
        return f"'{operation}' is not a supported operation ({', '.join(sorted(_OPERATIONS))})."
    try:
        numbers = _clean_numbers(arguments.get("numbers"))
    except (ValueError, TypeError) as error:
        return str(error)
    if operation == "stdev" and len(numbers) < 2:
        return "stdev needs at least two numbers."
    result = _OPERATIONS[operation](numbers)
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return str(result)


def make_data_stats_tool() -> Tool:
    return Tool(
        name="data_stats",
        description=(
            "Compute an exact statistic (sum, mean, median, min, max, count, or stdev) "
            "over a list of numbers. Use this instead of estimating an aggregate by eye."
        ),
        parameters={
            "type": "object",
            "properties": {
                "numbers": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "The numbers to aggregate.",
                },
                "operation": {
                    "type": "string",
                    "enum": sorted(_OPERATIONS),
                    "description": "Which statistic to compute.",
                },
            },
            "required": ["numbers", "operation"],
        },
        handler=_handler,
        # Pure computation: no I/O, no state mutation, no network.
        requires_permission=False,
    )


__all__ = ["make_data_stats_tool"]
