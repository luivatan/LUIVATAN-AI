"""Optional reranking stage.

Hybrid retrieval returns a *candidate pool* (Settings.top_k, ~10-20). A
reranker then scores each candidate against the actual question and only the
strongest few (Settings.rerank_top_k, 3-5) reach the LLM.

Two implementations:

- ``CrossEncoderReranker`` — a sentence-transformers cross-encoder
  (ms-marco-MiniLM by default). Best quality; needs the model locally, so it
  is *optional*: if it is missing, we fall back, never crash.
- ``LexicalReranker`` — always available, fully offline: BM25 scoring of the
  candidate texts against the question. Weaker than a cross-encoder but far
  better than sending raw fused order.

Modes (APEX_RERANKER): ``auto`` (default) = cross-encoder if available
locally, else lexical; ``cross_encoder``; ``lexical``; ``off`` (keep fused
order).
"""

from __future__ import annotations

import os
from pathlib import Path

from apex_ai.core.errors import RerankerUnavailableError
from apex_ai.core.logging import get_logger, timed
from apex_ai.core.types import RetrievedChunk
from apex_ai.retrieval.keyword import tokenize

log = get_logger("rerank")


class Reranker:
    name = "abstract"

    def rerank(self, query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
        raise NotImplementedError


class LexicalReranker(Reranker):
    """Offline fallback: BM25 over the candidate texts (self-contained)."""

    name = "lexical"

    def rerank(self, query, candidates):
        if not candidates:
            return candidates
        from rank_bm25 import BM25Plus  # positive IDF even for tiny pools

        query_tokens = tokenize(query)
        if not query_tokens:
            return candidates
        corpus = [tokenize(f"{c.section}\n{c.text}") for c in candidates]
        bm25 = BM25Plus(corpus)
        raw_scores = bm25.get_scores(query_tokens)
        query_terms = set(query_tokens)
        lexical_scores = [
            float(score) if query_terms.intersection(tokens) else 0.0
            for score, tokens in zip(raw_scores, corpus)
        ]
        if not any(lexical_scores):
            return candidates

        max_lexical = max(lexical_scores) or 1.0
        fused_scores = [max(0.0, float(c.retrieval_score)) for c in candidates]
        max_fused = max(fused_scores) or 1.0
        for candidate, lexical, fused in zip(candidates, lexical_scores, fused_scores):
            candidate.metadata["_fusion_score"] = fused
            normalized = 0.75 * (lexical / max_lexical) + 0.25 * (fused / max_fused)
            candidate.metadata["_reranker_score"] = normalized
            candidate.retrieval_score = normalized
        return sorted(candidates, key=lambda c: c.retrieval_score, reverse=True)


class CrossEncoderReranker(Reranker):
    name = "cross_encoder"

    def __init__(self, model_name: str, cache_dir=None, *, offline: bool = False) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.offline = offline
        self._model = None
        self._failed = False

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        if self._failed:
            raise RerankerUnavailableError(
                what="The reranker model is not available locally.",
                fix="Run once with internet, or set APEX_RERANKER=lexical for a fully "
                    "offline reranker.",
            )
        try:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(
                self.model_name,
                max_length=512,
                cache_folder=str(self.cache_dir) if self.cache_dir else None,
                local_files_only=self.offline,
            )
            log.info("Reranker model '%s' loaded.", self.model_name)
        except Exception as error:
            self._failed = True
            raise RerankerUnavailableError(
                what=f"Reranker model '{self.model_name}' could not be loaded.",
                why=str(error),
                fix="Set APEX_RERANKER=lexical (offline) or APEX_RERANKER=off, or download "
                    "the model once with internet access.",
            ) from error
        return self._model

    def rerank(self, query, candidates):
        if not candidates:
            return candidates
        model = self._ensure_model()
        with timed(log, f"cross-encoder rerank of {len(candidates)} candidates"):
            pairs = [[query, c.text] for c in candidates]
            scores = model.predict(pairs)
        for candidate, score in zip(candidates, scores):
            candidate.metadata["_fusion_score"] = candidate.retrieval_score
            candidate.metadata["_reranker_score"] = float(score)
            candidate.retrieval_score = float(score)
        return sorted(candidates, key=lambda c: c.retrieval_score, reverse=True)


class FallbackReranker(Reranker):
    """Use a primary reranker once available, otherwise degrade permanently.

    Model load/predict failures are attached only to the in-memory candidates
    for developer diagnostics; source metadata persisted in Chroma is not
    changed.
    """

    name = "cross_encoder_with_lexical_fallback"

    def __init__(self, primary: Reranker, fallback: Reranker | None = None) -> None:
        self.primary = primary
        self.fallback = fallback or LexicalReranker()
        self._primary_failed = False

    def rerank(self, query, candidates):
        if not candidates:
            return candidates
        if not self._primary_failed:
            try:
                return self.primary.rerank(query, candidates)
            except Exception as error:  # noqa: BLE001 - optional model boundary
                self._primary_failed = True
                log.warning("Primary reranker unavailable; using %s: %s", self.fallback.name, error)
                for candidate in candidates:
                    candidate.metadata["_reranker_fallback"] = (
                        f"{type(error).__name__}: {error}"
                    )
        return self.fallback.rerank(query, candidates)


class NoReranker(Reranker):
    """Explicit 'off' mode: keep the fused hybrid order."""

    name = "off"

    def rerank(self, query, candidates):
        return candidates


def make_reranker(settings) -> Reranker:
    """Factory honoring the graceful-degradation contract for 'auto'."""
    mode = (settings.reranker_mode or "auto").lower()
    if mode == "off":
        return NoReranker()
    if mode == "lexical":
        return LexicalReranker()
    cache_dir = getattr(settings, "cache_dir", None)
    # SentenceTransformerProvider sets HF_HOME to <cache>/huggingface; Hub
    # snapshots live in its ``hub`` child. Respect explicit HF cache choices.
    explicit_hub = os.environ.get("HF_HUB_CACHE")
    hf_home = os.environ.get("HF_HOME")
    if explicit_hub:
        cache_folder = Path(explicit_hub)
    elif hf_home:
        cache_folder = Path(hf_home) / "hub"
    elif cache_dir:
        cache_folder = cache_dir / "huggingface" / "hub"
    else:
        cache_folder = None
    if mode == "cross_encoder":
        primary = CrossEncoderReranker(
            settings.reranker_model,
            cache_dir=cache_folder,
            offline=getattr(settings, "offline", False),
        )
        return FallbackReranker(primary)

    # auto: use a cross-encoder only if its complete snapshot is local. This
    # path never initiates a network request during application startup/query.
    try:
        from sentence_transformers import CrossEncoder  # noqa: F401

        if _model_cached(settings.reranker_model, cache_folder):
            return FallbackReranker(
                CrossEncoderReranker(
                    settings.reranker_model,
                    cache_dir=cache_folder,
                    offline=True,
                )
            )
        log.info(
            "Reranker '%s' not cached; using offline lexical reranker.",
            settings.reranker_model,
        )
    except Exception:
        pass
    return LexicalReranker()


def _model_cached(model_name: str, cache_dir=None) -> bool:
    """Best-effort local-only check; never mutates process-wide offline flags."""
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(
            model_name,
            cache_dir=str(cache_dir) if cache_dir else None,
            local_files_only=True,
        )
        return True
    except Exception:
        return False
