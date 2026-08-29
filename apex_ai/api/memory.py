"""Explicit approval/rejection endpoints for pending memory candidates."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from apex_ai.api.errors import APIError
from apex_ai.api.schemas import (
    ApproveMemoryOut,
    DeletedCountOut,
    DeletedOut,
    LongTermMemoryOut,
    MemoryCandidateOut,
    RejectMemoryOut,
)
from apex_ai.core.errors import ApexError


def _unavailable() -> APIError:
    # A fresh instance per call: this is raised from concurrently-handled
    # requests, and a shared exception instance would mutate its own
    # __traceback__ under concurrent raises.
    return APIError(
        503,
        "Long-term memory is unavailable. Core chat remains available.",
        code="memory_unavailable",
        retryable=True,
    )


def create_memory_router(services) -> APIRouter:
    router = APIRouter(prefix="/memory", tags=["memory"])

    def confirmation_service():
        service = services.memory_confirmation
        if service is None:
            raise _unavailable()
        return service

    def memory_store():
        """Phase 46: confirmed-memory CRUD reads/writes the store directly —
        it does not go through the candidate-confirmation workflow at all."""
        store = services.long_term_memory
        if store is None:
            raise _unavailable()
        return store

    @router.get("/candidates", response_model=list[MemoryCandidateOut])
    def list_candidates():
        try:
            service = confirmation_service()
            payloads = []
            for item in service.pending():
                conflict = service.find_conflict(item)
                payloads.append(
                    {**item.to_dict(), "conflicts_with": conflict.to_dict() if conflict else None}
                )
            return payloads
        except ApexError as error:
            raise APIError.from_apex(error, status_code=503) from error

    @router.post("/candidates/{proposal_id}/approve", response_model=ApproveMemoryOut)
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

    @router.post("/candidates/{proposal_id}/reject", response_model=RejectMemoryOut)
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

    @router.get("", response_model=list[LongTermMemoryOut])
    def list_memories(kind: str | None = None):
        try:
            return [item.to_dict() for item in memory_store().list(kind=kind)]
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except ApexError as error:
            raise APIError.from_apex(error, status_code=503) from error

    @router.delete("/{memory_id}", response_model=DeletedOut)
    def delete_memory(memory_id: str):
        try:
            deleted = memory_store().delete(memory_id)
        except ApexError as error:
            raise APIError.from_apex(error, status_code=503) from error
        if not deleted:
            raise HTTPException(status_code=404, detail="That memory was not found.")
        return {"deleted": True}

    @router.delete("", response_model=DeletedCountOut)
    def clear_memories(kind: str | None = None):
        try:
            return {"deleted": memory_store().clear(kind=kind)}
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except ApexError as error:
            raise APIError.from_apex(error, status_code=503) from error

    return router


__all__ = ["create_memory_router"]
