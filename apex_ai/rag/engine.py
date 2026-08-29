"""RAG orchestration from question through grounded answer and real citations.

The engine composes the existing subsystems rather than replacing them:
query analysis -> hybrid retrieval -> optional reranking -> context building ->
evidence gate -> configured LLM. Conversation history can help retrieve a
follow-up but is never document evidence and can never become a citation.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field

from apex_ai.core.errors import ApexError, ProviderError
from apex_ai.core.logging import get_logger, log_event
from apex_ai.core.types import AnswerResult, Citation, RetrievedChunk
from apex_ai.memory.context import ConversationContext, build_conversation_context
from apex_ai.memory.relevance import format_memory_text, select_relevant_memories
from apex_ai.rag.context_builder import BuiltContext, build_context
from apex_ai.rag.prompts import INSUFFICIENT_EVIDENCE_ANSWER, build_messages
from apex_ai.rag.query_processing import QueryProcessor, QueryTrace
from apex_ai.retrieval.keyword import tokenize
from apex_ai.retrieval.pipeline import RetrievalTrace

log = get_logger("rag.engine")

_EXACT_ANCHOR = re.compile(
    r'"[^"]+"|\b[A-Z]{2,}\b|\b\w*\d\w*(?:[-./:]\w+)*\b|'
    r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b"
)

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "did",
    "do",
    "does",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "would",
    "you",
}


@dataclass
class PreparedTurn:
    """Everything decided before generation (fully testable and inspectable)."""

    question: str
    queries: list[str] = field(default_factory=list)
    candidates: list[RetrievedChunk] = field(default_factory=list)
    reranked_candidates: list[RetrievedChunk] = field(default_factory=list)
    evidence: list[RetrievedChunk] = field(default_factory=list)
    context: BuiltContext | None = None
    history: list[dict] = field(default_factory=list)
    conversation_context: ConversationContext | None = None
    memory_text: str = ""
    summary_text: str = ""
    confidence: float = 0.0
    lexical_support: float = 0.0
    exact_anchor_support: float = 0.0
    supported: bool = False
    support_reason: str = "no evidence"
    timings: dict[str, float] = field(default_factory=dict)
    query_trace: QueryTrace | None = None
    retrieval_trace: RetrievalTrace | None = None
    errors: list[str] = field(default_factory=list)
    semantic_threshold: float = 0.0
    lexical_threshold: float = 0.0

    def diagnostics(self) -> dict:
        """Bounded developer output; not included in normal chat responses."""
        evidence_ids = {chunk.chunk_id for chunk in self.evidence}
        return {
            "question": self.question,
            "queries": list(self.queries),
            "query_processing": self.query_trace.to_dict() if self.query_trace else None,
            "retrieval": self.retrieval_trace.to_dict() if self.retrieval_trace else None,
            "conversation_context": (
                self.conversation_context.diagnostics(include_text=True)
                if self.conversation_context
                else None
            ),
            "memory_text": self.memory_text or None,
            "summary_text": self.summary_text or None,
            "reranked_evidence": [
                {
                    "rank": rank,
                    "chunk_id": chunk.chunk_id,
                    "source": chunk.source,
                    "page_start": chunk.metadata.get("page_start", chunk.page),
                    "page_end": chunk.metadata.get("page_end", chunk.page),
                    "section": chunk.section,
                    "semantic_similarity": round(float(chunk.similarity), 6),
                    "score": round(float(chunk.retrieval_score), 8),
                    "selected_for_context_builder": chunk.chunk_id in evidence_ids,
                    "excerpt": chunk.text[:240],
                }
                for rank, chunk in enumerate(self.reranked_candidates, start=1)
            ],
            "context": self.context.diagnostics() if self.context else None,
            "final_context": self.context.text if self.context else "",
            "gate": {
                "supported": self.supported,
                "reason": self.support_reason,
                "semantic_similarity": round(self.confidence, 6),
                "semantic_threshold": self.semantic_threshold,
                "lexical_support": round(self.lexical_support, 6),
                "lexical_threshold": self.lexical_threshold,
                "exact_anchor_support": round(self.exact_anchor_support, 6),
            },
            "timings_ms": dict(self.timings),
            "errors": list(self.errors),
        }


class RagEngine:
    def __init__(
        self,
        settings,
        store,
        retriever,
        reranker,
        memory,
        llm_provider,
        query_processor: QueryProcessor | None = None,
        medical_mode: bool = True,
        long_term_memory=None,
        user_id: str = "",
    ) -> None:
        self.settings = settings
        self.store = store
        self.retriever = retriever
        self.reranker = reranker
        self.memory = memory
        self.llm = llm_provider
        self.query_processor = query_processor or QueryProcessor(enabled=False)
        self.medical_mode = medical_mode
        # Phase 47: optional and separate from ``memory`` above. ``memory`` is
        # short-term conversation history; this is explicitly user-confirmed
        # long-term preferences/context (Phase 42/45/46). Both stay out of
        # document evidence and citations either way.
        self.long_term_memory = long_term_memory
        # Phase 55: whose confirmed memory to read. Empty only for callers that
        # never wire long_term_memory in the first place (most existing tests);
        # any real long_term_memory store now requires a real user_id to query.
        self.user_id = user_id

    # -- preparation (retrieval + context; no generation) -----------------

    def _context_budget(self, question: str, history_text: str) -> int:
        """Constrain document evidence by the exact prepared history and model window.

        Four characters/token is an approximation, not tokenizer accounting;
        the debug trace and reports call out this limitation. The explicit
        reserve leaves room for instructions and output. Conversation history
        is built once, bounded independently, and accounted for here exactly.
        """
        context_tokens = max(1, int(getattr(self.settings, "llm_context_size", 4096)))
        reserve = max(0, int(getattr(self.settings, "context_token_reserve", 1024)))
        dynamic_chars = len(question) + len(history_text)
        dynamic_tokens = (dynamic_chars + 3) // 4
        available_tokens = max(50, context_tokens - reserve - dynamic_tokens)
        model_budget = available_tokens * 4
        return max(0, min(int(self.settings.context_char_limit), model_budget))

    @staticmethod
    def _lexical_evidence_score(
        queries: list[str], chunks: list[RetrievedChunk]
    ) -> tuple[float, int, float]:
        """Best informative-token coverage and exact-anchor coverage."""
        best = 0.0
        best_matches = 0
        best_anchor_support = 0.0
        search_texts = [f"{chunk.section}\n{chunk.text}" for chunk in chunks]
        chunk_term_sets = [set(tokenize(text)) for text in search_texts]
        folded_texts = [text.casefold() for text in search_texts]

        for query in queries:
            query_terms = {
                term
                for term in tokenize(query)
                if term not in _STOPWORDS and (len(term) > 1 or term.isdigit())
            }
            if query_terms:
                for chunk_terms in chunk_term_sets:
                    matches = len(query_terms & chunk_terms)
                    score = matches / len(query_terms)
                    if score > best:
                        best = score
                        best_matches = matches

            anchors = [match.group(0).strip('"') for match in _EXACT_ANCHOR.finditer(query)]
            if anchors:
                for text in folded_texts:
                    matched = sum(anchor.casefold() in text for anchor in anchors)
                    best_anchor_support = max(best_anchor_support, matched / len(anchors))
        return best, best_matches, best_anchor_support

    def _apply_evidence_gate(self, turn: PreparedTurn) -> None:
        used = turn.context.used_chunks if turn.context else []
        turn.confidence = max((chunk.similarity for chunk in used), default=0.0)
        (
            turn.lexical_support,
            lexical_matches,
            turn.exact_anchor_support,
        ) = self._lexical_evidence_score(turn.queries or [turn.question], used)
        semantic_without_words = max(0.65, float(self.settings.min_similarity) + 0.20)
        if not used:
            turn.supported = False
            turn.support_reason = "no context chunks"
        elif turn.confidence >= self.settings.min_similarity and (
            lexical_matches >= 1 or turn.confidence >= semantic_without_words
        ):
            # Requiring one informative word near the regular threshold blocks
            # generic embedding matches. A genuinely strong paraphrase can
            # still pass without literal overlap.
            turn.supported = True
            turn.support_reason = "semantic threshold met with corroboration"
        elif turn.exact_anchor_support >= 1.0 and lexical_matches >= 1:
            # Exact IDs, dates, numbers, names, and abbreviations survive weak
            # embedding scores without opening a broad lexical-only gate.
            turn.supported = True
            turn.support_reason = "exact lexical anchor matched"
        elif lexical_matches >= 3 and turn.lexical_support >= turn.lexical_threshold:
            turn.supported = True
            turn.support_reason = "strong multi-term lexical evidence"
        else:
            turn.supported = False
            turn.support_reason = "semantic and lexical evidence below thresholds"

    def prepare(
        self,
        question: str,
        use_memory: bool = True,
        history_override: list[dict] | None = None,
    ) -> PreparedTurn:
        prepare_started = time.perf_counter()
        history_stage = time.perf_counter()
        raw_history = (
            history_override
            if history_override is not None
            else (self.memory.recent() if (use_memory and self.memory) else [])
        )
        conversation_context = build_conversation_context(
            raw_history,
            max_turns=int(getattr(self.settings, "history_turns", 3)),
            char_limit=int(getattr(self.settings, "history_char_limit", 2400)),
            message_char_limit=int(
                getattr(self.settings, "history_message_char_limit", 1000)
            ),
        )
        turn = PreparedTurn(
            question=question,
            history=conversation_context.turns,
            conversation_context=conversation_context,
            semantic_threshold=float(self.settings.min_similarity),
            lexical_threshold=float(
                getattr(self.settings, "lexical_support_threshold", 0.60)
            ),
        )
        turn.timings["conversation_context"] = round(
            (time.perf_counter() - history_stage) * 1000, 3
        )

        # Phase 50: an optional summary of turns older than what conversation_context
        # shows in full. Duck-typed like ``memory.recent()``/``memory.add()`` above —
        # only ConversationMemoryAdapter (the SQLite-backed web chat memory) currently
        # implements it; the legacy JSON ConversationMemory does not, and is skipped.
        if use_memory and self.memory is not None:
            summary_getter = getattr(self.memory, "summary_text", None)
            if callable(summary_getter):
                try:
                    turn.summary_text = summary_getter() or ""
                except Exception as error:  # noqa: BLE001 - optional continuity boundary
                    turn.errors.append(f"summary: {type(error).__name__}: {error}")
                    log.warning(
                        "Conversation-summary lookup failed; continuing without it "
                        "(error_type=%s)",
                        type(error).__name__,
                    )

        memory_stage = time.perf_counter()
        if use_memory and self.long_term_memory and getattr(
            self.settings, "memory_prompt_use", True
        ):
            try:
                confirmed = self.long_term_memory.list(self.user_id, limit=50)
                relevant = select_relevant_memories(question, confirmed)
                turn.memory_text = format_memory_text(relevant)
            except Exception as error:  # noqa: BLE001 - optional personalization boundary
                # Long-term memory is explicitly optional (Phase 42): a failure here
                # must degrade to no personalization, never break the chat turn.
                turn.errors.append(f"memory: {type(error).__name__}: {error}")
                log.warning(
                    "Relevant-memory selection failed; continuing without it "
                    "(error_type=%s)",
                    type(error).__name__,
                )
        turn.timings["memory_retrieval"] = round(
            (time.perf_counter() - memory_stage) * 1000, 3
        )

        stage = time.perf_counter()
        if hasattr(self.query_processor, "expand_with_trace"):
            turn.queries, turn.query_trace = self.query_processor.expand_with_trace(
                question, turn.history
            )
            turn.errors.extend(turn.query_trace.errors)
        else:  # compatibility with custom processors implementing only expand()
            turn.queries = self.query_processor.expand(question, turn.history)
        turn.timings["query_processing"] = round((time.perf_counter() - stage) * 1000, 3)

        stage = time.perf_counter()
        if hasattr(self.retriever, "retrieve_with_trace"):
            run = self.retriever.retrieve_with_trace(
                turn.queries,
                self.user_id,
                include_debug=bool(getattr(self.settings, "rag_debug", False)),
            )
            turn.candidates = run.chunks
            turn.retrieval_trace = run.trace
            turn.errors.extend(run.trace.errors)
        else:  # compatibility with custom retrievers
            turn.candidates = self.retriever.retrieve(turn.queries, self.user_id)
        turn.timings["retrieval"] = round((time.perf_counter() - stage) * 1000, 3)
        if turn.retrieval_trace:
            for name, duration in turn.retrieval_trace.timings_ms.items():
                turn.timings[f"retrieval_{name}"] = duration

        stage = time.perf_counter()
        if turn.candidates and self.reranker is not None:
            try:
                turn.reranked_candidates = self.reranker.rerank(
                    question, turn.candidates
                )
                fallback_error = next(
                    (
                        chunk.metadata.get("_reranker_fallback")
                        for chunk in turn.reranked_candidates
                        if chunk.metadata.get("_reranker_fallback")
                    ),
                    None,
                )
                if fallback_error:
                    turn.errors.append(f"reranker fallback: {fallback_error}")
            except Exception as error:
                # Reranking is optional. Keep fused ranking if a configured
                # model disappears, cannot load offline, or fails at runtime.
                message = f"reranker: {type(error).__name__}: {error}"
                turn.errors.append(message)
                log.warning(
                    "Reranker failed; using fused retrieval order (error_type=%s)",
                    type(error).__name__,
                )
                turn.reranked_candidates = list(turn.candidates)
        else:
            turn.reranked_candidates = list(turn.candidates)
        turn.evidence = turn.reranked_candidates[: self.settings.rerank_top_k]
        turn.timings["rerank"] = round((time.perf_counter() - stage) * 1000, 3)

        stage = time.perf_counter()
        try:
            turn.context = build_context(
                turn.evidence,
                self._context_budget(question, conversation_context.text),
            )
        except Exception as error:
            message = f"context: {type(error).__name__}: {error}"
            turn.errors.append(message)
            log.exception("Context construction failed; refusing this turn")
            turn.context = BuiltContext(text="", used_chunks=[], char_limit=0)
        turn.timings["context"] = round((time.perf_counter() - stage) * 1000, 3)

        self._apply_evidence_gate(turn)
        turn.timings["prepare_total"] = round(
            (time.perf_counter() - prepare_started) * 1000, 3
        )
        return turn

    # -- answer paths -----------------------------------------------------

    def _generation_kwargs(self) -> dict[str, int | float]:
        """Generation controls from the centralized environment-backed settings."""
        return {
            "max_tokens": self.settings.generation_max_tokens,
            "temperature": self.settings.generation_temperature,
        }

    def _insufficient(self, turn: PreparedTurn, reason: str | None = None) -> AnswerResult:
        reason = reason or turn.support_reason
        log_event(
            log,
            logging.INFO,
            "rag.insufficient_evidence",
            "Answer generation skipped because evidence was insufficient",
            reason=reason,
            semantic_score=round(turn.confidence, 4),
            semantic_threshold=self.settings.min_similarity,
            lexical_score=round(turn.lexical_support, 4),
        )
        timings = dict(turn.timings)
        timings.setdefault("generation", 0.0)
        timings["total"] = timings.get("prepare_total", 0.0)
        timings["total_s"] = round(timings["total"] / 1000, 3)
        return AnswerResult(
            answer=INSUFFICIENT_EVIDENCE_ANSWER,
            citations=[],
            confidence=round(turn.confidence, 4),
            insufficient_evidence=True,
            queries_used=turn.queries,
            timings=timings,
        )

    def _citations(self, context: BuiltContext) -> list[Citation]:
        """Create citations strictly from chunks that entered model context."""
        return [
            Citation(
                index=index,
                source=chunk.metadata.get(
                    "document_name", chunk.metadata.get("filename", "unknown")
                ),
                page=chunk.metadata.get("page_start", chunk.metadata.get("page")),
                page_end=chunk.metadata.get(
                    "page_end", chunk.metadata.get("page_start", chunk.metadata.get("page"))
                ),
                section=chunk.metadata.get("section", ""),
                chunk_id=chunk.chunk_id,
                text=chunk.text,
                score=chunk.retrieval_score,
            )
            for index, chunk in enumerate(context.used_chunks, start=1)
        ]

    def _finalize(
        self,
        turn: PreparedTurn,
        answer: str,
        elapsed: float,
        generation_elapsed: float,
        *,
        update_memory: bool = True,
    ) -> AnswerResult:
        answer = _strip_preamble((answer or "").strip())
        if not answer:
            raise ProviderError(
                what="The configured language model returned an empty answer.",
                fix="Retry once, then check the selected model/provider and logs/apex.log.",
            )
        citations = self._citations(turn.context)
        timings = dict(turn.timings)
        timings["generation"] = round(generation_elapsed * 1000, 3)
        timings["total"] = round(elapsed * 1000, 3)
        timings["total_s"] = round(elapsed, 3)  # backward-compatible unit/key

        result = AnswerResult(
            answer=answer,
            citations=citations,
            confidence=round(turn.confidence, 4),
            insufficient_evidence=False,
            queries_used=turn.queries,
            timings=timings,
            context_chunk_ids=[chunk.chunk_id for chunk in turn.context.used_chunks],
            context_text=turn.context.text,
        )

        full_answer = f"{answer}\n\n{result.sources_block}" if citations else answer
        if update_memory and self.memory is not None:
            self.memory.add(turn.question, full_answer)
        log_event(
            log,
            logging.INFO,
            "rag.answer_completed",
            "Grounded answer completed",
            evidence_chunk_count=len(citations),
            duration_ms=round(elapsed * 1000, 3),
        )
        return result

    @staticmethod
    def _provider_failure(error: Exception) -> ApexError:
        if isinstance(error, ApexError):
            return error
        return ProviderError(
            what="The configured language model could not generate this answer.",
            why=f"The provider raised {type(error).__name__}; technical details are in the log.",
            fix=(
                "Check the selected model/provider and logs/apex.log, then retry. "
                "Your indexed documents were not changed."
            ),
        )

    def debug(
        self,
        question: str,
        use_memory: bool = True,
        *,
        generate: bool = True,
    ) -> dict:
        """Run one developer trace without writing a conversation-memory turn.

        This method is invoked only by the configuration-gated debug endpoint.
        It returns the exact prepared context and, when requested and supported,
        the real configured model response plus its real context-backed sources.
        """
        question = (question or "").strip()
        if not question:
            return {
                "question": question,
                "model_response": "Please ask a question first.",
                "sources": [],
                "generation_skipped": True,
            }
        if self.store.count(self.user_id) == 0:
            return {
                "question": question,
                "model_response": (
                    "There are no indexed documents yet. Upload a supported document first."
                ),
                "sources": [],
                "generation_skipped": True,
            }

        started = time.perf_counter()
        turn = self.prepare(question, use_memory)
        if not turn.supported:
            result = self._insufficient(turn)
            generation_skipped = True
        elif not generate:
            result = AnswerResult(
                answer="",
                confidence=round(turn.confidence, 4),
                queries_used=turn.queries,
                timings=dict(turn.timings),
                context_chunk_ids=[
                    chunk.chunk_id for chunk in turn.context.used_chunks
                ],
            )
            generation_skipped = True
        else:
            messages = build_messages(
                question,
                turn.context.text,
                turn.history,
                medical=self.medical_mode,
                history_text=turn.conversation_context.text,
                memory_text=turn.memory_text,
                summary_text=turn.summary_text,
            )
            generation_started = time.perf_counter()
            try:
                answer = self.llm.generate(
                    messages=messages,
                    **self._generation_kwargs(),
                )
            except Exception as error:
                log.exception("Debug generation failed")
                raise self._provider_failure(error) from error
            result = self._finalize(
                turn,
                answer,
                time.perf_counter() - started,
                time.perf_counter() - generation_started,
                update_memory=False,
            )
            generation_skipped = False

        payload = turn.diagnostics()
        payload["model_response"] = result.answer
        payload["sources"] = [
            {
                **citation.to_dict(),
                "label": citation.label(),
                "text": citation.text,
            }
            for citation in result.citations
        ]
        payload["generation_skipped"] = generation_skipped
        payload["timings_ms"] = {
            key: value for key, value in result.timings.items() if key != "total_s"
        }
        return payload

    def ask(
        self,
        question: str,
        use_memory: bool = True,
        *,
        history_override: list[dict] | None = None,
    ) -> AnswerResult:
        """Full non-streaming turn.

        ``history_override`` exists for deterministic evaluation fixtures;
        normal UI/API callers use the configured conversation memory.
        """
        if not question or not question.strip():
            return AnswerResult(answer="Please ask a question first.")

        if self.store.count(self.user_id) == 0:
            return AnswerResult(
                answer="There are no indexed documents yet. Open the Documents tab and "
                "upload a PDF, TXT, Markdown or JSON file first.",
                insufficient_evidence=True,
            )

        started = time.perf_counter()
        turn = self.prepare(question, use_memory, history_override=history_override)
        if not turn.supported:
            return self._insufficient(turn)

        messages = build_messages(
            question,
            turn.context.text,
            turn.history,
            medical=self.medical_mode,
            history_text=turn.conversation_context.text,
            memory_text=turn.memory_text,
            summary_text=turn.summary_text,
        )
        generation_started = time.perf_counter()
        try:
            answer = self.llm.generate(messages=messages, **self._generation_kwargs())
        except Exception as error:
            log.exception("Generation failed")
            raise self._provider_failure(error) from error
        return self._finalize(
            turn,
            answer,
            time.perf_counter() - started,
            time.perf_counter() - generation_started,
        )

    def ask_stream(self, question: str, use_memory: bool = True) -> Iterator[dict]:
        """Yield real provider tokens, then one final AnswerResult event."""
        if not question or not question.strip():
            yield {"type": "final", "result": AnswerResult(answer="Please ask a question first.")}
            return

        if self.store.count(self.user_id) == 0:
            yield {
                "type": "final",
                "result": AnswerResult(
                    answer="There are no indexed documents yet. Open the Documents tab and "
                    "upload a PDF, TXT, Markdown or JSON file first.",
                    insufficient_evidence=True,
                ),
            }
            return

        started = time.perf_counter()
        turn = self.prepare(question, use_memory)
        if not turn.supported:
            yield {"type": "final", "result": self._insufficient(turn)}
            return

        messages = build_messages(
            question,
            turn.context.text,
            turn.history,
            medical=self.medical_mode,
            history_text=turn.conversation_context.text,
            memory_text=turn.memory_text,
            summary_text=turn.summary_text,
        )
        generation_started = time.perf_counter()
        parts: list[str] = []
        try:
            for token in self.llm.stream(messages=messages, **self._generation_kwargs()):
                parts.append(token)
                yield {"type": "token", "text": token}
        except Exception as error:
            log.exception("Streaming generation failed")
            raise self._provider_failure(error) from error

        result = self._finalize(
            turn,
            "".join(parts),
            time.perf_counter() - started,
            time.perf_counter() - generation_started,
        )
        yield {"type": "final", "result": result}


def _strip_preamble(answer: str) -> str:
    """Small models sometimes echo a label; tidy common cases."""
    lowered = answer.lower()
    for prefix in ("answer:", "detailed answer:", "response:"):
        if lowered.startswith(prefix):
            return answer[len(prefix) :].lstrip()
    return answer
