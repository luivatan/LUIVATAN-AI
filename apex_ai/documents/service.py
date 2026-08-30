"""Document ingestion service.

One place that owns the full ingest workflow::

    file -> copy into uploads dir -> sha256 -> duplicate check
         -> extract (pages preserved) -> structure-aware chunking
         -> embed + upsert into ChromaDB -> update registry

It preserves two useful behaviors from the old project:

- the medical-keyword heuristic that warns when an uploaded document does not
  look medical (the app is medical-first), and
- sha256-based duplicate detection (now at document level via the registry,
  plus idempotent chunk IDs at the vector level).
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from apex_ai.core.errors import ApexError, DocumentProcessingError
from apex_ai.core.logging import get_logger, timed
from apex_ai.documents.chunking import Chunker
from apex_ai.documents.extraction import extract_document, supported
from apex_ai.documents.models import utc_now_iso
from apex_ai.security.files import (
    ensure_within,
    restrict_to_owner,
    sanitize_filename,
    sha256_file,
)

log = get_logger("ingest")

# Preserved from the original project (kept identical on purpose).
MEDICAL_KEYWORDS = {
    "anatomy", "antibiotic", "blood", "cardiac", "care", "cell", "clinical",
    "diagnosis", "disease", "doctor", "dose", "drug", "health", "hospital",
    "infection", "injury", "lab", "medical", "medicine", "nurse", "patient",
    "pharmacology", "physician", "prescription", "symptom", "therapy",
    "treatment", "vaccine",
}
MEDICAL_WARNING = (
    "This document does not look medical. Apex AI is designed for medical "
    "documents and may be less reliable for other content."
)


def is_likely_medical_document(text: str, threshold: int = 3) -> bool:
    lowered = text.lower()
    matches = sum(1 for keyword in MEDICAL_KEYWORDS if keyword in lowered)
    return matches >= threshold


@dataclass
class IngestResult:
    status: str  # "indexed" | "duplicate" | "empty"
    document_name: str
    document_id: str = ""
    chunks: int = 0
    warnings: list[str] = field(default_factory=list)
    message: str = ""
    # Phase 68: another indexed document with the same name (and different
    # content - this is never the duplicate-content case) already existed
    # when this one was indexed. Never acted on automatically - surfaced so
    # the caller can offer to remove the stale prior version via the
    # existing delete endpoint.
    previous_version_id: str | None = None


@dataclass
class DocumentInfo:
    """Registry entry for the document manager UI."""

    document_id: str
    name: str
    path: str
    chunks: int
    pages: int
    file_type: str
    created_at: str
    empty_pages: int = 0
    looks_medical: bool = True
    # Phase 55: defaults to "" so a pre-Phase-55 registry file (entries with no
    # owner yet) still loads; backfill_owner() assigns those to a real account.
    user_id: str = ""
    # Phase 66: "" means no collection (the whole-library default, and every
    # document before this phase existed).
    collection_id: str = ""

    def as_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "name": self.name,
            "path": self.path,
            "chunks": self.chunks,
            "pages": self.pages,
            "file_type": self.file_type,
            "created_at": self.created_at,
            "empty_pages": self.empty_pages,
            "looks_medical": self.looks_medical,
            "user_id": self.user_id,
            "collection_id": self.collection_id or None,
        }


class IngestionService:
    def __init__(self, settings, store) -> None:
        self.settings = settings
        self.store = store
        self.chunker = Chunker(settings)
        self.registry_path = settings.database_path.parent / "document_registry.json"
        # Phase 55: content-derived document_id alone is not a unique key once
        # two accounts can each hold their own copy of identical bytes -
        # every entry is scoped by (document_id, user_id).
        self._registry: dict[tuple[str, str], DocumentInfo] = {}
        self._load_registry()

    # -- registry (small JSON file: id -> DocumentInfo) -----------------------

    def _load_registry(self) -> None:
        import json

        if not self.registry_path.exists():
            return
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
            for item in data:
                info = DocumentInfo(**item)
                self._registry[(info.document_id, info.user_id)] = info
        except (json.JSONDecodeError, OSError, TypeError) as error:
            log.warning(
                "Document registry unreadable; starting fresh (error_type=%s).",
                type(error).__name__,
            )
            self._registry = {}

    def _save_registry(self) -> None:
        import json

        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [info.as_dict() for info in self._registry.values()]
        self.registry_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def backfill_owner(self, user_id: str) -> int:
        """Assign every pre-Phase-55 registry entry (``user_id==""``) to
        ``user_id``. Idempotent, same precedent as ``ChromaVectorStore``'s and
        the conversation/memory stores' ``backfill_owner``."""
        updated: dict[tuple[str, str], DocumentInfo] = {}
        changed = 0
        for (document_id, owner), info in self._registry.items():
            if not owner:
                info.user_id = user_id
                owner = user_id
                changed += 1
            updated[(document_id, owner)] = info
        if changed:
            self._registry = updated
            self._save_registry()
        return changed

    # -- ingest ---------------------------------------------------------------

    def ingest_path(
        self,
        path: str | Path,
        user_id: str,
        force: bool = False,
        collection_id: str = "",
    ) -> IngestResult:
        """Ingest one supported file into ``user_id``'s library, optionally
        into one named collection (Phase 66; ``""`` = no collection).

        `force=True` re-indexes an existing doc. Dedup is per-account: two
        accounts uploading identical bytes each get their own indexed copy
        (Phase 55) — a global content-hash dedup would mean the second
        account's upload silently attached to the first account's document,
        which is exactly the cross-account leak this phase closes.
        """
        source = Path(path)
        if not source.is_file():
            raise DocumentProcessingError(
                what=f"File not found: `{source}`",
                fix="Check the path, or upload the file through the UI.",
            )
        if not supported(source):
            raise DocumentProcessingError(
                what=f"Unsupported file type: `{source.suffix}`.",
                why="Supported types: PDF, TXT, Markdown, JSON.",
                fix="Convert the file to a supported format and try again.",
            )

        # Hash before copying so a duplicate upload cannot overwrite a managed file.
        # If two different files share a name, retain both with a short content-hash
        # suffix instead of silently making the older registry entry point at new bytes.
        user_upload_dir = self.settings.upload_dir / user_id
        user_upload_dir.mkdir(parents=True, exist_ok=True)
        restrict_to_owner(user_upload_dir)
        safe_name = sanitize_filename(source.name)
        document_id = sha256_file(source)

        if not force and self.store.has_document(document_id, user_id):
            log.info("Duplicate document skipped: %s", document_id[:12])
            return IngestResult(
                status="duplicate",
                document_name=safe_name,
                document_id=document_id,
                message=f"'{safe_name}' is already indexed (identical file content). "
                        "Use Re-index to force a rebuild.",
            )

        destination = ensure_within(user_upload_dir, user_upload_dir / safe_name)
        if source.resolve() != destination and destination.exists():
            if sha256_file(destination) != document_id:
                destination = ensure_within(
                    user_upload_dir,
                    user_upload_dir
                    / f"{destination.stem}-{document_id[:8]}{destination.suffix}",
                )
        if source.resolve() != destination:
            shutil.copy2(source, destination)
        restrict_to_owner(destination)

        with timed(log, "document ingestion", level=logging.INFO):
            document = extract_document(
                destination,
                max_pages=self.settings.max_document_pages,
                max_csv_rows=self.settings.max_csv_rows,
            )
            chunks = self.chunker.build_chunks(document)
            if not chunks:
                return IngestResult(
                    status="empty",
                    document_name=safe_name,
                    document_id=document_id,
                    message=f"'{safe_name}' produced no indexable text chunks.",
                )

            if force:
                self.store.delete_document(document_id, user_id)

            # Chunk IDs are otherwise pure content hashes (document_id:seq),
            # so two accounts ingesting identical bytes would collide on the
            # same Chroma row ID and silently overwrite each other's chunk.
            # Scoping both the metadata and the ID by user_id keeps every
            # account's copy in the shared collection distinct.
            for chunk in chunks:
                chunk.metadata["user_id"] = user_id
                scoped_id = f"{user_id}:{chunk.chunk_id}"
                chunk.metadata["chunk_id"] = scoped_id
                chunk.chunk_id = scoped_id

            self.store.upsert_chunks(chunks)

            looks_medical = is_likely_medical_document(document.full_text())
            warnings = [] if looks_medical else [MEDICAL_WARNING]

            self._registry[(document_id, user_id)] = DocumentInfo(
                document_id=document_id,
                name=safe_name,
                path=str(destination),
                chunks=len(chunks),
                pages=document.page_count,
                file_type=document.file_type,
                created_at=utc_now_iso(),
                empty_pages=len(document.empty_pages),
                looks_medical=looks_medical,
                user_id=user_id,
                collection_id=collection_id,
            )
            self._save_registry()

        message = f"Indexed {len(chunks)} chunk(s) from '{safe_name}' "
        message += f"({document.page_count} page(s))."
        if warnings:
            message += "\nWarning: " + warnings[0]
        previous_version = self.find_by_name(user_id, safe_name, exclude_document_id=document_id)
        if previous_version is not None:
            message += (
                f"\nA document also named '{safe_name}' is already indexed "
                "(likely an older version) - remove it if this replaces it."
            )
        return IngestResult(
            status="indexed",
            document_name=safe_name,
            document_id=document_id,
            chunks=len(chunks),
            warnings=warnings,
            message=message,
            previous_version_id=previous_version.document_id if previous_version else None,
        )

    # -- management ---------------------------------------------------------------

    def reindex(self, document_id: str, user_id: str) -> IngestResult:
        info = self._registry.get((document_id, user_id))
        if not info or not Path(info.path).is_file():
            raise DocumentProcessingError(
                what=f"Cannot re-index document {document_id[:12]} — the original file is "
                    "no longer available.",
                why="The registry points to a file that was moved or deleted.",
                fix="Upload the file again.",
            )
        # Preserve the existing collection assignment - re-indexing is a
        # content refresh, not a reason to un-organize the document.
        return self.ingest_path(
            info.path, user_id, force=True, collection_id=info.collection_id
        )

    def remove(self, document_id: str, user_id: str) -> str:
        removed = self.store.delete_document(document_id, user_id)
        self._registry.pop((document_id, user_id), None)
        self._save_registry()
        return f"Removed document {document_id[:12]} ({removed} chunk(s) deleted)."

    def list_documents(
        self, user_id: str, collection_id: str | None = None
    ) -> list[DocumentInfo]:
        """All of ``user_id``'s documents, or (Phase 66) only those in one
        collection when ``collection_id`` is given - including ``""`` to
        mean specifically the uncategorized ones, distinct from ``None``
        meaning "don't filter at all"."""
        matches = (
            info
            for (_, owner), info in self._registry.items()
            if owner == user_id
            and (collection_id is None or info.collection_id == collection_id)
        )
        return sorted(matches, key=lambda i: i.name.lower())

    def document_ids_for_collection(self, user_id: str, collection_id: str) -> list[str]:
        """Document IDs to restrict retrieval to for a collection-scoped
        conversation (Phase 67)."""
        return [info.document_id for info in self.list_documents(user_id, collection_id)]

    def find_by_name(
        self, user_id: str, name: str, exclude_document_id: str = ""
    ) -> DocumentInfo | None:
        """The most recently indexed *other* document owned by ``user_id``
        with this exact name, if any (Phase 68: a same-named, different-
        content upload is very likely a new version of the same logical
        document). Never acts on this by itself - see ``ingest_path``'s
        ``previous_version_id``."""
        matches = [
            info
            for (document_id, owner), info in self._registry.items()
            if owner == user_id and info.name == name and document_id != exclude_document_id
        ]
        if not matches:
            return None
        return max(matches, key=lambda info: info.created_at)

    def move_to_collection(
        self, document_id: str, user_id: str, collection_id: str
    ) -> DocumentInfo:
        """Reassign (or, with ``collection_id=""``, unassign) one document.
        A pure registry update - no re-ingestion, no Chroma writes."""
        info = self._registry.get((document_id, user_id))
        if info is None:
            raise KeyError(document_id)
        info.collection_id = collection_id
        self._save_registry()
        return info

    def unassign_collection(self, user_id: str, collection_id: str) -> int:
        """Clear every document's reference to a collection that's being
        deleted (Phase 66) - the documents themselves are untouched, they
        just become uncategorized again."""
        if not collection_id:
            return 0
        changed = 0
        for (_, owner), info in self._registry.items():
            if owner == user_id and info.collection_id == collection_id:
                info.collection_id = ""
                changed += 1
        if changed:
            self._save_registry()
        return changed

    def storage_bytes(self, user_id: str) -> int:
        """Real on-disk size of every document ``user_id`` owns (Phase
        87-88's storage-capacity check reads this). Not a stored/cached
        figure - the file on disk is the actual thing consuming storage,
        so a moved/deleted file (``OSError``) simply contributes nothing
        rather than raising and blocking the check."""
        total = 0
        for info in self.list_documents(user_id):
            try:
                total += Path(info.path).stat().st_size
            except OSError:
                continue
        return total

    def stats(self, user_id: str | None = None) -> dict:
        """Document/chunk counts. ``user_id=None`` is the whole instance's
        total, for system-wide diagnostics (health checks) only."""
        if user_id is None:
            return {"documents": len(self._registry), "chunks": self.store.count()}
        return {
            "documents": len(self.list_documents(user_id)),
            "chunks": self.store.count(user_id),
        }


__all__ = [
    "IngestionService",
    "IngestResult",
    "DocumentInfo",
    "is_likely_medical_document",
    "MEDICAL_KEYWORDS",
    "MEDICAL_WARNING",
    "ApexError",
]
