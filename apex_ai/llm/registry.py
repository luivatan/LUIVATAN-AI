"""Provider registry + runtime cache.

``build_provider`` maps a provider name to its class — adding a new backend
later (e.g. an Anthropic or a remote inference API) means adding one class and
one registry entry, nothing else in the app changes.

``get_active_provider`` caches the constructed provider so the same model is
never loaded twice; switching models (UI) replaces the cache entry.
"""

from __future__ import annotations

import hashlib

from apex_ai.core.errors import ConfigurationError
from apex_ai.core.logging import get_logger
from apex_ai.llm.base import LLMProvider, ModelInfo
from apex_ai.llm.local import LocalLLMProvider

log = get_logger("llm.registry")

_PROVIDERS: dict[str, type[LLMProvider]] = {
    "llama_cpp": LocalLLMProvider,
}


def _lazy_providers() -> dict[str, type[LLMProvider]]:
    """Import optional providers only when requested.

    Keeping ollama/openai/transformers imports lazy is not about speed —
    they are cheap — but about keeping the dependency graph explicit.
    """
    mapping = dict(_PROVIDERS)
    from apex_ai.llm.ollama import OllamaProvider
    from apex_ai.llm.openai_compat import OpenAICompatProvider
    from apex_ai.llm.transformers_local import TransformersProvider

    mapping.update({
        "ollama": OllamaProvider,
        "openai": OpenAICompatProvider,
        "openai_compatible": lambda settings: OpenAICompatProvider(settings, "openai_compatible"),
        "transformers": TransformersProvider,
    })
    return mapping


def available_providers() -> list[str]:
    return sorted(_lazy_providers().keys())


def build_provider(settings, provider_name: str | None = None) -> LLMProvider:
    """Construct (not load) a provider from settings."""
    name = (provider_name or settings.llm_provider or "llama_cpp").lower()
    mapping = _lazy_providers()
    if name not in mapping:
        raise ConfigurationError(
            what=f"Unknown LLM provider '{name}'.",
            why="APEX_LLM_PROVIDER is not one of the registered backends.",
            fix="Use one of: " + ", ".join(available_providers()) + ".",
        )
    factory = mapping[name]
    provider = factory(settings) if not isinstance(factory, type) else factory(settings)
    log.debug("Built provider %s", name)
    return provider


# -- runtime cache -----------------------------------------------------------

_active: LLMProvider | None = None
_active_key: tuple | None = None


def _secret_fingerprint(value: str) -> str:
    """Represent secret configuration in cache identity without retaining plaintext."""
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _cache_key(settings) -> tuple:
    return (
        settings.llm_provider,
        settings.model_path,
        settings.llm_context_size,
        settings.n_gpu_layers,
        settings.ollama_url,
        settings.ollama_model,
        settings.openai_api_base,
        _secret_fingerprint(settings.openai_api_key),
        settings.openai_model,
        settings.hf_model_path,
        settings.offline,
        settings.provider_connect_timeout_seconds,
        settings.provider_read_timeout_seconds,
    )


def get_active_provider(settings) -> LLMProvider:
    """Return a cached provider, rebuilt only when configuration changed."""
    global _active, _active_key
    key = _cache_key(settings)
    if _active is None or _active_key != key:
        _active = build_provider(settings)
        _active_key = key
        log.info("Active LLM provider: %s", _active.name)
    return _active


def reset_active_provider() -> None:
    """Drop the cached provider (called after model selection in the UI)."""
    global _active, _active_key
    _active = None
    _active_key = None


__all__ = [
    "LLMProvider",
    "ModelInfo",
    "LocalLLMProvider",
    "available_providers",
    "build_provider",
    "get_active_provider",
    "reset_active_provider",
]
