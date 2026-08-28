"""Phase 45 pending-memory approval and rejection workflow tests."""

from __future__ import annotations

import sqlite3

import pytest

from apex_ai.memory.confirmation import MemoryConfirmationService
from apex_ai.memory.extraction import MemoryCandidateExtractor
from apex_ai.memory.long_term import LongTermMemoryStore
from apex_ai.security.memory import UnsafeMemoryError


def _service(tmp_path):
    store = LongTermMemoryStore(tmp_path / "memory.db")
    return MemoryConfirmationService(MemoryCandidateExtractor(), store), store


def _fake_key() -> str:
    return "sk-" + ("Zx7_" * 8)


def test_safe_candidate_stays_pending_until_explicit_approval(tmp_path):
    service, store = _service(tmp_path)

    proposals = service.propose_from_user_message("I prefer concise answers.")

    assert len(proposals) == 1
    assert proposals[0].kind == "preference"
    assert store.count() == 0
    assert service.pending() == proposals

    memory = service.approve(proposals[0].id)

    assert memory.content == "I prefer concise answers."
    assert store.count() == 1
    assert service.pending() == []


def test_rejection_removes_content_and_suppresses_same_candidate(tmp_path):
    service, store = _service(tmp_path)
    proposal = service.propose_from_user_message(
        "My current project is the Atlas migration."
    )[0]

    assert service.reject(proposal.id)
    assert service.pending() == []
    assert store.count() == 0
    assert service.propose_from_user_message(proposal.content) == []
    assert not service.reject(proposal.id)

    with sqlite3.connect(store.path) as connection:
        decision = connection.execute(
            """SELECT decision,memory_id FROM memory_candidate_decisions
               WHERE candidate_id=?""",
            (proposal.id,),
        ).fetchone()
        retained_content = connection.execute(
            "SELECT content FROM pending_memories WHERE id=?",
            (proposal.id,),
        ).fetchone()
    assert decision == ("rejected", None)
    assert retained_content is None


def test_unsafe_and_ordinary_messages_never_become_pending(tmp_path):
    service, store = _service(tmp_path)

    assert service.propose_from_user_message("What is in the indexed document?") == []
    assert (
        service.propose_from_user_message("Remember my API key is " + _fake_key()) == []
    )
    assert service.pending() == []
    assert store.count() == 0


def test_duplicate_pending_and_already_confirmed_content_are_not_reproposed(tmp_path):
    service, store = _service(tmp_path)
    message = "Please always include exact version numbers."

    first = service.propose_from_user_message(message)
    second = service.propose_from_user_message(message)

    assert second == first
    service.approve(first[0].id)
    assert service.propose_from_user_message(message) == []
    assert store.count() == 1


def test_expired_pending_content_is_deleted_lazily(tmp_path):
    service, store = _service(tmp_path)
    proposal = service.propose_from_user_message("I prefer concise answers.")[0]

    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE pending_memories SET expires_at=? WHERE id=?",
            ("2000-01-01T00:00:00.000000Z", proposal.id),
        )

    assert service.pending() == []
    with pytest.raises(KeyError):
        service.approve(proposal.id)


def test_approval_rechecks_safety_and_keeps_unsafe_proposal_out_of_memory(tmp_path):
    service, store = _service(tmp_path)
    proposal = service.propose_from_user_message("I prefer concise answers.")[0]
    secret = _fake_key()

    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE pending_memories SET content=? WHERE id=?",
            ("My API key is " + secret, proposal.id),
        )

    with pytest.raises(UnsafeMemoryError) as excinfo:
        service.approve(proposal.id)

    assert secret not in excinfo.value.user_message()
    assert store.count() == 0
    assert service.pending() == []


def test_invalid_or_missing_proposal_cannot_be_approved(tmp_path):
    service, store = _service(tmp_path)

    with pytest.raises(ValueError, match="proposal ID"):
        service.approve("not-a-proposal")
    with pytest.raises(KeyError):
        service.approve("memcand_" + ("0" * 24))
    assert store.count() == 0
