"""Safe browser-upload adapter for the existing IngestionService."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, UploadFile

from apex_ai.api.errors import APIError, service_not_ready_error
from apex_ai.core.logging import get_logger
from apex_ai.documents.extraction import SUPPORTED_EXTENSIONS
from apex_ai.security.files import sanitize_filename

log = get_logger("api.uploads")


def create_upload_router(services) -> APIRouter:
    router = APIRouter(tags=["documents"])

    @router.post("/documents/upload")
    async def upload_document(file: Annotated[UploadFile, File()]):
        staging: Path | None = None
        try:
            if not services.ready:
                raise service_not_ready_error()

            safe_name = sanitize_filename(file.filename or "document")
            suffix = Path(safe_name).suffix.lower()
            if suffix not in SUPPORTED_EXTENSIONS:
                raise APIError(
                    415,
                    (
                        f"Unsupported file type `{suffix or '(none)'}`. "
                        "Upload PDF, TXT, Markdown, or JSON."
                    ),
                    code="unsupported_file_type",
                )

            max_bytes = services.settings.max_upload_mb * 1024 * 1024
            staging = services.settings.upload_dir / ".staging" / str(uuid.uuid4())
            staged_file = staging / safe_name
            staging.mkdir(parents=True, exist_ok=False)
            size = 0
            with staged_file.open("wb") as destination:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise APIError(
                            413,
                            (
                                f"`{safe_name}` exceeds the "
                                f"{services.settings.max_upload_mb} MB upload limit. "
                                "Change APEX_MAX_UPLOAD_MB only if this machine can safely "
                                "process larger files."
                            ),
                            code="upload_too_large",
                        )
                    destination.write(chunk)
            if size == 0:
                raise APIError(
                    400,
                    "The uploaded file is empty.",
                    code="empty_upload",
                )

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
        finally:
            try:
                await file.close()
            except Exception:  # noqa: BLE001 - framework-owned cleanup boundary
                log.warning("Could not close an uploaded file handle")
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)

    @router.post("/documents/{document_id}/reindex")
    def reindex_document(document_id: str):
        if not services.ready:
            raise service_not_ready_error()
        result = services.ingestion.reindex(document_id)
        return {
            "status": result.status,
            "document_id": result.document_id,
            "chunks": result.chunks,
            "message": result.message,
            "warnings": result.warnings,
        }

    return router
