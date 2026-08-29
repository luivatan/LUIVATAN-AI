"""Centralized configuration for Apex AI.

Design notes
------------
- Every knob comes from an environment variable (optionally via a ``.env``
  file). Nothing is hardcoded to a personal filesystem path.
- Relative paths are resolved against the **project root**, not the current
  working directory. The old code used ``./database`` which silently pointed
  somewhere else if you launched from another folder.
- Legacy variable names from the pre-Apex project (``LLM_PROVIDER``,
  ``LLAMA_MODEL_PATH``, ``OLLAMA_*``, ``OPENAI_*``, ``HF_MODEL_PATH``) are
  still honored so existing setups keep working; ``APEX_*`` names win.
"""

from __future__ import annotations

import math
import os
import warnings
from dataclasses import dataclass, field, replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_TRUTHY = {"1", "true", "yes", "on"}
_WARNED_LEGACY: set[str] = set()


def _env(*names: str, default: str = "") -> str:
    """Return the first non-empty env var among ``names``.

    ``names[0]`` should be the canonical ``APEX_*`` name; any further names are
    legacy aliases. A one-time deprecation warning is emitted for aliases.
    """
    for index, name in enumerate(names):
        value = os.environ.get(name)
        if value:
            if index > 0 and name not in _WARNED_LEGACY:
                _WARNED_LEGACY.add(name)
                warnings.warn(
                    f"Environment variable `{name}` is deprecated; use `{names[0]}` instead.",
                    DeprecationWarning,
                    stacklevel=2,
                )
            return value
    return default


def resolve_path(raw: str | Path) -> Path:
    """Resolve ``raw`` to an absolute Path, relative to the project root."""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of the entire application configuration."""

    # --- application -------------------------------------------------
    app_name: str = "Apex AI"
    offline: bool = False

    # --- paths (defaults are project-root relative) -------------------
    database_path: Path = field(default_factory=lambda: resolve_path("data/chroma"))
    collection_name: str = "apex_docs"
    upload_dir: Path = field(default_factory=lambda: resolve_path("data/uploads"))
    model_dir: Path = field(default_factory=lambda: resolve_path("models"))
    model_path: str = ""  # empty = not preselected; UI/manager decides
    log_dir: Path = field(default_factory=lambda: resolve_path("logs"))
    cache_dir: Path = field(default_factory=lambda: resolve_path("data/cache"))
    memory_path: Path = field(default_factory=lambda: resolve_path("data/conversation_memory.json"))
    conversation_db_path: Path = field(
        default_factory=lambda: resolve_path("data/conversations.db")
    )
    long_term_memory_db_path: Path = field(
        default_factory=lambda: resolve_path("data/long_term_memory.db")
    )

    # --- embeddings ---------------------------------------------------
    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_batch_size: int = 32

    # --- LLM ----------------------------------------------------------
    llm_provider: str = "llama_cpp"
    llm_context_size: int = 4096
    n_gpu_layers: int = 0  # 0 = CPU only; -1 = offload all layers (llama.cpp)
    n_threads: int = 0  # 0 = let llama.cpp decide
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5-coder:7b"
    openai_api_base: str = "https://api.openai.com/v1"
    # Secret: env/.env only; never committed or included in Settings repr.
    openai_api_key: str = field(
        default="",
        repr=False,
        metadata={"secret": True},
    )
    openai_model: str = "gpt-4.1-mini"
    hf_model_path: str = "Qwen/Qwen2.5-0.5B-Instruct"
    provider_connect_timeout_seconds: float = 5.0
    provider_read_timeout_seconds: float = 300.0

    # --- chunking -------------------------------------------------------
    chunk_size: int = 1000
    chunk_overlap: int = 150
    min_chunk_size: int = 200
    max_chunk_size: int = 1600

    # --- retrieval ------------------------------------------------------
    top_k: int = 12  # fused candidate pool (~10-20 per spec)
    semantic_candidate_k: int = 16  # per-query vector candidate pool
    keyword_candidate_k: int = 16  # per-query BM25 candidate pool
    rerank_top_k: int = 4  # final evidence given to the LLM (3-5 per spec)
    vector_weight: float = 0.6
    keyword_weight: float = 0.4
    rrf_k: int = 60
    min_similarity: float = 0.30  # semantic evidence gate
    lexical_support_threshold: float = 0.60  # exact-evidence fallback gate
    reranker_mode: str = "auto"  # auto | cross_encoder | lexical | off
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    query_processing: bool = True  # conservative deterministic processing
    query_decomposition: bool = True
    query_rewrite: bool = False  # optional LLM rewrite (extra latency)
    max_query_variants: int = 4
    medical_mode: bool = True  # adds the medical-safety addendum to prompts

    # --- context / generation --------------------------------------------
    context_char_limit: int = 6000
    context_token_reserve: int = 1024
    generation_max_tokens: int = 768
    generation_temperature: float = 0.2
    memory_turns: int = 8  # retained/retrieved turns; not all are sent to the model
    history_turns: int = 3  # newest complete turns eligible for one prompt
    history_char_limit: int = 2400  # strict total conversation-context budget
    history_message_char_limit: int = 1000  # strict limit per prior message

    # --- web application ---------------------------------------------------
    max_upload_mb: int = 50
    rag_debug: bool = False  # gated developer endpoint; never in normal chat payloads

    # --- server ------------------------------------------------------------
    # Loopback is the safe default while inbound authentication is absent.
    # Explicit deployments can opt into a wider bind with APEX_SERVER_NAME.
    server_name: str = "127.0.0.1"
    server_port: int = 7860


def _int(raw: str, default: int) -> int:
    try:
        return int(raw)
    except ValueError:
        return default


def _float(raw: str, default: float) -> float:
    try:
        return float(raw)
    except ValueError:
        return default


def _bounded_int(
    raw: str,
    default: int,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    """Parse an integer and fail safely to ``default`` outside its valid range."""
    value = _int(raw, default)
    if value < minimum or (maximum is not None and value > maximum):
        return default
    return value


def _bounded_float(
    raw: str,
    default: float,
    *,
    minimum: float,
    maximum: float | None = None,
) -> float:
    """Parse a finite float and fail safely to ``default`` when invalid."""
    value = _float(raw, default)
    if (
        not math.isfinite(value)
        or value < minimum
        or (maximum is not None and value > maximum)
    ):
        return default
    return value


def _bool(raw: str, default: bool) -> bool:
    if raw == "":
        return default
    return raw.strip().lower() in _TRUTHY


def load_settings() -> Settings:
    """Build a Settings snapshot from the environment (reads ``.env`` first)."""
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:  # pragma: no cover - dotenv is a core dep
        pass
    else:
        # Look for `.env` in the CWD first, then in the project root, so the
        # app behaves the same no matter where it is launched from.
        for candidate in (Path.cwd() / ".env", PROJECT_ROOT / ".env"):
            if candidate.is_file():
                load_dotenv(candidate, override=False)
                break

    offline_raw = _env("APEX_OFFLINE", default="")

    return Settings(
        offline=_bool(offline_raw, False),
        database_path=resolve_path(_env("APEX_DATABASE_PATH", default="data/chroma")),
        collection_name=_env("APEX_COLLECTION", default="apex_docs"),
        upload_dir=resolve_path(_env("APEX_UPLOAD_DIR", default="data/uploads")),
        model_dir=resolve_path(_env("APEX_MODEL_DIR", default="models")),
        model_path=_env("APEX_MODEL_PATH", "LLAMA_MODEL_PATH", default=""),
        log_dir=resolve_path(_env("APEX_LOG_DIR", default="logs")),
        cache_dir=resolve_path(_env("APEX_CACHE_DIR", default="data/cache")),
        memory_path=resolve_path(_env("APEX_MEMORY_PATH", default="data/conversation_memory.json")),
        conversation_db_path=resolve_path(
            _env("APEX_CONVERSATION_DB_PATH", default="data/conversations.db")
        ),
        long_term_memory_db_path=resolve_path(
            _env(
                "APEX_LONG_TERM_MEMORY_DB_PATH",
                default="data/long_term_memory.db",
            )
        ),
        embedding_model=_env("APEX_EMBEDDING_MODEL", default="all-MiniLM-L6-v2"),
        embedding_batch_size=_bounded_int(
            _env("APEX_EMBEDDING_BATCH_SIZE", default="32"),
            32,
            minimum=1,
            maximum=4096,
        ),
        llm_provider=_env("APEX_LLM_PROVIDER", "LLM_PROVIDER", default="llama_cpp").lower(),
        llm_context_size=_bounded_int(
            _env("APEX_LLM_CONTEXT_SIZE", "LLM_CONTEXT_SIZE", default="4096"),
            4096,
            minimum=256,
            maximum=1_048_576,
        ),
        n_gpu_layers=_int(_env("APEX_N_GPU_LAYERS", default="0"), 0),
        n_threads=_bounded_int(
            _env("APEX_N_THREADS", default="0"),
            0,
            minimum=0,
            maximum=4096,
        ),
        ollama_url=_env("APEX_OLLAMA_URL", "OLLAMA_URL", default="http://127.0.0.1:11434"),
        ollama_model=_env("APEX_OLLAMA_MODEL", "OLLAMA_MODEL", default="qwen2.5-coder:7b"),
        openai_api_base=_env("APEX_OPENAI_API_BASE", "OPENAI_API_BASE", default="https://api.openai.com/v1"),
        openai_api_key=_env("APEX_OPENAI_API_KEY", "OPENAI_API_KEY", default=""),
        openai_model=_env("APEX_OPENAI_MODEL", "OPENAI_MODEL", default="gpt-4.1-mini"),
        hf_model_path=_env("APEX_HF_MODEL_PATH", "HF_MODEL_PATH", default="Qwen/Qwen2.5-0.5B-Instruct"),
        provider_connect_timeout_seconds=_bounded_float(
            _env("APEX_PROVIDER_CONNECT_TIMEOUT_SECONDS", default="5"),
            5.0,
            minimum=0.1,
            maximum=3600.0,
        ),
        provider_read_timeout_seconds=_bounded_float(
            _env("APEX_PROVIDER_READ_TIMEOUT_SECONDS", default="300"),
            300.0,
            minimum=0.1,
            maximum=86_400.0,
        ),
        chunk_size=_int(_env("APEX_CHUNK_SIZE", default="1000"), 1000),
        chunk_overlap=_int(_env("APEX_CHUNK_OVERLAP", default="150"), 150),
        min_chunk_size=_int(_env("APEX_MIN_CHUNK_SIZE", default="200"), 200),
        max_chunk_size=_int(_env("APEX_MAX_CHUNK_SIZE", default="1600"), 1600),
        top_k=max(1, _int(_env("APEX_TOP_K", default="12"), 12)),
        semantic_candidate_k=max(
            1, _int(_env("APEX_SEMANTIC_CANDIDATES", default="16"), 16)
        ),
        keyword_candidate_k=max(
            1, _int(_env("APEX_KEYWORD_CANDIDATES", default="16"), 16)
        ),
        rerank_top_k=max(1, _int(_env("APEX_RERANK_TOP_K", default="4"), 4)),
        vector_weight=_float(_env("APEX_VECTOR_WEIGHT", default="0.6"), 0.6),
        keyword_weight=_float(_env("APEX_KEYWORD_WEIGHT", default="0.4"), 0.4),
        rrf_k=max(1, _int(_env("APEX_RRF_K", default="60"), 60)),
        min_similarity=_float(_env("APEX_MIN_SIMILARITY", default="0.30"), 0.30),
        lexical_support_threshold=_float(
            _env("APEX_LEXICAL_SUPPORT_THRESHOLD", default="0.60"), 0.60
        ),
        reranker_mode=_env("APEX_RERANKER", default="auto").lower(),
        reranker_model=_env("APEX_RERANKER_MODEL", default="cross-encoder/ms-marco-MiniLM-L-6-v2"),
        query_processing=_bool(_env("APEX_QUERY_PROCESSING", default=""), True),
        query_decomposition=_bool(_env("APEX_QUERY_DECOMPOSITION", default=""), True),
        query_rewrite=_bool(_env("APEX_QUERY_REWRITE", default=""), False),
        max_query_variants=max(
            1, _int(_env("APEX_MAX_QUERY_VARIANTS", default="4"), 4)
        ),
        medical_mode=_bool(_env("APEX_MEDICAL_MODE", default=""), True),
        context_char_limit=max(
            200, _int(_env("APEX_CONTEXT_CHAR_LIMIT", default="6000"), 6000)
        ),
        context_token_reserve=max(
            128, _int(_env("APEX_CONTEXT_TOKEN_RESERVE", default="1024"), 1024)
        ),
        generation_max_tokens=_bounded_int(
            _env("APEX_GENERATION_MAX_TOKENS", default="768"),
            768,
            minimum=1,
            maximum=131_072,
        ),
        generation_temperature=_bounded_float(
            _env("APEX_GENERATION_TEMPERATURE", default="0.2"),
            0.2,
            minimum=0.0,
            maximum=2.0,
        ),
        memory_turns=max(1, _int(_env("APEX_MEMORY_TURNS", default="8"), 8)),
        history_turns=max(0, _int(_env("APEX_HISTORY_TURNS", default="3"), 3)),
        history_char_limit=max(
            0, _int(_env("APEX_HISTORY_CHAR_LIMIT", default="2400"), 2400)
        ),
        history_message_char_limit=max(
            0,
            _int(
                _env("APEX_HISTORY_MESSAGE_CHAR_LIMIT", default="1000"),
                1000,
            ),
        ),
        max_upload_mb=max(1, _int(_env("APEX_MAX_UPLOAD_MB", default="50"), 50)),
        rag_debug=_bool(_env("APEX_RAG_DEBUG", default=""), False),
        server_name=_env("APEX_SERVER_NAME", default="127.0.0.1"),
        server_port=_bounded_int(
            _env("APEX_SERVER_PORT", default="7860"),
            7860,
            minimum=1,
            maximum=65_535,
        ),
    )


def with_overrides(settings: Settings, **changes) -> Settings:
    """Return a new Settings with selected fields replaced (frozen dataclass)."""
    return replace(settings, **changes)
