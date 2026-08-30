"""Phase 81-84/87-88: subscription plans, per-account subscription state,
usage tracking, and entitlement enforcement. No network, no real billing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from apex_ai.billing.entitlements import EntitlementService
from apex_ai.billing.plans import (
    BUSINESS_PLAN,
    DEFAULT_PLAN_ID,
    FREE_PLAN,
    PLANS,
    PRO_PLAN,
    get_plan,
)
from apex_ai.billing.subscriptions import SubscriptionStore
from apex_ai.billing.usage import UsageStore, month_start
from tests.conftest import USER

OTHER_USER = "user-2"


# ---------------- plans ----------------


def test_get_plan_returns_the_matching_plan():
    assert get_plan("pro") is PRO_PLAN
    assert get_plan("business") is BUSINESS_PLAN


def test_get_plan_falls_back_to_free_for_an_unknown_id():
    assert get_plan("does-not-exist") is FREE_PLAN
    assert get_plan("") is FREE_PLAN


def test_default_plan_id_is_free():
    assert DEFAULT_PLAN_ID == "free"
    assert PLANS[DEFAULT_PLAN_ID] is FREE_PLAN


def test_free_plan_has_no_price():
    assert FREE_PLAN.price_cents == 0


def test_pro_and_business_have_materially_higher_limits_than_free():
    for attr in (
        "max_documents",
        "max_storage_mb",
        "max_collections",
        "max_projects",
        "max_messages_per_month",
        "max_tool_calls_per_month",
    ):
        free_value = getattr(FREE_PLAN.limits, attr)
        pro_value = getattr(PRO_PLAN.limits, attr)
        business_value = getattr(BUSINESS_PLAN.limits, attr)
        assert pro_value is None or pro_value > free_value
        assert business_value is None or (pro_value is None or business_value >= pro_value)


def test_plan_to_dict_shape():
    payload = FREE_PLAN.to_dict()
    assert payload["id"] == "free"
    assert payload["limits"]["max_documents"] == FREE_PLAN.limits.max_documents
    assert payload["features"] == []


# ---------------- SubscriptionStore ----------------


def test_get_defaults_to_free_plan_with_no_row(tmp_path):
    store = SubscriptionStore(tmp_path / "billing.db")
    subscription = store.get(USER)
    assert subscription.plan_id == "free"
    assert subscription.status == "active"


def test_set_plan_persists_and_get_reflects_it(tmp_path):
    store = SubscriptionStore(tmp_path / "billing.db")
    store.set_plan(USER, "pro")
    assert store.get(USER).plan_id == "pro"

    reopened = SubscriptionStore(tmp_path / "billing.db")
    assert reopened.get(USER).plan_id == "pro"


def test_set_plan_rejects_an_unknown_plan_id(tmp_path):
    store = SubscriptionStore(tmp_path / "billing.db")
    with pytest.raises(ValueError, match="Unknown plan"):
        store.set_plan(USER, "does-not-exist")


def test_set_plan_rejects_an_unknown_status(tmp_path):
    store = SubscriptionStore(tmp_path / "billing.db")
    with pytest.raises(ValueError, match="Unknown subscription status"):
        store.set_plan(USER, "pro", status="past_due")


def test_cancel_reverts_to_the_free_plan(tmp_path):
    store = SubscriptionStore(tmp_path / "billing.db")
    store.set_plan(USER, "business")
    assert store.get(USER).plan_id == "business"

    canceled = store.cancel(USER)
    assert canceled.plan_id == "free"
    assert canceled.status == "active"


def test_set_plan_preserves_created_at_across_updates(tmp_path):
    store = SubscriptionStore(tmp_path / "billing.db")
    first = store.set_plan(USER, "pro")
    second = store.set_plan(USER, "business")
    assert second.created_at == first.created_at
    assert second.updated_at >= first.updated_at


def test_subscriptions_are_isolated_between_accounts(tmp_path):
    store = SubscriptionStore(tmp_path / "billing.db")
    store.set_plan(USER, "pro")
    assert store.get(OTHER_USER).plan_id == "free"  # untouched, still the default


# ---------------- UsageStore ----------------


def test_record_and_total_since(tmp_path):
    store = UsageStore(tmp_path / "billing.db")
    store.record(USER, "messages", 1)
    store.record(USER, "messages", 1)
    store.record(USER, "messages", 3)
    assert store.total_since(USER, "messages", "1970-01-01T00:00:00Z") == 5


def test_total_this_month_only_counts_current_month(tmp_path):
    store = UsageStore(tmp_path / "billing.db")
    store.record(USER, "messages", 10)
    assert store.total_this_month(USER, "messages") == 10
    assert store.total_since(USER, "messages", month_start()) == 10

    future = (datetime.now(timezone.utc) + timedelta(days=400)).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")
    assert store.total_since(USER, "messages", future) == 0


def test_record_rejects_a_nonpositive_amount(tmp_path):
    store = UsageStore(tmp_path / "billing.db")
    with pytest.raises(ValueError):
        store.record(USER, "messages", 0)
    with pytest.raises(ValueError):
        store.record(USER, "messages", -1)


def test_usage_is_isolated_between_accounts(tmp_path):
    store = UsageStore(tmp_path / "billing.db")
    store.record(USER, "messages", 5)
    store.record(OTHER_USER, "messages", 2)
    assert store.total_this_month(USER, "messages") == 5
    assert store.total_this_month(OTHER_USER, "messages") == 2


def test_usage_is_isolated_between_resources(tmp_path):
    store = UsageStore(tmp_path / "billing.db")
    store.record(USER, "messages", 5)
    store.record(USER, "tool_calls", 2)
    assert store.total_this_month(USER, "messages") == 5
    assert store.total_this_month(USER, "tool_calls") == 2


def test_month_start_is_midnight_utc_on_the_first():
    reference = datetime(2026, 8, 30, 15, 42, 7, tzinfo=timezone.utc)
    assert month_start(reference) == "2026-08-01T00:00:00.000000Z"


# ---------------- EntitlementService ----------------


@pytest.fixture()
def entitlements(tmp_path):
    subscriptions = SubscriptionStore(tmp_path / "billing.db")
    usage = UsageStore(tmp_path / "billing.db")
    return EntitlementService(subscriptions, usage), subscriptions, usage


def test_check_capacity_allows_under_the_limit(entitlements):
    service, _, _ = entitlements
    result = service.check_capacity(USER, "documents", current_count=5)
    assert result.allowed is True
    assert result.plan_id == "free"
    assert result.limit == FREE_PLAN.limits.max_documents
    assert result.remaining == FREE_PLAN.limits.max_documents - 5


def test_check_capacity_blocks_at_the_limit(entitlements):
    service, _, _ = entitlements
    limit = FREE_PLAN.limits.max_documents
    result = service.check_capacity(USER, "documents", current_count=limit)
    assert result.allowed is False
    assert "Free plan" in result.reason
    assert result.remaining == 0


def test_check_capacity_unlimited_plan_always_allows(entitlements):
    service, subscriptions, _ = entitlements
    subscriptions.set_plan(USER, "business")  # unlimited documents
    result = service.check_capacity(USER, "documents", current_count=1_000_000)
    assert result.allowed is True
    assert result.limit is None
    assert result.remaining is None


def test_check_capacity_unknown_resource_raises(entitlements):
    service, _, _ = entitlements
    with pytest.raises(ValueError):
        service.check_capacity(USER, "does-not-exist", current_count=0)


def test_check_rate_allows_under_the_limit(entitlements):
    service, _, usage = entitlements
    usage.record(USER, "messages", 5)
    result = service.check_rate(USER, "messages")
    assert result.allowed is True
    assert result.used == 5


def test_check_rate_blocks_after_recording_usage_up_to_the_limit(entitlements):
    service, _, usage = entitlements
    limit = FREE_PLAN.limits.max_messages_per_month
    usage.record(USER, "messages", limit)
    result = service.check_rate(USER, "messages")
    assert result.allowed is False
    assert "per month" in result.reason


def test_check_rate_unknown_resource_raises(entitlements):
    service, _, _ = entitlements
    with pytest.raises(ValueError):
        service.check_rate(USER, "does-not-exist")


def test_has_feature_reflects_the_plan(entitlements):
    service, subscriptions, _ = entitlements
    assert service.has_feature(USER, "priority_support") is False
    subscriptions.set_plan(USER, "pro")
    assert service.has_feature(USER, "priority_support") is True


def test_upgrading_the_plan_raises_the_effective_limit(entitlements):
    service, subscriptions, _ = entitlements
    at_free_limit = FREE_PLAN.limits.max_documents
    blocked = service.check_capacity(USER, "documents", current_count=at_free_limit)
    assert blocked.allowed is False

    subscriptions.set_plan(USER, "pro")
    allowed = service.check_capacity(USER, "documents", current_count=at_free_limit)
    assert allowed.allowed is True
