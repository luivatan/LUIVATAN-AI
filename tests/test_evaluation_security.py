"""Evaluation metrics + security helper tests."""

from __future__ import annotations

from apex_ai.core.types import RetrievedChunk
from apex_ai.evaluation.metrics import Report, evaluate_item, normalize_source, token_overlap
from apex_ai.security.files import ensure_within, human_size, sha256_text
import pytest

from apex_ai.core.errors import SecurityError


def test_normalize_source_is_lenient():
    assert normalize_source("World_Health-Statistics 2025.PDF") == normalize_source(
        "world health statistics 2025.pdf"
    )


def test_token_overlap_scores():
    assert token_overlap("fever is high", "a fever is high temperature") == 1.0
    assert token_overlap("fever is high", "totally unrelated") == 0.0


def _chunk(source, page, text="some evidence text"):
    return RetrievedChunk(
        chunk_id=f"{source}:{page}",
        text=text,
        metadata={"document_name": source, "page": page, "section": "s"},
    )


def test_evaluate_item_hits():
    item = {
        "question": "What is a fever?",
        "expected_answer": "38 C or higher",
        "expected_source": "sample_first_aid.pdf",
        "expected_page": 1,
    }
    retrieved = [
        _chunk("unrelated.pdf", 3, "unrelated"),
        _chunk("Sample First Aid.PDF", 1, "fever is 38 C or higher in adults"),
    ]
    metrics = evaluate_item(item, retrieved, context_text="fever is 38 C or higher")
    assert metrics.source_hit
    assert metrics.page_hit
    assert not metrics.first_hit  # match exists but not at rank 1
    assert metrics.context_relevance == 1.0


def test_evaluate_item_misses():
    item = {"question": "q", "expected_answer": "a", "expected_source": "other.pdf",
            "expected_page": 9}
    metrics = evaluate_item(item, [_chunk("sample.pdf", 1)], context_text="nothing relevant")
    assert not metrics.source_hit
    assert not metrics.page_hit
    assert metrics.context_relevance == 0.0


def test_groundedness_proxy_rewards_cited_sentences():
    item = {"question": "q", "expected_answer": "e", "expected_source": "s.pdf",
            "expected_page": 1}
    context = "Fever in adults is 38 C or higher according to the guide."
    answer = "Fever in adults is 38 C or higher according to the guide. [1] " \
             "Completely invented zebra quantum blockade unlinked."
    metrics = evaluate_item(item, [_chunk("s.pdf", 1, context)], context, answer=answer)
    assert metrics.groundedness_proxy is not None
    assert metrics.groundedness_proxy > 0.4


def test_report_summary_shapes():
    report = Report()
    report.items.append(evaluate_item(
        {"question": "q", "expected_answer": "a", "expected_source": "x.pdf",
         "expected_page": 1},
        [_chunk("x.pdf", 1)], "a",
    ))
    summary = report.summary()
    assert summary["items"] == 1
    assert summary["source_hit_rate"] == 1.0
    assert 0.0 <= summary["page_hit_rate"] <= 1.0


def test_dataset_loader_skips_comments_and_blanks(tmp_path):
    from apex_ai.evaluation.runner import load_dataset

    path = tmp_path / "d.jsonl"
    path.write_text(
        '# comment\n\n{"question": "q1", "expected_answer": "a", "expected_source": "s.pdf"}\n'
        '{"question": "q2", "expected_answer": "a", "expected_source": "s.pdf", "expected_page": 2}\n',
        encoding="utf-8",
    )
    items = load_dataset(path)
    assert len(items) == 2


# ---------------- security ----------------


def test_ensure_within_blocks_escape(tmp_path):
    inside = tmp_path / "uploads"
    inside.mkdir()
    assert ensure_within(inside, inside / "file.txt") == (inside / "file.txt").resolve()
    with pytest.raises(SecurityError):
        ensure_within(inside, tmp_path / "other" / "file.txt")


def test_sha256_text_is_stable():
    assert sha256_text("abc") == sha256_text("abc")
    assert sha256_text("abc") != sha256_text("abd")


def test_human_size():
    assert human_size(512) == "512 B"
    assert human_size(2048).endswith("KB")
    assert human_size(3 * 1024**3).endswith("GB")
