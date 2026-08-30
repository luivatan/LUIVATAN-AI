"""A reliable calculator tool (Phase 76) — real, deterministic arithmetic
instead of asking the LLM to guess.

Evaluation walks a parsed ``ast.Expression`` against a strict whitelist of
node types, operators, and function names; it never calls ``eval()`` or
``exec()``. Every intermediate result is bounded so a pathological
expression (e.g. deeply nested exponentiation) cannot hang the process or
exhaust memory before an error is raised — the same "never let one request
degrade the whole service" posture as every other bounded operation in this
codebase (context character limits, upload size, document page counts).
"""

from __future__ import annotations

import ast
import math
import operator

from apex_ai.tools.base import Tool

MAX_EXPRESSION_LENGTH = 200
MAX_MAGNITUDE = 1e15
MAX_EXPONENT = 1000

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_FUNCTIONS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "floor": math.floor,
    "ceil": math.ceil,
}


class ExpressionError(ValueError):
    """A user-facing reason an expression could not be evaluated safely."""


def _bounded(value: float) -> float | int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if math.isnan(value) or math.isinf(value):
            raise ExpressionError("The result is not a finite number.")
        if abs(value) > MAX_MAGNITUDE:
            raise ExpressionError("The result is too large to compute safely.")
    return value


def _eval_node(node: ast.AST):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ExpressionError("Only numbers are allowed in the expression.")
        return node.value
    if isinstance(node, ast.BinOp):
        op = _BINOPS.get(type(node.op))
        if op is None:
            raise ExpressionError(f"The operator '{type(node.op).__name__}' is not allowed.")
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Pow):
            if abs(right) > MAX_EXPONENT:
                raise ExpressionError("The exponent is too large to compute safely.")
            if abs(left) > 1_000_000 and abs(right) > 4:
                raise ExpressionError("This expression is too large to compute safely.")
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
            raise ExpressionError("Division by zero.")
        return _bounded(op(left, right))
    if isinstance(node, ast.UnaryOp):
        op = _UNARYOPS.get(type(node.op))
        if op is None:
            raise ExpressionError(f"The operator '{type(node.op).__name__}' is not allowed.")
        return _bounded(op(_eval_node(node.operand)))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
            raise ExpressionError("Only abs, round, min, max, sqrt, floor, and ceil are allowed.")
        if node.keywords:
            raise ExpressionError("Keyword arguments are not allowed.")
        args = [_eval_node(arg) for arg in node.args]
        try:
            return _bounded(_FUNCTIONS[node.func.id](*args))
        except (TypeError, ValueError) as error:
            raise ExpressionError(str(error)) from error
    raise ExpressionError(f"'{type(node).__name__}' is not allowed in an expression.")


def evaluate_expression(expression: str) -> float | int:
    """Evaluate a numeric arithmetic expression safely.

    Raises ``ExpressionError`` (never a raw parser/runtime exception) with a
    message safe to show directly to a user or a model.
    """
    expression = (expression or "").strip()
    if not expression:
        raise ExpressionError("No expression was given.")
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise ExpressionError(
            f"The expression is too long (limit {MAX_EXPRESSION_LENGTH} characters)."
        )
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError, RecursionError) as error:
        raise ExpressionError(f"'{expression}' is not a valid arithmetic expression.") from error
    try:
        return _eval_node(tree.body)
    except RecursionError as error:
        raise ExpressionError("The expression is too deeply nested.") from error
    except ZeroDivisionError as error:
        raise ExpressionError("Division by zero.") from error


def _handler(arguments: dict) -> str:
    expression = arguments.get("expression", "")
    try:
        result = evaluate_expression(str(expression))
    except ExpressionError as error:
        return f"Could not evaluate '{expression}': {error}"
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return str(result)


def make_calculator_tool() -> Tool:
    return Tool(
        name="calculator",
        description=(
            "Evaluate a numeric arithmetic expression exactly (addition, subtraction, "
            "multiplication, division, floor division, modulo, exponentiation, and the "
            "functions abs/round/min/max/sqrt/floor/ceil). Use this for any calculation "
            "instead of computing it yourself - it is exact where estimation is not."
        ),
        parameters={
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "A numeric arithmetic expression, e.g. '(12 + 8) * 3.5'.",
                }
            },
            "required": ["expression"],
        },
        handler=_handler,
        # Pure computation: no I/O, no state mutation, no network - the
        # textbook case Phase 74 designed the opt-out for.
        requires_permission=False,
    )


__all__ = ["ExpressionError", "evaluate_expression", "make_calculator_tool"]
