"""Explicit approval/rejection endpoints for pending memory candidates."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from apex_ai.api.errors import APIError
from apex_ai.core.errors import ApexError


def create_memory_router(services) -> APIRouter:
    router = APIRouter(prefix="/memory", tags=["memory"])

    def confirmation_service():
        service = services.memory_confirmation
        if service is None:
            raise APIError(
                503,
                "Long-term memory is unavailable. Core chat remains available.",
                code="memory_unavailable",
                retryable=True,
            )
        return service

    @router.get("/candidates")
    def list_candidates():
        try:
            return [item.to_dict() for item in confirmation_service().pending()]
        except ApexError as error:
            raise APIError.from_apex(error, status_code=503) from error

    @router.post("/candidates/{proposal_id}/approve")
    def approve_candidate(proposal_id: str):
        try:
            memory = confirmation_service().approve(proposal_id)
            return {"approved": True, "memory": memory.to_dict()}
        except KeyError as error:
            raise HTTPException(
                status_code=404,
                detail="That memory proposal is missing or expired.",
            ) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except ApexError as error:
            raise APIError.from_apex(error, status_code=400) from error

    @router.post("/candidates/{proposal_id}/reject")
    def reject_candidate(proposal_id: str):
        try:
            if not confirmation_service().reject(proposal_id):
                raise HTTPException(
                    status_code=404,
                    detail="That memory proposal is missing or expired.",
                )
            return {"rejected": True}
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except ApexError as error:
            raise APIError.from_apex(error, status_code=503) from error

    return router


__all__ = ["create_memory_router"]
