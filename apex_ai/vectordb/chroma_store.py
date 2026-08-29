"""ChromaDB persistence layer.

Responsibilities (and nothing else):
- own the persistent Chroma client + one collection
- store text, embeddings, metadata for chunks
- turn text queries into vector searches (embedding happens here so callers
  never touch the embedding provider directly)
- document-level operations: duplicate check, list, delete, stats

Design decisions worth understanding:

* **Cosine space.** New collections are created with ``hnsw:space=cosine``,
  which matches the normalized sentence-transformers vectors we store. The
  old project used Chroma's default (L2) — fine, but cosine similarity has an
  interpretable 0..1 range that we need for the low-confidence cutoff.
* **Embedding model versioning.** The collection metadata records
  ``embedding_model`` + ``embedding_dimension``. If the configured embedding
  model ever changes, queries against old vectors are meaningless — so we
  detect the mismatch and explain the rebuild instead of returning garbage.
* **Deterministic IDs** (``{sha256}:{seq}``) mean re-ingesting the same
  document is an idempotent ``upsert``, never a silent duplicate.
"""

from __future__ import annotations

from pathlib import Path

from apex_ai.core.errors import DatabaseError, EmbeddingMismatchError
from apex_ai.core.logging import get_logger, timed
from apex_ai.core.types import RetrievedChunk
from apex_ai.documents.models import Chunk

log = get_logger("vectordb")


class DocumentRecord:
    """Aggregate view of one indexed document (derived from chunk metadata)."""

    def __init__(self, document_id: str, name: str) -> None:
        self.document_id = document_id
        self.name = name
        self.pages: set[int] = set()
        self.chunks = 0

    def as_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "name": self.name,
            "pages": len(self.pages),
            "chunks": self.chunks,
        }


class ChromaVectorStore:
    def __init__(self, settings, embedding_provider, collection_name: str | None = None) -> None:
        self.settings = settings
        self.embedding = embedding_provider
        self.collection_name = collection_name or settings.collection_name

        try:
            import chromadb
        except ModuleNotFoundError as error:  # pragma: no cover
            raise DatabaseError(
                what="The `chromadb` package is not installed.",
                fix="Run: pip install -r requirements.txt",
            ) from error

        try:
            settings.database_path.mkdir(parents=True, exist_ok=True)
            self.client = chromadb.PersistentClient(path=str(settings.database_path))
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name,
                metadata=self._collection_metadata(),
            )
        except EmbeddingMismatchError:
            raise
        except Exception as error:
            raise DatabaseError(
                what=f"Could not open the vector database at {settings.database_path}.",
                why=str(error),
                fix="Check disk space and permissions. If the database is corrupted, "
                    "back it up and delete the folder to start fresh.",
            ) from error

        self._check_embedding_compatibility()
        self.version = 0  # bumped on every write; BM25 index uses it to rebuild

    # -- collection metadata / compatibility -------------------------------

    def _collection_metadata(self) -> dict:
        return {
            "hnsw:space": "cosine",
            "embedding_model": self.embedding.name,
            "embedding_dimension": self._safe_dimension(),
            "created_at": self._existing_created_at() or self._now(),
        }

    def _safe_dimension(self) -> int:
        try:
            return self.embedding.dimension
        except Exception:
            return 0

    @staticmethod
    def _now() -> str:
        from apex_ai.documents.models import utc_now_iso

        return utc_now_iso()

    def _existing_created_at(self) -> str | None:
        try:
            existing = self.client.get_collection(self.collection_name)
            return (existing.metadata or {}).get("created_at")
        except Exception:
            return None

    def _check_embedding_compatibility(self) -> None:
        metadata = self.collection.metadata or {}
        stored_model = metadata.get("embedding_model")
        stored_dim = metadata.get("embedding_dimension")

        if stored_model == self.embedding.name:
            current_dim = self._safe_dimension()
            try:
                recorded_dim = int(stored_dim or 0)
            except (TypeError, ValueError):
                recorded_dim = 0
            if self.collection.count() == 0:
                if current_dim and recorded_dim != current_dim:
                    self.collection.modify(metadata=self._collection_metadata())
                return
            if recorded_dim and current_dim and recorded_dim != int(current_dim):
                raise EmbeddingMismatchError(
                    what=(
                        f"The index records {stored_dim}-dimension vectors for embedding "
                        f"model '{stored_model}', but the available model reports "
                        f"{current_dim} dimensions."
                    ),
                    why="The model name is the same but its vector shape is incompatible.",
                    fix=(
                        "Restore the embedding build used for this index, or rebuild the "
                        "database and re-ingest the documents."
                    ),
                )
            return

        if stored_model is None and self.collection.count() == 0:
            # Fresh or legacy-empty collection: stamp current model identity.
            self.collection.modify(metadata=self._collection_metadata())
            return

        if stored_model is None:
            raise EmbeddingMismatchError(
                what=f"The existing collection '{self.collection_name}' was created by an "
                    "older Apex AI version without embedding metadata.",
                why="The old index most likely used default L2 distance and possibly a "
                    "different embedding model; mixing it with new embeddings would "
                    "corrupt retrieval quality.",
                fix="Rebuild the index: delete the database folder "
                    f"({self.settings.database_path}) or run the rebuild command, then "
                    "re-ingest your documents. Your uploaded files are kept in "
                    f"{self.settings.upload_dir}.",
            )

        raise EmbeddingMismatchError(
            what=f"The index was built with embedding model '{stored_model}', but the "
                f"current configuration uses '{self.embedding.name}'.",
            why="Embeddings from different models live in incompatible vector spaces; "
                "searching across them produces meaningless similarities.",
            fix=f"Either set APEX_EMBEDDING_MODEL={stored_model} in .env, or rebuild the "
                "index with the new model (delete the database folder and re-ingest; "
                "the ingested files are kept in the uploads folder).",
        )

    # -- write path -----------------------------------------------------------

    def backfill_owner(self, user_id: str) -> int:
        """Assign every pre-Phase-55 chunk (no ``user_id`` metadata at all) to
        ``user_id``. Idempotent: chunks that already carry an owner are left
        untouched. Mirrors ``ConversationStore``/``LongTermMemoryStore``'s
        ``backfill_owner`` — existing installations keep their indexed
        documents instead of them silently becoming unreachable once search
        starts filtering by owner."""
        try:
            result = self.collection.get(include=["metadatas"])
        except Exception as error:
            raise DatabaseError(
                what="Could not read chunk metadata for the ownership backfill.",
                why=str(error),
                fix="Check logs/apex.log.",
            ) from error

        stale_ids = []
        stale_metadatas = []
        for chunk_id, metadata in zip(result.get("ids", []), result.get("metadatas", [])):
            metadata = metadata or {}
            if not metadata.get("user_id"):
                metadata["user_id"] = user_id
                stale_ids.append(chunk_id)
                stale_metadatas.append(metadata)
        if stale_ids:
            self.collection.update(ids=stale_ids, metadatas=stale_metadatas)
        return len(stale_ids)

    def upsert_chunks(self, chunks: list[Chunk]) -> int:
        """Embed + persist chunks. Returns the number stored."""
        if not chunks:
            return 0
        try:
            # Section headings carry retrieval meaning (for example, a query
            # may name a policy heading whose body uses only pronouns). Embed
            # them with the body while storing only the exact source chunk, so
            # citations and the source viewer never display synthetic text.
            index_texts = [
                "\n".join(part for part in (str(c.metadata.get("section", "")), c.text) if part)
                for c in chunks
            ]
            with timed(log, f"embedding {len(chunks)} chunk(s)"):
                embeddings = self.embedding.embed_documents(index_texts)
            self.collection.upsert(
                ids=[c.chunk_id for c in chunks],
                documents=[c.text for c in chunks],
                embeddings=embeddings,
                metadatas=[dict(c.metadata) for c in chunks],
            )
        except EmbeddingMismatchError:
            raise
        except Exception as error:
            raise DatabaseError(
                what=f"Failed to store {len(chunks)} chunk(s) in the vector database.",
                why=str(error),
                fix="Check disk space and the logs. If the problem persists, rebuild "
                    "the database folder.",
            ) from error

        self.version += 1
        log.info("Upserted %d chunk(s) into '%s'", len(chunks), self.collection_name)
        return len(chunks)

    # -- read path ------------------------------------------------------------

    def search(
        self,
        query_text: str,
        user_id: str,
        k: int = 5,
        document_ids: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        """Vector search scoped to ``user_id``; sorted by cosine similarity.

        ``document_ids`` (Phase 67) further restricts the search to one
        knowledge-base collection's documents, when a conversation has one
        selected; ``None`` (the default) searches the whole account library.
        """
        if document_ids is not None and not document_ids:
            return []  # Chroma's $in rejects an empty list; zero IDs = zero matches anyway
        count = self.count(user_id)
        if count == 0:
            return []
        k = min(k, count)
        where = {"user_id": user_id}
        if document_ids is not None:
            where = {"$and": [where, {"document_id": {"$in": document_ids}}]}
        query_embedding = self.embedding.embed_query(query_text)
        try:
            result = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=k,
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as error:
            raise DatabaseError(
                what="Vector search failed.",
                why=str(error),
                fix="Check logs/apex.log; if the database is corrupted, rebuild it.",
            ) from error

        chunks = []
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        ids = result.get("ids", [[]])[0]
        distances = result.get("distances", [[]])[0]
        for doc_id, text, metadata, distance in zip(ids, documents, metadatas, distances):
            metadata = metadata or {}
            # cosine distance = 1 - cosine similarity
            chunks.append(
                RetrievedChunk(
                    chunk_id=doc_id,
                    text=text,
                    metadata=metadata,
                    similarity=1.0 - float(distance),
                )
            )
        return chunks

    def get_all_chunks(self, user_id: str) -> list[RetrievedChunk]:
        """Every chunk owned by ``user_id`` (used to build the BM25 keyword index)."""
        try:
            result = self.collection.get(
                where={"user_id": user_id}, include=["documents", "metadatas"]
            )
        except Exception as error:
            raise DatabaseError(
                what="Could not read chunks from the vector database.",
                why=str(error),
                fix="Check logs/apex.log.",
            ) from error

        chunks = []
        for chunk_id, text, metadata in zip(
            result.get("ids", []), result.get("documents", []), result.get("metadatas", [])
        ):
            chunks.append(
                RetrievedChunk(chunk_id=chunk_id, text=text or "", metadata=metadata or {})
            )
        return chunks

    # -- document management -----------------------------------------------------

    def has_document(self, document_id: str, user_id: str) -> bool:
        try:
            result = self.collection.get(
                where={"$and": [{"document_id": document_id}, {"user_id": user_id}]}, limit=1
            )
            return bool(result.get("ids"))
        except Exception:
            # some Chroma versions raise on empty where-results
            return False

    def delete_document(self, document_id: str, user_id: str) -> int:
        where = {"$and": [{"document_id": document_id}, {"user_id": user_id}]}
        try:
            before = self.collection.count()
            self.collection.delete(where=where)
            removed = before - self.collection.count()
        except Exception as error:
            raise DatabaseError(
                what=f"Failed to delete document {document_id}.",
                why=str(error),
                fix="Check logs/apex.log.",
            ) from error
        self.version += 1
        log.info("Deleted document %s (%d chunks removed)", document_id[:12], removed)
        return removed

    def list_documents(self, user_id: str) -> list[DocumentRecord]:
        """Aggregate chunk metadata owned by ``user_id`` into one record per document."""
        try:
            result = self.collection.get(where={"user_id": user_id}, include=["metadatas"])
        except Exception as error:
            raise DatabaseError(
                what="Could not list documents.",
                why=str(error),
                fix="Check logs/apex.log.",
            ) from error

        records: dict[str, DocumentRecord] = {}
        for metadata in result.get("metadatas", []):
            if not metadata:
                continue
            doc_id = metadata.get("document_id", "unknown")
            name = metadata.get("document_name", doc_id)
            if doc_id not in records:
                records[doc_id] = DocumentRecord(doc_id, name)
            record = records[doc_id]
            record.chunks += 1
            page_start = metadata.get("page_start", metadata.get("page"))
            page_end = metadata.get("page_end", page_start)
            if page_start is not None:
                try:
                    start, end = int(page_start), int(page_end or page_start)
                    record.pages.update(range(start, end + 1))
                except (TypeError, ValueError):
                    log.warning(
                        "Ignoring invalid page metadata for document %s",
                        str(doc_id)[:12],
                    )
        return sorted(records.values(), key=lambda r: r.name.lower())

    def count(self, user_id: str | None = None) -> int:
        """Chunk count. ``user_id=None`` is the whole instance's total — only
        for system-wide diagnostics (health checks); it must never back a
        per-user search bound, which always passes a real ``user_id``."""
        try:
            if user_id is None:
                return self.collection.count()
            return len(self.collection.get(where={"user_id": user_id}, include=[]).get("ids", []))
        except Exception as error:
            raise DatabaseError(
                what="Could not count chunks in the vector database.",
                why=str(error),
                fix="Check logs/apex.log; if the database is corrupted, rebuild it.",
            ) from error

    # -- maintenance -----------------------------------------------------------

    def reset(self) -> None:
        """Delete and recreate the collection (used by 'rebuild index')."""
        try:
            self.client.delete_collection(self.collection_name)
        except Exception:
            pass  # did not exist yet
        self.collection = self.client.get_or_create_collection(
            name=self.collection_name, metadata=self._collection_metadata()
        )
        self.version += 1
        log.warning("Collection '%s' was reset — all vectors removed.", self.collection_name)

    @staticmethod
    def database_location(settings) -> Path:
        return settings.database_path
