"""RAG engine tests: citations, gating, context budget, memory separation."""

from __future__ import annotations

import pytest

from apex_ai.core.types import RetrievedChunk
from apex_ai.rag.context_builder import build_context
from apex_ai.rag.engine import RagEngine
from apex_ai.rag.prompts import INSUFFICIENT_EVIDENCE_ANSWER, build_messages
from apex_ai.rag.query_processing import QueryProcessor
from tests.conftest import DATA_DIR, FakeLLM


def test_ask_returns_answer_with_citations(engine):
    result = engine.ask("What temperature counts as a fever in adults?")
    assert result.answer
    assert result.citations, "an evidenced answer must carry citations"
    citation = result.citations[0]
    assert citation.source.endswith(".pdf") or citation.source.endswith(".md")
    assert citation.page is not None
    assert citation.text  # the source viewer needs the chunk text
    assert not result.insufficient_evidence


def test_citations_only_from_used_chunks(engine):
    """Fabricated-citation guard: every citation must be a chunk that was
    actually placed into the LLM context."""
    result = engine.ask("How should burns be cooled?")
    context = FakeLLM.last_messages
    assert context is not None
    user_content = next(m["content"] for m in context if m["role"] == "user")
    for citation in result.citations:
        header = f"SOURCE: {citation.source}"
        assert header in user_content
        assert citation.text in user_content or citation.text[:100] in user_content


def test_memory_is_not_evidence(engine):
    result = engine.ask("What is a fever?")
    system = next(m["content"] for m in FakeLLM.last_messages if m["role"] == "system")
    assert "NOT evidence" in system
    assert "Apex AI" in system


def test_medical_mode_adds_safety_addendum(engine):
    engine.ask("What is a fever?")
    system = next(m["content"] for m in FakeLLM.last_messages if m["role"] == "system")
    assert "NOT medical" in system


def test_low_confidence_refuses_without_llm_call(settings, store, ingestion):
    """Forced-high threshold -> honest refusal, LLM never called."""
    from dataclasses import replace

    from apex_ai.memory.conversation import ConversationMemory
    from apex_ai.retrieval.keyword import BM25Index
    from apex_ai.retrieval.pipeline import HybridRetriever
    from apex_ai.retrieval.reranker import LexicalReranker

    ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf")
    strict = replace(settings, min_similarity=0.99)

    class ExplodingLLM:
        name = "exploding"
        supports_streaming = False

        def generate(self, *a, **k):
            raise AssertionError("LLM must not be called when evidence is missing")

    engine = RagEngine(
        settings=strict,
        store=store,
        retriever=HybridRetriever(store, strict, BM25Index(store)),
        reranker=LexicalReranker(),
        memory=None,
        llm_provider=ExplodingLLM(),
    )
    result = engine.ask("What is a fever in adults?")
    assert result.insufficient_evidence
    assert "couldn't find enough information" in result.answer
    assert result.citations == []


def test_empty_index_gives_upload_hint(settings, store):
    from apex_ai.memory.conversation import ConversationMemory
    from apex_ai.retrieval.pipeline import HybridRetriever
    from apex_ai.retrieval.reranker import LexicalReranker

    engine = RagEngine(
        settings=settings, store=store,
        retriever=HybridRetriever(store, settings), reranker=LexicalReranker(),
        memory=ConversationMemory(settings.memory_path), llm_provider=FakeLLM(),
    )
    result = engine.ask("anything at all")
    assert "no indexed documents" in result.answer


def test_streaming_engine_emits_tokens_then_final(engine):
    events = list(engine.ask_stream("What temperature counts as a fever?"))
    types = [e["type"] for e in events]
    assert "token" in types
    assert types[-1] == "final"
    final = events[-1]["result"]
    assert final.citations


def test_memory_updated_after_answer(engine):
    before = len(engine.memory.turns)
    engine.ask("What is a fever?")
    assert len(engine.memory.turns) == before + 1


def test_use_memory_false_skips_history(engine):
    engine.ask("What temperature counts as a fever in adults?")
    engine.ask("What are fever warning signs in children?", use_memory=False)
    user_content = next(m["content"] for m in FakeLLM.last_messages if m["role"] == "user")
    assert "(no previous conversation)" in user_content
    # and with memory ON the prior turn appears in the history section:
    engine.ask("What about children under three months?")
    user_content = next(m["content"] for m in FakeLLM.last_messages if m["role"] == "user")
    assert "fever in adults" in user_content


# ---------------- context builder ----------------


def _chunk(id_, text, page=1, source="doc.pdf", section="Intro"):
    return RetrievedChunk(
        chunk_id=id_, text=text,
        metadata={"document_name": source, "page": page, "section": section},
    )


def test_context_builder_format():
    built = build_context([_chunk("1", "Relevant text here.")], char_limit=2000)
    assert "[1]" in built.text
    assert "SOURCE: doc.pdf" in built.text
    assert "PAGE: 1" in built.text
    assert "SECTION: Intro" in built.text
    assert built.used_chunks[0].chunk_id == "1"


def test_context_builder_respects_budget():
    chunks = [_chunk(str(i), "x" * 400, page=i) for i in range(1, 6)]
    built = build_context(chunks, char_limit=1000)
    assert len(built.text) <= 1100  # small slack for the always-keep-first rule
    assert len(built.used_chunks) < 5


def test_context_builder_never_returns_empty_for_nonempty_input():
    chunks = [_chunk("1", "y" * 5000)]
    built = build_context(chunks, char_limit=500)
    assert built.used_chunks
    assert "truncated" in built.text


# ---------------- query processing ----------------


def test_query_processor_disabled_returns_original():
    qp = QueryProcessor(enabled=False, llm_provider=FakeLLM())
    assert qp.expand("what about it?", history=[{"user": "q", "assistant": "a"}]) == ["what about it?"]


def test_query_processor_keeps_original_first():
    calls = []

    class RecordingLLM(FakeLLM):
        def generate(self, prompt=None, **kwargs):
            calls.append(prompt)
            return "normal body temperature adult fever threshold"

    qp = QueryProcessor(enabled=True, llm_provider=RecordingLLM())
    queries = qp.expand(
        "what about it?",
        history=[{"user": "What is a fever in adults?", "assistant": "38 C or higher."}],
    )
    assert queries[0] == "what about it?"
    assert len(queries) >= 2
    assert calls  # the rewrite prompt was used


def test_decompose_splits_multi_part():
    class SubQueryLLM(FakeLLM):
        def generate(self, prompt=None, **kwargs):
            if "Break the question" in (prompt or ""):
                return "What treats dehydration?\nWhat treats fever?"
            return ""

    qp = QueryProcessor(enabled=True, llm_provider=SubQueryLLM())
    queries = qp.expand("What treats dehydration and also what treats fever?")
    assert queries[0].startswith("What treats dehydration and also")
    assert "What treats fever?" in queries


def test_llm_failure_in_rewrite_degrades_gracefully():
    class BrokenLLM(FakeLLM):
        def generate(self, prompt=None, **kwargs):
            raise RuntimeError("provider down")

    qp = QueryProcessor(enabled=True, llm_provider=BrokenLLM())
    queries = qp.expand("what about it?", history=[{"user": "x", "assistant": "y"}])
    assert queries == ["what about it?"]  # original always survives
