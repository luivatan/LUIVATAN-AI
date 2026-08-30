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
from dataclasses import dataclass, field
from typing import Iterator

from apex_ai.core.errors import ProviderError


@dataclass(frozen=True)
class ToolCall:
    """One function call the model asked for.

    ``arguments_json`` is left as the provider's raw JSON string rather than
    parsed here: a hallucinated or malformed payload is a tool-*execution*
    concern (Phase 73's ``ToolRegistry.execute``), not something a provider
    should have to validate on the model's behalf.
    """

    id: str
    name: str
    arguments_json: str


@dataclass(frozen=True)
class ToolCallResult:
    """What ``generate_with_tools`` returns: either final text, or one or
    more tool calls the caller must execute and feed back as ``tool`` role
    messages before asking again."""

    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)


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
    # Phase 73: whether generate_with_tools() below has a real implementation
    # for this provider. False is the honest default - Apex AI never
    # simulates tool-calling through prompt tricks for a provider whose API
    # doesn't actually support it.
    supports_tools: bool = False

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

    def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        max_tokens: int = 512,
        temperature: float = 0.2,
    ) -> ToolCallResult:
        """Ask the model to answer or call one of ``tools``.

        ``tools`` is the OpenAI-style function-schema list
        (``[{"type": "function", "function": {"name", "description",
        "parameters"}}]``) - the shape every provider that genuinely
        implements this already speaks natively, so no per-provider
        translation layer is needed here.

        Default: not supported. Only providers with a real, tested
        implementation override this.
        """
        raise ProviderError(
            what=f"The '{self.name}' provider does not support tool calling.",
            why=f"Tool calling requires the provider's own function-calling API; "
                f"'{self.name}' has none wired up in Apex AI.",
            fix="Use a provider with real tool-calling support "
                "(APEX_LLM_PROVIDER=openai_compatible), or don't offer tools "
                "for this request.",
        )

    @abstractmethod
    def get_model_info(self) -> ModelInfo:
        """Describe the configured model (used by the UI and logs)."""

    def validate(self) -> None:
        """Raise an ApexError early if configuration is unusable.

        Default: no-op. Backends with external dependencies (model files,
        API keys, servers) override this with precise, actionable errors.
        """
        return None
