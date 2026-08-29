"""Phase 51/52 — the account/session workflow used by the API layer.

Combines ``UserStore`` and ``SessionStore`` into the operations the API
actually needs (signup, login, logout, current-user lookup, and the
default-local-account bootstrap), so ``apex_ai/api/auth.py`` stays a thin
transport layer, matching how ``apex_ai/api/chat.py`` relates to ``RagEngine``.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from apex_ai.auth.sessions import Session, SessionStore
from apex_ai.auth.users import User, UserStore

DEFAULT_LOCAL_DISPLAY_NAME = "Local user"


class InvalidCredentialsError(ValueError):
    """Deliberately generic: callers must show one message regardless of
    whether the email or the password was wrong (Phase 52's "established
    security practices" — don't help an attacker enumerate valid emails)."""


@dataclass(frozen=True)
class AuthResult:
    user: User
    session: Session


class AuthService:
    def __init__(
        self,
        users: UserStore,
        sessions: SessionStore,
        *,
        session_ttl_days: int = 30,
    ) -> None:
        self.users = users
        self.sessions = sessions
        self.session_ttl_days = session_ttl_days

    def signup(self, email: str, password: str, *, display_name: str = "") -> AuthResult:
        # EmailAlreadyRegisteredError and plain ValueError (weak password, bad
        # email format) both propagate as-is; the API layer maps them to a 400
        # with the message already user-facing.
        user = self.users.create(email, password, display_name=display_name)
        session = self.sessions.create(user.id, ttl_days=self.session_ttl_days)
        return AuthResult(user, session)

    def login(self, email: str, password: str) -> AuthResult:
        user = self.users.verify_credentials(email, password)
        if user is None:
            raise InvalidCredentialsError("Incorrect email or password.")
        session = self.sessions.create(user.id, ttl_days=self.session_ttl_days)
        return AuthResult(user, session)

    def logout(self, token: str) -> None:
        self.sessions.delete(token)

    def user_for_session(self, token: str) -> User | None:
        session = self.sessions.get(token)
        if session is None:
            return None
        return self.users.get(session.user_id)

    def ensure_default_local_account(self) -> User:
        """Idempotent: safe to call on every startup. Creates the one
        auto-login account (Phase 51 "so python ui.py still works standalone")
        only if it doesn't already exist; a fresh random password is set so
        there is no known/shared default credential, even though the normal
        flow never needs it (see ``auto_login_local`` in Settings)."""
        existing = self.users.get_default_local()
        if existing is not None:
            return existing
        random_password = secrets.token_urlsafe(24)
        return self.users.create(
            "local@apex.local",
            random_password,
            display_name=DEFAULT_LOCAL_DISPLAY_NAME,
            is_default_local=True,
        )


__all__ = ["AuthResult", "AuthService", "InvalidCredentialsError"]
