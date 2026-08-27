"""LLM provider abstraction.

Apex AI must never be married to one specific model. Every backend — local
GGUF via llama.cpp, Ollama, an OpenAI-compatible API, or local Hugging Face
transformers — implements this one interface::

    LLMProvider
        generate(...)  -> str           one full answer
        stream(...)    -> Iterator[str] incremental answer (optional)
        get_model_info() -> ModelInfo   what is loaded

Providers are *lazy*: constructing one validates configuration but does not
load weights. That way the UI can start instantly and show clear errors for a
missing model without crashing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator


@dataclass
class ModelInfo:
    """Metadata about the model a provider is (or would be) using."""

    provider: str
    model: str
    path: str = ""
    context_size: int = 0
    details: str = ""

    def summary(self) -> str:
        parts = [f"{self.provider}:{self.model}"]
        if self.path:
            parts.append(f"path={self.path}")
        if self.context_size:
            parts.append(f"ctx={self.context_size}")
        if self.details:
            parts.append(self.details)
        return " | ".join(parts)


class LLMProvider(ABC):
    """Common interface for all generation backends."""

    name: str = "abstract"
    supports_streaming: bool = False

    @abstractmethod
    def generate(
        self,
        prompt: str | None = None,
        *,
        messages: list[dict] | None = None,
        max_tokens: int = 512,
        temperature: float = 0.2,
        stop: list[str] | None = None,
    ) -> str:
        """Return the full completion.

        Exactly one of ``prompt`` (raw completion) or ``messages``
        (chat roles) should be provided. Chat-aware backends use
        ``messages`` with the model's own chat template; backends without a
        chat template fall back to rendering ``messages`` as plain text.
        """

    def stream(
        self,
        prompt: str | None = None,
        *,
        messages: list[dict] | None = None,
        max_tokens: int = 512,
        temperature: float = 0.2,
        stop: list[str] | None = None,
    ) -> Iterator[str]:
        """Yield the answer incrementally. Default: single-shot fallback."""
        yield self.generate(prompt, messages=messages, max_tokens=max_tokens,
                            temperature=temperature, stop=stop)

    @abstractmethod
    def get_model_info(self) -> ModelInfo:
        """Describe the configured model (used by the UI and logs)."""

    def validate(self) -> None:
        """Raise an ApexError early if configuration is unusable.

        Default: no-op. Backends with external dependencies (model files,
        API keys, servers) override this with precise, actionable errors.
        """
        return None
