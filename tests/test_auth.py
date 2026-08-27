import pytest
from apex_auth import AccountError, AuthService


def service(tmp_path):
    return AuthService(tmp_path / "accounts.sqlite3")


def test_registration_verification_login_logout(tmp_path):
    auth = service(tmp_path)
    verification = auth.register("Ada@Example.com", "a sufficiently long password", "Ada")
    with pytest.raises(AccountError):
        auth.login("ada@example.com", "a sufficiently long password")
    auth.verify_email(verification)
    session = auth.login("ada@example.com", "a sufficiently long password")
    user = auth.current_user(session)
    assert user["email"] == "ada@example.com"
    auth.logout(user["session"])
    with pytest.raises(AccountError):
        auth.current_user(user["session"])


def test_password_reset_invalidates_sessions(tmp_path):
    auth = service(tmp_path)
    verify = auth.register("user@example.com", "a sufficiently long password", "User")
    auth.verify_email(verify)
    session = auth.login("user@example.com", "a sufficiently long password")
    reset = auth.request_password_reset("user@example.com")
    assert reset
    auth.reset_password(reset, "an even longer replacement password")
    with pytest.raises(AccountError):
        auth.current_user(session)
    assert auth.login("user@example.com", "an even longer replacement password")


def test_security_validation_and_non_enumerating_reset(tmp_path):
    auth = service(tmp_path)
    with pytest.raises(AccountError):
        auth.register("bad", "short", "")
    assert auth.request_password_reset("nobody@example.com") is None
    with pytest.raises(AccountError):
        auth.register("user@example.com", "short", "User")
