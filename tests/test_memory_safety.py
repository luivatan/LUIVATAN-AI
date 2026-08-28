"""Phase 44 safety enforcement for durable long-term memory."""

from __future__ import annotations

import sqlite3

import pytest

from apex_ai.memory.extraction import MemoryCandidateExtractor
from apex_ai.memory.long_term import LongTermMemoryStore
from apex_ai.security.memory import MemorySafetyPolicy, UnsafeMemoryError


def _fake_provider_key() -> str:
    return "sk-" + ("Ab3_" * 8)


def _fake_bearer() -> str:
    return "Bearer " + ("xY9." * 8)


def _fake_jwt() -> str:
    parts = ["headerABC", "payloadXYZ", "signature123"]
    return ".".join(parts)


def _fake_opaque_secret() -> str:
    return "aB3dE5fG7hJ9kL2mN4pQ6rS8tU0vW1xY"


@pytest.mark.parametrize(
    ("code", "content"),
    [
        ("labeled_credential", "Remember that my password is dummy-pass-123."),
        ("labeled_credential", "Remember SERVICE_API_KEY=dummy-key-789"),
        ("authorization_credential", "Remember " + _fake_bearer() + "."),
        ("private_key", "Remember -----BEGIN PRIVATE KEY-----"),
        ("credential_url", "Remember postgres://demo:dummy-pass@db.invalid/app"),
        ("jwt", "Remember token " + _fake_jwt()),
        ("provider_api_key", "Remember key " + _fake_provider_key()),
        ("social_security_number", "Remember SSN 000-12-3456"),
        (
            "labeled_sensitive_identifier",
            "Remember that my recovery phrase is alpha-beta-gamma-delta.",
        ),
        (
            "unnecessary_contact_detail",
            "Remember that my email address is me@example.test",
        ),
        ("personal_health_detail", "Remember that my diagnosis is Example Syndrome."),
        (
            "sensitive_profile_detail",
            "Remember that my political affiliation is Example.",
        ),
        ("high_entropy_credential", "Remember " + _fake_opaque_secret()),
    ],
)
def test_policy_blocks_secret_and_unnecessary_sensitive_categories(code, content):
    result = MemorySafetyPolicy().inspect(content)

    assert not result.safe
    assert code in result.reason_codes
    assert content not in repr(result)


def test_policy_detects_luhn_valid_payment_card_without_blocking_normal_numbers():
    policy = MemorySafetyPolicy()
    test_card = "4111" + ("1" * 12)

    blocked = policy.inspect("Remember card " + test_card)

    assert "payment_card_number" in blocked.reason_codes
    assert policy.inspect("Project ACME-104 uses PostgreSQL 16 in 2027.").safe


def test_policy_preserves_non_secret_exact_identifiers():
    policy = MemorySafetyPolicy()
    safe = (
        "I prefer exact identifiers such as 550e8400-e29b-41d4-a716-446655440000 "
        "and commit 0123456789abcdef0123456789abcdef01234567."
    )

    assert policy.inspect(safe).safe


def test_store_rejects_unsafe_create_and_update_without_echoing_value(tmp_path):
    store = LongTermMemoryStore(tmp_path / "memory.db")
    secret = _fake_provider_key()

    with pytest.raises(UnsafeMemoryError) as create_error:
        store.create("My API key is " + secret, kind="ongoing_context")
    assert secret not in create_error.value.user_message()
    assert store.count() == 0

    memory = store.create("I prefer concise answers.", kind="preference")
    with pytest.raises(UnsafeMemoryError) as update_error:
        store.update(memory.id, content="My password is dummy-pass-456")
    assert "dummy-pass-456" not in update_error.value.user_message()
    assert store.get(memory.id) == memory


def test_candidate_extractor_drops_unsafe_candidates_before_output():
    extractor = MemoryCandidateExtractor()

    assert (
        extractor.extract("Remember that my API key is " + _fake_provider_key()) == []
    )
    assert extractor.extract("I prefer concise answers.")


def test_store_removes_recognized_unsafe_legacy_rows_on_open(tmp_path):
    path = tmp_path / "memory.db"
    store = LongTermMemoryStore(path)
    memory = store.create("The Atlas migration is ongoing.", kind="ongoing_context")
    secret = _fake_provider_key()

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE long_term_memories SET content=? WHERE id=?",
            ("My API key is " + secret, memory.id),
        )

    reopened = LongTermMemoryStore(path)

    assert reopened.removed_unsafe_on_startup == 1
    assert reopened.count() == 0


def test_runtime_shares_one_policy_across_extractor_and_store(settings, embeddings):
    from apex_ai.runtime import build_services

    services = build_services(
        settings,
        embedding_factory=lambda unused_settings: embeddings,
    )

    assert isinstance(services.memory_safety, MemorySafetyPolicy)
    assert services.memory_extractor.safety_policy is services.memory_safety
    assert services.long_term_memory.safety_policy is services.memory_safety
