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
from apex_ai.api.schemas import PlanOut, SubscriptionOut
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

    return router


__all__ = ["create_billing_router"]
