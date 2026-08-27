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

_TOKEN = re.compile(r"\w+")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class BM25Index:
    def __init__(self, store) -> None:
        self._store = store
        self._version = -1
        self._chunk_ids: list[str] = []
        self._metadatas: list[dict] = []
        self._bm25 = None

    def _ensure_built(self) -> bool:
        """Build/rebuild if the store changed. Returns False when empty."""
        if self._bm25 is not None and self._version == self._store.version:
            return True
        chunks = self._store.get_all_chunks()
        if not chunks:
            self._bm25 = None
            self._version = self._store.version
            return False
        with timed(log, f"BM25 index build over {len(chunks)} chunks"):
            # BM25Plus instead of BM25Okapi: its IDF term stays positive even
            # for very small corpora (OKAPI's IDF degenerates to 0 when a
            # term appears in most documents), which matters because personal
            # libraries can start with only a handful of chunks.
            from rank_bm25 import BM25Plus

            self._chunk_ids = [c.chunk_id for c in chunks]
            self._metadatas = [c.metadata for c in chunks]
            self._texts = [c.text for c in chunks]
            self._bm25 = BM25Plus([tokenize(text) for text in self._texts])
            self._version = self._store.version
        return True

    def invalidate(self) -> None:
        self._version = -1

    def search(self, query: str, k: int = 5) -> list[RetrievedChunk]:
        """Top-k chunks by BM25 score. Score is positive; higher is better."""
        if not self._ensure_built():
            return []
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]

        results = []
        for index in ranked:
            if scores[index] <= 0:
                continue  # zero overlap is noise, not a hit
            results.append(
                RetrievedChunk(
                    chunk_id=self._chunk_ids[index],
                    text=self._texts[index],
                    metadata=dict(self._metadatas[index]),
                    retrieval_score=float(scores[index]),
                )
            )
        return results
