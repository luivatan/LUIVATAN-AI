"""Project workspace CRUD routes (Phase 71).

A project is a named container linking conversations, instructions, and a
document collection. It does not duplicate Phase 66/67's collection/retrieval
scoping machinery - a project holds only its own name/instructions plus a
pointer to an existing ``Collection``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from apex_ai.api.auth import make_require_user_dependency
from apex_ai.api.errors import APIError
from apex_ai.api.schemas import DeletedOut, ProjectOut
from apex_ai.core.errors import ApexError
from apex_ai.memory.conversations import ConversationStore


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    instructions: str = Field(default="", max_length=4000)
    collection_id: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    instructions: str | None = Field(default=None, max_length=4000)
    collection_id: str | None = None
    # A bare `collection_id: str | None = None` cannot distinguish "leave the
    # collection unchanged" (field omitted) from "clear it" (collection_id
    # explicitly ""), so clearing needs its own explicit flag.
    clear_collection: bool = False


def _unavailable() -> APIError:
    return APIError(
        503,
        "Projects are unavailable. Core chat and document upload remain available.",
        code="projects_unavailable",
        retryable=True,
    )


def create_projects_router(services, conversations: ConversationStore) -> APIRouter:
    router = APIRouter(prefix="/projects", tags=["projects"])
    require_user = make_require_user_dependency(services)

    def store():
        if services.projects is None:
            raise _unavailable()
        return services.projects

    def _validate_collection(user_id: str, collection_id: str) -> None:
        if collection_id and (
            services.collections is None
            or services.collections.get(user_id, collection_id) is None
        ):
            raise HTTPException(status_code=404, detail="Collection not found.")

    @router.get("", response_model=list[ProjectOut])
    def list_projects(user=Depends(require_user)):
        try:
            return [item.to_dict() for item in store().list(user.id)]
        except ApexError as error:
            raise APIError.from_apex(error, status_code=503) from error

    @router.post("", status_code=201, response_model=ProjectOut)
    def create_project(payload: ProjectCreate, user=Depends(require_user)):
        collection_id = payload.collection_id or ""
        _validate_collection(user.id, collection_id)
        try:
            return store().create(
                user.id, payload.name, payload.instructions, collection_id
            ).to_dict()
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except ApexError as error:
            raise APIError.from_apex(error, status_code=503) from error

    @router.get("/{project_id}", response_model=ProjectOut)
    def get_project(project_id: str, user=Depends(require_user)):
        try:
            project = store().get(user.id, project_id)
        except ApexError as error:
            raise APIError.from_apex(error, status_code=503) from error
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        return project.to_dict()

    @router.patch("/{project_id}", response_model=ProjectOut)
    def update_project(project_id: str, payload: ProjectUpdate, user=Depends(require_user)):
        collection_id = None
        if payload.clear_collection:
            collection_id = ""
        elif payload.collection_id is not None:
            collection_id = payload.collection_id
        if collection_id:
            _validate_collection(user.id, collection_id)
        try:
            return store().update(
                user.id,
                project_id,
                name=payload.name,
                instructions=payload.instructions,
                collection_id=collection_id,
            ).to_dict()
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Project not found.") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except ApexError as error:
            raise APIError.from_apex(error, status_code=503) from error

    @router.delete("/{project_id}", response_model=DeletedOut)
    def delete_project(project_id: str, user=Depends(require_user)):
        try:
            deleted = store().delete(user.id, project_id)
        except ApexError as error:
            raise APIError.from_apex(error, status_code=503) from error
        if not deleted:
            raise HTTPException(status_code=404, detail="Project not found.")
        # The project is gone; every conversation that pointed at it leaves
        # the project rather than referencing a dangling ID (same precedent
        # as deleting a collection, Phase 66's unassign_collection).
        conversations.unassign_project(user.id, project_id)
        return {"deleted": True}

    return router


__all__ = ["create_projects_router"]
