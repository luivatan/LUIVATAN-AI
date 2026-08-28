"""FastAPI backend shared by the Apex AI browser app and API clients.

The established RAG/LLM services remain the source of truth. This layer adds only
transport concerns: document upload, persistent conversation CRUD, NDJSON streaming,
and static delivery of the chat interface.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from apex_ai import APP_NAME, __version__
from apex_ai.api.chat import GenerationManager, create_chat_router
from apex_ai.api.uploads import create_upload_router
from apex_ai.core.errors import ApexError
from apex_ai.memory.conversations import ConversationStore
from apex_ai.models.manager import ModelManager
from apex_ai.runtime import ApexServices, build_services


# Request models stay at module scope. FastAPI resolves postponed annotations from
# this namespace (local models become unresolved ForwardRefs on Python 3.11).
class ModelSelection(BaseModel):
    name: str = Field(min_length=1)


class IngestRequest(BaseModel):
    path: str = Field(min_length=1)
    force: bool = False


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
    app = FastAPI(
        title=APP_NAME,
        version=__version__,
        docs_url="/api/docs" if include_web else "/docs",
        redoc_url=None,
    )
    app.state.apex_services = services
    app.state.conversations = conversations
    app.state.generations = GenerationManager()

    def _ensure_ready() -> None:
        if not services.ready:
            raise HTTPException(status_code=503, detail=services.startup_error)

    @app.get("/health")
    def health():
        payload = {
            "app": APP_NAME,
            "version": __version__,
            "ready": services.ready,
            "provider": services.settings.llm_provider,
            "model": services.settings.model_path or None,
            "embedding_model": services.embeddings.name if services.embeddings else None,
        }
        if services.ready:
            payload.update(services.ingestion.stats())
        if services.startup_error:
            payload["startup_error"] = services.startup_error
        return payload

    @app.get("/app-config")
    def app_config():
        stats = services.ingestion.stats() if services.ingestion else {"documents": 0, "chunks": 0}
        configured_model = services.settings.model_path
        if configured_model:
            configured_model = Path(configured_model).name
        elif services.settings.llm_provider == "ollama":
            configured_model = services.settings.ollama_model
        elif services.settings.llm_provider in {"openai", "openai_compatible"}:
            configured_model = services.settings.openai_model
        elif services.settings.llm_provider == "transformers":
            configured_model = services.settings.hf_model_path
        return {
            "app": APP_NAME,
            "version": __version__,
            "ready": services.ready,
            "startup_error": services.startup_error or None,
            "provider": services.settings.llm_provider,
            "model": configured_model or None,
            "embedding_model": services.embeddings.name if services.embeddings else None,
            "reranker": getattr(services.reranker, "name", None),
            "max_upload_mb": services.settings.max_upload_mb,
            **stats,
        }

    @app.get("/models")
    def models():
        manager = ModelManager(services.settings)
        return [vars(entry) | {"path": str(entry.path)} for entry in manager.discover()]

    @app.post("/models/select")
    def select_model(payload: ModelSelection):
        try:
            path = services.select_model(payload.name)
            return {"selected": path}
        except ApexError as error:
            raise HTTPException(status_code=400, detail=error.user_message()) from error

    @app.get("/documents")
    def documents():
        if not services.ingestion:
            raise HTTPException(status_code=503, detail=services.startup_error)
        return [document.as_dict() for document in services.ingestion.list_documents()]

    @app.post("/documents/ingest")
    def ingest(payload: IngestRequest):
        """Local automation endpoint. Browsers use safe multipart /documents/upload."""
        _ensure_ready()
        try:
            result = services.ingestion.ingest_path(payload.path, force=payload.force)
            return {
                "status": result.status,
                "document_id": result.document_id,
                "chunks": result.chunks,
                "message": result.message,
                "warnings": result.warnings,
            }
        except ApexError as error:
            raise HTTPException(status_code=400, detail=error.user_message()) from error

    @app.delete("/documents/{document_id}")
    def delete_document(document_id: str):
        _ensure_ready()
        try:
            return {"message": services.ingestion.remove(document_id)}
        except ApexError as error:
            raise HTTPException(status_code=400, detail=error.user_message()) from error

    @app.post("/query")
    def query(payload: QueryRequest):
        """Backward-compatible non-streaming endpoint."""
        _ensure_ready()
        try:
            result = services.engine.ask(payload.question, use_memory=payload.use_memory)
            return {
                "answer": result.answer,
                "insufficient_evidence": result.insufficient_evidence,
                "confidence": result.confidence,
                "citations": [citation.to_dict() for citation in result.citations],
                "queries_used": result.queries_used,
                "timings": result.timings,
            }
        except ApexError as error:
            raise HTTPException(status_code=500, detail=error.user_message()) from error

    if services.settings.rag_debug:
        # Developer-only diagnostics: no UI link, excluded from OpenAPI, and
        # the route does not exist at all unless explicitly enabled by env.
        @app.post("/debug/rag", include_in_schema=False)
        def debug_rag(payload: RagDebugRequest):
            _ensure_ready()
            try:
                return services.engine.debug(
                    payload.question,
                    use_memory=payload.use_memory,
                    generate=payload.generate,
                )
            except ApexError as error:
                raise HTTPException(status_code=500, detail=error.user_message()) from error

    app.include_router(create_upload_router(services))
    app.include_router(create_chat_router(services, conversations, app.state.generations))

    @app.delete("/conversations")
    def delete_all_conversations():
        return {"deleted": conversations.clear()}

    if include_web:
        from apex_ai.web.app import mount_web_ui

        mount_web_ui(app)
    return app


def main() -> None:
    from apex_ai.web.app import launch

    launch()


if __name__ == "__main__":
    main()
