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
    project_id: str | None = None


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


class ProjectOut(BaseModel):
    id: str
    name: str
    instructions: str
    collection_id: str | None = None
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


class RecommendedModelOut(BaseModel):
    task: str
    reason: str
    model: ModelEntryOut | None = None


class PlanLimitsOut(BaseModel):
    max_documents: int | None
    max_storage_mb: int | None
    max_collections: int | None
    max_projects: int | None
    max_messages_per_month: int | None
    max_tool_calls_per_month: int | None


class PlanOut(BaseModel):
    id: str
    name: str
    price_cents: int
    limits: PlanLimitsOut
    features: list[str]


class SubscriptionOut(BaseModel):
    user_id: str
    plan: PlanOut
    status: str
    created_at: str
    updated_at: str


class EntitlementOut(BaseModel):
    allowed: bool
    resource: str
    plan_id: str
    plan_name: str
    limit: int | None
    used: int
    remaining: int | None
    reason: str = ""


class UsageSummaryOut(BaseModel):
    subscription: SubscriptionOut
    entitlements: list[EntitlementOut]


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
    "EntitlementOut",
    "HealthOut",
    "IngestOut",
    "LlmStatusOut",
    "LongTermMemoryOut",
    "LongTermMemoryStatusOut",
    "MemoryCandidateOut",
    "MessageOut",
    "ModelEntryOut",
    "ModelSelectOut",
    "PlanLimitsOut",
    "PlanOut",
    "ProjectOut",
    "QueryOut",
    "RecommendedModelOut",
    "RejectMemoryOut",
    "RemovedOut",
    "StopOut",
    "SubscriptionOut",
    "UploadOut",
    "UsageSummaryOut",
    "UserOut",
]
