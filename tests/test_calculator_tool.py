"""Phase 76: the calculator tool - real, safe AST-based evaluation, never
eval()/exec()."""

from __future__ import annotations

import pytest

from apex_ai.tools.calculator import (
    MAX_EXPRESSION_LENGTH,
    ExpressionError,
    evaluate_expression,
    make_calculator_tool,
)


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("2 + 2", 4),
        ("(12 + 8) * 3.5", 70.0),
        ("10 / 4", 2.5),
        ("10 // 4", 2),
        ("10 % 4", 2),
        ("2 ** 10", 1024),
        ("-5 + 3", -2),
        ("+5", 5),
        ("abs(-7)", 7),
        ("round(3.14159, 2)", 3.14),
        ("min(3, 1, 2)", 1),
        ("max(3, 1, 2)", 3),
        ("sqrt(16)", 4.0),
        ("floor(3.9)", 3),
        ("ceil(3.1)", 4),
    ],
)
def test_evaluate_expression_matches_python_semantics(expression, expected):
    assert evaluate_expression(expression) == expected


def test_division_by_zero_is_a_friendly_error():
    with pytest.raises(ExpressionError, match="Division by zero"):
        evaluate_expression("1 / 0")


def test_floor_division_by_zero_is_a_friendly_error():
    with pytest.raises(ExpressionError, match="Division by zero"):
        evaluate_expression("1 // 0")


def test_invalid_syntax_is_a_friendly_error():
    with pytest.raises(ExpressionError):
        evaluate_expression("2 +")


def test_empty_expression_is_a_friendly_error():
    with pytest.raises(ExpressionError, match="No expression"):
        evaluate_expression("")


def test_expression_length_is_bounded():
    with pytest.raises(ExpressionError, match="too long"):
        evaluate_expression("1+" * MAX_EXPRESSION_LENGTH + "1")


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('echo hi')",
        "().__class__",
        "[1, 2, 3]",
        "{'a': 1}",
        "1 if True else 2",
        "'a' + 'b'",
        "open('/etc/passwd')",
        "x + 1",
        "exec('1')",
    ],
)
def test_unsafe_or_unsupported_expressions_are_rejected_not_executed(expression):
    with pytest.raises(ExpressionError):
        evaluate_expression(expression)


def test_a_huge_nested_exponent_is_rejected_quickly_not_hung_or_crashed():
    """9**9**9 is right-associative (9**(9**9)); the inner 9**9=387420489
    becomes an exponent far past what can be computed - this must be
    refused, not attempted."""
    with pytest.raises(ExpressionError, match="too large"):
        evaluate_expression("9**9**9")


def test_a_huge_base_raised_to_a_moderate_power_is_rejected():
    with pytest.raises(ExpressionError, match="too large"):
        evaluate_expression("(99999999) ** 9")


def test_a_result_exceeding_the_magnitude_cap_is_rejected():
    with pytest.raises(ExpressionError, match="too large"):
        evaluate_expression("2 ** 1000")


def test_calculator_tool_handler_returns_a_string_result():
    tool = make_calculator_tool()
    assert tool.name == "calculator"
    assert tool.requires_permission is False
    assert tool.handler({"expression": "2 + 2"}) == "4"


def test_calculator_tool_handler_reports_errors_without_raising():
    tool = make_calculator_tool()
    output = tool.handler({"expression": "1 / 0"})
    assert "Could not evaluate" in output
    assert "Division by zero" in output


def test_calculator_tool_handler_missing_expression_is_a_friendly_message():
    tool = make_calculator_tool()
    output = tool.handler({})
    assert "Could not evaluate" in output
