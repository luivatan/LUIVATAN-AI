"""Deterministic hash-based embedding provider.

This is **not** a semantic embedding model — it maps token hashes into a
fixed-size vector. It exists for two purposes:

1. Fast, dependency-free unit tests (no model download, no torch).
2. A `--embedding hashing` escape hatch for the evaluation script in CI.

Never use it for real retrieval quality: it matches on exact words only and
understands nothing about meaning. The application never selects it
automatically.
"""

from __future__ import annotations

import hashlib
import math
import re

from apex_ai.embeddings.base import EmbeddingProvider

_DIMENSION = 256
_TOKEN = re.compile(r"\w+")


class HashingEmbeddingProvider(EmbeddingProvider):
    name = "hashing-256-v1"

    def __init__(self, settings=None) -> None:  # settings accepted for interface parity
        self.settings = settings

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * _DIMENSION
        tokens = _TOKEN.findall(text.lower())
        for token in tokens:
            digest = hashlib.md5(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % _DIMENSION
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        # L2 normalize so cosine similarity behaves like with real models.
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    @property
    def dimension(self) -> int:
        return _DIMENSION
