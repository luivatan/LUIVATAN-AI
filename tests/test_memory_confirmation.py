"""Phase 45 pending-memory approval and rejection workflow tests."""

from __future__ import annotations

import sqlite3

import pytest

from apex_ai.memory.confirmation import MemoryConfirmationService
from apex_ai.memory.extraction import MemoryCandidateExtractor
from apex_ai.memory.long_term import LongTermMemoryStore
from apex_ai.security.memory import UnsafeMemoryError

USER = "user-1"


def _service(tmp_path):
    store = LongTermMemoryStore(tmp_path / "memory.db")
    return MemoryConfirmationService(MemoryCandidateExtractor(), store), store


def _fake_key() -> str:
    return "sk-" + ("Zx7_" * 8)


def test_safe_candidate_stays_pending_until_explicit_approval(tmp_path):
    service, store = _service(tmp_path)

    proposals = service.propose_from_user_message(USER, "I prefer concise answers.")

    assert len(proposals) == 1
    assert proposals[0].kind == "preference"
    assert store.count(USER) == 0
    assert service.pending(USER) == proposals

    memory = service.approve(USER, proposals[0].id)

    assert memory.content == "I prefer concise answers."
    assert store.count(USER) == 1
    assert service.pending(USER) == []


def test_rejection_removes_content_and_suppresses_same_candidate(tmp_path):
    service, store = _service(tmp_path)
    proposal = service.propose_from_user_message(
        USER, "My current project is the Atlas migration."
    )[0]

    assert service.reject(USER, proposal.id)
    assert service.pending(USER) == []
    assert store.count(USER) == 0
    assert service.propose_from_user_message(USER, proposal.content) == []
    assert not service.reject(USER, proposal.id)

    with sqlite3.connect(store.path) as connection:
        decision = connection.execute(
            """SELECT decision,memory_id FROM memory_candidate_decisions
               WHERE candidate_id=? AND user_id=?""",
            (proposal.id, USER),
        ).fetchone()
        retained_content = connection.execute(
            "SELECT content FROM pending_memories WHERE id=?",
            (proposal.id,),
        ).fetchone()
    assert decision == ("rejected", None)
    assert retained_content is None


def test_unsafe_and_ordinary_messages_never_become_pending(tmp_path):
    service, store = _service(tmp_path)

    assert service.propose_from_user_message(USER, "What is in the indexed document?") == []
    assert (
        service.propose_from_user_message(USER, "Remember my API key is " + _fake_key()) == []
    )
    assert service.pending(USER) == []
    assert store.count(USER) == 0


def test_duplicate_pending_and_already_confirmed_content_are_not_reproposed(tmp_path):
    service, store = _service(tmp_path)
    message = "Please always include exact version numbers."

    first = service.propose_from_user_message(USER, message)
    second = service.propose_from_user_message(USER, message)

    assert second == first
    service.approve(USER, first[0].id)
    assert service.propose_from_user_message(USER, message) == []
    assert store.count(USER) == 1


def test_expired_pending_content_is_deleted_lazily(tmp_path):
    service, store = _service(tmp_path)
    proposal = service.propose_from_user_message(USER, "I prefer concise answers.")[0]

    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE pending_memories SET expires_at=? WHERE id=?",
            ("2000-01-01T00:00:00.000000Z", proposal.id),
        )

    assert service.pending(USER) == []
    with pytest.raises(KeyError):
        service.approve(USER, proposal.id)


def test_approval_rechecks_safety_and_keeps_unsafe_proposal_out_of_memory(tmp_path):
    service, store = _service(tmp_path)
    proposal = service.propose_from_user_message(USER, "I prefer concise answers.")[0]
    secret = _fake_key()

    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE pending_memories SET content=? WHERE id=?",
            ("My API key is " + secret, proposal.id),
        )

    with pytest.raises(UnsafeMemoryError) as excinfo:
        service.approve(USER, proposal.id)

    assert secret not in excinfo.value.user_message()
    assert store.count(USER) == 0
    assert service.pending(USER) == []


def test_invalid_or_missing_proposal_cannot_be_approved(tmp_path):
    service, store = _service(tmp_path)

    with pytest.raises(ValueError, match="proposal ID"):
        service.approve(USER, "not-a-proposal")
    with pytest.raises(KeyError):
        service.approve(USER, "memcand_" + ("0" * 24))
    assert store.count(USER) == 0


def test_pending_proposals_are_isolated_between_accounts(tmp_path):
    service, _store = _service(tmp_path)
    other_user = "user-2"

    mine = service.propose_from_user_message(USER, "I prefer concise answers.")[0]

    assert service.pending(other_user) == []
    assert not service.reject(other_user, mine.id)  # can't act on another account's proposal
    assert service.pending(USER) == [mine]  # untouched by the failed cross-account reject


def test_rejecting_a_candidate_does_not_suppress_it_for_other_accounts(tmp_path):
    """A content-derived candidate ID (Phase 43) has no user component, so the
    dedup/decision table must be keyed per account too - otherwise one
    account's reject would silently hide the same phrase from everyone else."""
    service, _ = _service(tmp_path)
    other_user = "user-2"
    message = "I prefer concise answers."

    mine = service.propose_from_user_message(USER, message)[0]
    service.reject(USER, mine.id)

    theirs = service.propose_from_user_message(other_user, message)
    assert len(theirs) == 1
    assert theirs[0].id == mine.id  # same content-derived ID, independent decision
