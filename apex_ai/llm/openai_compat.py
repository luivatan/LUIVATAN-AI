"""OpenAI / OpenAI-compatible provider.

Works with api.openai.com, LM Studio, llama.cpp server, vLLM, OpenRouter —
anything speaking the `/chat/completions` dialect. The API key is read only
from the environment / .env (never hardcoded, never logged).
"""

from __future__ import annotations

import json
from typing import Iterator

import requests

from apex_ai.core.errors import ConfigurationError, ProviderError
from apex_ai.core.logging import get_logger
from apex_ai.llm.base import LLMProvider, ModelInfo, ToolCall, ToolCallResult
from apex_ai.llm.retry import call_with_retries

log = get_logger("llm.openai")


class OpenAICompatProvider(LLMProvider):
    name = "openai_compatible"
    supports_streaming = True
    # Phase 73: the standard OpenAI /chat/completions `tools` param - real,
    # documented, and what this provider already speaks for every other
    # request, so no separate translation layer is needed.
    supports_tools = True
    # Phase 77: the standard OpenAI `response_format: {"type": "json_schema"}`
    # structured-output mode.
    supports_structured_output = True

    def __init__(self, settings, provider_name: str = "openai_compatible") -> None:
        self.settings = settings
        self.name = provider_name
        self.base_url = settings.openai_api_base.rstrip("/")
        self.api_key = settings.openai_api_key
        self.model = settings.openai_model
        self.timeout = (
            settings.provider_connect_timeout_seconds,
            settings.provider_read_timeout_seconds,
        )

    def validate(self) -> None:
        if not self.api_key:
            raise ConfigurationError(
                what="No API key configured for the OpenAI-compatible provider.",
                why=f"`{self.name}` requires an API key, and APEX_OPENAI_API_KEY is empty.",
                fix="Set APEX_OPENAI_API_KEY in your .env file (never commit it), "
                    "or switch APEX_LLM_PROVIDER to a local provider such as llama_cpp or ollama.",
            )

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _post(self, payload: dict, *, stream: bool = False):
        """Issue the request, retrying with backoff (Phase 80) on a
        connection error, a timeout, or a retryable HTTP status (429/5xx).
        A non-retryable error status (401, 400, 404, ...) still raises
        ``requests.HTTPError`` on the first attempt via ``raise_for_status``,
        unchanged - every caller's existing ``except requests.RequestException``
        handling is untouched either way."""

        def attempt():
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
                stream=stream,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response

        return call_with_retries(
            attempt,
            max_attempts=self.settings.provider_retry_max_attempts,
            base_delay_seconds=self.settings.provider_retry_base_delay_seconds,
            provider_name=self.name,
        )

    def _payload(self, prompt, messages, max_tokens, temperature, stop, stream: bool) -> dict:
        body_messages = messages or [{"role": "user", "content": prompt or ""}]
        payload = {
            "model": self.model,
            "messages": body_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }
        if stop:
            payload["stop"] = stop
        return payload

    def generate(self, prompt=None, *, messages=None, max_tokens=512, temperature=0.2, stop=None):
        self.validate()
        try:
            response = self._post(
                self._payload(prompt, messages, max_tokens, temperature, stop, False)
            )
            return response.json()["choices"][0]["message"]["content"].strip()
        except requests.RequestException as error:
            raise ProviderError(
                what=f"The API at {self.base_url} could not be reached or returned an error.",
                why=str(error),
                fix="Check your internet connection, API key, and APEX_OPENAI_API_BASE. "
                    "For offline work use APEX_LLM_PROVIDER=llama_cpp.",
            ) from error
        except (KeyError, IndexError, json.JSONDecodeError) as error:
            raise ProviderError(
                what="The API response could not be parsed.",
                why=str(error),
                fix="Verify the endpoint is OpenAI-compatible (/v1/chat/completions).",
            ) from error

    def stream(self, prompt=None, *, messages=None, max_tokens=512, temperature=0.2, stop=None):
        self.validate()
        try:
            response = self._post(
                self._payload(prompt, messages, max_tokens, temperature, stop, True), stream=True
            )
            for raw_line in response.iter_lines(decode_unicode=True):
                if not raw_line or not raw_line.startswith("data:"):
                    continue
                data = raw_line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                delta = chunk["choices"][0].get("delta", {}).get("content")
                if delta:
                    yield delta
        except requests.RequestException as error:
            raise ProviderError(
                what="Streaming from the API failed.",
                why=str(error),
                fix="Check connectivity and the API base URL.",
            ) from error

    def generate_with_tools(
        self, messages, tools, *, max_tokens=512, temperature=0.2
    ) -> ToolCallResult:
        self.validate()
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "tools": tools,
        }
        try:
            response = self._post(payload)
            message = response.json()["choices"][0]["message"]
        except requests.RequestException as error:
            raise ProviderError(
                what=f"The API at {self.base_url} could not be reached or returned an error.",
                why=str(error),
                fix="Check your internet connection, API key, and APEX_OPENAI_API_BASE. "
                    "For offline work use APEX_LLM_PROVIDER=llama_cpp.",
            ) from error
        except (KeyError, IndexError, json.JSONDecodeError) as error:
            raise ProviderError(
                what="The API response could not be parsed.",
                why=str(error),
                fix="Verify the endpoint is OpenAI-compatible (/v1/chat/completions) "
                    "and supports the `tools` parameter.",
            ) from error
        raw_calls = message.get("tool_calls") or []
        tool_calls = tuple(
            ToolCall(
                id=item.get("id", ""),
                name=item.get("function", {}).get("name", ""),
                arguments_json=item.get("function", {}).get("arguments", "{}"),
            )
            for item in raw_calls
        )
        content = message.get("content")
        return ToolCallResult(content=content, tool_calls=tool_calls)

    def generate_structured(
        self, messages, schema, *, schema_name="response", max_tokens=512, temperature=0.2
    ) -> dict:
        self.validate()
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": schema, "strict": True},
            },
        }
        try:
            response = self._post(payload)
            content = response.json()["choices"][0]["message"]["content"]
        except requests.RequestException as error:
            raise ProviderError(
                what=f"The API at {self.base_url} could not be reached or returned an error.",
                why=str(error),
                fix="Check your internet connection, API key, and APEX_OPENAI_API_BASE. "
                    "For offline work use APEX_LLM_PROVIDER=llama_cpp.",
            ) from error
        except (KeyError, IndexError, json.JSONDecodeError) as error:
            raise ProviderError(
                what="The API response could not be parsed.",
                why=str(error),
                fix="Verify the endpoint is OpenAI-compatible (/v1/chat/completions) "
                    "and supports the `response_format` json_schema mode.",
            ) from error
        try:
            data = json.loads(content or "")
        except json.JSONDecodeError as error:
            raise ProviderError(
                what="The model's response was not valid JSON.",
                why=str(error),
                fix="This can happen even with structured-output mode enabled; retry the request.",
            ) from error
        if not isinstance(data, dict):
            raise ProviderError(
                what="The model's structured response was not a JSON object.",
                why=f"Expected an object, got {type(data).__name__}.",
                fix="Adjust the requested schema to describe a JSON object at the top level.",
            )
        return data

    def get_model_info(self) -> ModelInfo:
        return ModelInfo(provider=self.name, model=self.model, path=self.base_url)
