"""The RAG engine: orchestrates one full question -> grounded answer turn.

    question
      -> QueryProcessor.expand()            (optional extra retrieval queries)
      -> HybridRetriever.retrieve()         (vector + BM25 candidates)
      -> Reranker.rerank()                  (pick strongest evidence)
      -> build_context()                    (budgeted SOURCE/PAGE/SECTION blocks)
      -> confidence gate                    (insufficient evidence? refuse honestly)
      -> LLMProvider.generate/stream        (grounded system prompt + evidence)
      -> citations (only from chunks actually used) + memory update

Data flow of one turn (``ask``):

    str question -> PreparedTurn -> AnswerResult(answer, citations, confidence)

The engine never lets conversation memory become evidence: memory only fills
the "history" section of the prompt, which the system prompt explicitly marks
as non-citable context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from apex_ai.core.logging import get_logger, timed
from apex_ai.core.types import AnswerResult, Citation, RetrievedChunk
from apex_ai.rag.context_builder import BuiltContext, build_context
from apex_ai.rag.prompts import INSUFFICIENT_EVIDENCE_ANSWER, build_messages
from apex_ai.rag.query_processing import QueryProcessor

log = get_logger("rag.engine")


@dataclass
class PreparedTurn:
    """Everything decided BEFORE the LLM is called (fully testable)."""

    question: str
    queries: list[str] = field(default_factory=list)
    candidates: list[RetrievedChunk] = field(default_factory=list)
    evidence: list[RetrievedChunk] = field(default_factory=list)
    context: BuiltContext | None = None
    history: list[dict] = field(default_factory=list)
    confidence: float = 0.0


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
    ) -> None:
        self.settings = settings
        self.store = store
        self.retriever = retriever
        self.reranker = reranker
        self.memory = memory
        self.llm = llm_provider
        self.query_processor = query_processor or QueryProcessor(enabled=False)
        self.medical_mode = medical_mode

    # -- preparation (retrieval + context; no LLM call) ----------------------

    def prepare(self, question: str, use_memory: bool = True) -> PreparedTurn:
        history = self.memory.recent() if (use_memory and self.memory) else []
        turn = PreparedTurn(question=question, history=history)

        with timed(log, "prepare turn"):
            turn.queries = self.query_processor.expand(question, history)
            turn.candidates = self.retriever.retrieve(turn.queries)
            if turn.candidates and self.reranker is not None:
                turn.evidence = self.reranker.rerank(question, turn.candidates)
            else:
                turn.evidence = turn.candidates
            turn.evidence = turn.evidence[: self.settings.rerank_top_k]
            turn.context = build_context(turn.evidence, self.settings.context_char_limit)
            turn.confidence = max((c.similarity for c in turn.candidates), default=0.0)
        return turn

    # -- answer paths -----------------------------------------------------------

    def _insufficient(self, turn: PreparedTurn, reason: str) -> AnswerResult:
        log.info("Insufficient evidence (%s): confidence=%.3f threshold=%.2f",
                 reason, turn.confidence, self.settings.min_similarity)
        return AnswerResult(
            answer=INSUFFICIENT_EVIDENCE_ANSWER,
            citations=[],
            confidence=round(turn.confidence, 4),
            insufficient_evidence=True,
            queries_used=turn.queries,
        )

    def _citations(self, context: BuiltContext) -> list[Citation]:
        """Citations strictly from chunks that entered the LLM context."""
        return [
            Citation(
                index=index,
                source=chunk.metadata.get("document_name", "unknown"),
                page=chunk.metadata.get("page"),
                section=chunk.metadata.get("section", ""),
                chunk_id=chunk.chunk_id,
                text=chunk.text,
                score=chunk.retrieval_score,
            )
            for index, chunk in enumerate(context.used_chunks, start=1)
        ]

    def _finalize(self, turn: PreparedTurn, answer: str, elapsed: float) -> AnswerResult:
        answer = (answer or "").strip()
        answer = _strip_preamble(answer)
        citations = self._citations(turn.context)

        result = AnswerResult(
            answer=answer,
            citations=citations,
            confidence=round(turn.confidence, 4),
            insufficient_evidence=False,
            queries_used=turn.queries,
            timings={"total_s": round(elapsed, 2)},
        )

        full_answer = f"{answer}\n\n{result.sources_block}" if citations else answer
        if self.memory is not None:
            self.memory.add(turn.question, full_answer)
        log.info("Answered question (%d evidence chunk(s), %.2fs)", len(citations), elapsed)
        return result

    def ask(self, question: str, use_memory: bool = True) -> AnswerResult:
        """Full non-streaming turn. Returns AnswerResult (see core.types)."""
        import time

        if not question or not question.strip():
            return AnswerResult(answer="Please ask a question first.")

        if self.store.count() == 0:
            return AnswerResult(
                answer="There are no indexed documents yet. Open the Documents tab and "
                       "upload a PDF, TXT, Markdown or JSON file first.",
                insufficient_evidence=True,
            )

        start = time.perf_counter()
        turn = self.prepare(question, use_memory)

        if not turn.context.used_chunks:
            return self._insufficient(turn, "no candidates")
        if turn.confidence < self.settings.min_similarity:
            return self._insufficient(turn, "low similarity")

        messages = build_messages(
            question, turn.context.text, turn.history, medical=self.medical_mode
        )
        try:
            answer = self.llm.generate(
                messages=messages, max_tokens=768, temperature=0.2
            )
        except Exception as error:
            log.error("Generation failed: %s", error)
            raise
        return self._finalize(turn, answer, time.perf_counter() - start)

    def ask_stream(
        self, question: str, use_memory: bool = True
    ) -> Iterator[dict]:
        """Streaming variant.

        Yields {"type": "token", "text": ...} events, then a final
        {"type": "final", "result": AnswerResult}. The UI appends tokens to
        the answer box and uses the final event for citations.
        """
        import time

        if not question or not question.strip():
            yield {"type": "final", "result": AnswerResult(answer="Please ask a question first.")}
            return

        if self.store.count() == 0:
            yield {
                "type": "final",
                "result": AnswerResult(
                    answer="There are no indexed documents yet. Open the Documents tab and "
                           "upload a PDF, TXT, Markdown or JSON file first.",
                    insufficient_evidence=True,
                ),
            }
            return

        start = time.perf_counter()
        turn = self.prepare(question, use_memory)

        if not turn.context.used_chunks or turn.confidence < self.settings.min_similarity:
            yield {"type": "final", "result": self._insufficient(turn, "gate")}
            return

        messages = build_messages(
            question, turn.context.text, turn.history, medical=self.medical_mode
        )
        parts: list[str] = []
        try:
            for token in self.llm.stream(messages=messages, max_tokens=768, temperature=0.2):
                parts.append(token)
                yield {"type": "token", "text": token}
        except Exception as error:
            log.error("Streaming generation failed: %s", error)
            raise

        result = self._finalize(turn, "".join(parts), time.perf_counter() - start)
        yield {"type": "final", "result": result}


def _strip_preamble(answer: str) -> str:
    """Small models sometimes echo a label; tidy the common cases."""
    lowered = answer.lower()
    for prefix in ("answer:", "detailed answer:", "response:"):
        if lowered.startswith(prefix):
            return answer[len(prefix):].lstrip()
    return answer
