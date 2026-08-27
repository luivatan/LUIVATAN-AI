"""FastAPI interface (optional alternative to the Gradio UI).

Run with:  python -m apex_ai.api.server
Docs at:   http://localhost:7861/docs

Same ApexServices container as the UI, so behavior and configuration are
identical. Endpoints:

    GET    /health                 readiness + counts
    GET    /models                 discovered local models
    POST   /models/select          {"name": "..."}  validate + switch model
    GET    /documents              indexed documents
    POST   /documents/ingest       {"path": "..."}  ingest one file
    DELETE /documents/{id}         remove from index
    POST   /query                  {"question": "...", "use_memory": true}
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from apex_ai import APP_NAME, __version__
from apex_ai.core.errors import ApexError
from apex_ai.models.manager import ModelManager
from apex_ai.runtime import ApexServices, build_services


# NOTE: request models live at module level. With `from __future__ import
# annotations`, FastAPI resolves parameter annotations by name in this
# module's namespace — a model defined inside create_api would not resolve.
class ModelSelection(BaseModel):
    name: str = Field(min_length=1)


class IngestRequest(BaseModel):
    path: str = Field(min_length=1)
    force: bool = False


class QueryRequest(BaseModel):
    question: str = Field(min_length=1)
    use_memory: bool = True


def create_api(services: ApexServices | None = None) -> FastAPI:
    services = services or build_services()
    app = FastAPI(title=APP_NAME, version=__version__)

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
        return [d.as_dict() for d in services.ingestion.list_documents()]

    @app.post("/documents/ingest")
    def ingest(payload: IngestRequest):
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
        _ensure_ready()
        try:
            result = services.engine.ask(payload.question, use_memory=payload.use_memory)
            return {
                "answer": result.answer,
                "insufficient_evidence": result.insufficient_evidence,
                "confidence": result.confidence,
                "citations": [c.to_dict() for c in result.citations],
                "queries_used": result.queries_used,
                "timings": result.timings,
            }
        except ApexError as error:
            raise HTTPException(status_code=500, detail=error.user_message()) from error

    return app


def main() -> None:
    import uvicorn

    services = build_services()
    app = create_api(services)
    uvicorn.run(
        app,
        host=services.settings.server_name,
        port=services.settings.server_port + 1,  # UI on 7860, API on 7861
        log_level="info",
    )


if __name__ == "__main__":
    main()
