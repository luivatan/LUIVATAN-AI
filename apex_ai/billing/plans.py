"""Subscription plan architecture (Phase 81-84): plans, limits, and the
free/pro/business tiers.

This module defines what an account is *entitled* to. It never checks
usage or blocks anything itself - enforcing these limits against real,
live usage is `entitlements.py` (Phase 87), which this module intentionally
knows nothing about (a plan doesn't need to know how it's enforced).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PlanLimits:
    """``None`` means unlimited for that resource.

    Two different kinds of limit, checked two different ways (see
    ``entitlements.py``):

    - **Capacity** (``max_documents``, ``max_storage_mb``,
      ``max_collections``, ``max_projects``): a live current-state cap.
      Deleting a document frees room again - this is never a
      cumulative-ever-created tally.
    - **Rate** (``max_messages_per_month``, ``max_tool_calls_per_month``):
      a flow cap over the current calendar month, the one period
      granularity this architecture uses.
    """

    max_documents: int | None
    max_storage_mb: int | None
    max_collections: int | None
    max_projects: int | None
    max_messages_per_month: int | None
    max_tool_calls_per_month: int | None

    def to_dict(self) -> dict[str, int | None]:
        return {
            "max_documents": self.max_documents,
            "max_storage_mb": self.max_storage_mb,
            "max_collections": self.max_collections,
            "max_projects": self.max_projects,
            "max_messages_per_month": self.max_messages_per_month,
            "max_tool_calls_per_month": self.max_tool_calls_per_month,
        }


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    price_cents: int  # monthly price in USD cents; 0 = free
    limits: PlanLimits
    # Labels a future feature can key off of via EntitlementService.has_feature().
    # Nothing in the current codebase gates behavior on these yet - seeing
    # docs/PHASE81-84_SUBSCRIPTION_PLANS.md for what that would take before
    # relying on any of them meaning something live today.
    features: frozenset[str] = field(default_factory=frozenset)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "price_cents": self.price_cents,
            "limits": self.limits.to_dict(),
            "features": sorted(self.features),
        }


# Phase 82: a useful free tier - enough to genuinely try the product (index
# a real small document set, hold a real conversation history) without
# being a functional demo-only trial.
FREE_PLAN = Plan(
    id="free",
    name="Free",
    price_cents=0,
    limits=PlanLimits(
        max_documents=20,
        max_storage_mb=200,
        max_collections=3,
        max_projects=1,
        max_messages_per_month=100,
        max_tool_calls_per_month=50,
    ),
)

# Phase 83: materially higher capacity/rate limits than Free. "Premium
# capabilities" beyond limits are represented as feature labels
# (``features``) for a future feature to key off of - see the module
# docstring above and the phase doc for why none is enforced yet.
PRO_PLAN = Plan(
    id="pro",
    name="Pro",
    price_cents=1900,
    limits=PlanLimits(
        max_documents=500,
        max_storage_mb=5_000,
        max_collections=25,
        max_projects=10,
        max_messages_per_month=2_000,
        max_tool_calls_per_month=1_000,
    ),
    features=frozenset({"priority_support"}),
)

# Phase 84: "team-oriented features where justified" is deliberately NOT
# multi-seat/organization functionality - this codebase has no
# multi-user-per-account (organization) data model at all today (Phase 55
# isolates every store strictly per single account), and building one is a
# separate, much larger feature than a subscription tier. Business is a
# real, distinct tier (materially higher limits, its own feature labels)
# without pretending seats/shared workspaces exist - see the phase doc.
BUSINESS_PLAN = Plan(
    id="business",
    name="Business",
    price_cents=9_900,
    limits=PlanLimits(
        max_documents=None,
        max_storage_mb=50_000,
        max_collections=None,
        max_projects=None,
        max_messages_per_month=10_000,
        max_tool_calls_per_month=5_000,
    ),
    features=frozenset({"priority_support", "dedicated_support"}),
)

PLANS: dict[str, Plan] = {plan.id: plan for plan in (FREE_PLAN, PRO_PLAN, BUSINESS_PLAN)}
DEFAULT_PLAN_ID = FREE_PLAN.id


def get_plan(plan_id: str) -> Plan:
    """Unknown plan id -> the free plan, never an error: a corrupted or
    stale ``plan_id`` in storage must never block an account from using
    the app - it degrades to the safest (most limited) real plan instead."""
    return PLANS.get(plan_id, FREE_PLAN)


__all__ = [
    "BUSINESS_PLAN",
    "DEFAULT_PLAN_ID",
    "FREE_PLAN",
    "PLANS",
    "PRO_PLAN",
    "Plan",
    "PlanLimits",
    "get_plan",
]
