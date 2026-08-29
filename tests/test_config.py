"""Configuration, portability, and error-message tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from apex_ai.config.settings import PROJECT_ROOT, Settings, load_settings, resolve_path
from apex_ai.core.errors import ApexError, ModelNotFoundError


def test_relative_paths_resolve_against_project_root_not_cwd():
    # Simulate launching from an unrelated directory.
    import os

    original = Path.cwd()
    try:
        os.chdir(PROJECT_ROOT.parent)  # NOT the project root
        resolved = resolve_path("data/chroma")
        assert resolved == PROJECT_ROOT / "data" / "chroma"
    finally:
        os.chdir(original)


def test_legacy_env_vars_are_honored(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLAMA_MODEL_PATH", "/tmp/legacy.gguf")
    settings = load_settings()
    assert settings.llm_provider == "ollama"
    assert settings.model_path == "/tmp/legacy.gguf"


def test_apex_env_vars_win_over_legacy(monkeypatch):
    monkeypatch.setenv("APEX_LLM_PROVIDER", "llama_cpp")
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    assert load_settings().llm_provider == "llama_cpp"


def test_integer_parsing_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("APEX_TOP_K", "not-a-number")
    assert load_settings().top_k == 12


def test_phase2_rag_settings_are_environment_configurable(monkeypatch):
    monkeypatch.setenv("APEX_SEMANTIC_CANDIDATES", "19")
    monkeypatch.setenv("APEX_KEYWORD_CANDIDATES", "17")
    monkeypatch.setenv("APEX_QUERY_PROCESSING", "0")
    monkeypatch.setenv("APEX_MAX_QUERY_VARIANTS", "5")
    monkeypatch.setenv("APEX_RAG_DEBUG", "1")
    settings = load_settings()
    assert settings.semantic_candidate_k == 19
    assert settings.keyword_candidate_k == 17
    assert not settings.query_processing
    assert settings.max_query_variants == 5
    assert settings.rag_debug


def test_phase41_history_limits_are_environment_configurable(monkeypatch):
    monkeypatch.setenv("APEX_MEMORY_TURNS", "12")
    monkeypatch.setenv("APEX_HISTORY_TURNS", "4")
    monkeypatch.setenv("APEX_HISTORY_CHAR_LIMIT", "3200")
    monkeypatch.setenv("APEX_HISTORY_MESSAGE_CHAR_LIMIT", "900")
    settings = load_settings()
    assert settings.memory_turns == 12
    assert settings.history_turns == 4
    assert settings.history_char_limit == 3200
    assert settings.history_message_char_limit == 900


def test_phase42_long_term_memory_path_is_environment_configurable(
    monkeypatch, tmp_path
):
    configured = tmp_path / "independent-memory.db"
    monkeypatch.setenv("APEX_LONG_TERM_MEMORY_DB_PATH", str(configured))
    assert load_settings().long_term_memory_db_path == configured


def test_phase3_generation_and_provider_settings_are_environment_configurable(
    monkeypatch,
):
    monkeypatch.setenv("APEX_GENERATION_MAX_TOKENS", "1536")
    monkeypatch.setenv("APEX_GENERATION_TEMPERATURE", "0.65")
    monkeypatch.setenv("APEX_PROVIDER_CONNECT_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("APEX_PROVIDER_READ_TIMEOUT_SECONDS", "45")
    settings = load_settings()
    assert settings.generation_max_tokens == 1536
    assert settings.generation_temperature == 0.65
    assert settings.provider_connect_timeout_seconds == 2.5
    assert settings.provider_read_timeout_seconds == 45.0


@pytest.mark.parametrize(
    ("name", "attribute", "raw", "expected"),
    [
        ("APEX_GENERATION_MAX_TOKENS", "generation_max_tokens", "0", 768),
        ("APEX_GENERATION_TEMPERATURE", "generation_temperature", "nan", 0.2),
        ("APEX_PROVIDER_CONNECT_TIMEOUT_SECONDS", "provider_connect_timeout_seconds", "0", 5.0),
        ("APEX_PROVIDER_READ_TIMEOUT_SECONDS", "provider_read_timeout_seconds", "inf", 300.0),
        ("APEX_EMBEDDING_BATCH_SIZE", "embedding_batch_size", "-1", 32),
        ("APEX_SERVER_PORT", "server_port", "70000", 7860),
    ],
)
def test_phase3_bounded_values_fall_back_safely(
    monkeypatch, name, attribute, raw, expected
):
    monkeypatch.setenv(name, raw)
    assert getattr(load_settings(), attribute) == expected


def test_api_key_is_redacted_from_settings_repr():
    secret = "phase3-test-secret-value"
    representation = repr(Settings(openai_api_key=secret))
    assert secret not in representation
    assert "openai_api_key" not in representation


def test_server_defaults_to_loopback_and_allows_explicit_override(monkeypatch):
    assert Settings().server_name == "127.0.0.1"
    monkeypatch.setenv("APEX_SERVER_NAME", "0.0.0.0")
    assert load_settings().server_name == "0.0.0.0"


def test_env_example_documents_phase3_settings_without_a_key_value():
    example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    for name in (
        "APEX_COLLECTION",
        "APEX_EMBEDDING_BATCH_SIZE",
        "APEX_MAX_UPLOAD_MB",
        "APEX_GENERATION_MAX_TOKENS",
        "APEX_GENERATION_TEMPERATURE",
        "APEX_PROVIDER_CONNECT_TIMEOUT_SECONDS",
        "APEX_PROVIDER_READ_TIMEOUT_SECONDS",
    ):
        assert f"{name}=" in example

    examples = [line.lstrip("# ").strip() for line in example.splitlines()]
    assert [line for line in examples if line.startswith("APEX_OPENAI_API_KEY=")] == [
        "APEX_OPENAI_API_KEY="
    ]
    assert [line for line in examples if line.startswith("APEX_MODEL_PATH=")] == [
        "APEX_MODEL_PATH="
    ]


def test_apex_error_message_has_what_why_fix():
    error = ApexError(what="bad thing", why="reason", fix="do this")
    message = error.user_message()
    assert "WHAT HAPPENED" in message
    assert "WHY" in message
    assert "HOW TO FIX" in message


def test_missing_model_error_names_expected_path_and_fix(settings, tmp_path):
    from apex_ai.llm.local import LocalLLMProvider

    provider = LocalLLMProvider(settings)
    with pytest.raises(ModelNotFoundError) as excinfo:
        provider.validate()
    message = str(excinfo.value)
    assert "APEX_MODEL_PATH" in message
    assert str(settings.model_dir) in message


def test_missing_model_error_lists_available_models(settings, tmp_path):
    from apex_ai.llm.local import LocalLLMProvider

    fake_model = tmp_path / "models" / "tiny.gguf"
    fake_model.parent.mkdir(parents=True)
    fake_model.write_bytes(b"GGUF....")
    provider = LocalLLMProvider(settings)
    with pytest.raises(ModelNotFoundError) as excinfo:
        provider.validate()
    assert "tiny.gguf" in str(excinfo.value)


def test_frozen_settings_are_immutable(settings):
    with pytest.raises(AttributeError):
        settings.chunk_size = 999  # type: ignore[misc]


def test_default_settings_shapes():
    settings = Settings()
    assert settings.chunk_size == 1000
    assert settings.chunk_overlap == 150
    assert settings.top_k == 12
    assert settings.rerank_top_k == 4
    assert settings.generation_max_tokens == 768
    assert settings.generation_temperature == 0.2
    assert settings.provider_connect_timeout_seconds == 5.0
    assert settings.provider_read_timeout_seconds == 300.0
    assert settings.server_name == "127.0.0.1"
    assert settings.database_path == PROJECT_ROOT / "data" / "chroma"
    assert settings.long_term_memory_db_path == (
        PROJECT_ROOT / "data" / "long_term_memory.db"
    )
