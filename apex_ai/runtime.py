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

import logging
from dataclasses import dataclass, field
from typing import Any

from apex_ai.auth.service import AuthService
from apex_ai.auth.sessions import SessionStore
from apex_ai.auth.users import User, UserStore
from apex_ai.config.settings import Settings, with_overrides
from apex_ai.core.errors import UNEXPECTED_ERROR_MESSAGE, ApexError
from apex_ai.core.logging import get_logger, log_event, setup_logging
from apex_ai.memory.confirmation import MemoryConfirmationService
from apex_ai.memory.conversation import ConversationMemory
from apex_ai.memory.extraction import MemoryCandidateExtractor
from apex_ai.memory.long_term import LongTermMemoryStore
from apex_ai.models.manager import ModelManager
from apex_ai.rag.engine import RagEngine
from apex_ai.rag.query_processing import QueryProcessor
from apex_ai.retrieval.keyword import BM25Index
from apex_ai.retrieval.pipeline import HybridRetriever
from apex_ai.retrieval.reranker import make_reranker
from apex_ai.security.files import restrict_to_owner
from apex_ai.security.memory import MemorySafetyPolicy

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
    memory_safety: MemorySafetyPolicy | None = None
    memory_extractor: MemoryCandidateExtractor | None = None
    memory_confirmation: MemoryConfirmationService | None = None
    query_processor: Any = None
    engine: RagEngine | None = None
    models: ModelManager | None = None
    auth: AuthService | None = None
    default_local_user: User | None = None
    collections: Any = None
    projects: Any = None
    tools: Any = None
    tool_executor: Any = None
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
        log_event(
            log,
            logging.INFO,
            "model.selected",
            "Active model switched",
            model=resolved.name,
        )
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
    memory_safety = MemorySafetyPolicy()
    memory_extractor = MemoryCandidateExtractor(memory_safety)
    services = ApexServices(
        settings=settings,
        models=ModelManager(settings),
        memory_safety=memory_safety,
        memory_extractor=memory_extractor,
    )

    # Phase 51/52: accounts/sessions are foundational, not optional like the
    # memory/RAG boundaries below - a broken accounts database is a real
    # startup blocker, the same way a broken vector store already is.
    try:
        auth = AuthService(
            UserStore(settings.users_db_path),
            SessionStore(settings.users_db_path),
            session_ttl_days=settings.session_ttl_days,
        )
        services.auth = auth
        services.default_local_user = auth.ensure_default_local_account()
        log_event(
            log,
            logging.INFO,
            "auth.ready",
            "Accounts/sessions ready",
            user_count=auth.users.count(),
        )
    except ApexError as error:
        services.startup_error = error.public_message()
        log_event(
            log,
            logging.ERROR,
            "auth.startup_blocked",
            "Accounts/sessions startup was blocked by an expected error",
            exc_info=True,
            error_code=error.code,
            error_type=type(error).__name__,
        )
        return services
    except Exception:  # noqa: BLE001 - final startup boundary for this component
        services.startup_error = UNEXPECTED_ERROR_MESSAGE
        log_event(
            log,
            logging.ERROR,
            "auth.startup_failed",
            "Unexpected accounts/sessions startup failure",
            exc_info=True,
        )
        return services

    # Long-term memory is a separate optional persistence boundary. Phase 42
    # does not inject it into prompts, and a failure here must not disable the
    # existing chat/RAG stack.
    try:
        long_term_memory = LongTermMemoryStore(
            settings.long_term_memory_db_path,
            safety_policy=memory_safety,
        )
        # Phase 55: pre-Phase-55 rows have no owner yet; assign them to the
        # default local account, same precedent as conversations.backfill_owner
        # (called separately in api/server.py, where ConversationStore lives).
        if services.default_local_user is not None:
            long_term_memory.backfill_owner(services.default_local_user.id)
        item_count = (
            long_term_memory.count(services.default_local_user.id)
            if services.default_local_user is not None
            else 0
        )
        services.long_term_memory = long_term_memory
        services.memory_confirmation = MemoryConfirmationService(
            memory_extractor,
            long_term_memory,
        )
        log_event(
            log,
            logging.INFO,
            "memory.store_ready",
            "Long-term memory store ready",
            item_count=item_count,
        )
        if long_term_memory.removed_unsafe_on_startup:
            log_event(
                log,
                logging.WARNING,
                "memory.unsafe_records_removed",
                "Removed unsafe long-term memory records during startup",
                removed_count=long_term_memory.removed_unsafe_on_startup,
            )
    except ApexError as error:
        services._extras["long_term_memory_error"] = error.public_message()
        log_event(
            log,
            logging.WARNING,
            "memory.store_unavailable",
            "Long-term memory unavailable; core services will continue",
            exc_info=True,
            error_code=error.code,
            error_type=type(error).__name__,
            public_guidance=error.public_message(),
        )
    except Exception:  # noqa: BLE001 - defensive optional-component boundary
        services._extras["long_term_memory_error"] = UNEXPECTED_ERROR_MESSAGE
        log_event(
            log,
            logging.ERROR,
            "memory.store_initialization_failed",
            "Unexpected long-term-memory initialization failure",
            exc_info=True,
        )

    try:
        settings.database_path.mkdir(parents=True, exist_ok=True)
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        # Phase 57: these hold every account's document content and the
        # vector index derived from it - owner-only permissions, best-effort.
        restrict_to_owner(settings.database_path)
        restrict_to_owner(settings.upload_dir)

        if embedding_factory is None:
            from apex_ai.embeddings import SentenceTransformerProvider

            services.embeddings = SentenceTransformerProvider(settings)
        else:
            services.embeddings = embedding_factory(settings)

        from apex_ai.vectordb import ChromaVectorStore

        services.store = ChromaVectorStore(settings, services.embeddings)

        from apex_ai.documents.service import IngestionService

        services.ingestion = IngestionService(settings, services.store)

        from apex_ai.documents.collections import CollectionStore

        services.collections = CollectionStore(settings.collections_db_path)

        from apex_ai.projects.store import ProjectStore

        services.projects = ProjectStore(settings.projects_db_path)

        from apex_ai.tools import PermissionedToolExecutor, build_default_registry

        services.tools = build_default_registry()
        services.tool_executor = PermissionedToolExecutor(services.tools)

        # Phase 55: pre-Phase-55 chunks/registry entries have no owner yet;
        # assign them to the default local account, same precedent as
        # long_term_memory.backfill_owner above.
        if services.default_local_user is not None:
            services.store.backfill_owner(services.default_local_user.id)
            services.ingestion.backfill_owner(services.default_local_user.id)

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
            long_term_memory=services.long_term_memory,
            # This singleton engine backs /query and the CLI/Gradio entry
            # points, none of which resolve a per-request account (see Phase
            # 51-53's "Not yet done" boundary) - it always reads the default
            # local account's confirmed memory, same as those tools already
            # implicitly assume single-account usage everywhere else.
            user_id=services.default_local_user.id if services.default_local_user else "",
        )
        log_event(
            log,
            logging.INFO,
            "runtime.ready",
            "Apex AI services ready",
            document_count=len(
                services.ingestion.list_documents(services.default_local_user.id)
                if services.default_local_user is not None
                else []
            ),
            chunk_count=services.store.count(),
            reranker=services.reranker.name,
        )
    except ApexError as error:
        services.startup_error = error.public_message()
        log_event(
            log,
            logging.ERROR,
            "runtime.startup_blocked",
            "Apex AI startup was blocked by an expected error",
            exc_info=True,
            error_code=error.code,
            error_type=type(error).__name__,
            public_guidance=error.public_message(),
        )
    except Exception:  # noqa: BLE001 - final startup boundary
        services.startup_error = UNEXPECTED_ERROR_MESSAGE
        log_event(
            log,
            logging.ERROR,
            "runtime.startup_failed",
            "Unexpected startup failure",
            exc_info=True,
        )
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

    @property
    def supports_tools(self) -> bool:
        try:
            return self._provider().supports_tools
        except ApexError:
            return False

    def generate_with_tools(self, messages, tools, **kwargs):
        return self._provider().generate_with_tools(messages, tools, **kwargs)
