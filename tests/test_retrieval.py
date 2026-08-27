from apex_retrieval import Result, hybrid_retrieve, keyword_score, keyword_search, optimize_context


def docs():
    return [Result("Cardiac care guidelines", {"source": "a.pdf", "page": 2}, .8), Result("A diet and exercise plan", {"source": "b.pdf", "page": 1}, .2)]


def test_keyword_search_and_hybrid_deduplicate():
    assert keyword_score("cardiac care", docs()[0].text) == 1
    results = hybrid_retrieve("cardiac", docs(), keyword_search("cardiac", docs()), limit=2)
    assert results[0].metadata["source"] == "a.pdf"


def test_context_is_bounded_and_citation_ready():
    context = optimize_context(docs(), max_chars=60)
    assert "[1] a.pdf, page 2" in context
    assert len(context) <= 60
