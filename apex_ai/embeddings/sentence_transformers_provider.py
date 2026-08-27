"""Sentence-Transformers embedding provider (default for Apex AI).

Offline-first behaviour
-----------------------
1. Try to load the model with the Hugging Face cache only
   (``HF_HUB_OFFLINE=1``) — zero network, the normal case after first run.
2. If that fails *and* we are allowed network access, load normally (this
   downloads the model once into the local cache and logs a warning).
3. If that fails *or* ``APEX_OFFLINE=1`` is set, raise
   ``EmbeddingModelNotFoundError`` that says exactly what is missing and where
   the cache lives.

The HF cache is kept inside the project (``data/cache/huggingface`` by
default) unless the user already configured ``HF_HOME``, so the whole app
directory stays portable.
"""

from __future__ import annotations

import os
from pathlib import Path

from apex_ai.core.errors import EmbeddingModelNotFoundError
from apex_ai.core.logging import get_logger
from apex_ai.embeddings.base import EmbeddingProvider

log = get_logger("embeddings")


class SentenceTransformerProvider(EmbeddingProvider):
    def __init__(self, settings) -> None:
        self.model_name = settings.embedding_model
        self.batch_size = settings.embedding_batch_size
        self._model = None
        self._dimension: int | None = None
        self._configure_cache(settings.cache_dir)
        self._model = self._load(settings.offline)

    # -- setup ------------------------------------------------------------

    @staticmethod
    def _configure_cache(cache_dir: Path) -> None:
        """Keep model caches inside the project unless the user chose one."""
        if not os.environ.get("HF_HOME"):
            hf_home = cache_dir / "huggingface"
            hf_home.mkdir(parents=True, exist_ok=True)
            os.environ["HF_HOME"] = str(hf_home)

    @staticmethod
    def _set_offline_mode(enabled: bool) -> dict:
        """Turn Hugging Face offline mode on/off *reliably*.

        huggingface_hub copies HF_HUB_OFFLINE into a module constant at import
        time, so setting the env var alone is not enough when the library is
        already loaded (it is, transitively, in the UI process). We sync the
        constant too — this is what makes a missing model fail in <1s instead
        of after network timeouts.
        """
        previous = {k: os.environ.get(k) for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")}
        value = "1" if enabled else "0"
        os.environ["HF_HUB_OFFLINE"] = value
        os.environ["TRANSFORMERS_OFFLINE"] = value
        try:
            from huggingface_hub import constants as hub_constants

            previous_constants = hub_constants.HF_HUB_OFFLINE
            hub_constants.HF_HUB_OFFLINE = enabled
        except Exception:
            previous_constants = None
        return {"env": previous, "hub_constant": previous_constants}

    @staticmethod
    def _restore_offline_mode(previous: dict) -> None:
        for key, value in previous["env"].items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if previous["hub_constant"] is not None:
            try:
                from huggingface_hub import constants as hub_constants

                hub_constants.HF_HUB_OFFLINE = previous["hub_constant"]
            except Exception:
                pass

    def _load(self, offline: bool):
        try:
            from sentence_transformers import SentenceTransformer
        except ModuleNotFoundError as error:  # pragma: no cover
            raise EmbeddingModelNotFoundError(
                what="The `sentence-transformers` package is not installed.",
                why="Apex AI uses it to compute text embeddings.",
                fix="Run: pip install -r requirements.txt",
            ) from error

        # Attempt 1: local cache only (true offline operation).
        state = self._set_offline_mode(True)
        try:
            model = SentenceTransformer(self.model_name)
            log.info("Embedding model '%s' loaded from local cache.", self.model_name)
            return model
        except Exception as cache_error:
            log.debug("Local-cache load failed: %s", cache_error)
            if offline or state["env"]["HF_HUB_OFFLINE"] == "1":
                raise self._not_found_error() from cache_error
        finally:
            self._restore_offline_mode(state)

        # Attempt 2: allow a one-time download (logged, never silent).
        try:
            log.warning(
                "Embedding model '%s' not in local cache; downloading once into %s.",
                self.model_name,
                os.environ.get("HF_HOME", "~/.cache/huggingface"),
            )
            model = SentenceTransformer(self.model_name)
            log.info("Embedding model '%s' downloaded and cached.", self.model_name)
            return model
        except Exception as error:
            raise self._not_found_error() from error

    def _not_found_error(self) -> EmbeddingModelNotFoundError:
        from apex_ai.config.settings import resolve_path

        cache = os.environ.get("HF_HOME", str(resolve_path("data/cache/huggingface")))
        return EmbeddingModelNotFoundError(
            what=f"The embedding model '{self.model_name}' is not available locally.",
            why="It was never downloaded into the local Hugging Face cache, "
                "and Apex AI is running in offline mode.",
            fix=(
                "Either run once with internet access so the model is cached, "
                "or copy the model into the cache manually.\n"
                f"Expected cache location: {cache}\n"
                "You can also choose a different embedding model with "
                "APEX_EMBEDDING_MODEL in .env."
            ),
        )

    # -- interface ----------------------------------------------------------

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,  # cosine-friendly unit vectors
            show_progress_bar=False,
        )
        return [v.tolist() for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            self._dimension = len(self.embed_query("dimension probe"))
        return self._dimension
