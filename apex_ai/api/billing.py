"""Subscription/plan routes (Phase 81-84). Read-only: nothing here changes
which plan an account is on - there is no real payment provider connected
(Phase 85 is deliberately declined, see
docs/PHASE85_BILLING_INTEGRATION_DECISION.md), so an upgrade/downgrade is
an administrative action (``SubscriptionStore.set_plan``), not a self-serve
one, yet.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from apex_ai.api.auth import make_require_user_dependency
from apex_ai.api.errors import APIError
from apex_ai.api.schemas import PlanOut, SubscriptionOut, UsageSummaryOut
from apex_ai.billing.plans import PLANS
from apex_ai.core.errors import ApexError


def _unavailable() -> APIError:
    return APIError(
        503,
        "Billing is unavailable. Core chat and document upload remain available.",
        code="billing_unavailable",
        retryable=True,
    )


def create_billing_router(services) -> APIRouter:
    router = APIRouter(prefix="/billing", tags=["billing"])
    require_user = make_require_user_dependency(services)

    @router.get("/plans", response_model=list[PlanOut])
    def list_plans():
        return [plan.to_dict() for plan in sorted(PLANS.values(), key=lambda plan: plan.price_cents)]

    @router.get("/plan", response_model=SubscriptionOut)
    def current_plan(user=Depends(require_user)):
        if services.subscriptions is None:
            raise _unavailable()
        try:
            return services.subscriptions.get(user.id).to_dict()
        except ApexError as error:
            raise APIError.from_apex(error, status_code=503) from error

    @router.get("/usage", response_model=UsageSummaryOut)
    def usage_summary(user=Depends(require_user)):
        """Phase 88: where this account actually stands against its plan
        right now - real live counts (documents/storage/collections/
        projects) and real recorded usage (messages/tool calls this
        month), not the same request/increase check enforcement uses.
        A resource whose underlying store isn't wired up is omitted
        rather than failing the whole summary."""
        if services.subscriptions is None or services.entitlements is None:
            raise _unavailable()
        try:
            subscription = services.subscriptions.get(user.id)
        except ApexError as error:
            raise APIError.from_apex(error, status_code=503) from error

        results = []
        if services.ingestion is not None:
            document_count = services.ingestion.stats(user.id)["documents"]
            results.append(
                services.entitlements.check_capacity(
                    user.id, "documents", document_count, requested_increase=0
                )
            )
            storage_used_mb = services.ingestion.storage_bytes(user.id) // (1024 * 1024)
            results.append(
                services.entitlements.check_capacity(
                    user.id, "storage_mb", storage_used_mb, requested_increase=0
                )
            )
        if services.collections is not None:
            results.append(
                services.entitlements.check_capacity(
                    user.id, "collections", len(services.collections.list(user.id)),
                    requested_increase=0,
                )
            )
        if services.projects is not None:
            results.append(
                services.entitlements.check_capacity(
                    user.id, "projects", len(services.projects.list(user.id)),
                    requested_increase=0,
                )
            )
        results.append(services.entitlements.check_rate(user.id, "messages", requested_amount=0))
        results.append(
            services.entitlements.check_rate(user.id, "tool_calls", requested_amount=0)
        )

        return {
            "subscription": subscription.to_dict(),
            "entitlements": [result.to_dict() for result in results],
        }

    return router


__all__ = ["create_billing_router"]
