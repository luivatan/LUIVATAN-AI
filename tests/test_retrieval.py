"""Retrieval stack tests: BM25, hybrid fusion, reranking."""

from __future__ import annotations

from dataclasses import replace

from apex_ai.core.types import RetrievedChunk
from apex_ai.retrieval.keyword import BM25Index, tokenize
from apex_ai.retrieval.pipeline import HybridRetriever, rrf_merge
from apex_ai.retrieval.reranker import LexicalReranker, NoReranker, make_reranker
from tests.conftest import DATA_DIR


def _chunk(id_, text, similarity=0.0, metadata=None):
    return RetrievedChunk(
        chunk_id=id_, text=text, metadata=metadata or {"document_name": f"{id_}.txt"},
        similarity=similarity,
    )


def test_tokenize_is_lowercase_words():
    assert tokenize("Fever in Adults: 38 C!") == ["fever", "in", "adults", "38", "c"]


def test_bm25_finds_exact_terms(ingestion, store):
    # Several documents so BM25's IDF is meaningful (with only 2 chunks the
    # idf of any term is ~0 by construction).
    for name in ("sample_first_aid.pdf", "burn_care.md", "first_aid_faq.json"):
        ingestion.ingest_path(DATA_DIR / name)
    index = BM25Index(store)
    # A rare term that appears in exactly one chunk → strong BM25 signal.
    hits = index.search("intravenous", k=3)
    assert hits
    assert any("intravenous" in h.text.lower() for h in hits)


def test_bm25_rebuilds_after_ingestion(ingestion, store):
    index = BM25Index(store)
    assert index.search("fever") == []

    ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf")
    assert index.search("fever in adults", k=2), "index must rebuild after store change"


def test_rrf_merge_dedupes_and_fuses():
    a1 = _chunk("1", "alpha beta", similarity=0.9)
    a2 = _chunk("2", "gamma")
    b1 = _chunk("2", "gamma")  # same id, appears in both lists
    b2 = _chunk("3", "delta", similarity=0.5)

    merged = rrf_merge([[a1, a2], [b1, b2]], weights=[0.6, 0.4])
    ids = [c.chunk_id for c in merged]
    assert len(ids) == len(set(ids)) == 3
    # chunk 2 appeared in both lists -> outranks chunk 3 (list-2 only)
    assert ids.index("2") < ids.index("3")


def test_hybrid_retriever_combines_stages(engine):
    candidates = engine.retriever.retrieve(["fever temperature adults"], top_k=5)
    assert 0 < len(candidates) <= 5
    ids = [c.chunk_id for c in candidates]
    assert len(ids) == len(set(ids))


def test_lexical_reranker_boosts_relevant_chunk():
    query = "aspirin dosage for children"
    relevant = _chunk("rel", "The recommended aspirin dosage for children must be confirmed "
                              "by a doctor, as aspirin is not routinely recommended for children.")
    irrelevant = _chunk("irr", "The hospital cafeteria opens at seven in the morning and "
                               "serves lunch until two.")
    reranked = LexicalReranker().rerank(query, [irrelevant, relevant])
    assert reranked[0].chunk_id == "rel"


def test_reranker_off_keeps_order():
    candidates = [_chunk("a", "x"), _chunk("b", "y")]
    assert [c.chunk_id for c in NoReranker().rerank("q", candidates)] == ["a", "b"]


def test_make_reranker_modes(settings):
    assert isinstance(make_reranker(replace(settings, reranker_mode="off")), NoReranker)
    assert isinstance(make_reranker(replace(settings, reranker_mode="lexical")), LexicalReranker)
