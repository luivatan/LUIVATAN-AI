"""Phase 51/52/53: accounts, sessions, and password hashing — store and
service layer, before any API wiring (API-level tests live in test_api_ui.py
and test_conversations_web.py)."""

from __future__ import annotations

import time

import pytest

from apex_ai.auth.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    hash_password,
    verify_password,
)
from apex_ai.auth.service import AuthService, InvalidCredentialsError
from apex_ai.auth.sessions import SessionStore
from apex_ai.auth.users import EmailAlreadyRegisteredError, UserStore, normalize_email

# -- passwords ----------------------------------------------------------


def test_hash_password_never_stores_the_plaintext():
    hashed = hash_password("correct-horse-battery")
    assert "correct-horse-battery" not in hashed
    assert hashed.startswith("$argon2id$")


def test_verify_password_accepts_correct_and_rejects_wrong():
    hashed = hash_password("correct-horse-battery")
    assert verify_password("correct-horse-battery", hashed) is True
    assert verify_password("wrong-password", hashed) is False


def test_verify_password_never_raises_on_malformed_hash():
    assert verify_password("anything", "not-a-real-argon2-hash") is False
    assert verify_password("anything", "") is False
    assert verify_password("", "still-not-a-hash") is False


def test_password_length_bounds_are_enforced():
    with pytest.raises(ValueError):
        hash_password("short")
    with pytest.raises(ValueError):
        hash_password("x" * (MAX_PASSWORD_LENGTH + 1))
    hash_password("x" * MIN_PASSWORD_LENGTH)  # exactly at the floor: allowed


# -- UserStore ------------------------------------------------------------


def test_normalize_email_lowercases_and_trims():
    assert normalize_email("  User@Example.COM ") == "user@example.com"


def test_normalize_email_rejects_malformed_addresses():
    with pytest.raises(ValueError):
        normalize_email("not-an-email")


def test_user_create_get_and_credential_verification(tmp_path):
    store = UserStore(tmp_path / "users.db")
    user = store.create("person@example.com", "correct-horse-battery")

    assert store.get(user.id) == user
    assert store.get_by_email("PERSON@example.com") == user  # case-insensitive lookup

    assert store.verify_credentials("person@example.com", "correct-horse-battery") == user
    assert store.verify_credentials("person@example.com", "wrong-password") is None
    assert store.verify_credentials("nobody@example.com", "anything") is None


def test_user_to_dict_never_includes_the_password_hash(tmp_path):
    store = UserStore(tmp_path / "users.db")
    user = store.create("person@example.com", "correct-horse-battery")
    payload = user.to_dict()
    assert "password_hash" not in payload
    assert payload["email"] == "person@example.com"


def test_duplicate_email_is_rejected(tmp_path):
    store = UserStore(tmp_path / "users.db")
    store.create("person@example.com", "correct-horse-battery")
    with pytest.raises(EmailAlreadyRegisteredError):
        store.create("person@example.com", "another-password")


def test_get_default_local_returns_none_until_one_is_created(tmp_path):
    store = UserStore(tmp_path / "users.db")
    assert store.get_default_local() is None
    default = store.create("local@apex.local", "random-generated-pw", is_default_local=True)
    assert store.get_default_local() == default


def test_verify_credentials_for_unknown_email_still_runs_a_real_hash_check(tmp_path):
    """Not a strict timing assertion (too flaky in CI) - proves the
    constant-time-lookup path actually calls verify_password rather than
    short-circuiting instantly, which is what would make it worth doing."""
    store = UserStore(tmp_path / "users.db")
    start = time.perf_counter()
    store.verify_credentials("nobody@example.com", "anything")
    unknown_email_elapsed = time.perf_counter() - start

    store.create("person@example.com", "correct-horse-battery")
    start = time.perf_counter()
    store.verify_credentials("person@example.com", "wrong-password")
    wrong_password_elapsed = time.perf_counter() - start

    # Both paths hash-compute; neither should be orders of magnitude faster.
    assert unknown_email_elapsed > wrong_password_elapsed / 10


# -- SessionStore -----------------------------------------------------------


def test_session_create_and_get(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    session = store.create("user-1", ttl_days=30)
    assert store.get(session.token) == session
    assert len(session.token) > 20


def test_session_get_returns_none_for_unknown_token(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    assert store.get("does-not-exist") is None


def test_expired_session_is_treated_as_missing_and_cleaned_up(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    session = store.create("user-1", ttl_days=30)
    # Force it into the past directly (waiting a real 30 days is not an option).
    with store._connect() as connection:
        connection.execute(
            "UPDATE sessions SET expires_at='2000-01-01T00:00:00.000000Z' WHERE token=?",
            (session.token,),
        )
    assert store.get(session.token) is None
    with store._connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM sessions WHERE token=?", (session.token,)
        ).fetchone() is None


def test_session_delete_and_delete_all_for_user(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    a = store.create("user-1", ttl_days=30)
    b = store.create("user-1", ttl_days=30)
    other = store.create("user-2", ttl_days=30)

    assert store.delete(a.token) is True
    assert store.delete(a.token) is False  # already gone
    assert store.get(a.token) is None

    assert store.delete_all_for_user("user-1") == 1  # only b remains for user-1
    assert store.get(b.token) is None
    assert store.get(other.token) == other


# -- AuthService ------------------------------------------------------------


def _service(tmp_path) -> AuthService:
    return AuthService(
        UserStore(tmp_path / "users.db"), SessionStore(tmp_path / "sessions.db"), session_ttl_days=30
    )


def test_signup_creates_a_real_account_and_a_valid_session(tmp_path):
    service = _service(tmp_path)
    result = service.signup("person@example.com", "correct-horse-battery")
    assert result.user.email == "person@example.com"
    assert service.user_for_session(result.session.token) == result.user


def test_signup_rejects_a_duplicate_email(tmp_path):
    service = _service(tmp_path)
    service.signup("person@example.com", "correct-horse-battery")
    with pytest.raises(EmailAlreadyRegisteredError):
        service.signup("person@example.com", "another-password")


def test_login_succeeds_with_correct_credentials(tmp_path):
    service = _service(tmp_path)
    signed_up = service.signup("person@example.com", "correct-horse-battery")
    result = service.login("person@example.com", "correct-horse-battery")
    assert result.user == signed_up.user
    assert result.session.token != signed_up.session.token  # a fresh session, not reused


def test_login_fails_with_wrong_password_or_unknown_email(tmp_path):
    service = _service(tmp_path)
    service.signup("person@example.com", "correct-horse-battery")
    with pytest.raises(InvalidCredentialsError):
        service.login("person@example.com", "wrong-password")
    with pytest.raises(InvalidCredentialsError):
        service.login("nobody@example.com", "anything")


def test_logout_invalidates_the_session(tmp_path):
    service = _service(tmp_path)
    result = service.signup("person@example.com", "correct-horse-battery")
    service.logout(result.session.token)
    assert service.user_for_session(result.session.token) is None


def test_ensure_default_local_account_is_idempotent(tmp_path):
    service = _service(tmp_path)
    first = service.ensure_default_local_account()
    second = service.ensure_default_local_account()
    assert first.id == second.id
    assert first.is_default_local is True


# -- build_services() wiring -------------------------------------------------


def test_build_services_wires_auth_and_bootstraps_the_default_local_account(settings):
    from apex_ai.embeddings.hashing import HashingEmbeddingProvider
    from apex_ai.runtime import build_services

    services = build_services(
        settings, embedding_factory=lambda unused_settings: HashingEmbeddingProvider()
    )

    assert services.auth is not None
    assert services.default_local_user is not None
    assert services.default_local_user.is_default_local is True
    assert services.auth.user_for_session("no-such-token") is None


def test_build_services_default_local_account_is_stable_across_restarts(settings):
    from apex_ai.embeddings.hashing import HashingEmbeddingProvider
    from apex_ai.runtime import build_services

    first_run = build_services(
        settings, embedding_factory=lambda unused_settings: HashingEmbeddingProvider()
    )
    second_run = build_services(
        settings, embedding_factory=lambda unused_settings: HashingEmbeddingProvider()
    )

    assert first_run.default_local_user.id == second_run.default_local_user.id
    assert first_run.auth.users.count() == 1  # not re-created on the second "startup"


# -- /auth API routes ---------------------------------------------------


def _auth_client(settings, *, auto_login_local: bool = True):
    from fastapi.testclient import TestClient

    from apex_ai.api.server import create_api
    from apex_ai.auth.service import AuthService
    from apex_ai.auth.sessions import SessionStore
    from apex_ai.auth.users import UserStore
    from apex_ai.config.settings import with_overrides
    from apex_ai.runtime import ApexServices

    settings = with_overrides(settings, auto_login_local=auto_login_local)
    auth = AuthService(
        UserStore(settings.users_db_path),
        SessionStore(settings.users_db_path),
        session_ttl_days=settings.session_ttl_days,
    )
    default_user = auth.ensure_default_local_account()
    services = ApexServices(settings=settings, auth=auth, default_local_user=default_user)
    return TestClient(create_api(services, include_web=False)), services


def test_signup_sets_a_session_cookie_and_returns_the_new_user(settings):
    client, _ = _auth_client(settings)
    response = client.post(
        "/auth/signup",
        json={"email": "person@example.com", "password": "correct-horse-battery"},
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["email"] == "person@example.com"
    assert "password" not in payload and "password_hash" not in payload
    assert client.cookies.get("apex_session") is not None


def test_signup_rejects_a_duplicate_email_with_409(settings):
    client, _ = _auth_client(settings)
    client.post("/auth/signup", json={"email": "person@example.com", "password": "correct-horse-battery"})
    response = client.post(
        "/auth/signup", json={"email": "person@example.com", "password": "another-password"}
    )
    assert response.status_code == 409


def test_signup_rejects_a_weak_password_with_400(settings):
    client, _ = _auth_client(settings)
    response = client.post("/auth/signup", json={"email": "person@example.com", "password": "short"})
    assert response.status_code == 400


def test_login_then_me_returns_the_logged_in_user(settings):
    client, _ = _auth_client(settings)
    client.post("/auth/signup", json={"email": "person@example.com", "password": "correct-horse-battery"})
    client.cookies.clear()  # simulate a fresh browser session

    login = client.post(
        "/auth/login", json={"email": "person@example.com", "password": "correct-horse-battery"}
    )
    assert login.status_code == 200
    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "person@example.com"


def test_login_with_wrong_password_returns_401(settings):
    client, _ = _auth_client(settings)
    client.post("/auth/signup", json={"email": "person@example.com", "password": "correct-horse-battery"})
    response = client.post("/auth/login", json={"email": "person@example.com", "password": "wrong"})
    assert response.status_code == 401


def test_logout_invalidates_the_session_server_side(settings):
    """auto_login_local=False here so a stale session token would 401 rather
    than silently succeed via the default-local fallback - proving the
    session itself was deleted, not just that the browser's cookie expired."""
    client, _ = _auth_client(settings, auto_login_local=False)
    client.post("/auth/signup", json={"email": "person@example.com", "password": "correct-horse-battery"})
    session_token = client.cookies.get("apex_session")
    assert client.get("/auth/me").status_code == 200

    logout = client.post("/auth/logout")
    assert logout.status_code == 200

    client.cookies.set("apex_session", session_token)  # replay the now-logged-out token
    assert client.get("/auth/me").status_code == 401


def test_me_falls_back_to_default_local_account_when_auto_login_is_on(settings):
    client, services = _auth_client(settings, auto_login_local=True)
    response = client.get("/auth/me")
    assert response.status_code == 200
    assert response.json()["id"] == services.default_local_user.id
    assert response.json()["is_default_local"] is True


def test_me_requires_real_login_when_auto_login_is_off(settings):
    client, _ = _auth_client(settings, auto_login_local=False)
    response = client.get("/auth/me")
    assert response.status_code == 401


def test_explicit_login_takes_precedence_over_auto_login_default(settings):
    client, services = _auth_client(settings, auto_login_local=True)
    client.post("/auth/signup", json={"email": "person@example.com", "password": "correct-horse-battery"})
    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "person@example.com"
    assert me.json()["id"] != services.default_local_user.id
