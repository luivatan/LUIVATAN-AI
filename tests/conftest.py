"""Shared pytest fixtures.

The whole suite runs **offline and fast**: it uses the deterministic
HashingEmbeddingProvider and a FakeLLM — no model downloads, no network.
An `integration` marker is registered in ``pyproject.toml`` for future tests
that need real downloaded models/network; none exist yet, so it is not
currently selected or excluded by any default options.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from apex_ai.config.settings import Settings, resolve_path  # noqa: E402
from apex_ai.embeddings.hashing import HashingEmbeddingProvider  # noqa: E402
from apex_ai.memory.conversation import ConversationMemory  # noqa: E402
from apex_ai.rag.engine import RagEngine  # noqa: E402
from apex_ai.rag.query_processing import QueryProcessor  # noqa: E402
from apex_ai.retrieval.keyword import BM25Index  # noqa: E402
from apex_ai.retrieval.pipeline import HybridRetriever  # noqa: E402
from apex_ai.retrieval.reranker import LexicalReranker  # noqa: E402
from apex_ai.vectordb import ChromaVectorStore  # noqa: E402

DATA_DIR = Path(__file__).parent / "data"

# Shared default account for tests that don't care about multi-tenancy. Tests
# that specifically prove isolation (Phase 55) use their own second user_id.
USER = "user-1"


class FakeLLM:
    """Deterministic stand-in for an LLM provider.

    It echoes the evidence it was given, so tests can assert exactly which
    chunks reached the prompt and that citations match the context.
    """

    name = "fake"
    supports_streaming = True
    last_messages: list | None = None

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def generate(self, prompt=None, *, messages=None, max_tokens=512, temperature=0.2, stop=None):
        if self.fail:
            raise RuntimeError("fake llm failure")
        FakeLLM.last_messages = messages
        context = ""
        for message in messages or []:
            if message["role"] == "user":
                context = message["content"]
        return (
            "Based on the retrieved evidence: fever in adults is 38 C or higher. [1] "
            "Seek help above 40 C. [1]"
        )

    def stream(self, prompt=None, *, messages=None, max_tokens=512, temperature=0.2, stop=None):
        text = self.generate(prompt, messages=messages)
        for word in text.split(" "):
            yield word + " "

    def get_model_info(self):
        return {"provider": "fake"}


@pytest.fixture()
def settings(tmp_path) -> Settings:
    """Settings pointing every writable path at a temp directory."""
    return Settings(
        database_path=tmp_path / "chroma",
        upload_dir=tmp_path / "uploads",
        model_dir=tmp_path / "models",
        model_path="",
        log_dir=tmp_path / "logs",
        cache_dir=tmp_path / "cache",
        memory_path=tmp_path / "memory.json",
        conversation_db_path=tmp_path / "conversations.db",
        long_term_memory_db_path=tmp_path / "long_term_memory.db",
        users_db_path=tmp_path / "users.db",
        embedding_model="hashing-256-v1",
        top_k=6,
        rerank_top_k=3,
        # Hashing embeddings produce much lower cosine similarities than real
        # models, so tests use a permissive gate; the gate itself is tested
        # explicitly with a forced threshold.
        min_similarity=0.05,
        reranker_mode="lexical",
        context_char_limit=4000,
        # The whole suite can make far more than the production default's
        # per-minute budget of requests against one shared TestClient within
        # a single test run; rate limiting itself is tested explicitly
        # (test_rate_limiting.py) against its own dedicated app instance.
        rate_limit_enabled=False,
    )


@pytest.fixture()
def embeddings():
    return HashingEmbeddingProvider()


@pytest.fixture()
def store(settings, embeddings):
    return ChromaVectorStore(settings, embeddings, collection_name="test_docs")


@pytest.fixture()
def ingestion(settings, store):
    from apex_ai.documents.service import IngestionService

    return IngestionService(settings, store)


@pytest.fixture()
def engine(settings, store, ingestion):
    """A fully-wired engine with hashing embeddings + FakeLLM."""
    ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf", USER)
    ingestion.ingest_path(DATA_DIR / "burn_care.md", USER)

    store_for_bm25 = store
    retriever = HybridRetriever(store_for_bm25, settings, BM25Index(store_for_bm25))
    memory = ConversationMemory(settings.memory_path, settings.memory_turns)
    return RagEngine(
        settings=settings,
        store=store,
        retriever=retriever,
        reranker=LexicalReranker(),
        memory=memory,
        llm_provider=FakeLLM(),
        query_processor=QueryProcessor(enabled=False),
        medical_mode=True,
        user_id=USER,
    )
