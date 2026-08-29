"""Phase 43 conservative memory-candidate extraction tests."""

from __future__ import annotations

import pytest

from apex_ai.memory.extraction import (
    MAX_CANDIDATE_CHARS,
    MAX_MESSAGE_CHARS,
    MemoryCandidateExtractor,
)
from apex_ai.memory.long_term import LongTermMemoryStore


def test_extracts_explicit_preferences_and_ongoing_context_verbatim():
    extractor = MemoryCandidateExtractor()
    message = (
        "I prefer concise Markdown tables for API v2.7 IDs. "
        "We're currently migrating project ACME-104 to PostgreSQL 16."
    )

    candidates = extractor.extract(message)

    assert [(item.kind, item.content, item.rule) for item in candidates] == [
        (
            "preference",
            "I prefer concise Markdown tables for API v2.7 IDs.",
            "stated_preference",
        ),
        (
            "ongoing_context",
            "We're currently migrating project ACME-104 to PostgreSQL 16.",
            "active_work",
        ),
    ]
    assert candidates == extractor.extract(message)
    assert all(item.id.startswith("memcand_") for item in candidates)


def test_ordinary_questions_and_one_off_requests_are_not_candidates():
    extractor = MemoryCandidateExtractor()

    assert extractor.extract("What does the indexed document say about fever?") == []
    assert (
        extractor.extract("Please explain this function, then calculate 17 + 9.") == []
    )
    assert extractor.extract("The retrieved paragraph mentions PostgreSQL 16.") == []


def test_explicit_remember_and_persistent_instruction_are_candidates():
    extractor = MemoryCandidateExtractor()
    candidates = extractor.extract(
        "Remember that the Atlas migration runs through 2027.\n"
        "- Always include exact version numbers in answers."
    )

    assert [item.kind for item in candidates] == [
        "ongoing_context",
        "preference",
    ]
    assert candidates[0].rule == "explicit_remember"
    assert candidates[1].rule == "persistent_instruction"
    assert candidates[1].content == "Always include exact version numbers in answers."


def test_extraction_deduplicates_and_obeys_candidate_limit():
    extractor = MemoryCandidateExtractor()
    message = (
        "I prefer short answers.\n"
        "I prefer short answers.\n"
        "My current project is Atlas.\n"
        "Our project uses SQLite."
    )

    candidates = extractor.extract(message, max_candidates=2)

    assert len(candidates) == 2
    assert [item.content for item in candidates] == [
        "I prefer short answers.",
        "My current project is Atlas.",
    ]


def test_extraction_rejects_unsafe_sizes_instead_of_truncating_meaning():
    extractor = MemoryCandidateExtractor()

    with pytest.raises(ValueError, match="cannot exceed"):
        extractor.extract("x" * (MAX_MESSAGE_CHARS + 1))
    with pytest.raises(ValueError, match="max_candidates"):
        extractor.extract("I prefer examples.", max_candidates=0)
    with pytest.raises(TypeError, match="must be a string"):
        extractor.extract(None)  # type: ignore[arg-type]

    oversized_candidate = "I prefer " + ("x" * MAX_CANDIDATE_CHARS) + "."
    assert extractor.extract(oversized_candidate) == []


def test_extraction_is_ephemeral_and_never_writes_the_memory_store(tmp_path):
    store = LongTermMemoryStore(tmp_path / "long-term.db")
    extractor = MemoryCandidateExtractor()

    candidates = extractor.extract("I prefer answers with examples.")

    assert len(candidates) == 1
    assert store.count("user-1") == 0


def test_runtime_exposes_extractor_without_connecting_it_to_chat(settings, embeddings):
    from apex_ai.runtime import build_services

    services = build_services(
        settings,
        embedding_factory=lambda unused_settings: embeddings,
    )

    assert isinstance(services.memory_extractor, MemoryCandidateExtractor)
    assert services.long_term_memory is not None
    assert services.default_local_user is not None
    assert services.long_term_memory.count(services.default_local_user.id) == 0
