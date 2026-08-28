"""Safe browser-upload adapter for the existing IngestionService."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile

from apex_ai.core.errors import ApexError
from apex_ai.core.logging import get_logger
from apex_ai.documents.extraction import SUPPORTED_EXTENSIONS
from apex_ai.security.files import sanitize_filename

log = get_logger("api.uploads")


def create_upload_router(services) -> APIRouter:
    router = APIRouter(tags=["documents"])

    @router.post("/documents/upload")
    async def upload_document(file: Annotated[UploadFile, File()]):
        if not services.ready:
            raise HTTPException(status_code=503, detail=services.startup_error)
        try:
            safe_name = sanitize_filename(file.filename or "document")
        except ApexError as error:
            raise HTTPException(status_code=400, detail=error.user_message()) from error
        suffix = Path(safe_name).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            raise HTTPException(
                status_code=415,
                detail=(
                    f"Unsupported file type `{suffix or '(none)'}`. "
                    "Upload PDF, TXT, Markdown, or JSON."
                ),
            )

        max_bytes = services.settings.max_upload_mb * 1024 * 1024
        staging = services.settings.upload_dir / ".staging" / str(uuid.uuid4())
        staged_file = staging / safe_name
        staging.mkdir(parents=True, exist_ok=False)
        size = 0
        try:
            with staged_file.open("wb") as destination:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail=(
                                f"`{safe_name}` exceeds the {services.settings.max_upload_mb} MB "
                                "upload limit. Change APEX_MAX_UPLOAD_MB if this machine can "
                                "safely process larger files."
                            ),
                        )
                    destination.write(chunk)
            if size == 0:
                raise HTTPException(status_code=400, detail="The uploaded file is empty.")
            result = services.ingestion.ingest_path(staged_file)
            return {
                "status": result.status,
                "document_id": result.document_id,
                "document_name": result.document_name,
                "chunks": result.chunks,
                "message": result.message,
                "warnings": result.warnings,
                "size_bytes": size,
            }
        except HTTPException:
            raise
        except ApexError as error:
            raise HTTPException(status_code=400, detail=error.user_message()) from error
        except Exception as error:
            log.exception("Browser upload failed")
            raise HTTPException(
                status_code=500,
                detail="The upload could not be processed. Details were written to logs/apex.log.",
            ) from error
        finally:
            await file.close()
            shutil.rmtree(staging, ignore_errors=True)

    @router.post("/documents/{document_id}/reindex")
    def reindex_document(document_id: str):
        if not services.ready:
            raise HTTPException(status_code=503, detail=services.startup_error)
        try:
            result = services.ingestion.reindex(document_id)
            return {
                "status": result.status,
                "document_id": result.document_id,
                "chunks": result.chunks,
                "message": result.message,
                "warnings": result.warnings,
            }
        except ApexError as error:
            raise HTTPException(status_code=400, detail=error.user_message()) from error

    return router
