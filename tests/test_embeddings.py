"""Embedding provider abstraction tests."""

from __future__ import annotations

import math

import pytest

from apex_ai.embeddings.base import EmbeddingProvider
from apex_ai.embeddings.hashing import HashingEmbeddingProvider


def test_hashing_provider_is_deterministic():
    provider = HashingEmbeddingProvider()
    a = provider.embed_query("fever in adults")
    b = provider.embed_query("fever in adults")
    assert a == b
    assert len(a) == provider.dimension == 256


def test_hashing_vectors_are_unit_length():
    provider = HashingEmbeddingProvider()
    vector = provider.embed_query("some text to embed")
    norm = math.sqrt(sum(x * x for x in vector))
    assert abs(norm - 1.0) < 1e-6


def test_batch_and_query_shapes():
    provider = HashingEmbeddingProvider()
    docs = provider.embed_documents(["one", "two", "three"])
    assert len(docs) == 3
    assert all(len(d) == 256 for d in docs)
    assert len(provider.embed_documents([])) == 0


def test_similar_texts_score_higher_than_unrelated():
    provider = HashingEmbeddingProvider()

    def cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        return dot / (na * nb)

    query = provider.embed_query("fever treatment")
    close = provider.embed_query("fever treatment guidelines for adults")
    far = provider.embed_query("carburetor maintenance schedule")
    assert cosine(query, close) > cosine(query, far)


def test_provider_interface_contract():
    provider = HashingEmbeddingProvider()
    assert isinstance(provider, EmbeddingProvider)
    assert provider.name == "hashing-256-v1"


def test_sentence_transformers_provider_offline_error(settings, monkeypatch):
    """With APEX_OFFLINE=1 and a missing model, the error names the cache and fix."""
    from dataclasses import replace

    from apex_ai.core.errors import EmbeddingModelNotFoundError
    from apex_ai.embeddings.sentence_transformers_provider import SentenceTransformerProvider

    monkeypatch.setenv("HF_HOME", str(settings.cache_dir / "huggingface"))
    strict = replace(settings, embedding_model="no-such-model-xyz", offline=True)
    with pytest.raises(EmbeddingModelNotFoundError) as excinfo:
        SentenceTransformerProvider(strict)
    message = str(excinfo.value)
    assert "APEX_EMBEDDING_MODEL" in message
    assert "cache" in message.lower()
