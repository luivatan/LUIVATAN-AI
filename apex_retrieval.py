"""Retrieval layer: embeddings, persistent Chroma, keyword and hybrid search."""
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

@dataclass
class Result:
    text: str
    metadata: dict
    score: float

class RetrievalError(RuntimeError): pass

class EmbeddingSystem:
    def __init__(self, model_name="all-MiniLM-L6-v2", encoder=None):
        self.model_name, self._encoder = model_name, encoder
    @property
    def encoder(self):
        if self._encoder is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._encoder = SentenceTransformer(self.model_name)
            except Exception as exc: raise RetrievalError("Embedding model could not be loaded.") from exc
        return self._encoder
    def encode(self, texts: list[str]) -> list[list[float]]:
        return self.encoder.encode(texts, show_progress_bar=False).tolist()

class ChromaStore:
    def __init__(self, path="database", collection="medical_docs", embeddings=None):
        self.path, self.collection_name, self.embeddings = path, collection, embeddings
        try:
            import chromadb
            self.collection = chromadb.PersistentClient(path=str(path)).get_or_create_collection(collection)
        except Exception as exc: raise RetrievalError("Vector storage could not be initialized.") from exc
    def upsert(self, ids, texts, metadata):
        vectors = self.embeddings.encode(texts) if self.embeddings else None
        kwargs = dict(ids=ids, documents=texts, metadatas=metadata)
        if vectors: kwargs["embeddings"] = vectors
        self.collection.upsert(**kwargs)
    def search(self, query, limit=20) -> list[Result]:
        kwargs = {"n_results": limit, "include": ["documents", "metadatas", "distances"]}
        if self.embeddings: kwargs["query_embeddings"] = self.embeddings.encode([query])
        else: kwargs["query_texts"] = [query]
        try: raw = self.collection.query(**kwargs)
        except Exception as exc: raise RetrievalError("Vector search failed.") from exc
        return [Result(text, meta or {}, 1 / (1 + distance)) for text, meta, distance in zip(raw.get("documents", [[]])[0], raw.get("metadatas", [[]])[0], raw.get("distances", [[]])[0])]

_WORDS = re.compile(r"[\w']+")
def keyword_score(query, text):
    q, words = set(_WORDS.findall(query.lower())), _WORDS.findall(text.lower())
    return sum(word in words for word in q) / max(1, len(q))

def keyword_search(query: str, documents: list[Result], limit=20) -> list[Result]:
    return sorted((Result(r.text, r.metadata, keyword_score(query, r.text)) for r in documents), key=lambda r: r.score, reverse=True)[:limit]

def hybrid_retrieve(query: str, vector_results: list[Result], keyword_results: list[Result], limit=8, vector_weight=.7) -> list[Result]:
    """Fuse normalized vector and lexical candidates, deduplicating by document/page/chunk."""
    combined = {}
    for rank, result in enumerate(vector_results):
        key = (result.metadata.get("source"), result.metadata.get("page"), result.text)
        combined.setdefault(key, [result, 0.0])[1] += vector_weight * result.score / (rank + 1)
    for rank, result in enumerate(keyword_results):
        key = (result.metadata.get("source"), result.metadata.get("page"), result.text)
        combined.setdefault(key, [result, 0.0])[1] += (1 - vector_weight) * result.score / (rank + 1)
    ranked = [Result(item[0].text, item[0].metadata, item[1]) for item in combined.values()]
    return sorted(ranked, key=lambda r: r.score, reverse=True)[:limit]

def optimize_context(results: list[Result], max_chars=6000) -> str:
    """Build bounded, citation-ready context without splitting a result."""
    blocks, used = [], 0
    for index, result in enumerate(results, 1):
        source = result.metadata.get("source", "unknown source")
        page = result.metadata.get("page", "?")
        block = f"[{index}] {source}, page {page}\n{result.text.strip()}"
        if used + len(block) > max_chars: break
        blocks.append(block); used += len(block) + 2
    return "\n\n".join(blocks)
