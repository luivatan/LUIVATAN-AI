"""Enforcing plan limits against real usage (Phase 87).

Two independent kinds of check:

- :meth:`EntitlementService.check_capacity` - a live current-state cap
  (documents, storage, collections, projects). The caller supplies the
  current count; deleting something frees room again, so this is never a
  cumulative-ever-created tally.
- :meth:`EntitlementService.check_rate` - a per-calendar-month flow cap
  (messages, tool calls), checked against :class:`UsageStore`'s ledger.

Both return the same :class:`EntitlementResult` shape so a caller reacts
uniformly regardless of which kind of limit was checked.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apex_ai.billing.plans import get_plan
from apex_ai.billing.subscriptions import SubscriptionStore
from apex_ai.billing.usage import UsageStore

_CAPACITY_LIMIT_ATTR = {
    "documents": "max_documents",
    "storage_mb": "max_storage_mb",
    "collections": "max_collections",
    "projects": "max_projects",
}
_RATE_LIMIT_ATTR = {
    "messages": "max_messages_per_month",
    "tool_calls": "max_tool_calls_per_month",
}


@dataclass(frozen=True)
class EntitlementResult:
    allowed: bool
    resource: str
    plan_id: str
    plan_name: str
    limit: int | None
    used: int
    remaining: int | None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "resource": self.resource,
            "plan_id": self.plan_id,
            "plan_name": self.plan_name,
            "limit": self.limit,
            "used": self.used,
            "remaining": self.remaining,
            "reason": self.reason,
        }


class EntitlementService:
    def __init__(self, subscriptions: SubscriptionStore, usage: UsageStore) -> None:
        self.subscriptions = subscriptions
        self.usage = usage

    def _plan_for(self, user_id: str):
        subscription = self.subscriptions.get(user_id)
        return get_plan(subscription.plan_id)

    def check_capacity(
        self,
        user_id: str,
        resource: str,
        current_count: int,
        requested_increase: int = 1,
    ) -> EntitlementResult:
        if resource not in _CAPACITY_LIMIT_ATTR:
            raise ValueError(f"'{resource}' is not a capacity resource.")
        plan = self._plan_for(user_id)
        limit = getattr(plan.limits, _CAPACITY_LIMIT_ATTR[resource])
        used = max(0, current_count)
        if limit is None:
            return EntitlementResult(True, resource, plan.id, plan.name, None, used, None)
        allowed = used + requested_increase <= limit
        remaining = max(0, limit - used)
        reason = (
            ""
            if allowed
            else f"The {plan.name} plan allows up to {limit} {resource}; "
                 f"{used} are already in use."
        )
        return EntitlementResult(
            allowed, resource, plan.id, plan.name, limit, used, remaining, reason
        )

    def check_rate(
        self, user_id: str, resource: str, requested_amount: int = 1
    ) -> EntitlementResult:
        if resource not in _RATE_LIMIT_ATTR:
            raise ValueError(f"'{resource}' is not a rate resource.")
        plan = self._plan_for(user_id)
        limit = getattr(plan.limits, _RATE_LIMIT_ATTR[resource])
        used = self.usage.total_this_month(user_id, resource)
        if limit is None:
            return EntitlementResult(True, resource, plan.id, plan.name, None, used, None)
        allowed = used + requested_amount <= limit
        remaining = max(0, limit - used)
        reason = (
            ""
            if allowed
            else f"The {plan.name} plan allows {limit} {resource} per month; "
                 f"{used} have been used this month."
        )
        return EntitlementResult(
            allowed, resource, plan.id, plan.name, limit, used, remaining, reason
        )

    def has_feature(self, user_id: str, feature: str) -> bool:
        return feature in self._plan_for(user_id).features


__all__ = ["EntitlementResult", "EntitlementService"]
