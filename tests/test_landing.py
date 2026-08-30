"""Phase 96: pure formatting helpers behind the public landing page."""

from __future__ import annotations

from apex_ai.billing.plans import PlanLimits
from apex_ai.web.landing import (
    _format_count,
    _format_price,
    _format_storage,
    _limit_lines,
    render_landing_html,
)


def test_format_price_free_and_paid():
    assert _format_price(0) == "Free"
    assert _format_price(1900) == "$19/mo"
    assert _format_price(9900) == "$99/mo"
    assert _format_price(1050) == "$10.50/mo"


def test_format_count_unlimited_vs_bounded():
    assert _format_count(None, "documents") == "Unlimited documents"
    assert _format_count(20, "documents") == "20 documents"
    assert _format_count(2000, "messages", per_month=True) == "2,000 messages/month"
    assert _format_count(None, "messages", per_month=True) == "Unlimited messages"


def test_format_count_singularizes_exactly_one():
    assert _format_count(1, "projects") == "1 project"
    assert _format_count(1, "tool calls", per_month=True) == "1 tool call/month"
    assert _format_count(2, "projects") == "2 projects"


def test_format_storage_switches_units_at_1000mb():
    assert _format_storage(None) == "Unlimited storage"
    assert _format_storage(200) == "200 MB storage"
    assert _format_storage(5_000) == "5 GB storage"
    assert _format_storage(50_000) == "50 GB storage"


def test_limit_lines_cover_every_field():
    limits = PlanLimits(
        max_documents=20,
        max_storage_mb=200,
        max_collections=3,
        max_projects=1,
        max_messages_per_month=100,
        max_tool_calls_per_month=50,
    )
    lines = _limit_lines(limits)
    assert lines == [
        "20 documents",
        "200 MB storage",
        "3 collections",
        "1 project",
        "100 messages/month",
        "50 tool calls/month",
    ]


def test_render_landing_html_is_self_contained_and_well_formed():
    html = render_landing_html()
    assert html.strip().startswith("<!doctype html>")
    assert html.strip().endswith("</html>")
    # No inline scripts/styles - the shared Content-Security-Policy forbids them.
    assert "<script" not in html
    assert "<style" not in html
    assert 'style="' not in html
    assert 'href="/login"' in html
