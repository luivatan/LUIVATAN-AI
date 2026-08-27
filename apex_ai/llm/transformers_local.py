"""Hugging Face Transformers provider (local models, non-GGUF).

Kept from the old project. Useful for small models and for smoke-testing the
full pipeline without llama.cpp (e.g. a tiny random Llama in CI). Streaming is
not implemented here — the base class single-shot fallback covers it.
"""

from __future__ import annotations

from apex_ai.core.errors import ProviderError
from apex_ai.core.logging import get_logger, timed
from apex_ai.llm.base import LLMProvider, ModelInfo

log = get_logger("llm.transformers")


class TransformersProvider(LLMProvider):
    name = "transformers"
    supports_streaming = False

    def __init__(self, settings) -> None:
        self.settings = settings
        self.model_id = settings.hf_model_path
        self._pipeline = None

    def _ensure_pipeline(self):
        if self._pipeline is not None:
            return self._pipeline
        try:
            from transformers import pipeline
        except ModuleNotFoundError as error:
            raise ProviderError(
                what="The `transformers` package is not installed.",
                why="The transformers provider needs the transformers + torch packages.",
                fix="Run: pip install transformers torch",
            ) from error

        try:
            with timed(log, f"loading transformers model {self.model_id}"):
                import torch

                device = 0 if torch.cuda.is_available() else -1  # graceful CPU fallback
                self._pipeline = pipeline(
                    "text-generation",
                    model=self.model_id,
                    device=device,
                )
        except Exception as error:
            self._pipeline = None
            raise ProviderError(
                what=f"Could not load the transformers model '{self.model_id}'.",
                why=str(error),
                fix="Check the model id/path, or pre-download it. For offline use, "
                    "GGUF models with the llama_cpp provider are recommended.",
            ) from error
        return self._pipeline

    def _render(self, prompt, messages) -> str:
        pipe = self._ensure_pipeline()
        tokenizer = getattr(pipe, "tokenizer", None)
        if messages and tokenizer is not None and hasattr(tokenizer, "apply_chat_template"):
            try:
                return tokenizer.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True
                )
            except Exception:  # template missing -> plain rendering
                pass
        if messages:
            return "\n".join(f"{m['role']}: {m['content']}" for m in messages)
        return prompt or ""

    def generate(self, prompt=None, *, messages=None, max_tokens=512, temperature=0.2, stop=None):
        pipe = self._ensure_pipeline()
        text = self._render(prompt, messages)
        try:
            output = pipe(
                text,
                max_new_tokens=max_tokens,
                temperature=temperature if temperature > 0 else 1.0,
                do_sample=temperature > 0,
                return_full_text=False,
            )
            return output[0]["generated_text"].strip()
        except Exception as error:
            raise ProviderError(
                what="Generation with the transformers model failed.",
                why=str(error),
                fix="See logs/apex.log. Try a smaller model or reduce max tokens.",
            ) from error

    def get_model_info(self) -> ModelInfo:
        return ModelInfo(provider=self.name, model=self.model_id, details="local transformers")
