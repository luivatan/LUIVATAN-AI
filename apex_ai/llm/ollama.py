"""Ollama provider (local server, no compilation needed).

Ollama is a popular alternative llama.cpp distribution. This provider keeps
the old project's Ollama support: same env vars as before, wrapped in the new
provider interface with clear errors when the server is unreachable.
"""

from __future__ import annotations

from typing import Iterator

import requests

from apex_ai.core.errors import ConfigurationError, ProviderError
from apex_ai.core.logging import get_logger
from apex_ai.llm.base import LLMProvider, ModelInfo
from apex_ai.llm.retry import RETRYABLE_STATUS_CODES, call_with_retries

log = get_logger("llm.ollama")


class OllamaProvider(LLMProvider):
    name = "ollama"
    supports_streaming = True

    def __init__(self, settings) -> None:
        self.settings = settings
        self.base_url = settings.ollama_url.rstrip("/")
        self.model = settings.ollama_model
        self.timeout = (
            settings.provider_connect_timeout_seconds,
            settings.provider_read_timeout_seconds,
        )

    def _post(self, payload: dict, stream: bool = False):
        """Issue the request, retrying with backoff (Phase 80) on a
        connection error, a timeout, or a retryable HTTP status (429/5xx) -
        never on anything else, so a definitively wrong request (e.g. an
        unknown model, checked by the caller via ``status_code == 404``)
        still reaches the caller unchanged on the first attempt."""

        def attempt():
            response = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                stream=stream,
                timeout=self.timeout,
            )
            if response.status_code in RETRYABLE_STATUS_CODES:
                raise requests.HTTPError(response=response)
            return response

        try:
            return call_with_retries(
                attempt,
                max_attempts=self.settings.provider_retry_max_attempts,
                base_delay_seconds=self.settings.provider_retry_base_delay_seconds,
                provider_name=self.name,
            )
        except requests.ConnectionError as error:
            raise ProviderError(
                what=f"Ollama is not reachable at {self.base_url}.",
                why="No Ollama server answered the request.",
                fix="Start it with `ollama serve`, ensure the model is pulled "
                    f"(`ollama pull {self.model}`), or change APEX_OLLAMA_URL in .env.",
            ) from error
        except requests.Timeout as error:
            raise ProviderError(
                what="Ollama took too long to respond.",
                why=(
                    "The request exceeded the configured provider timeout "
                    f"({self.timeout[1]:g} seconds)."
                ),
                fix=(
                    "Try a smaller model, reduce generation length, or adjust "
                    "APEX_PROVIDER_READ_TIMEOUT_SECONDS deliberately."
                ),
            ) from error
        except requests.HTTPError as error:
            status = error.response.status_code if error.response is not None else "unknown"
            raise ProviderError(
                what=f"Ollama returned a server error (HTTP {status}).",
                why="The server responded with a retryable error on every attempt.",
                fix="Check the Ollama server's own logs, or try again shortly.",
            ) from error

    def _messages(self, prompt, messages) -> list[dict]:
        if messages:
            return messages
        return [{"role": "user", "content": prompt or ""}]

    def generate(self, prompt=None, *, messages=None, max_tokens=512, temperature=0.2, stop=None):
        response = self._post({
            "model": self.model,
            "messages": self._messages(prompt, messages),
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": temperature},
        })
        if response.status_code == 404:
            raise ProviderError(
                what=f"Ollama does not know the model '{self.model}'.",
                why="The API returned 404 for this model name.",
                fix=f"Run `ollama pull {self.model}` or set APEX_OLLAMA_MODEL to a pulled model.",
            )
        response.raise_for_status()
        return response.json().get("message", {}).get("content", "").strip()

    def stream(self, prompt=None, *, messages=None, max_tokens=512, temperature=0.2, stop=None):
        response = self._post({
            "model": self.model,
            "messages": self._messages(prompt, messages),
            "stream": True,
            "options": {"num_predict": max_tokens, "temperature": temperature},
        }, stream=True)
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            import json

            data = json.loads(line)
            delta = data.get("message", {}).get("content", "")
            if delta:
                yield delta
            if data.get("done"):
                break

    def get_model_info(self) -> ModelInfo:
        return ModelInfo(
            provider=self.name,
            model=self.model,
            path=self.base_url,
            details="remote ollama server",
        )


__all__ = ["OllamaProvider", "ConfigurationError"]
