"""FastAPI backend shared by the Apex AI browser app and API clients.

The established RAG/LLM services remain the source of truth. This layer adds only
transport concerns: document upload, persistent conversation CRUD, NDJSON streaming,
and static delivery of the chat interface.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from apex_ai import APP_NAME, __version__
from apex_ai.api.auth import create_auth_router, make_require_user_dependency
from apex_ai.api.chat import GenerationManager, create_chat_router
from apex_ai.api.collections import create_collections_router
from apex_ai.api.errors import APIError, install_error_handlers, service_not_ready_error
from apex_ai.api.memory import create_memory_router
from apex_ai.api.projects import create_projects_router
from apex_ai.api.rate_limit import install_rate_limiting
from apex_ai.api.schemas import (
    AppConfigOut,
    DeletedCountOut,
    DocumentOut,
    HealthOut,
    IngestOut,
    ModelEntryOut,
    ModelSelectOut,
    QueryOut,
    RecommendedModelOut,
    RemovedOut,
)
from apex_ai.api.uploads import create_upload_router
from apex_ai.core.logging import get_logger, log_event
from apex_ai.memory.conversations import ConversationStore
from apex_ai.models.manager import ModelManager
from apex_ai.runtime import ApexServices, build_services

log = get_logger("api.health")


# Request models stay at module scope. FastAPI resolves postponed annotations from
# this namespace (local models become unresolved ForwardRefs on Python 3.11).
class ModelSelection(BaseModel):
    name: str = Field(min_length=1)


class IngestRequest(BaseModel):
    path: str = Field(min_length=1)
    force: bool = False
    collection_id: str | None = None


class DocumentCollectionUpdate(BaseModel):
    collection_id: str | None = None


class QueryRequest(BaseModel):
    question: str = Field(min_length=1)
    use_memory: bool = True


class RagDebugRequest(QueryRequest):
    generate: bool = True


def create_api(
    services: ApexServices | None = None,
    *,
    conversations: ConversationStore | None = None,
    include_web: bool = True,
) -> FastAPI:
    services = services or build_services()
    conversations = conversations or ConversationStore(services.settings.conversation_db_path)
    # Phase 55: pre-Phase-55 conversations have no owner yet; assign them to the
    # default local account, same precedent as long_term_memory.backfill_owner
    # (runtime.py, where LongTermMemoryStore lives). The document store's own
    # backfill runs in runtime.py for the same reason: that's where it's built.
    if services.default_local_user is not None:
        conversations.backfill_owner(services.default_local_user.id)
    app = FastAPI(
        title=APP_NAME,
        version=__version__,
        docs_url="/api/docs" if include_web else "/docs",
        redoc_url=None,
    )
    install_error_handlers(app)
    install_rate_limiting(app, services.settings)
    if services.settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[
                origin.strip()
                for origin in services.settings.cors_allowed_origins.split(",")
                if origin.strip()
            ],
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Content-Type"],
        )
    app.state.apex_services = services
    app.state.conversations = conversations
    app.state.generations = GenerationManager()

    def _ensure_ready() -> None:
        if not services.ready:
            raise service_not_ready_error()

    def _configured_model_name() -> str:
        """The model name the active provider will try to use, independent of
        whether it has actually been verified reachable (see ``_database_status``
        and the ``llm`` health field for what *is* checked live)."""
        configured_model = services.settings.model_path
        if configured_model:
            return Path(configured_model).name
        if services.settings.llm_provider == "ollama":
            return services.settings.ollama_model
        if services.settings.llm_provider in {"openai", "openai_compatible"}:
            return services.settings.openai_model
        if services.settings.llm_provider == "transformers":
            return services.settings.hf_model_path
        return ""

    def _database_status() -> dict:
        """A cheap, synchronous liveness probe for the vector store.

        ``services.ready`` reflects whether the store was constructed at
        *startup*; it is not re-checked afterward. Counting the collection is a
        real read against the same ChromaDB handle the RAG engine queries, so a
        DB file removed, corrupted, or made unreadable after startup is caught
        here instead of only surfacing as a confusing failure mid-chat.
        """
        if services.store is None:
            return {"status": "unavailable", "detail": "not_initialized"}
        try:
            services.store.count()
        except Exception as error:  # noqa: BLE001 - health probe boundary
            log_event(
                log,
                logging.WARNING,
                "health.database_probe_failed",
                "Health check could not reach the vector store",
                error_type=type(error).__name__,
            )
            return {"status": "unavailable", "detail": type(error).__name__}
        return {"status": "ok", "detail": None}

    def _llm_status() -> dict:
        """Reports whether a model is *configured*, not whether it is reachable.

        Verifying reachability would mean a real generation/network call on
        every health check (slow, and for remote providers a paid or
        rate-limited request) — Apex validates the provider at question time
        instead, with a specific actionable error. Faking a "connected" result
        here would violate the no-fake-status rule as much as skipping the
        check entirely, so this field says exactly what it does and does not
        verify.
        """
        provider = services.settings.llm_provider
        return {
            "configured": bool(_configured_model_name()),
            "provider": provider,
            "note": "Configuration only; connectivity is verified when a question is asked.",
        }

    @app.get("/health", response_model=HealthOut, response_model_exclude_none=True)
    def health(response: Response):
        database = _database_status()
        overall_ready = services.ready and database["status"] == "ok"
        payload = {
            "app": APP_NAME,
            "version": __version__,
            "ready": overall_ready,
            "provider": services.settings.llm_provider,
            "model": services.settings.model_path or None,
            "embedding_model": services.embeddings.name if services.embeddings else None,
            "database": database,
            "llm": _llm_status(),
            "long_term_memory": {
                "status": "ready"
                if services.long_term_memory is not None
                else "unavailable",
                "optional": True,
                # Phase 47: reflects the real configured/wired state instead of
                # the hardcoded False this field held before that phase existed.
                "prompt_use": bool(
                    services.long_term_memory is not None
                    and getattr(services.settings, "memory_prompt_use", True)
                ),
            },
        }
        if overall_ready:
            payload.update(services.ingestion.stats())
        if services.startup_error:
            payload["startup_error"] = services.startup_error
        response.status_code = 200 if overall_ready else 503
        return payload

    @app.get("/app-config", response_model=AppConfigOut)
    def app_config():
        stats = services.ingestion.stats() if services.ingestion else {"documents": 0, "chunks": 0}
        return {
            "app": APP_NAME,
            "version": __version__,
            "ready": services.ready,
            "startup_error": services.startup_error or None,
            "provider": services.settings.llm_provider,
            "model": _configured_model_name() or None,
            "embedding_model": services.embeddings.name if services.embeddings else None,
            "reranker": getattr(services.reranker, "name", None),
            "max_upload_mb": services.settings.max_upload_mb,
            **stats,
        }

    @app.get("/models", response_model=list[ModelEntryOut])
    def models():
        manager = ModelManager(services.settings)
        return [vars(entry) | {"path": str(entry.path)} for entry in manager.discover()]

    @app.post("/models/select", response_model=ModelSelectOut)
    def select_model(payload: ModelSelection):
        path = services.select_model(payload.name)
        return {"selected": path}

    @app.get("/models/recommended", response_model=RecommendedModelOut)
    def recommended_model(task: str = "chat"):
        """Phase 79: which discovered model the router would pick for
        ``task`` - read-only, does not select or load anything itself."""
        if services.model_router is None:
            raise APIError(503, "Model routing is unavailable.", code="model_router_unavailable")
        try:
            decision = services.model_router.select(task)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        entry = None
        if decision.entry is not None:
            entry = vars(decision.entry) | {"path": str(decision.entry.path)}
        return {"task": decision.task, "reason": decision.reason, "model": entry}

    require_user = make_require_user_dependency(services)

    @app.get("/documents", response_model=list[DocumentOut])
    def documents(collection_id: str | None = None, user=Depends(require_user)):
        _ensure_ready()
        return [
            document.as_dict()
            for document in services.ingestion.list_documents(user.id, collection_id)
        ]

    @app.post("/documents/ingest", response_model=IngestOut)
    def ingest(payload: IngestRequest, user=Depends(require_user)):
        """Local automation endpoint. Browsers use safe multipart /documents/upload."""
        _ensure_ready()
        result = services.ingestion.ingest_path(
            payload.path, user.id, force=payload.force, collection_id=payload.collection_id or ""
        )
        return {
            "status": result.status,
            "document_id": result.document_id,
            "chunks": result.chunks,
            "message": result.message,
            "warnings": result.warnings,
            "previous_version_id": result.previous_version_id,
        }

    @app.delete("/documents/{document_id}", response_model=RemovedOut)
    def delete_document(document_id: str, user=Depends(require_user)):
        _ensure_ready()
        return {"message": services.ingestion.remove(document_id, user.id)}

    @app.patch("/documents/{document_id}/collection", response_model=DocumentOut)
    def move_document(
        document_id: str, payload: DocumentCollectionUpdate, user=Depends(require_user)
    ):
        _ensure_ready()
        collection_id = payload.collection_id or ""
        if collection_id and (
            services.collections is None or services.collections.get(user.id, collection_id) is None
        ):
            raise HTTPException(status_code=404, detail="Collection not found.")
        try:
            info = services.ingestion.move_to_collection(document_id, user.id, collection_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Document not found.") from error
        return info.as_dict()

    @app.post("/query", response_model=QueryOut)
    def query(payload: QueryRequest):
        """Backward-compatible non-streaming endpoint."""
        _ensure_ready()
        result = services.engine.ask(payload.question, use_memory=payload.use_memory)
        return {
            "answer": result.answer,
            "insufficient_evidence": result.insufficient_evidence,
            "confidence": result.confidence,
            "citations": [citation.to_dict() for citation in result.citations],
            "queries_used": result.queries_used,
            "timings": result.timings,
        }

    if services.settings.rag_debug:
        # Developer-only diagnostics: no UI link, excluded from OpenAPI, and
        # the route does not exist at all unless explicitly enabled by env.
        @app.post("/debug/rag", include_in_schema=False)
        def debug_rag(payload: RagDebugRequest):
            _ensure_ready()
            return services.engine.debug(
                payload.question,
                use_memory=payload.use_memory,
                generate=payload.generate,
            )

    app.include_router(create_auth_router(services))
    app.include_router(create_upload_router(services))
    app.include_router(create_memory_router(services))
    app.include_router(create_collections_router(services))
    app.include_router(create_projects_router(services, conversations))
    app.include_router(create_chat_router(services, conversations, app.state.generations))

    @app.delete("/conversations", response_model=DeletedCountOut)
    def delete_all_conversations(user=Depends(require_user)):
        return {"deleted": conversations.clear(user.id)}

    if include_web:
        from apex_ai.web.app import mount_web_ui

        mount_web_ui(app)
    return app


def main() -> None:
    from apex_ai.web.app import launch

    launch()


if __name__ == "__main__":
    main()
