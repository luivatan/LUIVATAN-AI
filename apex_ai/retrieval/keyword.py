"""Keyword retrieval (BM25).

Vector search finds *meaning*, but it can miss exact identifiers — error
codes, drug names, part numbers — where literal word matching wins. Hybrid
retrieval runs both and merges.

Implementation: an in-memory BM25 index (``rank_bm25``, tiny pure-Python)
built from all chunks in the vector store. Personal document collections are
thousands of chunks, so a full rebuild is cheap; the index rebuilds lazily
whenever the store's ``version`` counter changes (after ingestion/deletion),
which is why we never need to keep a second persistent index in sync.
"""

from __future__ import annotations

import re

from apex_ai.core.logging import get_logger, timed
from apex_ai.core.types import RetrievedChunk

log = get_logger("retrieval.keyword")

# Preserve exact identifiers/dates (``XJ-420``, ``2026-08-27``, ``v2.1``)
# as tokens while also emitting their components. The complete token provides
# exact-match precision; components still match prose that uses a different
# separator.
_TOKEN = re.compile(r"\w+(?:[-./:]\w+)*", re.UNICODE)
_TOKEN_PART = re.compile(r"[-./:]")


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _TOKEN.findall((text or "").lower()):
        tokens.append(match)
        if _TOKEN_PART.search(match):
            tokens.extend(part for part in _TOKEN_PART.split(match) if part)
    return tokens


class _UserIndex:
    """The built BM25 state for one account's chunks."""

    __slots__ = ("bm25", "chunk_ids", "metadatas", "texts", "token_sets")

    def __init__(self, chunk_ids, metadatas, texts, token_sets, bm25) -> None:
        self.chunk_ids = chunk_ids
        self.metadatas = metadatas
        self.texts = texts
        self.token_sets = token_sets
        self.bm25 = bm25


class BM25Index:
    """Per-account BM25 index over one shared Chroma collection.

    Each account only ever searches its own chunks, so the index is built and
    cached per ``user_id`` rather than once globally (Phase 55). All cached
    sub-indices share one staleness check: the store's ``version`` counter
    changing (any account's ingestion/deletion) invalidates every cached
    account's index, same as the previous single global index did.
    """

    def __init__(self, store) -> None:
        self._store = store
        self._version = -1
        self._indices: dict[str, _UserIndex | None] = {}

    def _ensure_built(self, user_id: str) -> _UserIndex | None:
        """Build/rebuild ``user_id``'s index if the store changed since it was
        last built. Returns ``None`` when that account has no chunks."""
        if self._version != self._store.version:
            self._indices.clear()
            self._version = self._store.version
        if user_id in self._indices:
            return self._indices[user_id]

        chunks = self._store.get_all_chunks(user_id)
        if not chunks:
            self._indices[user_id] = None
            return None
        with timed(log, f"BM25 index build over {len(chunks)} chunks (user={user_id[:12]})"):
            # BM25Plus instead of BM25Okapi: its IDF term stays positive even
            # for very small corpora (OKAPI's IDF degenerates to 0 when a
            # term appears in most documents), which matters because personal
            # libraries can start with only a handful of chunks.
            from rank_bm25 import BM25Plus

            search_texts = [
                "\n".join(
                    part for part in (str(c.metadata.get("section", "")), c.text) if part
                )
                for c in chunks
            ]
            tokenized = [tokenize(text) for text in search_texts]
            index = _UserIndex(
                chunk_ids=[c.chunk_id for c in chunks],
                metadatas=[c.metadata for c in chunks],
                texts=[c.text for c in chunks],
                token_sets=[set(tokens) for tokens in tokenized],
                bm25=BM25Plus(tokenized),
            )
            self._indices[user_id] = index
        return index

    def invalidate(self) -> None:
        self._version = -1

    def search(
        self,
        query: str,
        user_id: str,
        k: int = 5,
        document_ids: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        """Top-k chunks owned by ``user_id`` by BM25 score. Higher is better.

        ``document_ids`` (Phase 67) restricts candidates to one knowledge-base
        collection, mirroring ``ChromaVectorStore.search``'s parameter.
        """
        index = self._ensure_built(user_id)
        if index is None:
            return []
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = index.bm25.get_scores(tokens)
        query_terms = set(tokens)
        allowed_documents = set(document_ids) if document_ids is not None else None
        # BM25Plus adds a positive delta even when a document contains none of
        # the query terms. Explicit overlap filtering prevents those baseline
        # scores from turning unrelated chunks into apparent keyword hits.
        eligible = [
            i
            for i in range(len(scores))
            if query_terms.intersection(index.token_sets[i])
            and (
                allowed_documents is None
                or index.metadatas[i].get("document_id") in allowed_documents
            )
        ]
        ranked = sorted(eligible, key=lambda i: scores[i], reverse=True)[:k]

        results = []
        for i in ranked:
            metadata = dict(index.metadatas[i])
            overlap = len(query_terms.intersection(index.token_sets[i])) / max(
                1, len(query_terms)
            )
            metadata["_keyword_score"] = float(scores[i])
            metadata["_lexical_coverage"] = round(overlap, 6)
            results.append(
                RetrievedChunk(
                    chunk_id=index.chunk_ids[i],
                    text=index.texts[i],
                    metadata=metadata,
                    retrieval_score=float(scores[i]),
                )
            )
        return results
