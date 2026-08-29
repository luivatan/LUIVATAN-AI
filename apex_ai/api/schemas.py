"""Shared response schemas for the Apex API.

Phase 7 (API Structure) adds these so ``/api/docs`` publishes accurate response
shapes and FastAPI validates outgoing payloads against them, instead of every
route returning an undeclared ``dict``. Fields mirror the existing dataclasses
(``Conversation``, ``Message``, ``DocumentInfo``, ``ModelEntry``, ``IngestResult``)
exactly; nothing here changes what those dataclasses contain.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class MessageOut(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    citations: list[dict[str, Any]]
    status: str
    created_at: str
    feedback: str | None = None


class ConversationOut(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int
    preview: str
    collection_id: str | None = None


class ConversationDetailOut(ConversationOut):
    messages: list[MessageOut]


class DeletedOut(BaseModel):
    deleted: bool


class RemovedOut(BaseModel):
    message: str


class DeletedCountOut(BaseModel):
    deleted: int


class StopOut(BaseModel):
    stopping: bool


class LongTermMemoryOut(BaseModel):
    id: str
    kind: str
    content: str
    created_at: str
    updated_at: str


class MemoryCandidateOut(BaseModel):
    id: str
    kind: str
    content: str
    rule: str
    created_at: str
    expires_at: str
    conflicts_with: LongTermMemoryOut | None = None


class ApproveMemoryOut(BaseModel):
    approved: bool
    memory: dict[str, Any]


class RejectMemoryOut(BaseModel):
    rejected: bool


class DocumentOut(BaseModel):
    document_id: str
    name: str
    path: str
    chunks: int
    pages: int
    file_type: str
    created_at: str
    empty_pages: int
    looks_medical: bool
    collection_id: str | None = None


class CollectionOut(BaseModel):
    id: str
    name: str
    created_at: str
    updated_at: str


class ModelEntryOut(BaseModel):
    name: str
    path: str
    model_type: str
    size: str
    provider: str
    status: str
    active: bool
    loadable: bool


class ModelSelectOut(BaseModel):
    selected: str


class IngestOut(BaseModel):
    status: str
    document_id: str
    chunks: int
    message: str
    warnings: list[str]
    previous_version_id: str | None = None


class UploadOut(IngestOut):
    document_name: str
    size_bytes: int


class LongTermMemoryStatusOut(BaseModel):
    status: str
    optional: bool
    prompt_use: bool


class ComponentStatusOut(BaseModel):
    status: str
    detail: str | None = None


class LlmStatusOut(BaseModel):
    configured: bool
    provider: str
    note: str


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str
    created_at: str
    is_default_local: bool


class HealthOut(BaseModel):
    app: str
    version: str
    ready: bool
    provider: str
    model: str | None
    embedding_model: str | None
    database: ComponentStatusOut
    llm: LlmStatusOut
    long_term_memory: LongTermMemoryStatusOut
    documents: int | None = None
    chunks: int | None = None
    startup_error: str | None = None


class AppConfigOut(BaseModel):
    app: str
    version: str
    ready: bool
    startup_error: str | None
    provider: str
    model: str | None
    embedding_model: str | None
    reranker: str | None
    max_upload_mb: int
    documents: int
    chunks: int


class QueryOut(BaseModel):
    answer: str
    insufficient_evidence: bool
    confidence: float
    citations: list[dict[str, Any]]
    queries_used: list[str]
    timings: dict[str, Any]


__all__ = [
    "AppConfigOut",
    "ApproveMemoryOut",
    "ComponentStatusOut",
    "ConversationDetailOut",
    "ConversationOut",
    "DeletedCountOut",
    "DeletedOut",
    "DocumentOut",
    "HealthOut",
    "IngestOut",
    "LlmStatusOut",
    "LongTermMemoryOut",
    "LongTermMemoryStatusOut",
    "MemoryCandidateOut",
    "MessageOut",
    "ModelEntryOut",
    "ModelSelectOut",
    "QueryOut",
    "RejectMemoryOut",
    "RemovedOut",
    "StopOut",
    "UploadOut",
    "UserOut",
]
