"""Embedding provider abstraction.

Key rule: the embedding model is **independent** from the generation LLM.
It has its own configuration (``APEX_EMBEDDING_MODEL``) and its own identity,
which is stored in the vector collection metadata so that a mismatch (old
vectors embedded with model A, new queries with model B) is detected instead
of silently returning garbage retrieval results.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    """Interface every embedding backend must implement."""

    #: Identity string stored in collection metadata, e.g. "all-MiniLM-L6-v2".
    name: str = "abstract"

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of document chunks."""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Embed a single search query."""

    @property
    def dimension(self) -> int:
        raise NotImplementedError
