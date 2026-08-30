"""LLM provider tests: registry, error paths, model manager. No network."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest
import requests

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

    def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
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


def test_provider_without_structured_output_support_raises_a_clear_error(settings):
    from apex_ai.llm.local import LocalLLMProvider

    provider = LocalLLMProvider(settings)
    assert provider.supports_structured_output is False
    with pytest.raises(ProviderError) as excinfo:
        provider.generate_structured(
            [{"role": "user", "content": "hi"}], schema={"type": "object", "properties": {}}
        )
    assert "does not support structured output" in str(excinfo.value)


def test_openai_provider_generate_structured_returns_parsed_json(settings, monkeypatch):
    """Phase 77: a real (mocked HTTP) round trip through the OpenAI
    `response_format: json_schema` mode."""
    from apex_ai.llm.openai_compat import OpenAICompatProvider

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"summary": "ok", "score": 4}'}}]}

    seen_payloads = []

    def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
        seen_payloads.append(json)
        return Response()

    monkeypatch.setattr("requests.post", fake_post)
    provider = OpenAICompatProvider(with_overrides(settings, openai_api_key="configured"))
    assert provider.supports_structured_output is True

    schema = {
        "type": "object",
        "properties": {"summary": {"type": "string"}, "score": {"type": "integer"}},
        "required": ["summary", "score"],
    }
    result = provider.generate_structured(
        [{"role": "user", "content": "rate this"}], schema=schema, schema_name="rating"
    )

    assert result == {"summary": "ok", "score": 4}
    assert seen_payloads[0]["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "rating", "schema": schema, "strict": True},
    }


def test_openai_provider_generate_structured_rejects_invalid_json(settings, monkeypatch):
    from apex_ai.llm.openai_compat import OpenAICompatProvider

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "not json"}}]}

    monkeypatch.setattr("requests.post", lambda *args, **kwargs: Response())
    provider = OpenAICompatProvider(with_overrides(settings, openai_api_key="configured"))

    with pytest.raises(ProviderError) as excinfo:
        provider.generate_structured(
            [{"role": "user", "content": "hi"}], schema={"type": "object", "properties": {}}
        )
    assert "not valid JSON" in str(excinfo.value)


def test_openai_provider_generate_structured_rejects_a_non_object_top_level(settings, monkeypatch):
    from apex_ai.llm.openai_compat import OpenAICompatProvider

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "[1, 2, 3]"}}]}

    monkeypatch.setattr("requests.post", lambda *args, **kwargs: Response())
    provider = OpenAICompatProvider(with_overrides(settings, openai_api_key="configured"))

    with pytest.raises(ProviderError) as excinfo:
        provider.generate_structured(
            [{"role": "user", "content": "hi"}], schema={"type": "object", "properties": {}}
        )
    assert "not a JSON object" in str(excinfo.value)


def test_provider_cache_tracks_api_key_without_retaining_plaintext(settings):
    from apex_ai.llm.registry import _cache_key

    first_secret = "alpha"
    second_secret = "beta"
    first = _cache_key(with_overrides(settings, openai_api_key=first_secret))
    second = _cache_key(with_overrides(settings, openai_api_key=second_secret))

    assert first != second
    assert first_secret not in repr(first)
    assert second_secret not in repr(second)


def test_ollama_unreachable_gives_provider_error(settings):
    """Connection failure must surface as ProviderError with a fix, not a
    traceback. Retries disabled (max_attempts=1) - this test is about the
    error-wrapping behavior, not retry timing, which has its own tests."""
    provider = build_provider(with_overrides(
        settings, llm_provider="ollama", ollama_url="http://127.0.0.1:59999",
        provider_retry_max_attempts=1,
    ))
    with pytest.raises(ProviderError) as excinfo:
        provider.generate("hi")
    assert "HOW TO FIX" in str(excinfo.value)


def test_ollama_generate_retries_a_connection_error_then_recovers(settings, monkeypatch):
    """Phase 80: a real (mocked HTTP) provider actually retries, not just
    the generic retry helper in isolation."""
    from apex_ai.llm.ollama import OllamaProvider

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": "recovered"}}

    attempts = {"count": 0}

    def fake_post(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise requests.ConnectionError("transient")
        return Response()

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    provider = OllamaProvider(with_overrides(settings, llm_provider="ollama"))

    assert provider.generate("hi") == "recovered"
    assert attempts["count"] == 3


def test_ollama_generate_does_not_retry_a_model_not_found_response(settings, monkeypatch):
    """A 404 (unknown model) must reach the caller's friendly error on the
    first attempt - retrying it would never help and would just delay a
    real configuration problem the user needs to fix."""
    from apex_ai.llm.ollama import OllamaProvider

    class Response:
        status_code = 404

        def raise_for_status(self):
            return None

        def json(self):
            return {}

    attempts = {"count": 0}

    def fake_post(*args, **kwargs):
        attempts["count"] += 1
        return Response()

    monkeypatch.setattr("requests.post", fake_post)
    provider = OllamaProvider(with_overrides(settings, llm_provider="ollama"))

    with pytest.raises(ProviderError) as excinfo:
        provider.generate("hi")
    assert "does not know the model" in str(excinfo.value)
    assert attempts["count"] == 1


def test_openai_provider_generate_retries_a_503_then_recovers(settings, monkeypatch):
    from apex_ai.llm.openai_compat import OpenAICompatProvider

    class OkResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "recovered"}}]}

    attempts = {"count": 0}

    def fake_post(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] < 2:
            response = requests.Response()
            response.status_code = 503
            raise requests.HTTPError(response=response)
        return OkResponse()

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    provider = OpenAICompatProvider(with_overrides(settings, openai_api_key="configured"))

    assert provider.generate("hi") == "recovered"
    assert attempts["count"] == 2


def test_openai_provider_generate_exhausts_retries_and_raises_provider_error(
    settings, monkeypatch
):
    from apex_ai.llm.openai_compat import OpenAICompatProvider

    attempts = {"count": 0}

    def fake_post(*args, **kwargs):
        attempts["count"] += 1
        response = requests.Response()
        response.status_code = 503
        raise requests.HTTPError(response=response)

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    provider = OpenAICompatProvider(with_overrides(
        settings, openai_api_key="configured", provider_retry_max_attempts=3,
    ))

    with pytest.raises(ProviderError):
        provider.generate("hi")
    assert attempts["count"] == 3


def test_openai_provider_generate_does_not_retry_an_unauthorized_response(settings, monkeypatch):
    from apex_ai.llm.openai_compat import OpenAICompatProvider

    attempts = {"count": 0}

    def fake_post(*args, **kwargs):
        attempts["count"] += 1
        response = requests.Response()
        response.status_code = 401
        raise requests.HTTPError(response=response)

    monkeypatch.setattr("requests.post", fake_post)
    provider = OpenAICompatProvider(with_overrides(settings, openai_api_key="configured"))

    with pytest.raises(ProviderError):
        provider.generate("hi")
    assert attempts["count"] == 1


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
