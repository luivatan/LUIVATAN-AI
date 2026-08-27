"""LLM provider tests: registry, error paths, model manager. No network."""

from __future__ import annotations

import pytest

from apex_ai.config.settings import with_overrides
from apex_ai.core.errors import (
    ConfigurationError,
    ModelNotFoundError,
    ProviderError,
)
from apex_ai.llm import build_provider
from apex_ai.llm.local import LocalLLMProvider
from apex_ai.models.manager import ModelManager


def test_unknown_provider_is_a_friendly_error(settings):
    settings = with_overrides(settings, llm_provider="doesnotexist")
    with pytest.raises(ConfigurationError) as excinfo:
        build_provider(settings)
    assert "llama_cpp" in str(excinfo.value)  # lists valid options


def test_local_provider_missing_file_message_is_actionable(settings):
    settings = with_overrides(settings, model_path="/definitely/not/here.gguf")
    provider = LocalLLMProvider(settings)
    with pytest.raises(ModelNotFoundError) as excinfo:
        provider.validate()
    message = str(excinfo.value)
    assert "/definitely/not/here.gguf" in message
    assert "APEX_MODEL_PATH" in message


def test_local_provider_lazy_loading(settings, tmp_path):
    """A valid GGUF header must NOT load the model until generation is asked."""
    model = tmp_path / "models" / "fake.gguf"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"GGUF" + b"\x00" * 64)
    settings = with_overrides(settings, model_path=str(model))
    provider = LocalLLMProvider(settings)
    provider.validate()  # must pass without importing llama_cpp
    assert provider._model is None


def test_ollama_provider_builds(settings):
    from apex_ai.llm.ollama import OllamaProvider

    provider = build_provider(with_overrides(settings, llm_provider="ollama"))
    assert isinstance(provider, OllamaProvider)


def test_openai_provider_requires_key(settings):
    from apex_ai.llm.openai_compat import OpenAICompatProvider

    provider = OpenAICompatProvider(with_overrides(settings, openai_api_key=""))
    with pytest.raises(ConfigurationError):
        provider.validate()


def test_ollama_unreachable_gives_provider_error(settings, monkeypatch):
    """Connection failure must surface as ProviderError with a fix, not a traceback."""
    import requests

    provider = build_provider(with_overrides(
        settings, llm_provider="ollama", ollama_url="http://127.0.0.1:59999"
    ))
    with pytest.raises(ProviderError) as excinfo:
        provider.generate("hi")
    assert "HOW TO FIX" in str(excinfo.value)


def test_model_manager_discovery_and_validation(settings, tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    good = models / "good.gguf"
    good.write_bytes(b"GGUFmagic" + b"\x00" * 32)
    bad = models / "bad.gguf"
    bad.write_bytes(b"NOTGGMAGIC" + b"\x00" * 32)

    settings = with_overrides(settings, model_dir=models)
    manager = ModelManager(settings)
    entries = manager.discover()
    assert [e.name for e in entries] == ["bad.gguf", "good.gguf"]
    by_name = {e.name: e for e in entries}
    assert by_name["good.gguf"].status == "ready"
    assert by_name["good.gguf"].loadable
    assert by_name["bad.gguf"].status == "unknown format"
    assert not by_name["bad.gguf"].loadable


def test_model_manager_rejects_bad_header(settings, tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    (models / "bad.gguf").write_bytes(b"nope")
    manager = ModelManager(settings.__class__(model_dir=models, model_path=""))
    with pytest.raises(ModelNotFoundError):
        manager.resolve("bad.gguf")


def test_model_manager_missing_model_lists_alternatives(settings, tmp_path):
    manager = ModelManager(settings)
    with pytest.raises(ModelNotFoundError) as excinfo:
        manager.resolve("ghost.gguf")
    assert "APEX_MODEL_PATH" in str(excinfo.value)


def test_model_manager_includes_configured_path_outside_dir(settings, tmp_path):
    outside = tmp_path / "elsewhere.gguf"
    outside.write_bytes(b"GGUF" + b"\x00" * 16)
    settings2 = with_overrides(settings, model_path=str(outside))
    entries = ModelManager(settings2).discover()
    assert any(e.path == outside for e in entries)
