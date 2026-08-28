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
    assert settings.database_path == PROJECT_ROOT / "data" / "chroma"
