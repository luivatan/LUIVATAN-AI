"""Provider-neutral LLM layer for Apex AI.

Adapters expose one interface and keep model loading out of application import
paths. Network and model errors are converted to safe user-facing exceptions.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol


class LLMError(RuntimeError):
    """Safe, user-facing generation failure."""


class Generator(Protocol):
    def __call__(self, prompt: str, max_tokens: int = 500, temperature: float = .2) -> str: ...


@dataclass(frozen=True)
class ModelConfig:
    provider: str = "llama_cpp"
    model: str = ""
    context_size: int = 2048
    temperature: float = .2
    max_tokens: int = 500
    n_gpu_layers: int = 0
    base_url: str = "http://localhost:11434"
    api_key: str = ""

    @classmethod
    def from_env(cls) -> "ModelConfig":
        return cls(
            provider=os.getenv("LLM_PROVIDER", "llama_cpp").lower(),
            model=os.getenv("LLAMA_MODEL_PATH", os.getenv("OLLAMA_MODEL", "")),
            context_size=int(os.getenv("LLM_CONTEXT_SIZE", "2048")),
            temperature=float(os.getenv("LLM_TEMPERATURE", ".2")),
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "500")),
            n_gpu_layers=int(os.getenv("LLM_GPU_LAYERS", "0")),
            base_url=os.getenv("OLLAMA_URL", "http://localhost:11434"),
            api_key=os.getenv("OPENAI_API_KEY", ""),
        )


class ModelManager:
    """Discovers/selects GGUF files and lazily caches a configured generator."""
    def __init__(self, model_dir: str | Path = "models"):
        self.model_dir = Path(model_dir)
        self.selected: Path | None = None
        self._generator: Generator | None = None
        self._config: ModelConfig | None = None

    def available_models(self) -> list[Path]:
        return sorted(self.model_dir.glob("*.gguf")) if self.model_dir.exists() else []

    def select(self, path: str | Path) -> Path:
        candidate = Path(path)
        if candidate.suffix.lower() != ".gguf" or not candidate.is_file():
            raise LLMError("Select an existing .gguf model file.")
        self.selected = candidate.resolve()
        self._generator = None
        return self.selected

    def generator(self, config: ModelConfig) -> Generator:
        if self._generator is None or self._config != config:
            self._generator = build_generator(config, self.selected)
            self._config = config
        return self._generator


def build_generator(config: ModelConfig, selected: Path | None = None) -> Generator:
    provider = config.provider
    if provider == "llama_cpp":
        model_path = selected or (Path(config.model) if config.model else None)
        if not model_path or not model_path.is_file():
            raise LLMError("No GGUF model selected. Choose a model before chatting.")
        try:
            from llama_cpp import Llama
            llm = Llama(model_path=str(model_path), n_ctx=config.context_size, n_gpu_layers=config.n_gpu_layers)
        except Exception as exc:
            raise LLMError("The local model could not be loaded. Check the model file and GPU settings.") from exc
        def generate(prompt, max_tokens=500, temperature=.2):
            try:
                return llm(prompt, max_tokens=max_tokens, temperature=temperature)["choices"][0]["text"].strip()
            except Exception as exc:
                raise LLMError("The local model failed while generating a response.") from exc
        return generate
    if provider == "ollama":
        return _ollama_generator(config)
    if provider in {"openai", "openai_compatible"}:
        if not config.api_key:
            raise LLMError("The configured API key is missing.")
        return _openai_generator(config)
    raise LLMError(f"Unsupported LLM provider: {provider}")


def _ollama_generator(config: ModelConfig) -> Generator:
    if not config.model:
        raise LLMError("Select an Ollama model in OLLAMA_MODEL.")
    def generate(prompt, max_tokens=500, temperature=.2):
        try:
            import requests
            response = requests.post(f"{config.base_url.rstrip('/')}/api/generate", json={"model": config.model, "prompt": prompt, "stream": False, "options": {"num_predict": max_tokens, "temperature": temperature}}, timeout=180)
            response.raise_for_status()
            return response.json().get("response", "").strip()
        except Exception as exc:
            raise LLMError("The local Ollama service is unavailable or returned an invalid response.") from exc
    return generate


def _openai_generator(config: ModelConfig) -> Generator:
    def generate(prompt, max_tokens=500, temperature=.2):
        try:
            import requests
            response = requests.post(f"{config.base_url.rstrip('/')}/chat/completions", headers={"Authorization": f"Bearer {config.api_key}"}, json={"model": config.model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens, "temperature": temperature}, timeout=180)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            raise LLMError("The configured AI service failed to generate a response.") from exc
    return generate


def stream_text(text: str, words_per_chunk: int = 8) -> Iterator[str]:
    """Provide a UI-friendly stream even when a provider only returns complete text."""
    words = text.split()
    for start in range(0, len(words), max(1, words_per_chunk)):
        yield " ".join(words[start:start + words_per_chunk]) + (" " if start + words_per_chunk < len(words) else "")


class ConversationEngine:
    def __init__(self, generator: Generator, history_limit: int = 8):
        self.generator = generator
        self.history: list[dict[str, str]] = []
        self.history_limit = history_limit

    def ask(self, question: str, context: str) -> str:
        if not question.strip():
            raise LLMError("Ask a question first.")
        history = self.history[-self.history_limit:]
        prompt = "Answer only from the supplied context. Cite sources like [1].\n\n"
        prompt += "Conversation:\n" + json.dumps(history) + f"\n\nContext:\n{context}\n\nQuestion:\n{question}\n\nAnswer:"
        answer = self.generator(prompt)
        if not answer:
            raise LLMError("The AI returned an empty response. Try rephrasing your question.")
        self.history.append({"user": question, "assistant": answer})
        return answer
