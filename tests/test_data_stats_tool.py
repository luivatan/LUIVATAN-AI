"""Phase 76: the data_stats tool - exact aggregation, never an LLM guess."""

from __future__ import annotations

from apex_ai.tools.data_stats import MAX_NUMBERS, make_data_stats_tool


def _run(**arguments):
    return make_data_stats_tool().handler(arguments)


def test_sum_mean_median_min_max_count():
    numbers = [1, 2, 3, 4, 10]
    assert _run(numbers=numbers, operation="sum") == "20"
    assert _run(numbers=numbers, operation="mean") == "4"
    assert _run(numbers=numbers, operation="median") == "3"
    assert _run(numbers=numbers, operation="min") == "1"
    assert _run(numbers=numbers, operation="max") == "10"
    assert _run(numbers=numbers, operation="count") == "5"


def test_stdev_computes_the_real_sample_standard_deviation():
    import statistics

    numbers = [2, 4, 4, 4, 5, 5, 7, 9]
    expected = str(statistics.stdev(numbers))
    assert _run(numbers=numbers, operation="stdev") == expected


def test_stdev_needs_at_least_two_numbers():
    output = _run(numbers=[5], operation="stdev")
    assert "at least two" in output


def test_unsupported_operation_is_a_friendly_message():
    output = _run(numbers=[1, 2], operation="mode")
    assert "not a supported operation" in output


def test_operation_is_case_insensitive():
    assert _run(numbers=[1, 2, 3], operation="SUM") == "6"


def test_empty_list_is_rejected():
    output = _run(numbers=[], operation="sum")
    assert "non-empty list" in output


def test_non_list_input_is_rejected():
    output = _run(numbers="not a list", operation="sum")
    assert "non-empty list" in output


def test_non_numeric_items_are_rejected():
    output = _run(numbers=[1, "two", 3], operation="sum")
    assert "must be a number" in output


def test_booleans_are_rejected_as_numbers():
    """bool is a subclass of int in Python - True/False must not silently
    count as 1/0 in an aggregate a user asked for over real numbers."""
    output = _run(numbers=[1, True, 3], operation="sum")
    assert "must be a number" in output


def test_too_many_numbers_is_rejected():
    output = _run(numbers=[1] * (MAX_NUMBERS + 1), operation="sum")
    assert "Too many numbers" in output


def test_tool_metadata():
    tool = make_data_stats_tool()
    assert tool.name == "data_stats"
    assert tool.requires_permission is False
    assert tool.parameters["required"] == ["numbers", "operation"]
