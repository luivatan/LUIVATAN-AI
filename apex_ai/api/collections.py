"""Document collection CRUD routes (Phase 66)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from apex_ai.api.auth import make_require_user_dependency
from apex_ai.api.errors import APIError, entitlement_error
from apex_ai.api.schemas import CollectionOut, DeletedOut
from apex_ai.core.errors import ApexError


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class CollectionRename(BaseModel):
    name: str = Field(min_length=1, max_length=80)


def _unavailable() -> APIError:
    return APIError(
        503,
        "Collections are unavailable. Core chat and document upload remain available.",
        code="collections_unavailable",
        retryable=True,
    )


def create_collections_router(services) -> APIRouter:
    router = APIRouter(prefix="/collections", tags=["collections"])
    require_user = make_require_user_dependency(services)

    def store():
        if services.collections is None:
            raise _unavailable()
        return services.collections

    @router.get("", response_model=list[CollectionOut])
    def list_collections(user=Depends(require_user)):
        try:
            return [item.to_dict() for item in store().list(user.id)]
        except ApexError as error:
            raise APIError.from_apex(error, status_code=503) from error

    @router.post("", status_code=201, response_model=CollectionOut)
    def create_collection(payload: CollectionCreate, user=Depends(require_user)):
        if services.entitlements is not None:
            capacity = services.entitlements.check_capacity(
                user.id, "collections", len(store().list(user.id))
            )
            if not capacity.allowed:
                raise entitlement_error(capacity.reason)
        try:
            return store().create(user.id, payload.name).to_dict()
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except ApexError as error:
            raise APIError.from_apex(error, status_code=503) from error

    @router.patch("/{collection_id}", response_model=CollectionOut)
    def rename_collection(
        collection_id: str, payload: CollectionRename, user=Depends(require_user)
    ):
        try:
            return store().rename(user.id, collection_id, payload.name).to_dict()
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Collection not found.") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except ApexError as error:
            raise APIError.from_apex(error, status_code=503) from error

    @router.delete("/{collection_id}", response_model=DeletedOut)
    def delete_collection(collection_id: str, user=Depends(require_user)):
        try:
            deleted = store().delete(user.id, collection_id)
        except ApexError as error:
            raise APIError.from_apex(error, status_code=503) from error
        if not deleted:
            raise HTTPException(status_code=404, detail="Collection not found.")
        # The collection is gone; every document that pointed at it becomes
        # uncategorized again rather than referencing a dangling ID.
        if services.ingestion is not None:
            services.ingestion.unassign_collection(user.id, collection_id)
        return {"deleted": True}

    return router


__all__ = ["create_collections_router"]
