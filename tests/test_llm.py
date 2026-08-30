"""LLM provider tests: registry, error paths, model manager. No network."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

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


def test_http_providers_use_timeouts_in_offline_cache_mode(settings, monkeypatch):
    from apex_ai.llm.ollama import OllamaProvider
    from apex_ai.llm.openai_compat import OpenAICompatProvider

    seen: list[tuple[float, float]] = []

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            if len(seen) == 1:
                return {"message": {"content": "local answer"}}
            return {"choices": [{"message": {"content": "compatible answer"}}]}

    def fake_post(*args, **kwargs):
        seen.append(kwargs["timeout"])
        return Response()

    monkeypatch.setattr("requests.post", fake_post)
    configured = with_overrides(
        settings,
        offline=True,
        openai_api_key="configured",
        provider_connect_timeout_seconds=1.25,
        provider_read_timeout_seconds=42.5,
    )

    assert OllamaProvider(configured).generate("hello") == "local answer"
    assert OpenAICompatProvider(configured).generate("hello") == "compatible answer"
    assert seen == [(1.25, 42.5), (1.25, 42.5)]


def test_transformers_offline_mode_loads_model_and_tokenizer_from_cache_only(
    settings, monkeypatch
):
    from apex_ai.llm.transformers_local import TransformersProvider

    calls: list[tuple[str, str, bool]] = []

    class AutoTokenizer:
        @classmethod
        def from_pretrained(cls, model_id, *, local_files_only):
            calls.append(("tokenizer", model_id, local_files_only))
            return object()

    class AutoModelForCausalLM:
        @classmethod
        def from_pretrained(cls, model_id, *, local_files_only):
            calls.append(("model", model_id, local_files_only))
            return object()

    transformers = ModuleType("transformers")
    transformers.AutoTokenizer = AutoTokenizer
    transformers.AutoModelForCausalLM = AutoModelForCausalLM
    transformers.pipeline = lambda *args, **kwargs: SimpleNamespace(
        tokenizer=kwargs["tokenizer"]
    )
    torch = ModuleType("torch")
    torch.cuda = SimpleNamespace(is_available=lambda: False)
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    monkeypatch.setitem(sys.modules, "torch", torch)

    configured = with_overrides(
        settings,
        hf_model_path="cached-test-model",
        offline=True,
    )
    TransformersProvider(configured)._ensure_pipeline()

    assert calls == [
        ("tokenizer", "cached-test-model", True),
        ("model", "cached-test-model", True),
    ]


def test_openai_provider_requires_key(settings):
    from apex_ai.llm.openai_compat import OpenAICompatProvider

    provider = OpenAICompatProvider(with_overrides(settings, openai_api_key=""))
    with pytest.raises(ConfigurationError):
        provider.validate()


def test_openai_provider_generate_with_tools_returns_tool_calls(settings, monkeypatch):
    """Phase 73: a real (mocked HTTP) round trip through the OpenAI
    /chat/completions `tools` param, proving the response is parsed into
    ToolCall objects rather than simulated."""
    from apex_ai.llm.openai_compat import OpenAICompatProvider

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "function": {
                                        "name": "calculator",
                                        "arguments": '{"expression": "2 + 2"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }

    seen_payloads = []

    def fake_post(url, headers=None, json=None, timeout=None):
        seen_payloads.append(json)
        return Response()

    monkeypatch.setattr("requests.post", fake_post)
    provider = OpenAICompatProvider(with_overrides(settings, openai_api_key="configured"))
    assert provider.supports_tools is True

    result = provider.generate_with_tools(
        [{"role": "user", "content": "what is 2 + 2?"}],
        tools=[{"type": "function", "function": {"name": "calculator", "parameters": {}}}],
    )

    assert result.content is None
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call.id == "call-1"
    assert call.name == "calculator"
    assert call.arguments_json == '{"expression": "2 + 2"}'
    assert seen_payloads[0]["tools"] == [
        {"type": "function", "function": {"name": "calculator", "parameters": {}}}
    ]


def test_openai_provider_generate_with_tools_returns_plain_content_when_no_tool_is_called(
    settings, monkeypatch
):
    from apex_ai.llm.openai_compat import OpenAICompatProvider

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "The answer is 4."}}]}

    monkeypatch.setattr("requests.post", lambda *args, **kwargs: Response())
    provider = OpenAICompatProvider(with_overrides(settings, openai_api_key="configured"))

    result = provider.generate_with_tools(
        [{"role": "user", "content": "what is 2 + 2?"}], tools=[]
    )
    assert result.content == "The answer is 4."
    assert result.tool_calls == ()


def test_provider_cache_tracks_api_key_without_retaining_plaintext(settings):
    from apex_ai.llm.registry import _cache_key

    first_secret = "alpha"
    second_secret = "beta"
    first = _cache_key(with_overrides(settings, openai_api_key=first_secret))
    second = _cache_key(with_overrides(settings, openai_api_key=second_secret))

    assert first != second
    assert first_secret not in repr(first)
    assert second_secret not in repr(second)


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
