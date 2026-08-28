"""Runtime service container.

One object that owns the fully-wired application (settings -> logging ->
embeddings -> vector store -> ingestion -> retrieval -> reranker -> memory ->
LLM -> engine). Both the Gradio UI and the FastAPI layer build this once and
share it.

Failure philosophy: if an *optional or fixable* piece is missing (no GGUF
model yet, embedding model not cached), :func:`build_services` still returns
a working container with ``startup_error`` set, so the UI can open and show
the precise fix instead of crashing. Chat/ingest callbacks check
``services.ready`` first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from apex_ai.config.settings import Settings, with_overrides
from apex_ai.core.errors import ApexError
from apex_ai.core.logging import get_logger, setup_logging
from apex_ai.memory.conversation import ConversationMemory
from apex_ai.memory.long_term import LongTermMemoryStore
from apex_ai.models.manager import ModelManager
from apex_ai.rag.engine import RagEngine
from apex_ai.rag.query_processing import QueryProcessor
from apex_ai.retrieval.keyword import BM25Index
from apex_ai.retrieval.pipeline import HybridRetriever
from apex_ai.retrieval.reranker import make_reranker

log = get_logger("runtime")


@dataclass
class ApexServices:
    settings: Settings
    startup_error: str = ""
    # populated lazily / on success:
    embeddings: Any = None
    store: Any = None
    ingestion: Any = None
    retriever: Any = None
    reranker: Any = None
    memory: Any = None
    long_term_memory: LongTermMemoryStore | None = None
    query_processor: Any = None
    engine: RagEngine | None = None
    models: ModelManager | None = None
    _extras: dict = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        """True when retrieval + generation can run (LLM may still be
        unselected; that is checked at question time with a clear error)."""
        return self.store is not None and self.engine is not None

    # -- model selection (Models tab / API) -----------------------------------

    def select_model(self, name_or_path: str) -> str:
        """Validate + switch the active GGUF model at runtime."""
        manager = self.models or ModelManager(self.settings)
        resolved = manager.resolve(name_or_path)
        self.settings = with_overrides(self.settings, model_path=str(resolved))
        from apex_ai.llm import reset_active_provider

        reset_active_provider()
        log.info("Active model switched to %s", resolved)
        return str(resolved)

    def active_llm(self):
        """Return the current LLM provider (lazy; validates config)."""
        from apex_ai.llm import get_active_provider

        return get_active_provider(self.settings)


def build_services(
    settings: Settings | None = None,
    *,
    embedding_factory=None,
    quiet_llm: bool = True,
) -> ApexServices:
    """Construct the whole application. Never raises for *fixable* problems —
    they are captured in ``services.startup_error`` with the user-facing fix.

    ``embedding_factory(settings) -> EmbeddingProvider`` lets tests and the
    evaluation script substitute a different embedding backend (e.g. the
    deterministic hashing provider) without touching the wiring.
    """
    from apex_ai.config.settings import load_settings

    settings = settings or load_settings()
    setup_logging(settings.log_dir)
    services = ApexServices(settings=settings, models=ModelManager(settings))

    # Long-term memory is a separate optional persistence boundary. Phase 42
    # does not inject it into prompts, and a failure here must not disable the
    # existing chat/RAG stack.
    try:
        long_term_memory = LongTermMemoryStore(settings.long_term_memory_db_path)
        item_count = long_term_memory.count()
        services.long_term_memory = long_term_memory
        log.info("Long-term memory store ready: %d item(s)", item_count)
    except ApexError as error:
        services._extras["long_term_memory_error"] = error.user_message()
        log.warning(
            "Long-term memory unavailable; core services will continue: %s",
            error.what,
        )
    except Exception as error:  # defensive optional-component boundary
        services._extras["long_term_memory_error"] = (
            f"Unexpected {type(error).__name__} while opening long-term memory."
        )
        log.exception("Unexpected long-term-memory initialization failure")

    try:
        settings.database_path.mkdir(parents=True, exist_ok=True)
        settings.upload_dir.mkdir(parents=True, exist_ok=True)

        if embedding_factory is None:
            from apex_ai.embeddings import SentenceTransformerProvider

            services.embeddings = SentenceTransformerProvider(settings)
        else:
            services.embeddings = embedding_factory(settings)

        from apex_ai.vectordb import ChromaVectorStore

        services.store = ChromaVectorStore(settings, services.embeddings)

        from apex_ai.documents.service import IngestionService

        services.ingestion = IngestionService(settings, services.store)

        keyword = BM25Index(services.store)
        services.retriever = HybridRetriever(services.store, settings, keyword)
        services.reranker = make_reranker(settings)
        services.memory = ConversationMemory(settings.memory_path, settings.memory_turns)

        # Deterministic follow-up expansion/decomposition is automatic and
        # does not load the generation model. Optional LLM rewriting stays
        # separately gated and resolves the active provider lazily.
        query_llm = None
        if settings.query_rewrite:
            query_llm = _LazyLLM(services) if quiet_llm else services.active_llm()
        services.query_processor = QueryProcessor(
            llm_provider=query_llm,
            enabled=settings.query_processing,
            decompose=settings.query_decomposition,
            llm_rewrite=settings.query_rewrite,
            max_subqueries=max(0, settings.max_query_variants - 1),
        )
        services.engine = RagEngine(
            settings=settings,
            store=services.store,
            retriever=services.retriever,
            reranker=services.reranker,
            memory=services.memory,
            llm_provider=_LazyLLM(services),
            query_processor=services.query_processor,
            medical_mode=settings.medical_mode,
        )
        log.info(
            "Apex AI services ready: %d document(s), %d chunk(s), reranker=%s",
            len(services.ingestion.list_documents()),
            services.store.count(),
            services.reranker.name,
        )
    except ApexError as error:
        services.startup_error = error.user_message()
        log.error("Startup problem:\n%s", services.startup_error)
    except Exception as error:  # truly unexpected
        services.startup_error = (
            "Unexpected startup failure.\n\nDetails:\n" f"{type(error).__name__}: {error}"
        )
        log.exception("Unexpected startup failure")
    return services


class _LazyLLM:
    """Adapter so the engine can call generate/stream on the *current*
    provider, even after the user switches models at runtime."""

    def __init__(self, services: ApexServices) -> None:
        self._services = services

    def _provider(self):
        return self._services.active_llm()

    def generate(self, prompt=None, **kwargs):
        return self._provider().generate(prompt, **kwargs)

    def stream(self, prompt=None, **kwargs):
        yield from self._provider().stream(prompt, **kwargs)

    @property
    def supports_streaming(self) -> bool:
        try:
            return self._provider().supports_streaming
        except ApexError:
            return False
