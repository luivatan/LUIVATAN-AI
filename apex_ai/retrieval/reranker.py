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
        corpus = [tokenize(c.text) for c in candidates]
        bm25 = BM25Plus(corpus)
        scores = bm25.get_scores(query_tokens)
        for candidate, score in zip(candidates, scores):
            candidate.retrieval_score = float(score)
        return sorted(candidates, key=lambda c: c.retrieval_score, reverse=True)


class CrossEncoderReranker(Reranker):
    name = "cross_encoder"

    def __init__(self, model_name: str, cache_dir=None) -> None:
        self.model_name = model_name
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

            self._model = CrossEncoder(self.model_name, max_length=512)
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
            candidate.retrieval_score = float(score)
        return sorted(candidates, key=lambda c: c.retrieval_score, reverse=True)


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
    if mode == "cross_encoder":
        return CrossEncoderReranker(settings.reranker_model)

    # auto: try cross-encoder only if it's already cached locally.
    try:
        from sentence_transformers import CrossEncoder  # noqa: F401
        from huggingface_hub import try_to_load_from_cache  # noqa: F401

        cached = _model_cached(settings.reranker_model)
        if cached:
            return CrossEncoderReranker(settings.reranker_model)
        log.info("Reranker '%s' not cached; using offline lexical reranker.", settings.reranker_model)
    except Exception:
        pass
    return LexicalReranker()


def _model_cached(model_name: str) -> bool:
    """Best-effort check whether the HF hub cache holds the reranker model."""
    try:
        from huggingface_hub import snapshot_download
        import os

        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        snapshot_download(model_name, local_files_only=True)
        return True
    except Exception:
        return False
