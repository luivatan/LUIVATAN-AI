"""Phase 2 RAG regressions: structure, exact retrieval, fallbacks, and traces."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from apex_ai.config.settings import Settings
from apex_ai.core.errors import ProviderError, RerankerUnavailableError
from apex_ai.core.types import RetrievedChunk
from apex_ai.documents.chunking import Chunker
from apex_ai.documents.models import Document, Page
from apex_ai.evaluation.runner import load_dataset
from apex_ai.rag.context_builder import build_context
from apex_ai.rag.query_processing import QueryProcessor
from apex_ai.retrieval.keyword import BM25Index, tokenize
from apex_ai.retrieval.pipeline import HybridRetriever
from apex_ai.retrieval.reranker import FallbackReranker, make_reranker
from tests.conftest import USER, FakeLLM


def _chunk(
    chunk_id: str,
    text: str,
    *,
    source: str = "source.md",
    page: int = 1,
    document_id: str | None = None,
    score: float = 0.0,
    similarity: float = 0.0,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        text=text,
        metadata={
            "document_id": document_id or source,
            "document_name": source,
            "page": page,
            "page_start": page,
            "page_end": page,
            "section": "Test section",
            "chunk_index": 1,
        },
        retrieval_score=score,
        similarity=similarity,
    )


def test_spanning_section_retains_paragraph_pages_and_heading_boundaries():
    document = Document(
        document_id="d" * 64,
        document_name="pages.txt",
        source_path="pages.txt",
        file_type="txt",
        pages=[
            Page(1, "Shared Guidance\n\n" + "Page one sentence. " * 15),
            Page(
                2,
                "Continuation facts on second page include CODE-222. "
                + "More text. " * 15
                + "\n\nNext Heading\n\nTiny separate fact.",
            ),
        ],
    )
    chunks = Chunker(
        Settings(
            chunk_size=180,
            max_chunk_size=240,
            min_chunk_size=200,
            chunk_overlap=40,
        )
    ).build_chunks(document)

    second_page = next(chunk for chunk in chunks if "CODE-222" in chunk.text)
    assert second_page.metadata["page_start"] == 2
    assert second_page.metadata["page_end"] == 2
    assert second_page.metadata["page_number"] == 2
    assert second_page.metadata["section"] == "Shared Guidance"
    tiny = next(chunk for chunk in chunks if "Tiny separate fact" in chunk.text)
    assert tiny.metadata["section"] == "Next Heading"
    assert "CODE-222" not in tiny.text  # tiny merge must not cross headings


def test_chunk_metadata_is_auditable_and_overlap_never_breaks_hard_limit():
    document = Document(
        document_id="m" * 64,
        document_name="metadata.md",
        source_path="metadata.md",
        file_type="md",
        pages=[Page(1, "# Records\n\n" + "A complete sentence. " * 80)],
    )
    settings = Settings(
        chunk_size=180,
        max_chunk_size=220,
        min_chunk_size=0,
        chunk_overlap=80,
    )
    chunks = Chunker(settings).build_chunks(document)
    assert all(len(chunk.text) <= settings.max_chunk_size for chunk in chunks)
    for chunk in chunks:
        assert chunk.metadata["chunk_id"] == chunk.chunk_id
        assert chunk.metadata["filename"] == "metadata.md"
        assert chunk.metadata["character_count"] == len(chunk.text)
        assert len(chunk.metadata["content_sha256"]) == 64
        assert chunk.metadata["page_start"] <= chunk.metadata["page_end"]


def test_identifier_tokenization_preserves_whole_and_components():
    tokens = tokenize("Ticket XJ-420 shipped 2026-08-27 under SLA v2.1")
    assert {"xj-420", "xj", "420", "2026-08-27", "sla", "v2.1"} <= set(tokens)


def test_bm25plus_filters_no_overlap_baseline():
    class Store:
        version = 0

        @staticmethod
        def get_all_chunks(user_id):
            return [_chunk("1", "alpha beta"), _chunk("2", "gamma delta")]

    index = BM25Index(Store())
    assert index.search("totally-unrelated-identifier", USER) == []


def test_bm25_finds_exact_name_number_and_date(ingestion, store, tmp_path):
    document = tmp_path / "records.md"
    document.write_text(
        "# Release Record\n\nMira Chen approved budget 12.4 on 2026-04-17 for APX-447.",
        encoding="utf-8",
    )
    ingestion.ingest_path(document, USER)
    index = BM25Index(store)
    for query, expected in (
        ("Mira Chen", "Mira Chen"),
        ("12.4", "12.4"),
        ("2026-04-17", "2026-04-17"),
        ("APX-447", "APX-447"),
    ):
        hits = index.search(query, USER, k=1)
        assert hits and expected in hits[0].text


def test_semantic_failure_falls_back_to_exact_bm25(settings):
    class Store:
        version = 0

        @staticmethod
        def search(query, user_id, k, document_ids=None):
            raise RuntimeError("embedding unavailable")

        @staticmethod
        def get_all_chunks(user_id):
            return [_chunk("exact", "The release identifier is APX-447.")]

    run = HybridRetriever(Store(), settings).retrieve_with_trace(["APX-447"], USER)
    assert [chunk.chunk_id for chunk in run.chunks] == ["exact"]
    assert run.trace.errors and run.trace.keyword_counts == [1]
    assert run.chunks[0].metadata["_retrieval_channels"] == ["keyword"]


def test_keyword_failure_falls_back_to_semantic(settings):
    hit = _chunk("semantic", "meaningfully related", similarity=0.82)

    class Store:
        @staticmethod
        def search(query, user_id, k, document_ids=None):
            return [hit]

    class BrokenKeyword:
        @staticmethod
        def search(query, user_id, k, document_ids=None):
            raise RuntimeError("BM25 unavailable")

    run = HybridRetriever(Store(), settings, BrokenKeyword()).retrieve_with_trace(
        ["related"], USER
    )
    assert run.chunks[0].chunk_id == "semantic"
    assert run.trace.errors and run.trace.semantic_counts == [1]


def test_semantic_and_keyword_candidate_pool_sizes_are_honored(settings):
    requested = {}

    class Store:
        @staticmethod
        def search(query, user_id, k, document_ids=None):
            requested["semantic"] = k
            return []

    class Keyword:
        @staticmethod
        def search(query, user_id, k, document_ids=None):
            requested["keyword"] = k
            return []

    configured = replace(
        settings,
        top_k=12,
        semantic_candidate_k=2,
        keyword_candidate_k=3,
    )
    HybridRetriever(Store(), configured, Keyword()).retrieve(["query"], USER)
    assert requested == {"semantic": 2, "keyword": 3}


def test_unavailable_cross_encoder_degrades_to_lexical(settings, monkeypatch):
    reranker = make_reranker(
        replace(
            settings,
            reranker_mode="cross_encoder",
            reranker_model="definitely/missing-reranker",
            offline=True,
        )
    )
    assert isinstance(reranker, FallbackReranker)

    def unavailable():
        raise RerankerUnavailableError(what="not cached")

    monkeypatch.setattr(reranker.primary, "_ensure_model", unavailable)
    candidates = [_chunk("a", "unrelated words"), _chunk("b", "APX-447 release record")]
    ranked = reranker.rerank("APX-447", candidates)
    assert ranked[0].chunk_id == "b"
    assert ranked[0].metadata["_reranker_fallback"]


def test_simple_query_is_not_rewritten_or_decomposed():
    processor = QueryProcessor(enabled=True, llm_rewrite=False)
    question = "What is the APX-447 release date?"
    history = [{"user": "An unrelated earlier question", "assistant": "An answer"}]
    assert processor.expand(question, history) == [question]


def test_followup_expansion_preserves_exact_terminology_without_llm():
    processor = QueryProcessor(enabled=True, llm_rewrite=False)
    queries, trace = processor.expand_with_trace(
        "When was it approved?",
        [{"user": "Tell me about APX-447 and SLA v2.1.", "assistant": "A release."}],
    )
    assert queries[0] == "When was it approved?"
    assert "APX-447" in queries[1] and "SLA v2.1" in queries[1]
    assert trace.follow_up and trace.strategies == ["history_expansion"]


def test_llm_rewrite_that_drops_names_and_ids_is_rejected():
    class DroppingLLM(FakeLLM):
        def generate(self, prompt=None, **kwargs):
            return "generic approval date"

    processor = QueryProcessor(enabled=True, llm_provider=DroppingLLM())
    queries, trace = processor.expand_with_trace(
        "When was it approved?",
        [{"user": "Did Mira Chen approve APX-447?", "assistant": "Unknown."}],
    )
    assert len(queries) == 2
    assert "Mira Chen" in queries[1] and "APX-447" in queries[1]
    assert "llm_rewrite" not in trace.strategies
    assert any("rejected" in error for error in trace.errors)


def test_multi_part_processing_adds_variants_but_keeps_original():
    question = "What is APX-447? And what does RTO mean?"
    queries, trace = QueryProcessor(enabled=True, llm_rewrite=False).expand_with_trace(question)
    assert queries[0] == question
    assert len(queries) == 3
    assert trace.multi_part


def test_context_deduplicates_exact_passages_and_preserves_real_source():
    repeated = "Quarterly access reviews occur on the first Monday of each quarter."
    first = _chunk("one", repeated, source="one.md", score=0.9)
    duplicate = _chunk("two", repeated, source="two.md", score=0.8)
    unique = _chunk("three", "The archive seal is ZETA-991.", source="three.md")
    built = build_context([first, duplicate, unique], char_limit=1000)
    assert [chunk.chunk_id for chunk in built.used_chunks] == ["one", "three"]
    assert built.dropped_duplicate_ids == ["two"]
    assert built.text.count(repeated) == 1


def test_near_duplicate_filter_keeps_different_exact_values():
    template = "The quarterly register records the approved recovery target as {} hours. "
    first = _chunk("four", template.format("4") * 8, document_id="same")
    second = _chunk("eight", template.format("8") * 8, document_id="same")
    built = build_context([first, second], char_limit=4000)
    assert [chunk.chunk_id for chunk in built.used_chunks] == ["four", "eight"]


def test_context_budget_is_strict_and_page_range_is_visible():
    spanning = _chunk("span", "x" * 2000, page=3)
    spanning.metadata["page_end"] = 4
    built = build_context([spanning], char_limit=320)
    assert len(built.text) <= 320
    assert "PAGE: 3-4" in built.text
    assert "[…truncated]" in built.text
    assert built.truncated_chunk_ids == ["span"]
    assert len(built.used_chunks[0].text) < len(spanning.text)
    assert built.used_chunks[0].text in built.text
    assert spanning.text not in built.text


def test_prepare_exposes_stage_timings_and_bounded_diagnostics(engine):
    engine.settings = replace(engine.settings, rag_debug=True)
    turn = engine.prepare("What temperature counts as a fever in adults?")
    assert {"query_processing", "retrieval", "rerank", "context", "prepare_total"} <= set(
        turn.timings
    )
    diagnostics = turn.diagnostics()
    assert diagnostics["retrieval"]["candidates"]
    assert diagnostics["context"]["used_chunk_ids"]
    assert diagnostics["gate"]["semantic_threshold"] == engine.settings.min_similarity
    assert all(len(item["excerpt"]) <= 240 for item in diagnostics["retrieval"]["candidates"])


def test_model_failure_becomes_actionable_provider_error(engine):
    engine.llm = FakeLLM(fail=True)
    with pytest.raises(ProviderError) as excinfo:
        engine.ask("What temperature counts as a fever in adults?")
    assert "configured language model" in excinfo.value.what
    assert "indexed documents were not changed" in excinfo.value.fix


def test_context_failure_refuses_without_calling_model(engine, monkeypatch):
    def broken_context(*args, **kwargs):
        raise RuntimeError("context formatter failed")

    monkeypatch.setattr("apex_ai.rag.engine.build_context", broken_context)
    engine.llm = FakeLLM(fail=True)
    result = engine.ask("What temperature counts as a fever in adults?")
    assert result.insufficient_evidence
    assert result.citations == []


def test_example_dataset_covers_required_quality_categories():
    dataset = load_dataset(Path(__file__).parents[1] / "eval" / "dataset.example.jsonl")
    categories = {item["category"] for item in dataset}
    assert len(dataset) >= 15
    assert {
        "direct",
        "semantic",
        "exact-match",
        "multi-part",
        "negative",
        "follow-up",
        "multi-document",
        "duplicate",
        "long",
        "multi-page",
    } <= categories
