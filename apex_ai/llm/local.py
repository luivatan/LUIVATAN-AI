"""Local GGUF provider backed by llama-cpp-python.

This is the primary offline backend. Important properties:

- The model path is never hardcoded: it comes from Settings
  (``APEX_MODEL_PATH`` or a selection made in the UI from ``APEX_MODEL_DIR``).
- A missing model raises ``ModelNotFoundError`` that states the exact
  expected path and two concrete fixes — instead of an obscure traceback.
- Hardware: ``APEX_N_GPU_LAYERS`` controls GPU offload (0 = CPU only).
  No CUDA is assumed; the app degrades gracefully to CPU.
- Chat messages use the model's own chat template via
  ``create_chat_completion`` (correct for instruct-tuned GGUF models);
  a raw ``prompt`` still works for plain completion models.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator

from apex_ai.core.errors import ModelNotFoundError, ProviderError
from apex_ai.core.logging import get_logger, timed
from apex_ai.llm.base import LLMProvider, ModelInfo
from apex_ai.security.files import human_size

log = get_logger("llm.local")


class LocalLLMProvider(LLMProvider):
    name = "llama_cpp"
    supports_streaming = True

    def __init__(self, settings) -> None:
        self.settings = settings
        self.model_path = Path(settings.model_path) if settings.model_path else None
        self._model = None  # loaded lazily

    # -- validation ---------------------------------------------------------

    def validate(self) -> None:
        """Fail early with an actionable message if no usable model is set."""
        if self.model_path is None or str(self.model_path) == "":
            models_dir = self.settings.model_dir
            found = sorted(models_dir.glob("*.gguf")) if models_dir.is_dir() else []
            hint = ""
            if found:
                hint = f"\nModels found in {models_dir}:\n" + "\n".join(f"  - {m.name}" for m in found)
            raise ModelNotFoundError(
                what="No GGUF model is configured.",
                why="Apex AI is set to the local llama.cpp provider, but no model file was selected.",
                fix=(
                    "Set APEX_MODEL_PATH=/path/to/model.gguf in .env, or place a .gguf "
                    f"file in {models_dir} and select it in the Models tab.{hint}"
                ),
            )
        if not self.model_path.is_file():
            raise ModelNotFoundError(
                what=f"The configured model file does not exist:\n  {self.model_path}",
                why="APEX_MODEL_PATH points to a file that is not on disk (moved, renamed, or a "
                    "path from another computer).",
                fix=(
                    "1. Set APEX_MODEL_PATH to the correct .gguf file, or\n"
                    f"2. Copy a .gguf model into {self.settings.model_dir} and select it "
                    "in the Models tab."
                ),
            )

    # -- lazy loading ---------------------------------------------------------

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        self.validate()

        try:
            from llama_cpp import Llama
        except ModuleNotFoundError as error:
            raise ProviderError(
                what="The `llama-cpp-python` package is not installed.",
                why="It provides the llama.cpp runtime used to run GGUF models locally.",
                fix="Run: pip install llama-cpp-python\n"
                    "If the build fails, see README → Troubleshooting, or use the Ollama "
                    "provider instead (APEX_LLM_PROVIDER=ollama), which needs no compiler.",
            ) from error

        kwargs = dict(
            model_path=str(self.model_path),
            n_ctx=self.settings.llm_context_size,
            n_gpu_layers=self.settings.n_gpu_layers,
            verbose=False,
        )
        if self.settings.n_threads > 0:
            kwargs["n_threads"] = self.settings.n_threads

        try:
            with timed(log, "local model loading", level=logging.INFO):
                self._model = Llama(**kwargs)
        except Exception as error:
            self._model = None
            raise ProviderError(
                what=f"Failed to load the model `{self.model_path.name}`.",
                why=f"llama.cpp reported: {error}\n"
                    "Common causes: not a GGUF file, quantization unsupported by this "
                    "llama.cpp build, or the file is incomplete/corrupted.",
                fix="Try a different .gguf model, re-download the file, or rebuild "
                    "llama-cpp-python. Verify the model runs with `llama-cli` if available.",
            ) from error

        log.info("Model ready: %s (ctx=%d, gpu_layers=%d)",
                 self.model_path.name, self.settings.llm_context_size, self.settings.n_gpu_layers)
        return self._model

    # -- generation ------------------------------------------------------------

    def generate(self, prompt=None, *, messages=None, max_tokens=512, temperature=0.2, stop=None):
        model = self._ensure_model()
        try:
            if messages:
                output = model.create_chat_completion(
                    messages=messages, max_tokens=max_tokens, temperature=temperature, stop=stop,
                )
                return output["choices"][0]["message"]["content"].strip()
            output = model(
                prompt, max_tokens=max_tokens, temperature=temperature, stop=stop or [],
            )
            return output["choices"][0]["text"].strip()
        except ProviderError:
            raise
        except Exception as error:
            raise ProviderError(
                what="The local model failed during generation.",
                why=str(error),
                fix="Check logs/apex.log for details. Try a smaller context or another model.",
            ) from error

    def stream(self, prompt=None, *, messages=None, max_tokens=512, temperature=0.2, stop=None):
        model = self._ensure_model()
        try:
            if messages:
                iterator = model.create_chat_completion(
                    messages=messages, max_tokens=max_tokens, temperature=temperature,
                    stop=stop, stream=True,
                )
                for chunk in iterator:
                    delta = chunk["choices"][0].get("delta", {}).get("content")
                    if delta:
                        yield delta
            else:
                iterator = model(prompt, max_tokens=max_tokens, temperature=temperature,
                                 stop=stop or [], stream=True)
                for chunk in iterator:
                    text = chunk["choices"][0].get("text")
                    if text:
                        yield text
        except Exception as error:
            raise ProviderError(
                what="Streaming failed mid-generation.",
                why=str(error),
                fix="See logs/apex.log; try regenerating or lowering max tokens.",
            ) from error

    def get_model_info(self) -> ModelInfo:
        size = ""
        if self.model_path and self.model_path.is_file():
            size = human_size(self.model_path.stat().st_size)
        return ModelInfo(
            provider=self.name,
            model=self.model_path.name if self.model_path else "(none selected)",
            path=str(self.model_path) if self.model_path else "",
            context_size=self.settings.llm_context_size,
            details=f"size={size} gpu_layers={self.settings.n_gpu_layers}",
        )
